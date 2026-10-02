"""Shared pytest fixtures.

The runtime always calls real Gemini (no fake mode in the app). Tests that exercise Gemini
paths inject `fake_genai` into the running app instead, so CI never needs credentials:

    def test_photo(app_client, fake_genai):
        fake_genai.on("label_reader", {"legibility": "full", ...})   # match by prompt substring
        r = app_client.post("/v1/scan/photo", files=..., data=...)

`fake_genai.on(match, payload)`: when `match` appears in the system instruction or contents
(case-insensitive), return `payload` (dict -> JSON text, str -> raw text, Exception -> raised,
callable(model, contents, config) -> any of those). Unmatched calls raise AssertionError so
a test never silently passes on an unexpected model call. `fake_genai.calls` records every call.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("PEAR_STORE", "memory")
os.environ.setdefault("PEAR_TRACE", "0")
os.environ.setdefault("PEAR_AUTH", "optional")
os.environ.setdefault("PEAR_LOG_FORMAT", "pretty")
# Skip google.auth's GCE metadata probe (~3 s per app startup off-GCP). Tests only: Cloud Run
# needs the metadata server for its credentials.
os.environ.setdefault("NO_GCE_CHECK", "true")

import pytest  # noqa: E402

from models.contracts import Household  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEMO_HOUSEHOLD_PATH = REPO / "contracts" / "household.example.json"


def demo_household_dict() -> dict:
    return json.loads(DEMO_HOUSEHOLD_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def household_dict() -> dict:
    return demo_household_dict()


@pytest.fixture
def household() -> Household:
    return Household.model_validate(demo_household_dict())


# ----------------------------------------------------------------------------- fake Gemini


class _Web:
    def __init__(self, uri: str, title: str) -> None:
        self.uri, self.title = uri, title


class _Chunk:
    def __init__(self, uri: str, title: str) -> None:
        self.web = _Web(uri, title)


class _Grounding:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self.grounding_chunks = chunks
        self.search_entry_point = None


class _Candidate:
    def __init__(self, grounding: _Grounding | None) -> None:
        self.grounding_metadata = grounding
        self.finish_reason = "STOP"


class FakeResponse:
    def __init__(self, text: str, grounded_urls: list[str] | None = None) -> None:
        self.text = text
        self.parsed = None
        g = _Grounding([_Chunk(u, u.split("/")[2] if "//" in u else u) for u in grounded_urls]) if grounded_urls else None
        self.candidates = [_Candidate(g)]
        self.usage_metadata = None


class _Embedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _EmbedResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = [_Embedding(v) for v in vectors]


def fake_vector(text: str, dim: int = 768) -> list[float]:
    """Deterministic pseudo-embedding: similar strings share token buckets."""
    v = [0.0] * dim
    for tok in str(text).lower().split():
        h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    return v


def _flatten(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return " ".join(_flatten(i) for i in x)
    text = getattr(x, "text", None)
    return text if isinstance(text, str) else ""


class _Models:
    def __init__(self, owner: "FakeGenai") -> None:
        self._o = owner

    async def generate_content(self, *, model: str, contents: Any, config: Any = None) -> FakeResponse:
        system = _flatten(getattr(config, "system_instruction", None)) if config is not None else ""
        haystack = (system + " " + _flatten(contents)).lower()
        self._o.calls.append({"model": model, "contents": contents, "config": config, "haystack": haystack})
        for match, payload, grounded in self._o.handlers:
            if match.lower() in haystack:
                if callable(payload) and not isinstance(payload, (dict, str)):
                    payload = payload(model, contents, config)
                if isinstance(payload, BaseException):
                    raise payload
                if isinstance(payload, FakeResponse):
                    return payload
                text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                return FakeResponse(text, grounded)
        raise AssertionError(f"fake_genai: no handler for call to {model}: {haystack[:300]!r}")

    async def embed_content(self, *, model: str, contents: Any, config: Any = None) -> _EmbedResponse:
        texts = contents if isinstance(contents, list) else [contents]
        self._o.embed_calls.append({"model": model, "texts": texts})
        dim = getattr(config, "output_dimensionality", None) or 768
        return _EmbedResponse([fake_vector(_flatten(t), dim) for t in texts])


class _Aio:
    def __init__(self, owner: "FakeGenai") -> None:
        self.models = _Models(owner)


class FakeGenai:
    def __init__(self) -> None:
        self.handlers: list[tuple[str, Any, list[str] | None]] = []
        self.calls: list[dict] = []
        self.embed_calls: list[dict] = []
        self.aio = _Aio(self)

    def on(self, match: str, payload: Any, *, grounded_urls: list[str] | None = None) -> "FakeGenai":
        self.handlers.append((match, payload, grounded_urls))
        return self


@pytest.fixture
def fake_genai() -> FakeGenai:
    return FakeGenai()


@pytest.fixture
def app_client(fake_genai):
    """TestClient over the real app with the in-memory store and fake Gemini injected."""
    from fastapi.testclient import TestClient

    import main

    try:  # reset per-process state owned by pipeline modules, if they expose a reset()
        from pipeline import product_store

        getattr(product_store, "reset_memory", lambda: None)()
    except Exception:
        pass
    with TestClient(main.app) as client:
        main.app.state.clients.genai = fake_genai
        main.app.state.clients.genai_error = None
        yield client
