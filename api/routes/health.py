"""GET /health — cheap liveness + config summary. The app calls it on launch and when the
camera opens to warm the instance and the TLS connection (addendum 5.6).

GET /health?deep=1 also does a Firestore read and a 1-token Gemini call, and reports each
with its latency. Use it after a deploy: `make smoke URL=...`.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request

from models.contracts import HealthResponse

router = APIRouter(tags=["health"])


async def _deep_checks(request: Request) -> dict:
    clients = request.app.state.clients
    s = clients.settings
    out: dict = {}

    async def firestore_check():
        if clients.db is None:
            return {"ok": False, "why": clients.db_error or "memory store"}
        t = time.perf_counter()
        try:
            await asyncio.wait_for(clients.db.collection("products").document("_health").get(), 3.0)
            return {"ok": True, "ms": int((time.perf_counter() - t) * 1000)}
        except Exception as e:
            return {"ok": False, "why": repr(e)[:300]}

    async def gemini_check():
        if clients.genai is None:
            return {"ok": False, "why": clients.genai_error}
        t = time.perf_counter()
        try:
            from pipeline.gemini import generate_raw

            r = await generate_raw(clients, call="health", model=s.model_text, contents="Reply with: ok",
                                   timeout_s=5.0, max_output_tokens=8)
            return {"ok": True, "ms": int((time.perf_counter() - t) * 1000), "model": s.model_text,
                    "text": (getattr(r, "text", "") or "")[:20]}
        except Exception as e:
            return {"ok": False, "why": repr(e)[:300], "model": s.model_text}

    out["firestore"], out["gemini"] = await asyncio.gather(firestore_check(), gemini_check())
    return out


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health(request: Request, deep: int = 0) -> HealthResponse:
    app = request.app
    clients = app.state.clients
    s = clients.settings
    router_errors = getattr(app.state, "router_errors", {})
    required_broken = any(v["required"] for v in router_errors.values())
    kb_version = getattr(app.state, "kb_version", None)
    checks: dict | None = None
    if deep or router_errors or kb_version is None:
        checks = {"router_errors": router_errors, "tracing": getattr(app.state, "tracing", None),
                  "store_error": clients.db_error, "kb_error": getattr(app.state, "kb_error", None)}
        if deep:
            checks.update(await _deep_checks(request))
    return HealthResponse(
        ok=not required_broken and kb_version is not None,
        service=s.service,
        version=f"{s.version}+{s.git_sha}",
        env=s.env,
        region=s.region,
        store=clients.store_kind,
        gemini=clients.gemini_status,
        auth=s.auth_mode,
        ask_engine=s.ask_engine,
        kb_version=kb_version,
        uptime_s=int(time.time() - app.state.started),
        features=s.features,
        checks=checks,
    )


@router.get("/", include_in_schema=False)
async def root():
    return {"service": "pear-api", "docs": "/docs", "health": "/health", "mock": "/mock/v1/..."}
