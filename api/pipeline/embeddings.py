"""Product and query embeddings (Gemini Embedding 2, 768-d) for name matching and picks.

    text = embedding_text(np)            # "{brand} {name}. {category}. {first 8 ingredients}"
    vec = await embed_product(clients, np)          # RETRIEVAL_DOCUMENT, None on failure
    vec = await embed_query(clients, "top ramen")   # RETRIEVAL_QUERY, None on failure
    vecs = await embed_products(clients, nps)       # batched (scripts/ingest.py)

All calls go through pipeline.gemini.embed_texts (model + fallback model, timeout,
stage("embed")). Failures return None and log `embed.failed`; they never break a scan.

A small in-process cache (keyed by task type + text, with in-flight de-duplication) means
the background save after a scan and the picks prefetch share one embedding call for the
same product. `stats()` shows hits/misses/failures.

Debugging: `embed.failed` log lines carry the reason (no ADC locally = "unavailable").
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Any

from core.clients import Clients
from core.logging import log_event
from models.domain import NormalizedProduct

DOC_TASK, QUERY_TASK = "RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"
CACHE_MAX = 512
BATCH = 50

_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_INFLIGHT: dict[str, asyncio.Future] = {}
_STATS: dict[str, int] = {"hits": 0, "misses": 0, "failed": 0, "shared": 0}


def stats() -> dict[str, Any]:
    return {**_STATS, "cached": len(_CACHE), "inflight": len(_INFLIGHT)}


def reset_cache() -> None:
    _CACHE.clear()
    _INFLIGHT.clear()


def _category_words(category_main: str | None) -> str:
    if not category_main:
        return ""
    return category_main.split(":", 1)[-1].replace("-", " ").strip()


def embedding_text(np: NormalizedProduct) -> str:
    """Addendum 8.3: "{brand} {name}. {category_main}. {first 8 ingredients}"."""
    p = np.product
    head = " ".join(x for x in ((p.brand or "").strip(), (p.name or "").strip()) if x)
    cat = _category_words(np.category_main)
    ingr = ", ".join(i.strip() for i in p.ingredients[:8] if i and i.strip())
    return ". ".join(x for x in (head, cat, ingr) if x)


def _cache_key(task: str, text: str) -> str:
    return task + ":" + hashlib.sha1(text.encode("utf-8")).hexdigest()


def _remember(key: str, vec: list[float]) -> None:
    _CACHE[key] = vec
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)


async def _embed_one(clients: Clients, text: str, task: str) -> list[float] | None:
    text = (text or "").strip()
    if not text:
        return None
    key = _cache_key(task, text)
    if key in _CACHE:
        _STATS["hits"] += 1
        _CACHE.move_to_end(key)
        return _CACHE[key]
    if getattr(clients, "genai", None) is None:
        # No Gemini client at all (no ADC locally): nothing to retry, so don't log a WARNING per
        # scan. One INFO line per process; /health already reports why Gemini is unavailable.
        _STATS["failed"] += 1
        _STATS["unavailable"] = _STATS.get("unavailable", 0) + 1
        if _STATS["unavailable"] == 1:
            log_event("embed.skipped", task=task, why=getattr(clients, "genai_error", None) or "genai client not built")
        return None
    fut = _INFLIGHT.get(key)
    if fut is not None:
        _STATS["shared"] += 1
        try:
            return await asyncio.shield(fut)
        except Exception:
            return None
    _STATS["misses"] += 1
    loop = asyncio.get_running_loop()
    fut = _INFLIGHT[key] = loop.create_future()
    vec: list[float] | None = None
    try:
        from pipeline.gemini import embed_texts

        vecs = await embed_texts(clients, [text], task_type=task)
        vec = vecs[0] if vecs and vecs[0] else None
        if vec:
            _remember(key, vec)
    except Exception as e:  # noqa: BLE001 - never break a scan over an embedding
        _STATS["failed"] += 1
        log_event("embed.failed", level=logging.WARNING, task=task, why=repr(e)[:300], text=text[:120])
    finally:
        _INFLIGHT.pop(key, None)
        if not fut.done():
            fut.set_result(vec)
    return vec


async def embed_product(clients: Clients, np: NormalizedProduct) -> list[float] | None:
    return await _embed_one(clients, embedding_text(np), DOC_TASK)


async def embed_query(clients: Clients, text: str) -> list[float] | None:
    return await _embed_one(clients, text, QUERY_TASK)


async def embed_products(clients: Clients, nps: list[NormalizedProduct], *, batch: int = BATCH,
                         timeout_s: float = 20.0) -> list[list[float] | None]:
    """Batch document embeddings (ingest). A failed batch yields None for its products."""
    from pipeline.gemini import embed_texts

    texts = [embedding_text(np) for np in nps]
    out: list[list[float] | None] = [None] * len(texts)
    for start in range(0, len(texts), batch):
        chunk = texts[start:start + batch]
        try:
            vecs = await embed_texts(clients, chunk, task_type=DOC_TASK, timeout_s=timeout_s)
        except Exception as e:  # noqa: BLE001
            _STATS["failed"] += 1
            log_event("embed.failed", level=logging.WARNING, task=DOC_TASK, batch=len(chunk), why=repr(e)[:300])
            continue
        for i, v in enumerate(vecs[:len(chunk)]):
            if v:
                out[start + i] = v
                _remember(_cache_key(DOC_TASK, chunk[i]), v)
    return out
