"""The one place that talks to Gemini (addendum 5.5): every call goes through here so
timeouts, thinking level, JSON schema mode, error mapping, timing and debug capture are
identical everywhere.

    data = await generate_json(clients, call="label_reader", model=s.model_label,
                               contents=[image_part, "Read this label."],
                               schema=LABEL_SCHEMA, system=SYSTEM, timeout_s=s.t_label)

    resp = await generate_raw(clients, call="price_finder", ..., tools=[...])  # no JSON mode

With ?debug=1 the request's debug bag gets `gemini.<call>` = model, ms, token usage and the
raw text (truncated), so you can see exactly what the model returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from core.clients import Clients
from core.errors import PearError
from core.logging import log_event
from core.timing import debug_put, stage

log = logging.getLogger("pear.gemini")


def _types():
    from google.genai import types

    return types


def image_part(data: bytes, mime_type: str = "image/jpeg"):
    return _types().Part.from_bytes(data=data, mime_type=mime_type)


def audio_part(data: bytes, mime_type: str = "audio/mp4"):
    # iOS AVAudioRecorder .m4a is AAC in an MP4 container -> "audio/mp4" (or audio/m4a).
    return _types().Part.from_bytes(data=data, mime_type=mime_type)


def _map_error(call: str, e: Exception, timeout_s: float) -> PearError:
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return PearError("internal", "Pear took too long to read that. Try again?", next="retry",
                         detail={"call": call, "why": f"timeout after {timeout_s}s"})
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code == 429 or "RESOURCE_EXHAUSTED" in str(e):
        return PearError("rate_limited", "Pear is busy. Try again in a moment.", next="retry",
                         detail={"call": call, "why": repr(e)[:500]})
    if code in (401, 403) or "PERMISSION_DENIED" in str(e):
        return PearError("unavailable", "Pear's AI reader isn't authorized right now.", next="retry",
                         detail={"call": call, "why": repr(e)[:500],
                                 "fix": "grant roles/aiplatform.user to the service account"})
    if code == 404 or "NOT_FOUND" in str(e):
        return PearError("internal", "Pear's AI model isn't available.", next="retry",
                         detail={"call": call, "why": repr(e)[:500],
                                 "fix": "check PEAR_MODEL_* and PEAR_GEMINI_LOCATION=global"})
    return PearError("internal", "Something went wrong reading that. Try again?", next="retry",
                     detail={"call": call, "why": repr(e)[:500]})


def _usage(resp: Any) -> dict[str, Any] | None:
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return None
    return {k: getattr(u, k, None) for k in ("prompt_token_count", "candidates_token_count",
                                             "thoughts_token_count", "total_token_count")}


async def generate_raw(
    clients: Clients,
    *,
    call: str,
    model: str,
    contents: Any,
    timeout_s: float,
    system: str | None = None,
    max_output_tokens: int = 1024,
    tools: list | None = None,
    response_schema: dict | None = None,
    thinking_level: str = "minimal",
) -> Any:
    """Call generate_content with Pear's defaults. Returns the SDK response object."""
    types = _types()
    client = clients.require_genai()
    cfg_kwargs: dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        # No temperature/top_p/top_k: Google's Gemini 3.5 guidance (addendum 5.5).
        "thinking_config": types.ThinkingConfig(thinking_level=thinking_level.upper()),
    }
    if system:
        cfg_kwargs["system_instruction"] = system
    if tools:
        cfg_kwargs["tools"] = tools
    if response_schema is not None:
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["response_json_schema"] = response_schema
    cfg = types.GenerateContentConfig(**cfg_kwargs)

    t0 = time.perf_counter()
    with stage(call, model=model):
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(model=model, contents=contents, config=cfg),
                timeout=timeout_s,
            )
        except PearError:
            raise
        except Exception as e:
            err = _map_error(call, e, timeout_s)
            ms = int((time.perf_counter() - t0) * 1000)
            log_event("gemini.error", level=logging.WARNING, call=call, model=model, ms=ms,
                      error_code=err.code, why=err.detail.get("why"))
            debug_put(f"gemini.{call}", {"model": model, "ms": ms, "error": err.detail})
            raise err from e
    ms = int((time.perf_counter() - t0) * 1000)
    usage = _usage(resp)
    log_event("gemini.call", call=call, model=model, ms=ms, usage=usage)
    text = getattr(resp, "text", None)
    debug_put(f"gemini.{call}", {"model": model, "ms": ms, "usage": usage,
                                 "text": (text or "")[:4000]})
    return resp


async def generate_json(
    clients: Clients,
    *,
    call: str,
    model: str,
    contents: Any,
    schema: dict,
    timeout_s: float,
    system: str | None = None,
    max_output_tokens: int = 1024,
) -> dict[str, Any]:
    """generate_raw with JSON schema output; returns the parsed object."""
    resp = await generate_raw(clients, call=call, model=model, contents=contents, timeout_s=timeout_s,
                              system=system, max_output_tokens=max_output_tokens,
                              response_schema=schema)
    text = getattr(resp, "text", None) or ""
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        finish = None
        try:
            finish = str(resp.candidates[0].finish_reason)
        except Exception:
            pass
        log_event("gemini.bad_json", level=logging.WARNING, call=call, model=model,
                  finish_reason=finish, text=text[:1000])
        raise PearError("internal", "Pear couldn't understand the reader's answer. Try again?",
                        next="retry", detail={"call": call, "finish_reason": finish,
                                              "text": text[:1000]}) from e


async def embed_texts(clients: Clients, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT",
                      timeout_s: float | None = None) -> list[list[float]]:
    """Gemini Embedding 2, 768-d, same settings for store+query. One vector per text.

    embed_content on Vertex returns ONE embedding per request (a list of strings becomes one
    Content with many parts), so a batch is sent as one request per text (8 in flight).
    Stored vectors (RETRIEVAL_DOCUMENT) never fall back to gemini-embedding-001: that is a
    different vector space and would be compared against gemini-embedding-2 query vectors.
    """
    types = _types()
    s = clients.settings
    client = clients.require_genai()
    cfg = types.EmbedContentConfig(output_dimensionality=s.embedding_dim, task_type=task_type)
    limit = timeout_s or s.t_embed
    models = (s.model_embedding,) if task_type == "RETRIEVAL_DOCUMENT" else (s.model_embedding, s.model_embedding_fallback)
    sem = asyncio.Semaphore(8)

    async def one(model: str, text: str) -> list[float]:
        async with sem:
            resp = await asyncio.wait_for(
                client.aio.models.embed_content(model=model, contents=text, config=cfg), timeout=limit)
        embs = list(resp.embeddings or [])
        if len(embs) != 1:
            raise RuntimeError(f"embed returned {len(embs)} vectors for 1 text")
        return list(embs[0].values)

    last: Exception | None = None
    for model in models:
        try:
            with stage("embed", model=model):
                out = await asyncio.gather(*(one(model, x) for x in texts))
            if len(out) != len(texts):
                raise RuntimeError("embedding count mismatch")
            return out
        except Exception as e:  # try the fallback model once (query vectors only)
            last = e
            log_event("gemini.embed_error", level=logging.WARNING, model=model, why=repr(e)[:300])
    raise _map_error("embed", last or RuntimeError("embed failed"), timeout_s or s.t_embed)
