"""Per-request stage timings (-> `timings_ms`), Cloud Trace spans, and the debug bag.

Usage anywhere below a route, no plumbing needed:

    from core.timing import stage, debug_put

    with stage("lookup"):              # adds timings_ms["lookup"] and a "lookup" trace span
        product = await lookup(...)
    debug_put("off_status", 200)       # appears under `debug` when the client sent ?debug=1

The middleware in main.py creates one RequestContext per request. Code running outside a
request (background tasks, scripts) gets a throwaway context, so `stage()` never fails.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

try:  # tracing is optional at import time; core.tracing wires the provider.
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("pear")
except Exception:  # pragma: no cover
    _tracer = None


@dataclass
class RequestContext:
    request_id: str = "-"
    debug: bool = False
    t0: float = field(default_factory=time.perf_counter)
    stages: dict[str, float] = field(default_factory=dict)  # ms, accumulated per name
    bag: dict[str, Any] = field(default_factory=dict)
    uid: str | None = None

    def timings_ms(self) -> dict[str, int]:
        out = {k: int(round(v)) for k, v in self.stages.items()}
        out["total"] = int(round((time.perf_counter() - self.t0) * 1000))
        return out


_ctx: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("pear_ctx", default=None)


def current() -> RequestContext:
    ctx = _ctx.get()
    if ctx is None:
        ctx = RequestContext()
        _ctx.set(ctx)
    return ctx


def begin(request_id: str, debug: bool = False) -> contextvars.Token:
    return _ctx.set(RequestContext(request_id=request_id, debug=debug))


def end(token: contextvars.Token) -> None:
    _ctx.reset(token)


@contextlib.contextmanager
def stage(name: str, **attrs: Any) -> Iterator[None]:
    """Time a block. Nested/concurrent stages with different names are fine."""
    ctx = current()
    t = time.perf_counter()
    span_cm = _tracer.start_as_current_span(name) if _tracer is not None else contextlib.nullcontext()
    with span_cm as span:
        if span is not None and attrs:
            for k, v in attrs.items():
                try:
                    span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
                except Exception:
                    pass
        try:
            yield
        finally:
            ctx.stages[name] = ctx.stages.get(name, 0.0) + (time.perf_counter() - t) * 1000


def debug_put(key: str, value: Any) -> None:
    """Attach something to the request's debug bag (only serialized when debug is on)."""
    ctx = current()
    if ctx.debug:
        ctx.bag[key] = value


def annotate(key: str, value: Any) -> None:
    """Always recorded (debug or not). `product_key` and `states` also go on the request log line."""
    current().bag[key] = value


def debug_append(key: str, value: Any) -> None:
    ctx = current()
    if ctx.debug:
        ctx.bag.setdefault(key, []).append(value)


def timings() -> dict[str, int]:
    return current().timings_ms()


def debug_payload() -> dict[str, Any] | None:
    ctx = current()
    return dict(ctx.bag) if ctx.debug else None


def request_id() -> str:
    return current().request_id
