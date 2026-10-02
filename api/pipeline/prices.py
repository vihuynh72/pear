"""Grounded price finder (addendum 9, P2): "About $3.99 at Target. Found with Google Search."

Flow
    picks.compute() picks 3 products --> start(clients, {key, name, brand, quantity, code})
        background _lookup():
            prices/{key} in the store, fresh (< 24 h)  -> return it at once (cache hit)
            else find_price(): ONE Gemini call with the Google Search tool and NO JSON schema
                 (JSON output silently turns grounding off, addendum 9.3), parse
                 "PRICE|<retailer>|<amount>|<size>" lines, keep 0.25 <= amount <= 100,
                 and throw the answer away unless it has web grounding chunks
                 -> put_price() on success
    POST /v1/prices --> for_keys(): start a lookup for every key not already in flight
                        (loading the product with pipeline.products.load_product), wait up to
                        settings.t_price (6 s) in total, null for whatever is not done.

A price with no search result behind it never reaches the screen.

Debugging
- GET /debug/inflight includes stats(): every key in PRICE_INFLIGHT, running/done/failed, the
  amount found, and counters (cache_hits, found, none, ungrounded, errors, ...).
- Log events: prices.found, prices.none, prices.ungrounded, prices.error, prices.cache_hit,
  prices.product_missing, prices.store_error. Filter on event=prices.* in Cloud Logging.
- /v1/prices?debug=1: the debug bag gets `prices` (per-key state) and `gemini.price_finder`
  (model, ms, raw text) from pipeline/gemini.py; routes/prices.py copies it into the
  X-Pear-Debug response header (PricesResponse has no debug field).
- Reproduce one lookup by hand:  await find_price(clients, {"key": "off:...", "name": ...})
- PEAR_FEATURE_PRICES=0 turns /v1/prices into all-null and skips lookups from picks.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from core.background import spawn
from core.clients import Clients
from core.logging import log_event
from core.timing import debug_put, request_id, stage
from models.contracts import Price, PricesResponse

PRICE_PROMPT = """Find the current US retail price for this exact product and package size.
Product: {brand} {name}, {size}. UPC {code}.
Search US grocery, drugstore and big-box retailers.
Reply with at most 2 lines, each exactly:
PRICE|<retailer>|<price in USD, digits and a dot only>|<package size>
If you can't find this exact product and size, reply exactly: NONE"""

LINE = re.compile(r"^PRICE\|([^|]+)\|\$?(\d+(?:\.\d{1,2})?)\|(.+)$")
MIN_AMOUNT = 0.25
MAX_AMOUNT = 100.0
FRESH = timedelta(hours=24)
NONE_TTL_S = 600.0  # retry a "no price" (or failed) lookup after 10 minutes
MAX_INFLIGHT = 500
MAX_KEYS = 10  # per /v1/prices request (the app sends 3)
_PRICE_FIELDS = tuple(Price.model_fields)

PRICE_INFLIGHT: dict[str, asyncio.Task] = {}
_BORN: dict[str, float] = {}

_COUNTERS: dict[str, int] = {
    "started": 0, "cache_hits": 0, "found": 0, "none": 0, "ungrounded": 0, "errors": 0,
    "product_missing": 0, "store_errors": 0,
}


# ----------------------------------------------------------------------------- lazy deps
# Imported lazily so this module loads even while a sibling lane is broken; tests
# monkeypatch these accessors with fakes.


def _store():
    from pipeline import product_store

    return product_store


def _products():
    from pipeline import products

    return products


def _types():
    from google.genai import types

    return types


# ----------------------------------------------------------------------------- find_price


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_prompt(p: dict[str, Any]) -> str:
    return PRICE_PROMPT.format(
        brand=(p.get("brand") or "").strip(),
        name=(p.get("name") or "").strip(),
        size=(p.get("quantity") or p.get("size") or "").strip() or "size not listed",
        code=(p.get("code") or "").strip() or "unknown",
    ).replace("Product:  ", "Product: ")


