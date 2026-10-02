"""Product cache, scan log and price cache: Firestore when clients.db is set, else memory.

    sp = await get(clients, "off:0049000028911")           # StoredProduct | None
    sp = await get_by_code(clients, "049000028911")        # one get_all over every key spelling
    await put(clients, StoredProduct(...))                 # embedding stored as a Firestore Vector
    rows = await find_nearest(clients, vec, category="food", limit=50)   # [(sp, cosine distance)]
    rows = await by_category_main(clients, "en:instant-noodles", limit=60)
    await log_scan(clients, uid=..., product_key=..., states={...}, timings_ms={...}, source=...)
    await get_price(clients, key) / await put_price(clients, key, {...})

Firestore layout (plan 7.7, addendum 8)
  products/{key}  StoredProduct.model_dump(mode="json"), `embedding` as Vector(768),
                  flat category / category_main / kb_ids for queries, updated_at.
  scans/{auto}    uid, product_key, states, timings_ms, source, request_id, created_at. No photos.
  prices/{key}    the price dict as given (picks lane decides freshness).
  Vector query: where(category == food).find_nearest(embedding, COSINE, limit,
                distance_result_field="dist") -> needs the composite index from addendum 8.4.

Failure policy: every Firestore call is capped by settings.t_firestore. A failed or slow read
is logged (`store.read_failed`) and treated as a miss, so a Firestore hiccup never breaks a
scan. A failed write is logged (`store.write_failed`) and swallowed (writes run in the
background). Reads time under stage("cache_get"), writes under stage("cache_put").

Memory mode (PEAR_STORE=memory, tests, no ADC): plain dicts + brute-force cosine.
reset_memory() clears it. GET /debug/store (contracts lane) can print memory_stats().
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any

from core.clients import Clients
from core.keys import barcode_variants, canonical_barcode
from core.logging import log_event
from core.timing import request_id as current_request_id
from core.timing import stage
from models.domain import StoredProduct

PRODUCTS, SCANS, PRICES = "products", "scans", "prices"
KEY_PREFIXES = ("off", "obf", "fdc")
log = logging.getLogger("pear.store")

_MEM: dict[str, Any] = {"products": {}, "scans": [], "prices": {}}


def reset_memory() -> None:
    _MEM["products"] = {}
    _MEM["scans"] = []
    _MEM["prices"] = {}


def memory_stats() -> dict[str, int]:
    return {"products": len(_MEM["products"]), "scans": len(_MEM["scans"]), "prices": len(_MEM["prices"])}


def memory_scans() -> list[dict[str, Any]]:
    """Scan log rows written in memory mode (tests, local debugging)."""
    return list(_MEM["scans"])


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- (de)serialize


def to_doc(sp: StoredProduct) -> dict[str, Any]:
    """StoredProduct -> Firestore document (embedding as Vector, or omitted)."""
    doc = sp.model_dump(mode="json")
    raw = doc.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("model_kb_ids"), list):
        # Firestore rejects an array directly inside an array: wrap each row ({"ids": [...]}).
        raw["model_kb_ids"] = [{"ids": list(ids or [])} for ids in raw["model_kb_ids"]]
    emb = doc.pop("embedding", None)
    if emb:
        from google.cloud.firestore_v1.vector import Vector

        doc["embedding"] = Vector([float(x) for x in emb])
    try:
        from google.cloud import firestore

        doc["updated_at"] = firestore.SERVER_TIMESTAMP
    except Exception:  # pragma: no cover - firestore lib always installed
        pass
    return doc


def _assert_firestore_shape(v: Any, path: str = "") -> None:
    """Raise ValueError if `v` has a list directly inside a list (Firestore INVALID_ARGUMENT)."""
    if isinstance(v, dict):
        for k, x in v.items():
            _assert_firestore_shape(x, f"{path}.{k}")
    elif isinstance(v, list | tuple):
        for i, x in enumerate(v):
            if isinstance(x, list | tuple):
                raise ValueError(f"nested array at {path}[{i}]")
            _assert_firestore_shape(x, f"{path}[{i}]")


def from_doc(data: dict[str, Any] | None) -> StoredProduct | None:
    if not data:
        return None
    d = dict(data)
    emb = d.get("embedding")
    if emb is not None and not isinstance(emb, list):
        try:
            d["embedding"] = [float(x) for x in emb]  # firestore Vector is a Sequence
        except TypeError:
            d["embedding"] = None
    d.pop("updated_at", None)
    d.pop("dist", None)
    raw = d.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("model_kb_ids"), list):
        raw = dict(raw)
        raw["model_kb_ids"] = [(x.get("ids") or []) if isinstance(x, dict) else (x or [])
                               for x in raw["model_kb_ids"]]
        d["raw"] = raw
    try:
        return StoredProduct.model_validate(d)
    except Exception as e:  # a bad doc is a miss, not a crash
        log_event("store.bad_doc", level=logging.WARNING, key=d.get("key"), why=repr(e)[:300])
        return None


def code_keys(code: str) -> list[str]:
    """Every products/{key} a barcode could be stored under, most likely first."""
    out: list[str] = []
    for prefix in KEY_PREFIXES:
        for v in barcode_variants(code):
            k = f"{prefix}:{canonical_barcode(v)}"
            if k not in out:
                out.append(k)
    return out


async def _capped(clients: Clients, coro: Any, *, op: str, key: str | None = None) -> Any:
    """Run a Firestore coroutine under settings.t_firestore. Raises on failure (callers decide)."""
    return await asyncio.wait_for(coro, timeout=clients.settings.t_firestore)


def _read_failed(op: str, e: BaseException, **fields: Any) -> None:
    log_event("store.read_failed", level=logging.WARNING, op=op, why=repr(e)[:300], **fields)


def _write_failed(op: str, e: BaseException, **fields: Any) -> None:
    log_event("store.write_failed", level=logging.WARNING, op=op, why=repr(e)[:300], **fields)


# ----------------------------------------------------------------------------- products


async def get(clients: Clients, key: str) -> StoredProduct | None:
    with stage("cache_get"):
        return await _get(clients, key)


async def _get(clients: Clients, key: str) -> StoredProduct | None:
    if clients.db is None:
        sp = _MEM["products"].get(key)
        return sp.model_copy(deep=True) if sp is not None else None
    try:
        snap = await _capped(clients, clients.db.collection(PRODUCTS).document(key).get(), op="get")
    except Exception as e:  # noqa: BLE001 - read failure == cache miss
        _read_failed("get", e, key=key)
        return None
    return from_doc(snap.to_dict()) if getattr(snap, "exists", False) else None


async def get_by_code(clients: Clients, code: str) -> StoredProduct | None:
    """One round trip over off/obf/fdc x barcode spellings; the first existing key wins."""
    with stage("cache_get"):
        return await _get_by_code(clients, code)


async def _get_by_code(clients: Clients, code: str) -> StoredProduct | None:
    keys = code_keys(code)
    if not keys:
        return None
    if clients.db is None:
        for k in keys:
            sp = _MEM["products"].get(k)
            if sp is not None:
                return sp.model_copy(deep=True)
        return None
    async def _all() -> dict[str, Any]:
        refs = [clients.db.collection(PRODUCTS).document(k) for k in keys]
        found: dict[str, Any] = {}
        async for snap in clients.db.get_all(refs):
            if getattr(snap, "exists", False):
                found[snap.id] = snap.to_dict()
        return found

    try:
        found = await _capped(clients, _all(), op="get_all")
    except Exception as e:  # noqa: BLE001 - read failure == cache miss
        _read_failed("get_by_code", e, code=code)
        return None
    for k in keys:
        if k in found:
            return from_doc(found[k])
    return None


async def put(clients: Clients, stored: StoredProduct) -> bool:
    """Write products/{key}. True on success; failures are logged, never raised."""
    if not stored.fetched_at:
        stored = stored.model_copy(update={"fetched_at": _now_iso()})
    if clients.db is None:
        _assert_firestore_shape(to_doc(stored))  # memory mode enforces the same no-nested-array rule
        _MEM["products"][stored.key] = stored.model_copy(deep=True)
        return True
    with stage("cache_put"):
        try:
            await _capped(clients, clients.db.collection(PRODUCTS).document(stored.key).set(to_doc(stored)),
                          op="put")
            return True
        except Exception as e:  # noqa: BLE001
            _write_failed("put", e, key=stored.key)
            return False


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


async def find_nearest(clients: Clients, vector: list[float], *, category: str | None = "food",
                       limit: int = 50) -> list[tuple[StoredProduct, float]]:
    """Nearest stored products by COSINE distance (0 = same direction). [] on any failure."""
    if not vector:
        return []
    if clients.db is None:
        rows = [(sp, _cosine_distance(vector, sp.embedding)) for sp in _MEM["products"].values()
                if sp.embedding and (category is None or sp.category == category)]
        rows.sort(key=lambda r: r[1])
        return [(sp.model_copy(deep=True), d) for sp, d in rows[:limit]]
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        q: Any = clients.db.collection(PRODUCTS)
        if category is not None:
            q = q.where(filter=FieldFilter("category", "==", category))
        vq = q.find_nearest(vector_field="embedding", query_vector=Vector([float(x) for x in vector]),
                            distance_measure=DistanceMeasure.COSINE, limit=limit,
                            distance_result_field="dist")
        with stage("store_nearest"):
            docs = await _capped(clients, vq.get(), op="find_nearest")
    except Exception as e:  # noqa: BLE001 - index not ready / timeout -> caller falls back
        _read_failed("find_nearest", e, category=category)
        return []
    out: list[tuple[StoredProduct, float]] = []
    for snap in docs:
        data = snap.to_dict() or {}
        dist = data.get("dist")
        sp = from_doc(data)
        if sp is not None:
            out.append((sp, float(dist) if dist is not None else 1.0))
    return out


async def by_category_main(clients: Clients, category_main: str, *, limit: int = 60) -> list[StoredProduct]:
    """Products sharing the scanned product's most specific category (picks fallback)."""
    if not category_main:
        return []
    if clients.db is None:
        rows = [sp for sp in _MEM["products"].values() if sp.category_main == category_main]
        return [sp.model_copy(deep=True) for sp in rows[:limit]]
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = clients.db.collection(PRODUCTS).where(filter=FieldFilter("category_main", "==", category_main)).limit(limit)
        with stage("store_category"):
            docs = await _capped(clients, q.get(), op="by_category_main")
    except Exception as e:  # noqa: BLE001
        _read_failed("by_category_main", e, category_main=category_main)
        return []
    return [sp for sp in (from_doc(d.to_dict()) for d in docs) if sp is not None]


