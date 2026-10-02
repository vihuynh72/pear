"""Tests for agent/graph.py: the pear_ask ADK Workflow (no network, no Gemini, no API key).

pipeline.ask is owned by another lane, so every test replaces its four functions with fakes
on the module object. If pipeline/ask.py is missing or not importable yet, a stub module is
put in sys.modules instead, so these tests verify the graph in isolation either way.

Run:  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_agent.py -q -s
"""

from __future__ import annotations

import asyncio
import importlib
import statistics
import sys
import time
import types
from typing import Any

import pytest

from core import timing
from core.errors import PearError


class FakeAsk:
    """Records calls; `intent` is what understand() reports."""

    def __init__(self) -> None:
        self.intent: Any = "check"
        self.calls: list[tuple[str, Any, dict]] = []
        self.understand_delay = 0.0

    async def understand(self, clients: Any, body: dict) -> dict:
        self.calls.append(("understand", clients, body))
        if self.understand_delay:
            await asyncio.sleep(self.understand_delay)
        return {
            "language": body.get("language") or "en",
            "intent": self.intent,
            "product_query": f"q:{body.get('text')}",
            "member_ids": ["m1"],
            "transcript": body.get("text") or "",
        }

    def _answer(self, node: str, body: dict) -> dict:
        return {
            "node": node,
            "transcript": body.get("transcript", ""),
            "language": body.get("language", "en"),
            "intent": body.get("intent"),
            "product_query": body.get("product_query"),
            "member_ids": body.get("member_ids", []),
            "next": "scan_needed" if node == "unclear" else "answer",
            "answer": {"text": f"{node} answer", "language": body.get("language", "en")},
        }

    async def check(self, clients: Any, body: dict) -> dict:
        self.calls.append(("check", clients, body))
        with timing.stage("fake_check"):
            await asyncio.sleep(0)
        return self._answer("check", body)

    async def alternatives(self, clients: Any, body: dict) -> dict:
        self.calls.append(("alternatives", clients, body))
        return self._answer("alternatives", body)

    def scan_needed(self, body: dict) -> dict:  # sync, like the agreed interface
        self.calls.append(("scan_needed", None, body))
        return self._answer("unclear", body)

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _ask_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    import pipeline

    try:
        return importlib.import_module("pipeline.ask")
    except Exception:  # not written yet / mid-edit by its lane: use a stub module
        stub = types.ModuleType("pipeline.ask")
        monkeypatch.setitem(sys.modules, "pipeline.ask", stub)
        monkeypatch.setattr(pipeline, "ask", stub, raising=False)
        return stub


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAsk:
    f = FakeAsk()
    mod = _ask_module(monkeypatch)
    for name in ("understand", "check", "alternatives", "scan_needed"):
        monkeypatch.setattr(mod, name, getattr(f, name), raising=False)
    return f


@pytest.fixture
def graph():
    from agent import graph as g

    g.warm()
    return g


def _body(text: str = "can grandma eat this", **extra: Any) -> dict:
    return {"text": text, "language": "en", "last_product_key": "off:123",
            "household": {"members": [{"id": "m1"}]}, **extra}


# ----------------------------------------------------------------------------- routing


@pytest.mark.parametrize(
    ("intent", "node", "fn"),
    [("check", "check", "check"), ("alternatives", "alternatives", "alternatives"),
     ("unclear", "unclear", "scan_needed")],
)
async def test_each_intent_routes_to_its_node(graph, fake, intent, node, fn):
    fake.intent = intent
    clients = object()
    body = _body()
    out = await graph.run_ask(clients, body, "uid-1")

    assert out is not None and out["node"] == node
    assert fake.names() == ["understand", fn]
    # understand gets the original body and the request's clients
    assert fake.calls[0][1] is clients and fake.calls[0][2] is body
    # the routed node gets body + understood fields
    routed = fake.calls[1][2]
    assert routed["text"] == body["text"] and routed["last_product_key"] == "off:123"
    assert routed["intent"] == intent and routed["product_query"] == f"q:{body['text']}"
    assert routed["household"] == body["household"]
    if fn != "scan_needed":
        assert fake.calls[1][1] is clients
    assert out["intent"] == intent


@pytest.mark.parametrize("weird", ["buy", None, ""])
async def test_unknown_intent_falls_back_to_unclear(graph, fake, weird):
    fake.intent = weird
    out = await graph.run_ask(object(), _body(), "uid-1")
    assert out["node"] == "unclear" and out["intent"] == "unclear"
    assert fake.names() == ["understand", "scan_needed"]


async def test_audio_only_body_without_text(graph, fake):
    out = await graph.run_ask(object(), {"audio": b"\x00\x01" * 1000, "household": {}}, "")
    assert out["node"] == "check"
    assert fake.calls[0][2]["audio"] == b"\x00\x01" * 1000


# ----------------------------------------------------------------------------- errors


