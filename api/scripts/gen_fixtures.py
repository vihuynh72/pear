"""Regenerate the scan / ask / picks fixtures from contracts/demo_products through the REAL
rules engine, so what the iOS app sees under /mock always matches what /v1 will say.

    cd api
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/gen_fixtures.py            # rewrite fixtures
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/gen_fixtures.py --check    # exit 1 on drift
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/gen_fixtures.py --only scan_photo.lotion

Pipeline per scan fixture: RawProduct (demo_products/<name>.json) -> rules.normalize.normalize
-> (scenario overrides the pipeline would apply, e.g. front-only "matched by name")
-> pipeline.result.run_rules + build_scan_result with contracts/household.example.json.
Ask answers come from rules.templates (ask_answer / picks_answer / scan_needed_answer).
Kept from the existing file: timings_ms (realistic numbers for the UI), request_id/debug = null.
Untouched: errors, health, kb_search, profile_parse, prices, explain.sse.txt (no engine involved).

If the rules engine is still a stub (NotImplementedError) nothing is written and the
hand-written fixtures stay; templates that are still stubs keep the existing answer text.
Debug a surprising verdict with POST /debug/evaluate {"demo": "<demo product name>"}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("PEAR_STORE", "memory")
os.environ.setdefault("PEAR_TRACE", "0")

from core import timing  # noqa: E402
from models.contracts import (  # noqa: E402
    AskResponse,
    Household,
    PicksResponse,
    PickVerdict,
    ScanResult,
)
from models.domain import RawProduct  # noqa: E402

REPO = API_DIR.parent
CONTRACTS = REPO / "contracts"
FIXTURES = CONTRACTS / "fixtures"
DEMO = CONTRACTS / "demo_products"

T_BARCODE = {"cache": 41, "lookup": 486, "normalize": 3, "rules": 2, "total": 548}
T_PHOTO = {"label_reader": 1720, "normalize": 3, "rules": 2, "total": 1790}
FRONT_ONLY = {"less_sure": True, "ask_for_ingredients": True, "data_confidence": "medium",
              "matched_by": "name", "photo_kind": "front_only"}


@dataclass
class ScanCase:
    name: str
    demo: str
    member_ids: list[str] | None = None
    overrides: dict[str, Any] = field(default_factory=dict)  # applied to np.product before rules
    timings: dict[str, int] = field(default_factory=lambda: dict(T_BARCODE))
    barcode: bool = False  # add product.code -> this fixture to index.json scan_barcode_by_code


SCANS = [
    ScanCase("scan_barcode.sesame_crackers", "sesame_crackers", barcode=True),
    ScanCase("scan_barcode.ripe", "rice_cakes", barcode=True),
    ScanCase("scan_barcode.noodles_vi", "mi_tom_noodles", barcode=True),
    ScanCase("scan_barcode.gummy_candy", "gummy_candy", barcode=True),
    ScanCase("scan_barcode.body_wash", "kids_body_wash", barcode=True),
    ScanCase("scan_photo.lotion", "kids_lotion", timings=dict(T_PHOTO)),
    ScanCase("scan_photo.front_only", "front_only_snack", overrides=FRONT_ONLY, timings=dict(T_PHOTO)),
    ScanCase("scan_photo.partial_label", "partial_label_chips",
             overrides={"less_sure": True, "ask_for_ingredients": True}, timings=dict(T_PHOTO)),
    ScanCase("scan_photo.honey_vegan_bar", "honey_vegan_bar", timings=dict(T_PHOTO)),
]
PICK_DEMOS = ["rice_cakes", "pick_rice_crackers", "pick_corn_chips"]
FIT_STATES = {"RIPE", "EASY"}


# ----------------------------------------------------------------------------- io


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def existing(name: str) -> dict[str, Any] | None:
    path = FIXTURES / f"{name}.json"
    return load_json(path) if path.exists() else None


def demo_raw(name: str) -> RawProduct:
    return RawProduct.model_validate(load_json(DEMO / f"{name}.json"))


def demo_household() -> Household:
    return Household.model_validate(load_json(CONTRACTS / "household.example.json"))


def dump(body: dict[str, Any]) -> str:
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------- engine


def normalized(case_demo: str, overrides: dict[str, Any] | None = None):
    from rules.normalize import normalize

    np = normalize(demo_raw(case_demo))
    if overrides:
        np.product = np.product.model_copy(update=overrides)
    return np


def scan_body(np, hh: Household, member_ids: list[str] | None, timings_ms: dict[str, int]):
    """(ScanResult dict, Evaluation) exactly as the API builds it, with stable timings."""
    from pipeline.result import build_scan_result, run_rules

    token = timing.begin("gen_fixtures", debug=False)
    try:
        ev = run_rules(np, hh, member_ids)
        sr = build_scan_result(np, hh, member_ids, evaluation=ev)
    finally:
        timing.end(token)
    body = sr.model_dump(mode="json")
    body.update(timings_ms=timings_ms, request_id=None, debug=None)
    return ScanResult.model_validate(body).model_dump(mode="json"), ev


def gen_scan(case: ScanCase, hh: Household) -> dict[str, Any]:
    old = existing(case.name) or {}
    body, _ = scan_body(normalized(case.demo, case.overrides), hh, case.member_ids,
                        old.get("timings_ms") or case.timings)
    return body


def gen_picks(hh: Household) -> dict[str, Any]:
    from rules.engine import evaluate

    old = existing("picks.default") or {}
    items = []
    for name in PICK_DEMOS:
        np = normalized(name)
        ev = evaluate(np, hh)
        p = np.product
        verdicts = [PickVerdict(member_id=v.member_id, state=v.state, label=v.label).model_dump() for v in ev.verdicts]
        bad = [f"{v['member_id']}={v['state']}" for v in verdicts if v["state"] not in FIT_STATES]
        if bad:
            print(f"  WARNING pick {name} does not fit everyone ({', '.join(bad)}); change the demo product")
        items.append({"product": {"key": p.key, "name": p.name, "brand": p.brand, "image_url": p.image_url,
                                  "category": p.category}, "verdicts": verdicts})
    body = {"items": items, "prefetched": True, "timings_ms": old.get("timings_ms") or {"wait": 40, "total": 60},
            "request_id": None}
    return PicksResponse.model_validate(body).model_dump(mode="json")


def _template(fn_name: str, fallback: str, *args) -> str:
    """rules.templates.<fn>(*args), or the existing text while templates are still stubs."""
    try:
        from rules import templates

        return getattr(templates, fn_name)(*args)
    except NotImplementedError:
        return fallback


def gen_asks(hh: Household, picks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    old = existing("ask.check_vi") or {}
    np = normalized("mi_tom_noodles")
    sr, ev = scan_body(np, hh, ["m_ba"], (old.get("scan_result") or {}).get("timings_ms")
                       or {"resolve": 46, "normalize": 2, "rules": 1, "total": 49})
    answer = _template("ask_answer", (old.get("answer") or {}).get("text", ""), ev, hh, ["m_ba"], "vi", np.product.name)
    out["ask.check_vi"] = {
        "transcript": "Bà ăn mì này được không?", "language": "vi", "intent": "check", "product_query": "mì",
        "member_ids": ["m_ba"], "next": "result", "answer": {"text": answer, "language": "vi"},
        "scan_result": sr, "picks": None,
        "timings_ms": old.get("timings_ms") or {"question_reader": 712, "resolve": 46, "rules": 1, "total": 781},
        "request_id": None, "debug": None}

    old = existing("ask.alternatives") or {}
    names = [it["product"]["name"] for it in picks["items"] if it["product"]["name"]]
    answer = _template("picks_answer", (old.get("answer") or {}).get("text", ""), names, "es")
    out["ask.alternatives"] = {
        "transcript": "¿Hay otra opción para toda la familia?", "language": "es", "intent": "alternatives",
        "product_query": None, "member_ids": [], "next": "result", "answer": {"text": answer, "language": "es"},
        "scan_result": None, "picks": picks,
        "timings_ms": old.get("timings_ms") or {"question_reader": 684, "picks_wait": 112, "total": 815},
        "request_id": None, "debug": None}

    old = existing("ask.scan_needed") or {}
    answer = _template("scan_needed_answer", (old.get("answer") or {}).get("text", ""), "en")
    out["ask.scan_needed"] = {
        "transcript": "Can Mia have this?", "language": "en", "intent": "check", "product_query": None,
        "member_ids": ["m_mia"], "next": "scan_needed", "answer": {"text": answer, "language": "en"},
        "scan_result": None, "picks": None,
        "timings_ms": old.get("timings_ms") or {"question_reader": 655, "total": 668},
        "request_id": None, "debug": None}
    return {k: AskResponse.model_validate(v).model_dump(mode="json") for k, v in out.items()}


def generate(hh: Household) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case in SCANS:
        out[case.name] = gen_scan(case, hh)
    out["picks.default"] = gen_picks(hh)
    out.update(gen_asks(hh, out["picks.default"]))
    example = json.loads(json.dumps(out["scan_barcode.sesame_crackers"]))
    example["product"].update(key="off:0123456789012", code="0123456789012", brand="Example Brand")
    example["timings_ms"] = {"lookup": 420, "rules": 6, "total": 610}
    out["../scan_result.example"] = ScanResult.model_validate(example).model_dump(mode="json")
    return out


# ----------------------------------------------------------------------------- index + report


def updated_index(generated: dict[str, dict[str, Any]]) -> dict[str, Any]:
    idx = load_json(FIXTURES / "index.json")
    by_code = idx.setdefault("scan_barcode_by_code", {})
    scenarios = idx.setdefault("scenarios", {})
    for case in SCANS:
        if case.name not in generated:
            continue
        file_name = f"{case.name}.json"
        code = generated[case.name]["product"].get("code")
        if case.barcode and code:
            by_code[code] = file_name
        group = scenarios.setdefault(case.name.split(".")[0], [])
        if file_name not in group:
            group.append(file_name)
    return idx


def summary(body: dict[str, Any]) -> str:
    sr = body if "verdicts" in body else body.get("scan_result")
    if sr:
        states = " ".join(f"{v['member_id']}={v['state']}" for v in sr["verdicts"])
        return f"{states} | {sr['headline']['text']}"
    if "items" in body:
        return " ; ".join(f"{it['product']['name']}: " + ",".join(v["state"] for v in it["verdicts"])
                          for it in body["items"])
    return (body.get("answer") or {}).get("text", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="don't write; exit 1 if any fixture would change")
    ap.add_argument("--only", nargs="*", help="fixture names to write (default: all)")
    args = ap.parse_args(argv)

    try:
        generated = generate(demo_household())
    except NotImplementedError as e:
        print(f"rules engine not ready ({e}); keeping the hand-written fixtures. Nothing written.")
        return 0
    if args.only:
        generated = {k: v for k, v in generated.items() if k in args.only}

    changed = []
    for name, body in generated.items():
        path = (FIXTURES / f"{name}.json").resolve()
        old = load_json(path) if path.exists() else None
        mark = "same   " if old == body else ("new    " if old is None else "CHANGED")
        if old != body:
            changed.append(name)
            if old is not None and summary(old) != summary(body):
                print(f"  was  {name}: {summary(old)}")
        print(f"{mark} {name}: {summary(body)}")
        if not args.check and old != body:
            path.write_text(dump(body), encoding="utf-8")

    idx = updated_index(generated)
    if idx != load_json(FIXTURES / "index.json"):
        changed.append("index.json")
        print("CHANGED index.json (new barcode / scenario entries)")
        if not args.check:
            (FIXTURES / "index.json").write_text(dump(idx), encoding="utf-8")

    if args.check:
        print(f"{len(changed)} fixture(s) differ from the engine" if changed else "fixtures match the engine")
        return 1 if changed else 0
    print(f"wrote {len(changed)} file(s)" if changed else "nothing to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
