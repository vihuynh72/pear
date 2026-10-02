"""POST /v1/prices (P2, addendum 6 + 9): grounded prices for the picks row.

    {"product_keys": ["off:0987654321098", "off:0222222222222"], "country": "US"}
    -> {"prices": {"off:0987654321098": {"amount": 3.99, "currency": "USD", "retailer": "Target",
                   ...}, "off:0222222222222": null}, "timings_ms": {"wait": 2100, "total": 2100}}

Waits up to settings.t_price (6 s) in total for lookups already in flight (picks starts them
when it chooses a pick) and starts the rest; anything not done is null. The app shows
"About $3.99 at Target. Found with Google Search." and must render search_entry_html.

Debugging
- ?debug=1: per-key state (running/found/none/failed, reused) and gemini.price_finder (raw
  model text, ms) come back as JSON in the X-Pear-Debug response header.
- PEAR_FEATURE_PRICES=0 -> every key null, debug reason "feature_prices off".
- GET /debug/inflight shows pipeline.prices.stats().
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Response

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.timing import debug_payload, debug_put, request_id
from models.contracts import PricesRequest, PricesResponse
from pipeline import prices

router = APIRouter(tags=["prices"])
_MAX_DEBUG_HEADER = 8000


def _debug_header(response: Response) -> None:
    payload = debug_payload()
    if payload:
        response.headers["X-Pear-Debug"] = json.dumps(payload, default=str,
                                                      ensure_ascii=True)[:_MAX_DEBUG_HEADER]


@router.post("/v1/prices", response_model=PricesResponse)
async def post_prices(body: PricesRequest, response: Response,
                      clients: Clients = Depends(get_clients),
                      user: User = Depends(current_user)) -> PricesResponse:
    keys = list(dict.fromkeys(k for k in body.product_keys if k))
    if not clients.settings.feature_prices:
        debug_put("prices", {"skipped": "feature_prices off (PEAR_FEATURE_PRICES=0)"})
        out = PricesResponse(prices={k: None for k in keys}, timings_ms={"wait": 0, "total": 0},
                             request_id=request_id())
    else:
        out = await prices.for_keys(clients, keys, body.country)
    _debug_header(response)
    return out