def _grounding(resp: Any) -> tuple[list[Any], str | None]:
    """(web grounding chunks, Search Suggestions html) from a generate_content response."""
    cands = getattr(resp, "candidates", None) or []
    gm = getattr(cands[0], "grounding_metadata", None) if cands else None
    if gm is None:
        return [], None
    chunks = [ch for ch in (getattr(gm, "grounding_chunks", None) or []) if getattr(ch, "web", None)]
    sep = getattr(gm, "search_entry_point", None)
    html = getattr(sep, "rendered_content", None) if sep is not None else None
    return chunks, html


def _best_chunk(chunks: list[Any], retailer: str) -> Any:
    """The chunk from the retailer's own site when there is one, else the first."""
    want = re.sub(r"[^a-z0-9]", "", retailer.lower())
    if want:
        for ch in chunks:
            hay = re.sub(r"[^a-z0-9]", "", f"{ch.web.title or ''}{ch.web.uri or ''}".lower())
            if want in hay:
                return ch
    return chunks[0]


def parse_price_text(text: str) -> tuple[str, float, str] | None:
    """First valid "PRICE|retailer|amount|size" line -> (retailer, amount, size)."""
    for line in (text or "").splitlines():
        m = LINE.match(line.strip().strip("`*").strip())
        if not m:
            continue
        amount = float(m.group(2))
        if MIN_AMOUNT <= amount <= MAX_AMOUNT and m.group(1).strip():
            return m.group(1).strip(), amount, m.group(3).strip()
    return None


def parse_price_response(resp: Any, *, checked_at: datetime | None = None) -> tuple[dict | None, str]:
    """(price dict | None, outcome) where outcome is found | none | ungrounded."""
    chunks, html = _grounding(resp)
    if not chunks:  # not grounded: never show it
        return None, "ungrounded"
    parsed = parse_price_text(getattr(resp, "text", None) or "")
    if parsed is None:
        return None, "none"
    retailer, amount, size = parsed
    ch = _best_chunk(chunks, retailer)
    return {
        "amount": amount,
        "currency": "USD",
        "retailer": retailer,
        "size": size or None,
        "source_title": getattr(ch.web, "title", None),
        "source_url": getattr(ch.web, "uri", None),
        "search_entry_html": html,
        "checked_at": _iso(checked_at or _now()),
        "source": "google_search",
    }, "found"


async def find_price(clients: Clients, p: dict[str, Any], timeout_s: float | None = None) -> dict | None:
    """One grounded Gemini call. Returns a Price-shaped dict or None. Never raises."""
    from pipeline.gemini import generate_raw

    key = p.get("key")
    if not p.get("name"):
        log_event("prices.none", key=key, why="product has no name")
        return None
    s = clients.settings
    types = _types()
    try:
        resp = await generate_raw(
            clients, call="price_finder", model=s.model_price, contents=build_prompt(p),
            tools=[types.Tool(google_search=types.GoogleSearch())], max_output_tokens=512,
            timeout_s=timeout_s or s.t_price,
        )
    except Exception as e:  # timeout, quota, auth, network (generate_raw already logged it)
        _COUNTERS["errors"] += 1
        log_event("prices.error", level=logging.WARNING, key=key, why=repr(e)[:300])
        return None
    price, outcome = parse_price_response(resp)
    _COUNTERS[outcome] += 1
    text = (getattr(resp, "text", None) or "")[:300]
    if price is None:
        log_event(f"prices.{outcome}", key=key, code=p.get("code"), text=text)
    else:
        log_event("prices.found", key=key, amount=price["amount"], retailer=price["retailer"],
                  source=price["source_title"])
    return price


# ----------------------------------------------------------------------------- cache


