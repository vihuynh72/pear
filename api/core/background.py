"""Fire-and-forget tasks that run after the response (cache writes, scan logs, embeddings,
picks prefetch). Keeps a strong reference (asyncio only holds weak ones) and logs failures,
so a broken background job shows up in logs instead of vanishing.

    from core.background import spawn
    spawn("cache_put", store.put(clients, record))

Cloud Run must run with --no-cpu-throttling or these stall until the next request
(addendum 13.2).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Coroutine

log = logging.getLogger("pear.bg")
_TASKS: set[asyncio.Task] = set()
_STATS: dict[str, dict[str, Any]] = {}


def spawn(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    t0 = time.perf_counter()
    task = asyncio.create_task(coro, name=f"bg:{name}")
    _TASKS.add(task)
    st = _STATS.setdefault(name, {"started": 0, "ok": 0, "failed": 0, "last_error": None, "last_ms": None})
    st["started"] += 1

    def _done(t: asyncio.Task) -> None:
        _TASKS.discard(t)
        st["last_ms"] = int((time.perf_counter() - t0) * 1000)
        if t.cancelled():
            st["failed"] += 1
            st["last_error"] = "cancelled"
            return
        exc = t.exception()
        if exc is not None:
            st["failed"] += 1
            st["last_error"] = repr(exc)
            log.warning("background task %s failed: %r", name, exc, exc_info=exc)
        else:
            st["ok"] += 1

    task.add_done_callback(_done)
    return task


def stats() -> dict[str, Any]:
    """For GET /debug/background."""
    return {"running": len(_TASKS), "by_name": _STATS}


async def drain(timeout: float = 5.0) -> None:
    """Wait for in-flight background work (tests and shutdown)."""
    if _TASKS:
        await asyncio.wait(list(_TASKS), timeout=timeout)
