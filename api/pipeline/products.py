"""Product identity: barcode -> product, key -> product, name -> product, and the background
work after a scan (cache write + embedding, enrichment, scan log).

    raw, np, cached = await identify_barcode(clients, "049000028911")   # PearError not_found
    np = await load_product(clients, "off:0049000028911")    # store, else re-fetch by prefix
    np = await match_by_name(clients, brand="Nissin", name="Top Ramen Chicken")   # less sure
    after_scan(clients, raw=raw, np=np, household=hh, uid=uid, evaluation=ev, cached=cached)

identify_barcode
  stage "cache_get"  product_store.get_by_code (one Firestore get_all over every key spelling)
  stage "lookup"     lookup_off.lookup_barcode (OFF + OBF race, then FDC) on a miss
  stage "normalize"  rules.normalize.normalize; stored products normalized with an older
                     kb version are re-normalized (enrichment hits are carried over) and
                     re-saved in the background.

match_by_name (front-only photos, spoken product names)
  1. embed_query + product_store.find_nearest(category food), top hit with cosine distance
     <= PEAR_NAME_MATCH_MAX_DIST (default 0.35) and at least one shared word with the query.
  2. else lookup_off.search_by_name, first hit sharing a word, re-read in full by code.
  The result is marked less_sure, ask_for_ingredients, data_confidence medium (or low),
  matched_by "name". The store always keeps the unmarked version.

Debugging: ?debug=1 shows `cache` (hit, key, refreshed), `lookup` (race details) and
`name_match` (candidates with distances). Logs: product.cache_hit, product.lookup_miss,
product.name_match, product.saved, product.enrich_failed, product.after_scan_failed.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from core.background import spawn
from core.clients import Clients
from core.errors import PearError
from core.keys import barcode_variants, digits, split_key
from core.logging import log_event
from core.timing import debug_put, stage, timings
from models.contracts import Household
from models.domain import Evaluation, Hit, NormalizedProduct, RawProduct, StoredProduct
from pipeline import embeddings, lookup_fdc, lookup_off, product_store

NAME_MATCH_MAX_DIST = float(os.environ.get("PEAR_NAME_MATCH_MAX_DIST", "0.35"))
# A cached record without an ingredient list is re-checked at the source after this long
# (someone may have added the ingredients on Open Food Facts since).
MISSING_RECHECK_S = float(os.environ.get("PEAR_MISSING_RECHECK_S", "600"))
log = logging.getLogger("pear.products")

# Keys of cached products whose stored record has no embedding yet (after_scan retries).
_NEEDS_EMBED: set[str] = set()
_STOPWORDS = {"the", "and", "with", "for", "of", "con", "de", "la", "el", "los", "las", "và", "flavor",
              "flavour", "original", "new", "brand"}


def tr(key: str, language: str, default: str, **kw: Any) -> str:
    """rules.templates.t with a local fallback, so a missing/broken templates module can't
    turn an error message into a 500."""
    try:
        from rules.templates import t

        return t(key, language, default=default, **kw)
    except Exception:  # noqa: BLE001
        try:
            return default.format(**kw)
        except Exception:  # noqa: BLE001
            return default


# ----------------------------------------------------------------------------- normalization


def _kb_version() -> str | None:
    try:
        from rules.kb import get_kb

        return get_kb().version
    except Exception:  # noqa: BLE001 - stub/broken kb: trust what is stored
        return None


def normalize_raw(raw: RawProduct, extra_hits: list[Hit] | None = None) -> NormalizedProduct:
    from rules.normalize import normalize

    with stage("normalize"):
        return normalize(raw, extra_hits=extra_hits)


def current_normalized(sp: StoredProduct) -> tuple[NormalizedProduct, bool]:
    """(normalized product, refreshed?) — re-normalizes when the kb version changed."""
    ver = _kb_version()
    if sp.normalized is not None and (ver is None or sp.normalized.kb_version == ver):
        return sp.normalized, False
    carried = [h for h in (sp.normalized.hits if sp.normalized else []) if h.where == "enrich"]
    return normalize_raw(sp.raw, extra_hits=carried or None), True


def stored_from(raw: RawProduct, np: NormalizedProduct, embedding: list[float] | None) -> StoredProduct:
    return StoredProduct(
        key=np.product.key,
        source=raw.source,
        category=np.product.category,
        category_main=np.category_main or raw.category_main,
        kb_ids=sorted(np.kb_ids()),
        kb_version=np.kb_version,
        raw=raw,
        normalized=np,
        embedding=embedding,
        fetched_at=raw.fetched_at,
    )


async def save_product(clients: Clients, raw: RawProduct, np: NormalizedProduct, *,
                       embedding: list[float] | None = None, only_if_embedded: bool = False) -> bool:
    """Embed (unless given) and write products/{key}. Background-safe: never raises."""
    key = np.product.key
    try:
        emb = embedding or await embeddings.embed_product(clients, np)
        if only_if_embedded and not emb:
            return False
        ok = await product_store.put(clients, stored_from(raw, np, emb))
        if emb:
            _NEEDS_EMBED.discard(key)
        else:
            _NEEDS_EMBED.add(key)
        log_event("product.saved", product_key=key, ok=ok, embedded=bool(emb))
        return ok
    except Exception as e:  # noqa: BLE001
        log_event("product.save_failed", level=logging.WARNING, product_key=key, why=repr(e)[:300])
        return False


# ----------------------------------------------------------------------------- barcode


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return (datetime.now(UTC) - t).total_seconds()


def _recheck_missing(sp: StoredProduct) -> bool:
    if sp.raw.legibility != "missing" or sp.raw.source == "photo":
        return False
    age = _age_s(sp.raw.fetched_at or sp.fetched_at)
    return age is None or age > MISSING_RECHECK_S


async def _lookup(clients: Clients, code: str, language: str) -> RawProduct | None:
    with stage("lookup"):
        try:
            return await lookup_off.lookup_barcode(clients, code)
        except lookup_off.LookupThrottled as e:
            raise PearError("rate_limited", tr("error.rate_limited", language,
                                               default="Pear is busy. Try again in a moment."),
                            next="retry", detail={"code": code, "why": "Open Food Facts rate limit"}) from e


async def identify_barcode(clients: Clients, code: str, *, language: str = "en"
                           ) -> tuple[RawProduct, NormalizedProduct, bool]:
    """Barcode -> (raw, normalized, cached). Raises PearError not_found (next take_photo)."""
    c = digits(code)
    if len(c) < 6:
        raise PearError("bad_request", tr("error.barcode_invalid", language,
                                          default="That barcode doesn't look right. Try scanning again?"),
                        next="retry", detail={"code": code})
    stored = await product_store.get_by_code(clients, c)
    if stored is not None and _recheck_missing(stored):
        try:
            fresh = await _lookup(clients, c, language)
        except PearError:
            fresh = None
        debug_put("recheck_missing", {"key": stored.key, "found_ingredients": bool(fresh and fresh.ingredients)})
        if fresh is not None:  # cached=False -> after_scan re-saves it (fresh fetched_at)
            log_event("product.recheck", product_key=fresh.key, has_ingredients=bool(fresh.ingredients))
            return fresh, normalize_raw(fresh), False
    if stored is not None:
        np, refreshed = current_normalized(stored)
        if refreshed:
            spawn("product_refresh", save_product(clients, stored.raw, np, embedding=stored.embedding))
        if stored.embedding:
            _NEEDS_EMBED.discard(stored.key)
        else:
            _NEEDS_EMBED.add(stored.key)
        debug_put("cache", {"hit": True, "key": stored.key, "refreshed": refreshed,
                            "embedded": bool(stored.embedding)})
        log_event("product.cache_hit", product_key=stored.key, refreshed=refreshed)
        return stored.raw, np, True
    debug_put("cache", {"hit": False, "variants": barcode_variants(c)})
    raw = await _lookup(clients, c, language)
    if raw is None:
        log_event("product.lookup_miss", code=c)
        raise PearError("not_found", tr("error.barcode_not_found", language,
                                        default="We couldn't find this barcode. Snap the ingredients?"),
                        next="take_photo", detail={"code": c, "variants": barcode_variants(c)})
    return raw, normalize_raw(raw), False


# ----------------------------------------------------------------------------- by key


async def load_product(clients: Clients, key: str) -> NormalizedProduct | None:
    """Store first; else re-fetch from the source named by the key prefix (photo:* store only)."""
    sp = await product_store.get(clients, key)
    if sp is not None:
        np, refreshed = current_normalized(sp)
        if refreshed:
            spawn("product_refresh", save_product(clients, sp.raw, np, embedding=sp.embedding))
        return np
    prefix, code = split_key(key)
    raw: RawProduct | None = None
    try:
        if prefix in ("off", "obf"):
            raw = await lookup_off.lookup_source(clients, prefix, code)  # type: ignore[arg-type]
        elif prefix == "fdc":
            raw = await lookup_fdc.lookup_gtin(clients, code)
    except Exception as e:  # noqa: BLE001
        log_event("product.load_failed", level=logging.WARNING, product_key=key, why=repr(e)[:300])
        return None
    if raw is None:
        return None
    np = normalize_raw(raw)
    spawn("product_save", save_product(clients, raw, np))
    return np


# ----------------------------------------------------------------------------- by name


def _tokens(text: str | None) -> set[str]:
    words = re.findall(r"\w+", (text or "").lower())
    return {w for w in words if (len(w) >= 3 or not w.isascii()) and w not in _STOPWORDS and not w.isdigit()}


def _shares_word(query: str, name: str | None, brand: str | None) -> bool:
    q = _tokens(query)
    p = _tokens(f"{brand or ''} {name or ''}")
    if not q or not p:
        return False
    return bool(q & p)


def mark_name_match(np: NormalizedProduct) -> NormalizedProduct:
    conf = "low" if np.product.data_confidence == "low" else "medium"
    product = np.product.model_copy(update={"less_sure": True, "ask_for_ingredients": True,
                                            "data_confidence": conf, "matched_by": "name"})
    return np.model_copy(update={"product": product})


async def _vector_match(clients: Clients, text: str, category: str, diag: dict[str, Any]
                        ) -> NormalizedProduct | None:
    vec = await embeddings.embed_query(clients, text)
    if not vec:
        diag["vector"] = "no query vector"
        return None
    rows = await product_store.find_nearest(clients, vec, category=category, limit=5)
    diag["vector"] = [{"key": sp.key, "name": sp.raw.name, "dist": round(d, 4)} for sp, d in rows]
    for sp, dist in rows:
        if dist <= NAME_MATCH_MAX_DIST and _shares_word(text, sp.raw.name, sp.raw.brand):
            diag["matched"] = {"by": "vector", "key": sp.key, "dist": round(dist, 4)}
            np, refreshed = current_normalized(sp)
            if refreshed:
                spawn("product_refresh", save_product(clients, sp.raw, np, embedding=sp.embedding))
            return np
    return None


async def _search_match(clients: Clients, text: str, brand: str | None, name: str | None,
                        query: str | None, diag: dict[str, Any]) -> NormalizedProduct | None:
    hits = await lookup_off.search_by_name(clients, name or query or text, brand=brand, limit=5)
    diag["search"] = [{"key": r.key, "name": r.name, "brand": r.brand} for r in hits]
    for hit in hits:
        if not _shares_word(text, hit.name, hit.brand):
            continue
        sp = await product_store.get(clients, hit.key)
        if sp is not None:
            diag["matched"] = {"by": "search+store", "key": sp.key}
            return current_normalized(sp)[0]
        code = hit.code or split_key(hit.key)[1]
        full = await lookup_off.lookup_source(clients, "off", code) if code else None
        raw = full or hit  # search hits lack ingredients; the full read has them
        np = normalize_raw(raw)
        spawn("product_save", save_product(clients, raw, np))
        diag["matched"] = {"by": "search", "key": raw.key, "full_record": full is not None}
        return np
    return None


async def match_by_name(clients: Clients, *, brand: str | None = None, name: str | None = None,
                        query: str | None = None, category: str | None = None) -> NormalizedProduct | None:
    """Best stored/OFF product for a brand + name (or free query). Marked "less sure"."""
    text = (query or " ".join(x for x in (brand, name) if x) or "").strip()
    if not text:
        return None
    diag: dict[str, Any] = {"text": text, "max_dist": NAME_MATCH_MAX_DIST}
    np: NormalizedProduct | None = None
    try:
        with stage("name_match"):
            if clients.settings.feature_vector:
                np = await _vector_match(clients, text, category or "food", diag)
            if np is None:
                np = await _search_match(clients, text, brand, name, query, diag)
    except Exception as e:  # noqa: BLE001 - a failed match is "no match", never a crash
        diag["error"] = repr(e)[:300]
        log_event("product.name_match_failed", level=logging.WARNING, text=text, why=repr(e)[:300])
        np = None
    debug_put("name_match", diag)
    log_event("product.name_match", text=text, matched=diag.get("matched"))
    return mark_name_match(np) if np is not None else None


# ----------------------------------------------------------------------------- after a scan


async def _save_and_enrich(clients: Clients, raw: RawProduct, np: NormalizedProduct, *,
                           cached: bool, enrich: bool) -> None:
    if np.product.matched_by == "name":  # never store the "less sure" name-match marks
        if raw.matched_by == "name":
            raw = raw.model_copy(update={"matched_by": None})
        np = normalize_raw(raw)
    emb = await embeddings.embed_product(clients, np)
    await save_product(clients, raw, np, embedding=emb, only_if_embedded=cached)
    if not enrich:
        return
    try:
        from pipeline.normalize_unknown import enrich_product

        enriched = await enrich_product(clients, raw, np)
    except Exception as e:  # noqa: BLE001 - enrichment is optional
        log_event("product.enrich_failed", level=logging.WARNING, product_key=np.product.key, why=repr(e)[:300])
        return
    if enriched is not None:
        await save_product(clients, raw, enriched, embedding=emb)


def after_scan(clients: Clients, *, raw: RawProduct, np: NormalizedProduct, household: Household | None,
               uid: str, evaluation: Evaluation | None, cached: bool) -> None:
    """Spawn the background work for a finished scan. Returns at once, never raises."""
    s = clients.settings
    key = np.product.key
    try:
        need_save = (not cached) or key in _NEEDS_EMBED
        if need_save:
            enrich = (not cached) and s.feature_enrich and bool(np.unmatched) and raw.source != "photo"
            spawn("product_save", _save_and_enrich(clients, raw, np, cached=cached, enrich=enrich))
        if s.feature_scan_log:
            states = {v.member_id: v.state for v in evaluation.verdicts} if evaluation else {}
            spawn("scan_log", product_store.log_scan(clients, uid=uid, product_key=key, states=states,
                                                     timings_ms=timings(), source=raw.source))
    except Exception as e:  # noqa: BLE001
        log_event("product.after_scan_failed", level=logging.WARNING, product_key=key, why=repr(e)[:300])
