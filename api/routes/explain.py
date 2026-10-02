"""POST /v1/explain (P1): ProductKeyRequest -> text/event-stream of friendlier lines.

    data: {"member_id": "m_mia", "language": "en", "text": "..."}\\n\\n     one per member, as ready
    event: done\\ndata: {}\\n\\n                                              always last

The product is loaded and the first line computed before the response starts, so a missing
product is a normal JSON error (404 not_found, next=take_photo) instead of a broken stream.
After that the stream never errors: pipeline.explain falls back to the templated reason line.
Headers: Cache-Control no-cache, X-Accel-Buffering no (no proxy buffering).

Debugging: log events explain.line / explain.done. Try it:
    curl -N -X POST localhost:8080/v1/explain -H 'content-type: application/json' \
         -d '{"product_key": "off:...", "household": {...}}'
PEAR_FEATURE_EXPLAIN=0 turns the endpoint into 503 unavailable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.errors import PearError
from models.contracts import ExplainEvent, ProductKeyRequest
from pipeline import explain
from pipeline.prompts import say

router = APIRouter(tags=["explain"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
DONE = "event: done\ndata: {}\n\n"


def sse(event: ExplainEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _body(first: ExplainEvent | None, rest: AsyncIterator[ExplainEvent]) -> AsyncIterator[str]:
    try:
        if first is not None:
            yield sse(first)
            async for ev in rest:
                yield sse(ev)
        yield DONE
    finally:
        await rest.aclose()


@router.post("/v1/explain", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}},
                              "description": "SSE: data: ExplainEvent JSON per member, then event: done"}})
async def post_explain(body: ProductKeyRequest, clients: Clients = Depends(get_clients),
                       user: User = Depends(current_user)) -> StreamingResponse:
    if not clients.settings.feature_explain:
        raise PearError("unavailable", say("error.explain_off", body.household.language,
                                           default="Explanations are off right now."), next="retry")
    stream = explain.explain_stream(clients, product_key=body.product_key, household=body.household,
                                    member_ids=body.member_ids)
    try:
        first = await anext(stream)  # raises not_found here, before any byte is sent
    except StopAsyncIteration:
        first = None
    return StreamingResponse(_body(first, stream), media_type="text/event-stream", headers=SSE_HEADERS)
