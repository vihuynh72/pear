"""Debug tools under /debug (only when PEAR_DEBUG_ROUTES is on, the default).

The main tool is POST /debug/evaluate: "why did Pear say X?" Give it a product (raw JSON,
a stored key, or a demo product name) and a household, and it returns the normalized
product (every kb hit with the exact matched words) plus the evaluation with hidden scores
and the engine's debug block.

    GET  /debug/config              resolved settings (secrets redacted), clients, router errors
    GET  /debug/routes              every registered route
    GET  /debug/kb/match?text=&lang= rules.normalize.match_text hits for free text
    POST /debug/evaluate            {product | product_key | demo, household?, member_ids?, extra_hits?}
    GET  /debug/demo_products       names usable as {"demo": "<name>"} above
    GET  /debug/product/{key}       full stored products/{key} record (?full=1 keeps the embedding)
    GET  /debug/off/{code}          raw Open Food Facts + Open Beauty Facts responses
    GET  /debug/background          core.background.stats()
    GET  /debug/inflight            picks + prices in-flight task stats
    POST /debug/label_reader        multipart image (+front_image) -> raw label_reader JSON, no rules
    GET  /debug/metrics             p50/p95 per path over the last 200 requests (in-process)

Every other lane's module is imported lazily inside its handler, so this router always
imports even while those lanes are broken; a missing/unfinished dependency answers 503
`unavailable` naming the module (or 501-ish detail for NotImplementedError stubs).

Metrics: main.py's middleware already logs one `request` event per request (path, status,
timings_ms). This module attaches a tiny logging handler to the "pear" logger that copies
those events into a ring buffer, so no change to main.py is needed. `record(path, ms)` is
public too, for callers that want to feed it directly. If PEAR_LOG_LEVEL is above INFO the
`request` lines are filtered before any handler runs and /debug/metrics stays empty.

Debugging this module: GET /debug/routes shows whether /debug/* registered at all;
GET /debug/config shows router_errors if another router failed to import.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Request, UploadFile

from core import background, timing
from core.config import get_settings
from core.deps import MAX_IMAGE_BYTES, read_upload
from core.errors import PearError
from core.logging import log_event
from models.contracts import Household

_r = APIRouter(prefix="/debug", tags=["debug"])


# ----------------------------------------------------------------------------- helpers


def _lazy(module: str) -> Any:
    """Import another lane's module at call time; 503 with the reason if it can't load."""
    try:
        return importlib.import_module(module)
    except Exception as e:  # noqa: BLE001 - any import failure means "not ready"
        raise PearError("unavailable", f"{module} is not available yet.", next="retry",
                        detail={"module": module, "why": repr(e)[:500]}) from e


def _not_ready(what: str, e: Exception) -> PearError:
    return PearError("unavailable", f"{what} is not implemented yet.", next="retry",
                     detail={"why": repr(e)[:500]})


async def _call(what: str, fn, *args, **kwargs) -> Any:
    """Call a (sync or async) dependency; NotImplementedError -> 503 unavailable."""
    try:
        out = fn(*args, **kwargs)
        if hasattr(out, "__await__"):
            out = await out
        return out
    except NotImplementedError as e:
        raise _not_ready(what, e) from e


def _clients(request: Request) -> Any:
    clients = getattr(request.app.state, "clients", None)
    if clients is None:
        raise PearError("unavailable", "App clients are not built (lifespan did not run).")
    return clients


def _dump(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, (list, tuple)):
        return [_dump(i) for i in x]
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    return x


def _repo_contracts() -> Path:
    return Path(get_settings().fixtures_dir).parent


def demo_products_dir() -> Path:
    return _repo_contracts() / "demo_products"


def demo_household() -> dict[str, Any]:
    path = _repo_contracts() / "household.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_routes(app) -> list[dict[str, Any]]:
    """Flatten app.routes, including routers mounted with include_router (FastAPI >= 0.141)."""
    try:
        from fastapi.routing import iter_route_contexts

        items = list(iter_route_contexts(app.routes))
    except ImportError:  # older FastAPI keeps included routes flat
        items = list(app.routes)
    out = []
    for r in items:
        methods = sorted((getattr(r, "methods", None) or set()) - {"HEAD"})
        out.append({"path": getattr(r, "path", None), "methods": methods, "name": getattr(r, "name", None)})
    return sorted(out, key=lambda d: (d["path"] or "", d["methods"]))


# ----------------------------------------------------------------------------- metrics


class _Metrics:
    """Last N requests as (path, status, timings_ms); percentiles computed on read."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buf: deque[tuple[str, int | None, dict[str, int], float]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, path: str, total_ms: float, status: int | None = None,
               timings_ms: dict[str, Any] | None = None) -> None:
        tm = {k: int(v) for k, v in (timings_ms or {}).items() if isinstance(v, (int, float))}
        tm["total"] = int(total_ms)
        with self._lock:
            self._buf.append((path, status, tm, time.time()))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self._buf)
        by_path: dict[str, list[tuple[int | None, dict[str, int]]]] = {}
        for path, status, tm, _ in rows:
            by_path.setdefault(path, []).append((status, tm))
        paths = {}
        for path, items in sorted(by_path.items()):
            stages: dict[str, list[int]] = {}
            for _, tm in items:
                for k, v in tm.items():
                    stages.setdefault(k, []).append(v)
            paths[path] = {
                "count": len(items),
                "errors": sum(1 for s, _ in items if s is not None and s >= 400),
                "p50_ms": _pct(stages.get("total", []), 50),
                "p95_ms": _pct(stages.get("total", []), 95),
                "max_ms": max(stages.get("total", [0]) or [0]),
                "stages": {k: {"p50_ms": _pct(v, 50), "p95_ms": _pct(v, 95), "n": len(v)}
                           for k, v in sorted(stages.items()) if k != "total"},
            }
        return {"window": self._buf.maxlen, "requests": len(rows), "paths": paths}


