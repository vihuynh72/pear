"""Better picks (addendum 7): three food products that fit EVERY household member.

Flow
    identify (barcode / photo / ask) --> start_prefetch(clients, np, household)   returns at once
        background compute():
            candidates  vector path: the scanned product's stored embedding (else embed it now)
                        -> product_store.find_nearest(50, food)
                        fallback:    product_store.by_category_main(category_main, 60)
            normalize   stored .normalized, re-normalized from .raw when kb_version changed
            rank        rules.engine.evaluate_many for all members; drop the scanned product and
                        anything ALLERGY / UNSURE / DIET / SKIP for anyone (and products with no
                        ingredient list); sort same category_main first, then all-RIPE before any
                        EASY, then vector distance; top 3
            prices      prices.start() for each pick (P2, background, cached)
    POST /v1/picks --> get(): reuse the in-flight task (prefetched=True) or load + compute now
                       (prefetched=False); wait at most settings.t_picks_wait, else items [].

In-flight tasks live in INFLIGHT, keyed "<product_key>|<hh_hash(household)>", newest 200 kept.
One process only: the prefetch and the /v1/picks call must land on the same instance
(Cloud Run --max-instances 1, one uvicorn worker; addendum 13.3).

Debugging
- GET /debug/inflight shows stats(): every key, whether it is running/done/failed, the
  candidate path (vector | category | vector+category when no neighbour fit | none),
  candidate count, drop reasons and stage ms.
- Log events: picks.prefetch, picks.computed, picks.vector_failed, picks.timeout,
  picks.failed, picks.product_missing, picks.load_failed, picks.rank_slow (> 100 ms).
  Filter on event=picks.* in Cloud Logging.
- /v1/picks?debug=1 puts the same diagnostics in the X-Pear-Debug response header
  (PicksResponse has no debug field) and in the request's debug bag.
- Background work runs in its own timing context, so its stages never leak into the scan
  request's timings_ms; the stage ms come back in the result's "diag" instead.
- Feature flags: PEAR_FEATURE_PICKS=0 turns picks into an empty response, PEAR_FEATURE_VECTOR=0
  forces the category_main fallback, PEAR_FEATURE_PRICES=0 skips price lookups.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from typing import Any

from core import timing
from core.background import spawn
from core.clients import Clients
from core.logging import log_event
from core.timing import debug_put, request_id, stage
from models.contracts import Household, PickItem, PickProduct, PicksResponse, PickVerdict
from models.domain import NormalizedProduct, StoredProduct

INFLIGHT: dict[str, asyncio.Task] = {}
_BORN: dict[str, float] = {}  # key -> time.time() the task started (for TTL + stats)
MAX_INFLIGHT = 200
RESULT_TTL_S = 600.0  # recompute a finished result after 10 minutes (new products get ingested)
TOP_N = 3
VECTOR_LIMIT = 50
CATEGORY_LIMIT = 60
RANK_BUDGET_MS = 100  # addendum 7: 50 candidates x 5 members
DROP_STATES = frozenset({"ALLERGY", "UNSURE", "DIET", "SKIP"})
_NO_LIST = frozenset({"missing", "unreadable"})

_COUNTERS: dict[str, Any] = {
    "prefetch_started": 0, "cold_started": 0, "reused": 0, "timeouts": 0, "failed": 0,
    "paths": {"vector": 0, "category": 0, "none": 0},
}


# ----------------------------------------------------------------------------- lazy deps
# Imported lazily so this module (and /v1/picks) loads even while a sibling lane is broken;
# tests monkeypatch these three accessors with fakes.


def _store():
    from pipeline import product_store

    return product_store


def _embeddings():
    from pipeline import embeddings

    return embeddings


def _products():
    from pipeline import products

    return products


def _prices():
    from pipeline import prices

    return prices


# ----------------------------------------------------------------------------- keys


def _as_household(household: Household | dict | None) -> Household:
    if isinstance(household, Household):
        return household
    return Household.model_validate(household or {})


def hh_hash(household: Household | dict) -> str:
    """Stable 12-hex hash over the members (same household JSON -> same key)."""
    hh = _as_household(household)
    members = [m.model_dump(mode="json") for m in hh.members]
    return hashlib.sha1(json.dumps(members, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def inflight_key(product_key: str, household: Household | dict) -> str:
    return f"{product_key}|{hh_hash(household)}"


def _live_task(key: str) -> asyncio.Task | None:
    """The task to reuse for key, or None when there is none / it failed / it is stale."""
    task = INFLIGHT.get(key)
    if task is None:
        return None
    if not task.done():
        return task
    if task.cancelled() or task.exception() is not None:
        return None
    if time.time() - _BORN.get(key, 0.0) > RESULT_TTL_S:
        return None
    return task


def _register(key: str, task: asyncio.Task) -> None:
    INFLIGHT.pop(key, None)  # re-insert so dict order = age order
    INFLIGHT[key] = task
    _BORN[key] = time.time()
    while len(INFLIGHT) > MAX_INFLIGHT:
        oldest = next(iter(INFLIGHT))
        INFLIGHT.pop(oldest, None)  # background.spawn still holds a strong ref if it runs
        _BORN.pop(oldest, None)


def reset() -> None:
    """Forget every in-flight/finished task (tests, /debug)."""
    INFLIGHT.clear()
    _BORN.clear()


# ----------------------------------------------------------------------------- prefetch


def start_prefetch(clients: Clients, np: NormalizedProduct, household: Household | dict) -> None:
    """Call right after identify. Returns at once, never raises (food only)."""
    try:
        if not clients.settings.feature_picks or np.product.category != "food":
            return
        asyncio.get_running_loop()
        hh = _as_household(household)
        key = inflight_key(np.product.key, hh)
        if _live_task(key) is not None:
            return
        _register(key, spawn("picks_prefetch", compute(clients, np, hh)))
        _COUNTERS["prefetch_started"] += 1
        log_event("picks.prefetch", product_key=np.product.key, key=key)
    except Exception as e:  # picks must never break the scan path
        log_event("picks.prefetch_error", level=logging.WARNING, product_key=np.product.key, why=repr(e))


# ----------------------------------------------------------------------------- compute


async def _stored_vector(clients: Clients, key: str) -> list[float] | None:
    """The scanned product's stored embedding (cache-hit scans), saving one embed call."""
    try:
        sp = await _store().get(clients, key)
    except Exception:
        return None
    return list(sp.embedding) if sp is not None and sp.embedding else None


