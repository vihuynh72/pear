"""POST /v1/scan/barcode — barcode -> per-person verdicts. No LLM on this path, ever.

    curl -s localhost:8080/v1/scan/barcode?debug=1 -H 'content-type: application/json' \\
      -d '{"code": "049000028911", "household": '"$(cat ../contracts/household.example.json)"'}'

Steps: identify_barcode (cache_get -> lookup race -> normalize) -> picks prefetch (food only,
lazy import, a broken picks module never breaks a scan) -> rules -> ScanResult ->
after_scan (background: save + embed, enrichment, scan log) -> respond.

Errors: unknown barcode -> 404 {"error": {"code": "not_found", "next": "take_photo"}};
OFF rate limit with nothing else answering -> 429 rate_limited (next retry).
?debug=1 adds `cache`, `lookup`, `hits`, `evaluation` to the response's `debug`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from core.auth import User, current_user
from core.clients import Clients, get_clients
from core.logging import log_event
from core.timing import debug_put
from models.contracts import BarcodeRequest, Household, ScanResult
from models.domain import NormalizedProduct
from pipeline.products import after_scan, identify_barcode
from pipeline.result import build_scan_result, run_rules

router = APIRouter(tags=["scan"])


def _start_picks(clients: Clients, np: NormalizedProduct, household: Household) -> None:
    if np.product.category != "food":
        return
    try:
        from pipeline.picks import start_prefetch

        start_prefetch(clients, np, household)
    except Exception as e:  # noqa: BLE001 - picks are optional; the verdict is not
        log_event("picks.prefetch_unavailable", level=logging.WARNING, product_key=np.product.key,
                  why=repr(e)[:300])


@router.post("/v1/scan/barcode", response_model=ScanResult)
async def scan_barcode(body: BarcodeRequest, clients: Clients = Depends(get_clients),
                       user: User = Depends(current_user)) -> ScanResult:
    raw, np, cached = await identify_barcode(clients, body.code, language=body.household.language)
    debug_put("cached", cached)
    _start_picks(clients, np, body.household)
    ev = run_rules(np, body.household, body.member_ids)
    result = build_scan_result(np, body.household, body.member_ids, ev)
    after_scan(clients, raw=raw, np=np, household=body.household, uid=user.uid, evaluation=ev,
               cached=cached)
    log_event("scan.barcode", product_key=np.product.key, cached=cached, source=raw.source,
              states={v.member_id: v.state for v in ev.verdicts}, timings_ms=result.timings_ms)
    return result
