"""Process-wide clients, built once in the FastAPI lifespan (addendum 5.1).

    clients = get_clients(request)      # FastAPI dependency
    clients.http                        # httpx.AsyncClient(http2=True), shared TLS pool
    clients.require_genai()             # google.genai.Client or PearError("unavailable")
    clients.db                          # firestore.AsyncClient or None (memory store)

Nothing here raises at startup: a missing credential just marks that backend unavailable,
which /health reports and the affected endpoints turn into a clear error. The barcode path
needs no Gemini, so it keeps working even if Agent Platform auth is broken.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from starlette.requests import Request

from core.config import Settings
from core.errors import gemini_unavailable

log = logging.getLogger("pear.clients")


@dataclass
class Clients:
    settings: Settings
    http: httpx.AsyncClient
    genai: Any | None = None  # google.genai.Client
    genai_error: str | None = None
    db: Any | None = None  # google.cloud.firestore.AsyncClient
    db_error: str | None = None
    store_kind: str = "memory"
    started_at: float = field(default_factory=time.time)
    # Free-form registry for long-lived per-process state (in-flight tasks, caches).
    state: dict[str, Any] = field(default_factory=dict)

    def require_genai(self):
        if self.genai is None:
            raise gemini_unavailable(self.genai_error or "genai client not built")
        return self.genai

    @property
    def gemini_status(self) -> str:
        return "ok" if self.genai is not None else f"unavailable: {self.genai_error}"


def _adc_available() -> tuple[bool, str | None]:
    try:
        import google.auth

        _, project = google.auth.default()
        return True, project
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _build_genai(settings: Settings):
    from google import genai

    # Current SDK: enterprise=True (Gemini Enterprise Agent Platform). Older: vertexai=True.
    try:
        return genai.Client(enterprise=True, project=settings.project, location=settings.gemini_location)
    except TypeError:
        return genai.Client(vertexai=True, project=settings.project, location=settings.gemini_location)


def _build_firestore(settings: Settings):
    from google.cloud import firestore

    return firestore.AsyncClient(project=settings.project)


async def build_clients(settings: Settings) -> Clients:
    http = httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(5.0, connect=2.0),
        headers={"User-Agent": settings.user_agent},
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=120),
        follow_redirects=True,
    )
    clients = Clients(settings=settings, http=http)

    # google.auth.default() can probe the metadata server; keep it off the event loop.
    has_adc, adc_info = await asyncio.to_thread(_adc_available)

    if has_adc:
        try:
            clients.genai = await asyncio.to_thread(_build_genai, settings)
        except Exception as e:
            clients.genai_error = f"{type(e).__name__}: {e}"
    else:
        clients.genai_error = f"no Application Default Credentials ({adc_info})"

    want = settings.store
    if want == "memory" or (want == "auto" and not has_adc):
        clients.store_kind = "memory"
        if want == "auto":
            clients.db_error = "no ADC; using in-memory store (data is lost on restart)"
    else:
        try:
            clients.db = await asyncio.to_thread(_build_firestore, settings)
            clients.store_kind = "firestore"
        except Exception as e:
            clients.db_error = f"{type(e).__name__}: {e}"
            if want == "firestore":
                raise
            clients.store_kind = "memory"

    log.info(
        "clients ready",
        extra={"store": clients.store_kind, "gemini": clients.gemini_status,
               "db_error": clients.db_error, "project": settings.project},
    )
    if clients.genai is None:
        log.warning("Gemini unavailable: %s. Photo/ask/explain/picks embeddings will fail; "
                    "barcode path still works. Fix: gcloud auth application-default login",
                    clients.genai_error)
    return clients


async def close_clients(clients: Clients) -> None:
    await clients.http.aclose()
    if clients.db is not None:
        try:
            clients.db.close()
        except Exception:
            pass


def get_clients(request: Request) -> Clients:
    """FastAPI dependency: `clients: Clients = Depends(get_clients)`."""
    return request.app.state.clients