async def _query_vector(clients: Clients, np: NormalizedProduct, diag: dict[str, Any]) -> list[float] | None:
    vec = await _stored_vector(clients, np.product.key)
    if vec:
        diag["query_vector"] = "stored"
        return vec
    emb = _embeddings()
    diag["query_vector"] = "embedded"
    vec = await emb.embed_product(clients, np)
    if not vec:
        vec = await emb.embed_query(clients, emb.embedding_text(np))
    return vec or None


async def _candidates(clients: Clients, np: NormalizedProduct,
                      diag: dict[str, Any]) -> tuple[list[tuple[StoredProduct, float | None]], str]:
    """(stored product, distance|None) pairs plus the path that produced them."""
    s = clients.settings
    store = _store()
    if s.feature_vector:
        try:
            with stage("picks_embed"):
                vec = await _query_vector(clients, np, diag)
            if vec:
                with stage("picks_nearest"):
                    found = await store.find_nearest(clients, vec, category="food", limit=VECTOR_LIMIT)
                if found:
                    return [(sp, float(d) if d is not None else None) for sp, d in found], "vector"
                diag["vector_skip"] = "no neighbours"
            else:
                diag["vector_skip"] = "no query vector"
        except Exception as e:  # index not ready, embed failed, store down -> fallback
            diag["vector_skip"] = repr(e)[:300]
            log_event("picks.vector_failed", level=logging.WARNING, product_key=np.product.key,
                      why=repr(e)[:300])
    else:
        diag["vector_skip"] = "feature_vector off"
    rows = await _category_candidates(clients, np, diag)
    return rows, ("category" if rows else "none")


async def _category_candidates(clients: Clients, np: NormalizedProduct,
                               diag: dict[str, Any]) -> list[tuple[StoredProduct, float | None]]:
    """Addendum 7 fallback: same category_main, limit 60, no distance."""
    if not np.category_main:
        diag["category_skip"] = "scanned product has no category_main"
        return []
    try:
        with stage("picks_category"):
            rows = await _store().by_category_main(clients, np.category_main, limit=CATEGORY_LIMIT)
    except Exception as e:
        diag["category_skip"] = repr(e)[:300]
        log_event("picks.category_failed", level=logging.WARNING, product_key=np.product.key,
                  why=repr(e)[:300])
        return []
    return [(sp, None) for sp in rows]


