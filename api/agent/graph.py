"""The /v1/ask path as a Google ADK 2.x Workflow ("pear_ask"), addendum 11.

Graph (google-adk==2.3.0):

    START -> read_question --route=intent--> check        -> check_product  (pipeline.ask.check)
                                             alternatives -> suggest        (pipeline.ask.alternatives)
                                             unclear      -> unclear        (pipeline.ask.scan_needed)

read_question is the only node that calls a model (pipeline.ask.understand, one Gemini call
through pipeline/gemini.py). The route is chosen by code from its `intent`, never by ADK or a
model. ADK runs no LLM itself, so it needs no GOOGLE_API_KEY. Nodes are plain async functions
(FunctionNode); each is a thin wrapper over the same pipeline.ask function FastAPI's direct
engine calls, so pipeline.ask.answer can fall back to answer_direct at any time.

How per-request data reaches the nodes
    The Workflow and Runner are built once (first use or warm()) and shared by every request.
    `clients` and the original request body travel in a per-call ContextVar (_RunScope), set by
    run_ask() before the run. asyncio copies the context into every task ADK creates, so each
    concurrent run sees only its own scope, and nothing leaks after run_ask() resets it. They are
    NOT put in ADK session state: InMemorySessionService deep-copies sessions, which would copy
    the genai/httpx/Firestore clients, and the body can hold audio bytes.
    The user message ADK records is just the question text. read_question emits
    Event(output={**body, **understood}, route=intent); the routed node receives that dict.
    Each run uses a fresh session id, deleted afterwards so memory stays flat.

Errors
    An exception inside a node (PearError or anything else) propagates out of run_ask unchanged,
    so pipeline.ask.answer can re-raise PearError and fall back to answer_direct on the rest.
    ADK logs unexpected node failures itself; expected PearErrors are filtered from those logs.
    run_ask returns None only if the workflow produced no terminal output.

Debug
    timings_ms["adk"] = whole run (includes the node work, e.g. the understand Gemini call). Log line `adk.run` has
    total_ms, node_ms, overhead_ms (= total minus time inside node functions), route, nodes.
    ?debug=1 adds debug.ask_engine = "adk" and debug.adk = {route, nodes, overhead_ms, ...}.
    stats() returns counters and the median overhead (handy for /debug).
    Standalone (no server, fake pipeline functions):
        PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_agent.py -q -s
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import logging
import os
import statistics
import time
import uuid
import warnings
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from core.errors import PearError
from core.logging import log_event
from core.timing import debug_put, stage

APP_NAME = "pear_ask"
ROUTES: tuple[str, ...] = ("check", "alternatives", "unclear")
FALLBACK_ROUTE = "unclear"

# ADK's legacy spans copy message content by default; household data must not land in traces.
os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")


# ----------------------------------------------------------------------------- per-call scope


@dataclass
class _RunScope:
    """Everything one run_ask() call needs that must not live in ADK session state."""

    clients: Any
    body: dict
    node_ms: float = 0.0  # wall time spent inside node functions
    nodes: list[str] = field(default_factory=list)
    route: str | None = None


_scope: contextvars.ContextVar[_RunScope | None] = contextvars.ContextVar("pear_adk_scope", default=None)


def _current_scope() -> _RunScope:
    scope = _scope.get()
    if scope is None:
        raise RuntimeError("agent.graph node ran outside run_ask(): no per-call scope is set")
    return scope


@contextlib.contextmanager
def _in_node(name: str) -> Iterator[_RunScope]:
    """Time a node body and record that it ran."""
    scope = _current_scope()
    scope.nodes.append(name)
    t = time.perf_counter()
    try:
        yield scope
    finally:
        scope.node_ms += (time.perf_counter() - t) * 1000


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _ask_module():
    """pipeline.ask, imported lazily (another lane owns it; tests may stub it)."""
    from pipeline import ask

    return ask


def _route_for(understood: dict | None) -> str:
    intent = (understood or {}).get("intent")
    return intent if intent in ROUTES else FALLBACK_ROUTE


# ----------------------------------------------------------------------------- nodes


async def read_question(node_input: Any = None) -> Any:
    """Model node: pipeline.ask.understand (one Gemini call), then route on intent in code."""
    from google.adk.events.event import Event

    with _in_node("read_question") as scope:
        understood = await _maybe_await(_ask_module().understand(scope.clients, scope.body))
        route = _route_for(understood)
        scope.route = route
        merged = {**scope.body, **(understood or {}), "intent": route}
    return Event(output=merged, route=route)


async def check_product(node_input: Any) -> Any:
    """Code node: resolve the product, run the rules, template the answer."""
    with _in_node("check_product") as scope:
        return await _maybe_await(_ask_module().check(scope.clients, node_input))


async def suggest(node_input: Any) -> Any:
    """Code node: picks for the question (the /v1/picks logic)."""
    with _in_node("suggest") as scope:
        return await _maybe_await(_ask_module().alternatives(scope.clients, node_input))


async def unclear(node_input: Any) -> Any:
    """Code node: the "Scan it" answer, next = scan_needed."""
    with _in_node("unclear"):
        return await _maybe_await(_ask_module().scan_needed(node_input))


ROUTE_NODES = {"check": check_product, "alternatives": suggest, "unclear": unclear}


# ----------------------------------------------------------------------------- build once


class _QuietPearErrors(logging.Filter):
    """Drop ADK's traceback logs for expected contract errors (not_found etc.)."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, PearError)


_ADK_LOGGERS = ("google_adk.google.adk.runners", "google_adk.google.adk.workflow._node_runner")


@dataclass
class _Built:
    workflow: Any
    runner: Any
    sessions: Any
    build_ms: float