async def test_pear_error_in_node_propagates_unchanged(graph, fake, monkeypatch):
    async def boom(clients, body):
        raise PearError("not_found", "no product", next="take_photo")

    monkeypatch.setattr(sys.modules["pipeline.ask"], "check", boom)
    before = graph.stats()["failed"]
    with pytest.raises(PearError) as ei:
        await graph.run_ask(object(), _body(), "uid-1")
    assert ei.value.code == "not_found"
    assert graph.stats()["failed"] == before + 1


async def test_unexpected_error_in_model_node_propagates(graph, fake, monkeypatch):
    async def boom(clients, body):
        raise ValueError("gemini said no")

    monkeypatch.setattr(sys.modules["pipeline.ask"], "understand", boom)
    with pytest.raises(ValueError, match="gemini said no"):
        await graph.run_ask(object(), _body(), "uid-1")
    assert "gemini said no" in graph.stats()["last_error"]


async def test_scope_does_not_leak_and_next_run_recovers(graph, fake, monkeypatch):
    mod = sys.modules["pipeline.ask"]
    good_check = mod.check

    async def boom(clients, body):
        raise RuntimeError("x")

    monkeypatch.setattr(mod, "check", boom)
    with pytest.raises(RuntimeError):
        await graph.run_ask(object(), _body(), "uid-1")
    assert graph._scope.get() is None

    monkeypatch.setattr(mod, "check", good_check)
    assert (await graph.run_ask(object(), _body(), "uid-1"))["node"] == "check"
    assert graph._scope.get() is None


# ----------------------------------------------------------------------------- isolation


async def test_concurrent_runs_see_only_their_own_clients_and_body(graph, fake):
    fake.understand_delay = 0.005
    pairs = [(object(), _body(f"question {i}")) for i in range(12)]
    outs = await asyncio.gather(*(graph.run_ask(c, b, f"uid-{i % 3}") for i, (c, b) in enumerate(pairs)))

    for (_, body), out in zip(pairs, outs, strict=True):
        assert out["transcript"] == body["text"]
        assert out["product_query"] == f"q:{body['text']}"
    seen = {(id(c), b["text"]) for name, c, b in fake.calls if name == "check"}
    assert seen == {(id(c), b["text"]) for c, b in pairs}


async def test_sessions_are_deleted_after_each_run(graph, fake):
    for i in range(3):
        await graph.run_ask(object(), _body(), f"uid-clean-{i}")
    sessions = graph._get_built()[0].sessions
    users = sessions.sessions.get(graph.APP_NAME, {})
    assert not any(k.startswith("uid-clean-") for k in users)
    assert all(not v for v in users.values())


async def test_timings_and_debug_reach_the_request_context(graph, fake):
    token = timing.begin("req-test", debug=True)
    try:
        await graph.run_ask(object(), _body(), "uid-1")
        t = timing.timings()
        dbg = timing.debug_payload() or {}
    finally:
        timing.end(token)
    assert "adk" in t and "fake_check" in t  # stage() inside a node lands on the request
    assert dbg["ask_engine"] == "adk"
    assert dbg["adk"]["route"] == "check"
    assert dbg["adk"]["nodes"] == ["read_question", "check_product"]
    assert dbg["adk"]["overhead_ms"] >= 0


# ----------------------------------------------------------------------------- no model in ADK


async def test_adk_calls_no_model_and_needs_no_api_key(graph, fake, monkeypatch):
    from google import genai

    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)

    def no_client(*a, **kw):
        raise AssertionError("ADK tried to build a genai client")

    monkeypatch.setattr(genai, "Client", no_client)
    for intent in ("check", "alternatives", "unclear"):
        fake.intent = intent
        assert (await graph.run_ask(object(), _body(), "uid-1"))["intent"] == intent
    assert fake.names().count("understand") == 3  # the only "model" call is ours


def test_workflow_shape(graph):
    wf = graph.get_workflow()
    assert wf.name == graph.APP_NAME
    assert set(graph.ROUTE_NODES) == {"check", "alternatives", "unclear"}


# ----------------------------------------------------------------------------- overhead


async def test_overhead_median_under_50ms(graph, fake):
    await graph.run_ask(object(), _body(), "uid-warm")  # first run warms ADK's lazy paths
    overheads: list[float] = []
    totals: list[float] = []
    for i in range(20):
        fake.intent = ("check", "alternatives", "unclear")[i % 3]
        token = timing.begin(f"req-{i}", debug=True)
        try:
            t0 = time.perf_counter()
            await graph.run_ask(object(), _body(f"q{i}"), "uid-bench")
            totals.append((time.perf_counter() - t0) * 1000)
            overheads.append(timing.debug_payload()["adk"]["overhead_ms"])
        finally:
            timing.end(token)
    med = statistics.median(overheads)
    print(f"\nADK overhead over 20 runs: median={med:.2f} ms  max={max(overheads):.2f} ms  "
          f"p90={sorted(overheads)[17]:.2f} ms  total median={statistics.median(totals):.2f} ms  "
          f"build={graph.stats()['build_ms']} ms")
    assert med < 50