def _kb_version() -> str | None:
    try:
        from rules.kb import get_kb

        return get_kb().version
    except Exception:
        return None


def _normalized(sp: StoredProduct, kb_version: str | None) -> NormalizedProduct | None:
    """Stored normalization when current, else re-normalize from raw (stale beats nothing)."""
    stored = sp.normalized
    if stored is not None and (kb_version is None or stored.kb_version == kb_version):
        return stored
    try:
        from rules.normalize import normalize

        return normalize(sp.raw)
    except Exception as e:
        log_event("picks.renormalize_failed", level=logging.WARNING, key=sp.key, why=repr(e)[:200])
        return stored


def _same_code(a: NormalizedProduct, b: NormalizedProduct) -> bool:
    return bool(a.product.code) and a.product.code == b.product.code


def _eligible(scanned: NormalizedProduct, pairs: list[tuple[StoredProduct, float | None]],
              diag: dict[str, Any]) -> list[tuple[NormalizedProduct, float | None]]:
    """Drop the scanned product, duplicates, non-food, nameless and ingredient-less candidates."""
    kbv = _kb_version()
    drops = diag.setdefault("dropped", {})
    seen: set[str] = {scanned.product.key}
    out: list[tuple[NormalizedProduct, float | None]] = []
    for sp, dist in pairs:
        if sp.key in seen:
            drops["scanned_or_duplicate"] = drops.get("scanned_or_duplicate", 0) + 1
            continue
        seen.add(sp.key)
        cand = _normalized(sp, kbv)
        why = None
        if cand is None:
            why = "not_normalized"
        elif _same_code(cand, scanned):
            why = "scanned_or_duplicate"
        elif cand.product.category != "food":
            why = "not_food"
        elif not cand.product.name:
            why = "no_name"
        elif cand.legibility in _NO_LIST or not cand.product.ingredients:
            why = "no_ingredients"  # never recommend a product we can't check
        if why:
            drops[why] = drops.get(why, 0) + 1
            continue
        out.append((cand, dist))
    return out


