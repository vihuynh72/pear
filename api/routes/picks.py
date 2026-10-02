"""POST /v1/picks: three food products that fit everyone (addendum 6 + 7).

    {"product_key": "off:0123456789012", "household": {...}, "member_ids": null}
    -> {"items": [{"product": {...}, "verdicts": [...]}], "prefetched": true,
        "timings_ms": {"wait": 40, "total": 60}, "request_id": "..."}

Thin route: pipeline.picks.get() reuses the prefetch started at identify, or computes now.
It never errors for picks problems (timeout, product missing, vector index down): the app
just gets items [] and keeps showing the verdict.

Debugging
- ?debug=1 (or X-Pear-Debug: 1): PicksResponse has no debug field, so the request's debug bag
  (candidate path, candidate count, drop reasons, rank ms, timeout) comes back as JSON in the
  X-Pear-Debug response header.
- PEAR_FEATURE_PICKS=0 -> items [] with debug reason "feature_picks off".
- GET /debug/inflight shows pipeline.picks.stats().
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Response

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.timing import debug_payload, debug_put, request_id, timings
from models.contracts import PicksResponse, ProductKeyRequest
from pipeline import picks

router = APIRouter(tags=["picks"])
_MAX_DEBUG_HEADER = 8000


def _debug_header(response: Response) -> None:
    payload = debug_payload()
    if payload:
        response.headers["X-Pear-Debug"] = json.dumps(payload, default=str,
                                                      ensure_ascii=True)[:_MAX_DEBUG_HEADER]


@router.post("/v1/picks", response_model=PicksResponse)
async def post_picks(body: ProductKeyRequest, response: Response,
                     clients: Clients = Depends(get_clients),
                     user: User = Depends(current_user)) -> PicksResponse:
    if not clients.settings.feature_picks:
        debug_put("picks", {"skipped": "feature_picks off (PEAR_FEATURE_PICKS=0)"})
        out = PicksResponse(items=[], prefetched=False, timings_ms=timings(), request_id=request_id())
    else:
        out = await picks.get(clients, body.product_key, body.household, body.member_ids)
    _debug_header(response)
    return out