# ----------------------------------------------------------------------------- scans + prices


async def log_scan(clients: Clients, *, uid: str, product_key: str, states: dict[str, str],
                   timings_ms: dict[str, int], source: str, request_id: str | None = None) -> bool:
    """scans/{auto-id}. Never stores photos or household details beyond member states."""
    row: dict[str, Any] = {"uid": uid, "product_key": product_key, "states": dict(states),
                           "timings_ms": dict(timings_ms), "source": source,
                           "request_id": request_id or current_request_id()}
    if clients.db is None:
        _MEM["scans"].append({**row, "created_at": _now_iso()})
        del _MEM["scans"][:-500]  # keep memory bounded
        return True
    from google.cloud import firestore

    with stage("cache_put"):
        try:
            await _capped(clients, clients.db.collection(SCANS).add({**row, "created_at": firestore.SERVER_TIMESTAMP}),
                          op="log_scan")
            return True
        except Exception as e:  # noqa: BLE001
            _write_failed("log_scan", e, product_key=product_key)
            return False


async def get_price(clients: Clients, key: str) -> dict[str, Any] | None:
    if clients.db is None:
        p = _MEM["prices"].get(key)
        return dict(p) if p is not None else None
    with stage("cache_get"):
        try:
            snap = await _capped(clients, clients.db.collection(PRICES).document(key).get(), op="get_price")
        except Exception as e:  # noqa: BLE001
            _read_failed("get_price", e, key=key)
            return None
    return snap.to_dict() if getattr(snap, "exists", False) else None


async def put_price(clients: Clients, key: str, price: dict[str, Any]) -> bool:
    if clients.db is None:
        _MEM["prices"][key] = dict(price)
        return True
    with stage("cache_put"):
        try:
            await _capped(clients, clients.db.collection(PRICES).document(key).set(dict(price)), op="put_price")
            return True
        except Exception as e:  # noqa: BLE001
            _write_failed("put_price", e, key=key)
            return False