_built: _Built | None = None


def _build() -> _Built:
    t = time.perf_counter()
    with warnings.catch_warnings():  # ADK's import emits a DeprecationWarning (BaseAgentConfig)
        warnings.simplefilter("ignore", DeprecationWarning)
        from google.adk import Runner, Workflow
        from google.adk.sessions import InMemorySessionService
        from google.adk.workflow import START

    workflow = Workflow(
        name=APP_NAME,
        description="Pear ask: one model node, deterministic routes on intent.",
        edges=[(START, read_question), (read_question, dict(ROUTE_NODES))],
    )
    sessions = InMemorySessionService()
    runner = Runner(node=workflow, session_service=sessions, auto_create_session=True)
    for name in _ADK_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, _QuietPearErrors) for f in lg.filters):
            lg.addFilter(_QuietPearErrors())
    build_ms = (time.perf_counter() - t) * 1000
    log_event("adk.build", build_ms=round(build_ms, 1), app=APP_NAME, routes=list(ROUTE_NODES))
    return _Built(workflow=workflow, runner=runner, sessions=sessions, build_ms=build_ms)


def _get_built() -> tuple[_Built, bool]:
    """Return the shared Workflow/Runner, building it on first use. Second value: built now."""
    global _built
    if _built is None:
        _built = _build()
        return _built, True
    return _built, False


def warm() -> float:
    """Import ADK and build the graph now (call from startup). Returns build ms (0 if built)."""
    built, fresh = _get_built()
    return built.build_ms if fresh else 0.0


def get_workflow() -> Any:
    """The google.adk Workflow object (for inspection / standalone runs)."""
    return _get_built()[0].workflow


# ----------------------------------------------------------------------------- run


_STATS: dict[str, Any] = {"runs": 0, "ok": 0, "failed": 0, "no_output": 0, "last_error": None}
_OVERHEAD_MS: deque[float] = deque(maxlen=100)


def stats() -> dict:
    """Counters plus median/max overhead of the last 100 runs."""
    recent = list(_OVERHEAD_MS)
    return {
        **_STATS,
        "built": _built is not None,
        "build_ms": round(_built.build_ms, 1) if _built else None,
        "overhead_ms_median": round(statistics.median(recent), 2) if recent else None,
        "overhead_ms_max": round(max(recent), 2) if recent else None,
        "samples": len(recent),
    }


def _message_text(body: dict) -> str:
    text = body.get("text") or body.get("transcript") or ""
    return text if isinstance(text, str) else str(text)


def _is_root_output(event: Any) -> bool:
    """True for the event that carries the Workflow's own output (output_for has the root path)."""
    paths = getattr(getattr(event, "node_info", None), "output_for", None) or []
    return any("/" not in p and p.split("@", 1)[0] == APP_NAME for p in paths)


async def _drive(built: _Built, user_id: str, session_id: str, body: dict) -> dict | None:
    from google.genai import types

    message = types.Content(role="user", parts=[types.Part(text=_message_text(body))])
    final: Any = None
    async for event in built.runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
        if event.output is not None and _is_root_output(event):
            final = event.output
    if final is not None and hasattr(final, "model_dump"):
        final = final.model_dump()
    return final


async def _forget_session(built: _Built, user_id: str, session_id: str) -> None:
    """Delete this run's session, and the user's (then empty) bucket, so memory stays flat."""
    try:
        await built.sessions.delete_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        users = getattr(built.sessions, "sessions", {}).get(APP_NAME, {})
        if user_id in users and not users[user_id]:  # no await between check and pop: race-free
            users.pop(user_id, None)
    except Exception as e:  # never let cleanup mask the real result
        log_event("adk.session_cleanup_failed", level=logging.WARNING, error=repr(e))


async def run_ask(clients: Any, body: dict, uid: str) -> dict | None:
    """Run the pear_ask Workflow for one question. Returns the AskResponse-shaped dict.

    Raises whatever a node raised (PearError unchanged). Returns None if no node produced
    the workflow output.
    """
    built, fresh = _get_built()  # build time is reported separately, not as overhead
    scope = _RunScope(clients=clients, body=body)
    token = _scope.set(scope)
    user_id = uid or "anon"
    session_id = uuid.uuid4().hex
    _STATS["runs"] += 1
    t0 = time.perf_counter()
    out: dict | None = None
    error: BaseException | None = None
    try:
        with stage("adk"):
            try:
                out = await _drive(built, user_id, session_id, body)
            finally:
                await _forget_session(built, user_id, session_id)
        return out
    except BaseException as e:
        error = e
        raise
    finally:
        _scope.reset(token)
        _record(scope, (time.perf_counter() - t0) * 1000, out, error, built.build_ms if fresh else None)


def _record(scope: _RunScope, total_ms: float, out: dict | None, error: BaseException | None,
            build_ms: float | None) -> None:
    overhead_ms = max(total_ms - scope.node_ms, 0.0)
    _OVERHEAD_MS.append(overhead_ms)
    if error is not None:
        _STATS["failed"] += 1
        _STATS["last_error"] = repr(error)[:300]
    elif out is None:
        _STATS["no_output"] += 1
    else:
        _STATS["ok"] += 1
    summary = {
        "route": scope.route,
        "nodes": list(scope.nodes),
        "total_ms": round(total_ms, 2),
        "node_ms": round(scope.node_ms, 2),
        "overhead_ms": round(overhead_ms, 2),
        "ok": error is None and out is not None,
    }
    if build_ms is not None:
        summary["build_ms"] = round(build_ms, 1)
    if error is not None:
        summary["error"] = repr(error)[:300]
    log_event("adk.run", **summary)
    debug_put("ask_engine", "adk")
    debug_put("adk", summary)