def rank(scanned: NormalizedProduct, pairs: list[tuple[StoredProduct, float | None]],
         household: Household, diag: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Pure ranking (addendum 7). Returns up to TOP_N entries:
    {"product": PickProduct, "verdicts": [PickVerdict], "all_ripe", "same_category", "distance",
     "price_product": {key, name, brand, quantity, code}}.
    """
    from rules.engine import evaluate_many

    diag = diag if diag is not None else {}
    with stage("picks_normalize"):
        cands = _eligible(scanned, pairs, diag)
    if not cands:
        return []
    t0 = time.perf_counter()
    with stage("picks_rank"):
        evals = evaluate_many([c for c, _ in cands], household)
        drops = diag.setdefault("dropped", {})
        scored: list[tuple[tuple, dict[str, Any]]] = []
        for i, ((cand, dist), ev) in enumerate(zip(cands, evals, strict=True)):
            states = [v.state for v in ev.verdicts]
            bad = DROP_STATES.intersection(states)
            if bad:
                for st in bad:
                    drops[st] = drops.get(st, 0) + 1
                continue
            same = bool(scanned.category_main) and cand.category_main == scanned.category_main
            all_ripe = all(st == "RIPE" for st in states)
            sort_key = (0 if same else 1, 0 if all_ripe else 1,
                        dist if dist is not None else math.inf, i)
            scored.append((sort_key, _entry(cand, ev.verdicts, same, all_ripe, dist)))
        scored.sort(key=lambda x: x[0])
    diag["rank_ms"] = int((time.perf_counter() - t0) * 1000)
    diag["evaluated"] = len(cands)
    if diag["rank_ms"] > RANK_BUDGET_MS:
        log_event("picks.rank_slow", level=logging.WARNING, ms=diag["rank_ms"], candidates=len(cands),
                  members=len(household.members), budget_ms=RANK_BUDGET_MS)
    return [e for _, e in scored[:TOP_N]]


def _entry(cand: NormalizedProduct, verdicts: list, same: bool, all_ripe: bool,
           dist: float | None) -> dict[str, Any]:
    p = cand.product
    return {
        "product": PickProduct(key=p.key, name=p.name, brand=p.brand, image_url=p.image_url,
                               category=p.category),
        "verdicts": [PickVerdict(member_id=v.member_id, state=v.state, label=v.label) for v in verdicts],
        "all_ripe": all_ripe,
        "same_category": same,
        "distance": dist,
        "price_product": {"key": p.key, "name": p.name, "brand": p.brand, "quantity": p.quantity,
                          "code": p.code},
    }


async def compute(clients: Clients, np: NormalizedProduct, household: Household | dict) -> dict[str, Any]:
    """Candidates -> rank -> start prices. Runs as a background task (own timing context)."""
    token = timing.begin(f"picks:{np.product.key}")  # keeps the scan request's timings clean
    try:
        return await _compute(clients, np, _as_household(household))
    finally:
        timing.end(token)


async def _compute(clients: Clients, np: NormalizedProduct, hh: Household) -> dict[str, Any]:
    t0 = time.perf_counter()
    diag: dict[str, Any] = {"product_key": np.product.key, "category_main": np.category_main}
    pairs, path = await _candidates(clients, np, diag)
    _COUNTERS["paths"][path] = _COUNTERS["paths"].get(path, 0) + 1
    diag.update(path=path, candidates=len(pairs))
    items = rank(np, pairs, hh, diag)
    if not items and path == "vector":
        # 50 neighbours and none fit everyone: the same-category shelf may still have one.
        seen = {sp.key for sp, _ in pairs}
        more = [(sp, d) for sp, d in await _category_candidates(clients, np, diag) if sp.key not in seen]
        if more:
            path = "vector+category"
            diag.update(path=path, candidates=len(pairs) + len(more))
            items = rank(np, more, hh, diag)
    if clients.settings.feature_prices and items:
        prices = _prices()
        for it in items:
            try:
                prices.start(clients, it["price_product"])
            except Exception as e:  # prices are P2; never fail picks over them
                log_event("picks.price_start_failed", level=logging.WARNING, why=repr(e)[:200])
    diag["stages_ms"] = timing.timings()
    diag["compute_ms"] = int((time.perf_counter() - t0) * 1000)
    log_event("picks.computed", product_key=np.product.key, path=path, candidates=len(pairs),
              items=len(items), ms=diag["compute_ms"], rank_ms=diag.get("rank_ms"),
              dropped=diag.get("dropped"))
    return {"items": items, "path": path, "diag": diag}


# ----------------------------------------------------------------------------- get


def _response(items: list[dict[str, Any]], prefetched: bool, member_ids: list[str] | None,
              household: Household, wait_ms: int, t0: float, extra: dict[str, int] | None = None,
              ) -> PicksResponse:
    shown = {m.id for m in household.select(member_ids)}
    out: list[PickItem] = []
    for it in items:
        verdicts = [v for v in it["verdicts"] if v.member_id in shown]
        out.append(PickItem(product=it["product"], verdicts=verdicts))
    tm = {"wait": wait_ms, **(extra or {}), "total": int((time.perf_counter() - t0) * 1000)}
    return PicksResponse(items=out, prefetched=prefetched, timings_ms=tm, request_id=request_id())


async def _load(clients: Clients, product_key: str) -> NormalizedProduct | None:
    """Cold path product load; a store error means "no picks", never a failed request."""
    try:
        with stage("picks_load"):
            return await _products().load_product(clients, product_key)
    except Exception as e:
        log_event("picks.load_failed", level=logging.WARNING, product_key=product_key, why=repr(e)[:300])
        debug_put("picks_load_error", repr(e)[:500])
        return None


async def get(clients: Clients, product_key: str, household: Household | dict,
              member_ids: list[str] | None = None, *, np: NormalizedProduct | None = None,
              ) -> PicksResponse:
    """Picks for product_key. Never raises for picks-specific problems: returns items [].

    member_ids only filters which members' chips are shown; picks always fit everyone.
    np: the product, when the caller already has it (skips load_product on the cold path).
    """
    t0 = time.perf_counter()
    hh = _as_household(household)
    if not clients.settings.feature_picks:
        debug_put("picks", {"skipped": "feature_picks off (PEAR_FEATURE_PICKS=0)"})
        return _response([], False, member_ids, hh, 0, t0)
    key = inflight_key(product_key, hh)
    task = _live_task(key)
    prefetched = task is not None
    if prefetched:
        _COUNTERS["reused"] += 1
    else:
        if np is None:
            np = await _load(clients, product_key)
        if np is None:
            log_event("picks.product_missing", level=logging.WARNING, product_key=product_key)
            debug_put("picks", {"skipped": "product not found", "product_key": product_key})
            return _response([], False, member_ids, hh, 0, t0)
        if np.product.category != "food":
            debug_put("picks", {"skipped": f"category {np.product.category} (picks are food only)"})
            return _response([], False, member_ids, hh, 0, t0)
        task = spawn("picks_cold", compute(clients, np, hh))
        _register(key, task)
        _COUNTERS["cold_started"] += 1
    return await _await_task(clients, task, key, prefetched, member_ids, hh, t0)


async def _await_task(clients: Clients, task: asyncio.Task, key: str, prefetched: bool,
                      member_ids: list[str] | None, hh: Household, t0: float) -> PicksResponse:
    tw = time.perf_counter()
    items: list[dict[str, Any]] = []
    extra: dict[str, int] = {}
    try:
        with stage("picks_wait"):
            result = await asyncio.wait_for(asyncio.shield(task), timeout=clients.settings.t_picks_wait)
        items = result["items"]
        diag = result.get("diag", {})
        debug_put("picks", {**diag, "prefetched": prefetched, "key": key})
        if diag.get("compute_ms") is not None:
            extra["compute"] = int(diag["compute_ms"])
    except TimeoutError:
        _COUNTERS["timeouts"] += 1
        log_event("picks.timeout", level=logging.WARNING, key=key, prefetched=prefetched,
                  wait_s=clients.settings.t_picks_wait)
        debug_put("picks", {"timeout_s": clients.settings.t_picks_wait, "key": key,
                            "prefetched": prefetched, "note": "task keeps running; retry gets it"})
    except Exception as e:  # compute failed (store/engine); logged by background.spawn too
        _COUNTERS["failed"] += 1
        log_event("picks.failed", level=logging.WARNING, key=key, why=repr(e)[:300])
        debug_put("picks", {"error": repr(e)[:500], "key": key})
    wait_ms = int((time.perf_counter() - tw) * 1000)
    return _response(items, prefetched, member_ids, hh, wait_ms, t0, extra)


async def for_question(clients: Clients, body: dict) -> PicksResponse | None:
    """Picks for /v1/ask intent=alternatives. None when no product can be resolved.

    body: last_product_key (or product_key), household, optional member_ids / product_query.
    """
    try:
        hh = _as_household(body.get("household"))
        member_ids = body.get("member_ids") or None
        key = body.get("last_product_key") or body.get("product_key")
        np: NormalizedProduct | None = None
        if not key and body.get("product_query"):
            np = await _products().match_by_name(clients, query=body["product_query"])
            key = np.product.key if np is not None else None
        if not key:
            debug_put("picks", {"skipped": "no last_product_key or matchable product_query"})
            return None
        return await get(clients, key, hh, member_ids, np=np)
    except Exception as e:
        log_event("picks.for_question_failed", level=logging.WARNING, why=repr(e)[:300])
        return None


# ----------------------------------------------------------------------------- stats


def _task_state(task: asyncio.Task) -> str:
    if not task.done():
        return "running"
    if task.cancelled():
        return "cancelled"
    return "failed" if task.exception() is not None else "done"


def stats() -> dict[str, Any]:
    """For GET /debug/inflight."""
    now = time.time()
    states = {k: _task_state(t) for k, t in INFLIGHT.items()}
    newest = []
    for k in list(INFLIGHT)[-10:][::-1]:
        t = INFLIGHT[k]
        row: dict[str, Any] = {"key": k, "state": states[k], "age_s": round(now - _BORN.get(k, now), 1)}
        if row["state"] == "done":
            res = t.result()
            row.update(items=[i["product"].key for i in res["items"]], path=res.get("path"),
                       diag=res.get("diag"))
        elif row["state"] == "failed":
            row["error"] = repr(t.exception())[:300]
        newest.append(row)
    return {
        "count": len(INFLIGHT),
        "running": sum(1 for s in states.values() if s == "running"),
        "done": sum(1 for s in states.values() if s == "done"),
        "failed": sum(1 for s in states.values() if s in ("failed", "cancelled")),
        "counters": {**_COUNTERS, "paths": dict(_COUNTERS["paths"])},
        "newest": newest,
    }
