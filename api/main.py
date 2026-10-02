"""Pear API (FastAPI on Cloud Run).

    uvicorn main:app --reload --port 8080        # from api/, see Makefile `make dev`

Request lifecycle
  middleware   request id (X-Request-ID), ?debug=1, trace header, per-request timing context,
               one `request` summary log line with status + timings_ms
  route        thin: parse -> pipeline/* -> rules/* -> contract model
  errors       PearError -> {"error": {...}} with the right HTTP status (core/errors.py)

Routers are imported one by one. A router that fails to import is skipped and listed in
/health (`ok: false`, `checks.router_errors`) so one broken P2 feature can't take the
barcode demo down with it.
"""

from __future__ import annotations

import importlib
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import background, timing
from core.clients import build_clients, close_clients
from core.config import get_settings
from core.errors import PearError
from core.logging import log_event, request_id_var, setup_logging, trace_var
from core.tracing import setup_tracing

settings = get_settings()
setup_logging(settings.log_format, settings.log_level, settings.project)
log = logging.getLogger("pear")

# Module name -> required? (required routers failing to import make /health ok=false)
ROUTERS: list[tuple[str, bool]] = [
    ("routes.health", True),
    ("routes.scan_barcode", True),
    ("routes.scan_photo", True),
    ("routes.ask", True),
    ("routes.profile", True),
    ("routes.kb", True),
    ("routes.mock", True),
    ("routes.explain", False),
    ("routes.picks", False),
    ("routes.prices", False),
    ("routes.debug", False),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started = time.time()
    app.state.clients = await build_clients(settings)
    try:
        from rules.kb import get_kb

        kb = get_kb()
        app.state.kb_version = kb.version
        log.info("kb loaded", extra={"kb_version": kb.version})
    except Exception as e:  # keep serving mocks/health even if the kb is broken
        app.state.kb_version = None
        app.state.kb_error = repr(e)
        log.exception("kb failed to load")
    if settings.ask_engine == "adk":
        # Build the ADK graph now so the first /v1/ask doesn't pay the ~1 s ADK import.
        try:
            from agent import graph as adk_graph

            log.info("adk warmed", extra={"build_ms": round(adk_graph.warm(), 1)})
        except Exception:
            log.exception("adk warm failed; /v1/ask falls back to the direct engine")
    yield
    await background.drain(timeout=3.0)
    await close_clients(app.state.clients)


app = FastAPI(
    title="Pear API",
    version=settings.version,
    description="Scan once, know for the whole family. Contracts: contracts/README.md",
    lifespan=lifespan,
)
app.state.router_errors = {}
app.state.tracing = setup_tracing(app, settings.trace, settings.project)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Request-ID", "Server-Timing"])

for module_name, required in ROUTERS:
    try:
        mod = importlib.import_module(module_name)
        app.include_router(mod.router)
    except Exception as e:  # noqa: BLE001 - surfaced via /health
        app.state.router_errors[module_name] = {"required": required, "error": repr(e)}
        logging.getLogger("pear").exception("router %s failed to import", module_name)


def _want_debug(request: Request) -> bool:
    return request.query_params.get("debug") in ("1", "true") or request.headers.get("x-pear-debug") == "1"


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    rid_token = request_id_var.set(rid)
    cloud_trace = request.headers.get("x-cloud-trace-context", "").split("/")[0] or None
    trace_token = trace_var.set(cloud_trace)
    ctx_token = timing.begin(rid, debug=_want_debug(request))
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        tm = timing.timings()
        response.headers["X-Request-ID"] = rid
        response.headers["Server-Timing"] = ", ".join(f"{k};dur={v}" for k, v in tm.items())
        return response
    finally:
        if request.url.path != "/health":
            ctx = timing.current()
            log_event("request", method=request.method, path=request.url.path, status=status,
                      timings_ms=ctx.timings_ms(), uid=ctx.uid,
                      **{k: v for k, v in ctx.bag.items() if k in ("product_key", "states")})
        timing.end(ctx_token)
        trace_var.reset(trace_token)
        request_id_var.reset(rid_token)


def _error_json(request: Request, err: PearError) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    body: dict = {"error": {"code": err.code, "message": err.message, "next": err.next, "request_id": rid}}
    if _want_debug(request) and err.detail:
        body["debug"] = err.detail
    return JSONResponse(body, status_code=err.status, headers={"X-Request-ID": rid or ""})


@app.exception_handler(PearError)
async def pear_error_handler(request: Request, err: PearError):
    level = logging.WARNING if err.status >= 500 else logging.INFO
    log_event("error", level=level, code=err.code, status=err.status, error_message=err.message,
              detail=err.detail, path=request.url.path)
    return _error_json(request, err)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    err = PearError("bad_request", f"Invalid request: {where}: {first.get('msg', 'invalid')}",
                    detail={"errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in errs[:20]]})
    log_event("error", level=logging.INFO, code="bad_request", path=request.url.path, detail=err.detail)
    return _error_json(request, err)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    code = {401: "unauthorized", 404: "not_found", 429: "rate_limited"}.get(exc.status_code, "bad_request")
    if exc.status_code >= 500:
        code = "internal"
    msg = f"No route {request.method} {request.url.path}" if exc.status_code in (404, 405) else str(exc.detail)
    return _error_json(request, PearError(code, msg, status=exc.status_code))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    err = PearError("internal", "Something went wrong. Try again?", next="retry",
                    detail={"why": repr(exc)[:1000]})
    return _error_json(request, err)