def _pct(values: list[int], p: int) -> int | None:
    """Nearest-rank percentile (no interpolation): honest for small samples."""
    if not values:
        return None
    s = sorted(values)
    rank = max(1, -(-p * len(s) // 100))  # ceil(p/100 * n)
    return s[min(rank, len(s)) - 1]


METRICS = _Metrics()


def record(path: str, total_ms: float, status: int | None = None,
           timings_ms: dict[str, Any] | None = None) -> None:
    """Feed one finished request into /debug/metrics. Never raises."""
    try:
        METRICS.record(path, total_ms, status, timings_ms)
    except Exception:  # noqa: BLE001 - metrics must never break a request
        pass


class _RequestLogTap(logging.Handler):
    """Copies main.py's `request` log events into METRICS. Writes nothing itself."""

    pear_metrics_tap = True

    def emit(self, rec: logging.LogRecord) -> None:
        try:
            if getattr(rec, "event", None) != "request":
                return
            tm = getattr(rec, "timings_ms", None) or {}
            path = getattr(rec, "path", None)
            if path is None or "total" not in tm:
                return
            record(path, tm["total"], getattr(rec, "status", None), tm)
        except Exception:  # noqa: BLE001
            pass


def install_log_tap(logger_name: str = "pear") -> None:
    """Idempotent: replaces any earlier tap (module reloads in tests)."""
    lg = logging.getLogger(logger_name)
    lg.handlers[:] = [h for h in lg.handlers if not getattr(h, "pear_metrics_tap", False)]
    lg.addHandler(_RequestLogTap())


# ----------------------------------------------------------------------------- routes


@_r.get("/config")
async def debug_config(request: Request) -> dict[str, Any]:
    app = request.app
    clients = getattr(app.state, "clients", None)
    cl = None
    if clients is not None:
        cl = {"store": clients.store_kind, "gemini": clients.gemini_status, "genai_error": clients.genai_error,
              "db_error": clients.db_error, "started_at": clients.started_at,
              "state_keys": sorted(clients.state.keys())}
    return {
        "settings": get_settings().redacted(),
        "clients": cl,
        "router_errors": getattr(app.state, "router_errors", {}),
        "kb": {"version": getattr(app.state, "kb_version", None), "error": getattr(app.state, "kb_error", None)},
        "tracing": getattr(app.state, "tracing", None),
        "python": sys.version.split()[0],
    }


@_r.get("/routes")
async def debug_routes(request: Request) -> dict[str, Any]:
    return {"routes": list_routes(request.app)}


@_r.get("/kb/match")
async def debug_kb_match(text: str, lang: str | None = None) -> dict[str, Any]:
    normalize = _lazy("rules.normalize")
    with timing.stage("match"):
        hits = await _call("rules.normalize.match_text", normalize.match_text, text, lang)
    labels = {}
    try:
        kb = _lazy("rules.kb").get_kb()
        labels = {h.kb_id: kb.label(h.kb_id, lang or "en") for h in hits}
    except Exception:  # noqa: BLE001 - labels are a nicety
        pass
    return {"text": text, "lang": lang, "hits": _dump(hits), "labels": labels, "timings_ms": timing.timings()}


async def _raw_product(clients_getter, body: dict[str, Any]) -> tuple[Any, str]:
    """RawProduct from body.product | body.demo | body.product_key, plus where it came from."""
    from models.domain import RawProduct

    if body.get("product"):
        return RawProduct.model_validate(body["product"]), "body"
    if body.get("demo"):
        name = str(body["demo"]).removesuffix(".json")
        path = demo_products_dir() / f"{name}.json"
        if "/" in name or not path.exists():
            raise PearError("not_found", f"No demo product {name!r}. See GET /debug/demo_products.")
        return RawProduct.model_validate(json.loads(path.read_text(encoding="utf-8"))), f"demo:{name}"
    if body.get("product_key"):
        store = _lazy("pipeline.product_store")
        stored = await _call("pipeline.product_store.get", store.get, clients_getter(), body["product_key"])
        if stored is None:
            raise PearError("not_found", f"No stored product {body['product_key']!r}.")
        return stored.raw, f"store:{body['product_key']}"
    raise PearError("bad_request", "Send one of: product (RawProduct JSON), demo (name), product_key.")


@_r.post("/evaluate")
async def debug_evaluate(request: Request) -> dict[str, Any]:
    """The "why did Pear say X" tool. Scores and engine debug are always included here."""
    try:
        body = await request.json()
    except Exception as e:
        raise PearError("bad_request", "Body must be JSON.") from e
    if not isinstance(body, dict):
        raise PearError("bad_request", "Body must be a JSON object.")
    from models.domain import Hit

    raw, origin = await _raw_product(lambda: _clients(request), body)
    household = Household.model_validate(body.get("household") or demo_household())
    member_ids = body.get("member_ids") or None
    extra = [Hit.model_validate(h) for h in body.get("extra_hits") or []] or None

    normalize = _lazy("rules.normalize")
    engine = _lazy("rules.engine")
    with timing.stage("normalize"):
        np = await _call("rules.normalize.normalize", normalize.normalize, raw, extra_hits=extra)
    with timing.stage("rules"):
        ev = await _call("rules.engine.evaluate", engine.evaluate, np, household, member_ids, debug=True)
    log_event("debug.evaluate", origin=origin, product_key=np.product.key,
              states={v.member_id: v.state for v in ev.verdicts})
    return {
        "origin": origin,
        "raw": _dump(raw),
        "normalized": _dump(np),
        "evaluation": _dump(ev),
        "summary": {v.member_id: {"state": v.state, "label": v.label, "score": v.score,
                                  "codes": [r.code for r in v.reasons]} for v in ev.verdicts},
        "timings_ms": timing.timings(),
    }


@_r.get("/demo_products")
async def debug_demo_products() -> dict[str, Any]:
    d = demo_products_dir()
    return {"dir": str(d), "names": sorted(p.stem for p in d.glob("*.json")) if d.exists() else []}


@_r.get("/product/{key}")
async def debug_product(request: Request, key: str, full: int = 0) -> dict[str, Any]:
    store = _lazy("pipeline.product_store")
    stored = await _call("pipeline.product_store.get", store.get, _clients(request), key)
    if stored is None:
        raise PearError("not_found", f"No stored product {key!r}.")
    out = _dump(stored)
    emb = out.get("embedding") if isinstance(out, dict) else None
    if emb and not full:
        out["embedding"] = {"dim": len(emb), "head": emb[:4], "hint": "add ?full=1 for all values"}
    price = None
    if hasattr(store, "get_price"):
        try:
            price = await _call("pipeline.product_store.get_price", store.get_price, _clients(request), key)
        except PearError:
            price = None
    return {"product": out, "price": price}


@_r.get("/off/{code}")
async def debug_off(request: Request, code: str) -> dict[str, Any]:
    off = _lazy("pipeline.lookup_off")
    with timing.stage("off_raw"):
        data = await _call("pipeline.lookup_off.fetch_raw_json", off.fetch_raw_json, _clients(request), code)
    return {"code": code, "responses": data, "timings_ms": timing.timings()}


@_r.get("/background")
async def debug_background() -> dict[str, Any]:
    return background.stats()


def _stats_of(module: str) -> Any:
    try:
        mod = importlib.import_module(module)
        return mod.stats()
    except Exception as e:  # noqa: BLE001 - show the reason instead of failing the page
        return {"error": repr(e)[:300]}


@_r.get("/inflight")
async def debug_inflight() -> dict[str, Any]:
    out = {"picks": _stats_of("pipeline.picks"), "prices": _stats_of("pipeline.prices")}
    for key, module in (("lookup_off", "pipeline.lookup_off"), ("adk", "agent.graph")):
        if module in sys.modules:  # only if already loaded; importing ADK is slow
            out[key] = _stats_of(module)
    return out


@_r.post("/label_reader")
async def debug_label_reader(request: Request, image: UploadFile = File(...),
                             front_image: UploadFile | None = File(None)) -> dict[str, Any]:
    """Raw label_reader JSON for prompt tuning. No normalization, no rules."""
    photo = _lazy("pipeline.photo_gemini")
    img = await read_upload(image, limit=MAX_IMAGE_BYTES, what="image")
    front = await read_upload(front_image, limit=MAX_IMAGE_BYTES, what="front_image") if front_image else None
    with timing.stage("label_reader"):
        data = await _call("pipeline.photo_gemini.read_label", photo.read_label, _clients(request), img, front)
    return {"label": data, "bytes": {"image": len(img), "front_image": len(front) if front else 0},
            "timings_ms": timing.timings()}


@_r.get("/metrics")
async def debug_metrics(reset: int = 0) -> dict[str, Any]:
    out = METRICS.summary()
    if reset:
        METRICS.clear()
    return out


# Only expose the routes when enabled; main.py always imports `router`.
if get_settings().debug_routes:
    router = _r
    install_log_tap()
else:
    router = APIRouter()
