"""Build the contract ScanResult from a normalized product. Every path (barcode, photo, ask)
ends here, so verdict + headline + timings + debug look the same everywhere.
"""

from __future__ import annotations

from core.timing import annotate, current, debug_payload, debug_put, request_id, stage, timings
from models.contracts import Household, ScanResult
from models.domain import Evaluation, NormalizedProduct
from rules.engine import evaluate


def run_rules(np: NormalizedProduct, household: Household, member_ids: list[str] | None = None) -> Evaluation:
    with stage("rules"):
        ev = evaluate(np, household, member_ids, debug=current().debug)
    annotate("product_key", np.product.key)
    annotate("states", {v.member_id: v.state for v in ev.verdicts})
    return ev


def build_scan_result(
    np: NormalizedProduct,
    household: Household,
    member_ids: list[str] | None = None,
    evaluation: Evaluation | None = None,
) -> ScanResult:
    ev = evaluation or run_rules(np, household, member_ids)
    dbg = current().debug
    verdicts = ev.verdicts if dbg else [v.model_copy(update={"score": None}) for v in ev.verdicts]
    if dbg:
        debug_put("evaluation", ev.debug)
        debug_put("hits", [h.model_dump() for h in np.hits])
        debug_put("unmatched", np.unmatched)
        debug_put("legibility", np.legibility)
        debug_put("kb_version", np.kb_version)
    return ScanResult(
        product=np.product,
        verdicts=verdicts,
        headline=ev.headline,
        timings_ms=timings(),
        request_id=request_id(),
        debug=debug_payload(),
    )