def is_fresh(price: dict | None, now: datetime | None = None) -> bool:
    if not price or price.get("amount") is None or not price.get("checked_at"):
        return False
    try:
        ts = datetime.fromisoformat(str(price["checked_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now or _now()) - ts < FRESH


async def _cached(clients: Clients, key: str) -> dict | None:
    try:
        hit = await _store().get_price(clients, key)
    except Exception as e:
        _COUNTERS["store_errors"] += 1
        log_event("prices.store_error", level=logging.WARNING, key=key, op="get", why=repr(e)[:300])
        return None
    if is_fresh(hit):
        _COUNTERS["cache_hits"] += 1
        log_event("prices.cache_hit", key=key, checked_at=hit.get("checked_at"))
        return hit
    return None


async def _save(clients: Clients, key: str, price: dict) -> None:
    try:
        await _store().put_price(clients, key, price)
    except Exception as e:
        _COUNTERS["store_errors"] += 1
        log_event("prices.store_error", level=logging.WARNING, key=key, op="put", why=repr(e)[:300])


def product_dict(np: Any) -> dict[str, Any]:
    """NormalizedProduct -> the dict start()/find_price() take."""
    p = np.product
    return {"key": p.key, "name": p.name, "brand": p.brand, "quantity": p.quantity, "code": p.code}


async def _load(clients: Clients, key: str) -> dict | None:
    try:
        np = await _products().load_product(clients, key)
    except Exception as e:
        log_event("prices.product_missing", level=logging.WARNING, key=key, why=repr(e)[:300])
        np = None
    if np is None:
        _COUNTERS["product_missing"] += 1
        log_event("prices.product_missing", key=key)
        return None
    return product_dict(np)


async def _lookup(clients: Clients, key: str, p: dict[str, Any] | None) -> dict | None:
    """Store cache (24 h) -> load the product if needed -> find_price -> store write."""
    hit = await _cached(clients, key)
    if hit is not None:
        return hit
    if p is None:
        p = await _load(clients, key)
        if p is None:
            return None
    price = await find_price(clients, p)
    if price is not None:
        await _save(clients, key, price)
    return price


async def cached_price(clients: Clients, p: dict[str, Any]) -> dict | None:
    """prices/{key} when fresh (< 24 h), else find_price and write a success back."""
    return await _lookup(clients, p["key"], p)


# ----------------------------------------------------------------------------- in flight


def _live_task(key: str) -> asyncio.Task | None:
    """Reuse: running; done with a price (< 24 h); done with no price (< 10 min)."""
    task = PRICE_INFLIGHT.get(key)
    if task is None:
        return None
    if not task.done():
        return task
    if task.cancelled() or task.exception() is not None:
        return None
    age = time.time() - _BORN.get(key, 0.0)
    ttl = FRESH.total_seconds() if task.result() is not None else NONE_TTL_S
    return task if age < ttl else None


def _register(key: str, task: asyncio.Task) -> None:
    PRICE_INFLIGHT.pop(key, None)  # re-insert so dict order = age order
    PRICE_INFLIGHT[key] = task
    _BORN[key] = time.time()
    while len(PRICE_INFLIGHT) > MAX_INFLIGHT:
        oldest = next(iter(PRICE_INFLIGHT))
        PRICE_INFLIGHT.pop(oldest, None)
        _BORN.pop(oldest, None)


def _ensure(clients: Clients, key: str, p: dict[str, Any] | None) -> asyncio.Task:
    task = _live_task(key)
    if task is None:
        task = spawn("price_lookup", _lookup(clients, key, p))
        _register(key, task)
        _COUNTERS["started"] += 1
    return task


def reset() -> None:
    """Forget every lookup (tests, /debug)."""
    PRICE_INFLIGHT.clear()
    _BORN.clear()


def start(clients: Clients, product: dict[str, Any]) -> None:
    """Start a background price lookup for product (key, name, brand, quantity, code).

    Returns at once and never raises; a lookup already running or recently done is reused.
    """
    try:
        if not clients.settings.feature_prices or not product or not product.get("key"):
            return
        asyncio.get_running_loop()
        _ensure(clients, product["key"], product)
    except Exception as e:  # prices are P2: never break picks
        log_event("prices.start_error", level=logging.WARNING, key=(product or {}).get("key"),
                  why=repr(e)[:300])


def _to_price(value: Any) -> Price | None:
    if not isinstance(value, dict):
        return None
    try:
        return Price.model_validate({k: value[k] for k in _PRICE_FIELDS if k in value})
    except Exception as e:
        log_event("prices.bad_record", level=logging.WARNING, why=repr(e)[:300])
        return None


def _task_value(task: asyncio.Task) -> Any:
    if not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def _empty(keys: list[str], t0: float, why: str) -> PricesResponse:
    debug_put("prices", {"skipped": why})
    return PricesResponse(prices={k: None for k in keys},
                          timings_ms={"wait": 0, "total": int((time.perf_counter() - t0) * 1000)},
                          request_id=request_id())


async def for_keys(clients: Clients, keys: list[str], country: str = "US") -> PricesResponse:
    """Prices for keys: wait up to settings.t_price in total, null for anything not ready."""
    t0 = time.perf_counter()
    keys = list(dict.fromkeys(k for k in keys if k))
    s = clients.settings
    if not s.feature_prices:
        return _empty(keys, t0, "feature_prices off (PEAR_FEATURE_PRICES=0)")
    if (country or "US").upper() != "US":
        return _empty(keys, t0, f"country {country}: price finder is US only")
    asked, extra = keys[:MAX_KEYS], keys[MAX_KEYS:]
    reused = {k for k in asked if _live_task(k) is not None}
    tasks = {k: _ensure(clients, k, None) for k in asked}
    tw = time.perf_counter()
    pending = [t for t in tasks.values() if not t.done()]
    if pending:
        with stage("prices_wait"):
            await asyncio.wait(pending, timeout=s.t_price)  # never cancels; late ones finish + cache
    wait_ms = int((time.perf_counter() - tw) * 1000)
    prices = {k: _to_price(_task_value(t)) for k, t in tasks.items()}
    prices.update({k: None for k in extra})
    debug_put("prices", {k: {"state": _task_state(t), "reused": k in reused} for k, t in tasks.items()}
              | ({"dropped_over_limit": extra} if extra else {}))
    late = [k for k, t in tasks.items() if not t.done()]
    if late:
        log_event("prices.wait_timeout", level=logging.INFO, keys=late, wait_s=s.t_price)
    return PricesResponse(prices=prices,
                          timings_ms={"wait": wait_ms, "total": int((time.perf_counter() - t0) * 1000)},
                          request_id=request_id())


# ----------------------------------------------------------------------------- stats


def _task_state(task: asyncio.Task) -> str:
    if not task.done():
        return "running"
    if task.cancelled():
        return "cancelled"
    if task.exception() is not None:
        return "failed"
    return "found" if task.result() is not None else "none"


def stats() -> dict[str, Any]:
    """For GET /debug/inflight."""
    now = time.time()
    states = {k: _task_state(t) for k, t in PRICE_INFLIGHT.items()}
    newest = []
    for k in list(PRICE_INFLIGHT)[-10:][::-1]:
        row: dict[str, Any] = {"key": k, "state": states[k], "age_s": round(now - _BORN.get(k, now), 1)}
        val = _task_value(PRICE_INFLIGHT[k])
        if isinstance(val, dict):
            row.update(amount=val.get("amount"), retailer=val.get("retailer"),
                       checked_at=val.get("checked_at"))
        elif states[k] == "failed":
            row["error"] = repr(PRICE_INFLIGHT[k].exception())[:300]
        newest.append(row)
    return {
        "count": len(PRICE_INFLIGHT),
        "running": sum(1 for v in states.values() if v == "running"),
        "found": sum(1 for v in states.values() if v == "found"),
        "none": sum(1 for v in states.values() if v == "none"),
        "failed": sum(1 for v in states.values() if v in ("failed", "cancelled")),
        "counters": dict(_COUNTERS),
        "newest": newest,
    }
