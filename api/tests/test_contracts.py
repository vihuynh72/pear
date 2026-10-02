"""Every file in contracts/ matches api/models/contracts.py, so the iOS app (built against
/mock) decodes the real API the same way.

- fixtures/<endpoint>.<scenario>.json validate against the model for <endpoint>
- explain.sse.txt parses into ExplainEvent lines + a final `done` event
- index.json only points at files that exist; every error fixture has the right status
- demo_products/*.json are valid RawProducts; scan_result.example.json is a valid ScanResult
- copy rules: no "safe" in user-facing text, reasons in each member's language, scores hidden
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from core.errors import STATUS
from models.contracts import (
    AskResponse,
    ErrorResponse,
    ExplainEvent,
    HealthResponse,
    Household,
    KbSearchResponse,
    PicksResponse,
    PricesResponse,
    ProfileParseResponse,
    ScanResult,
)
from models.domain import RawProduct

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "contracts"
FIXTURES = CONTRACTS / "fixtures"
DEMO = CONTRACTS / "demo_products"

MODELS: dict[str, type[BaseModel]] = {
    "scan_barcode": ScanResult,
    "scan_photo": ScanResult,
    "ask": AskResponse,
    "picks": PicksResponse,
    "prices": PricesResponse,
    "profile_parse": ProfileParseResponse,
    "kb_search": KbSearchResponse,
    "health": HealthResponse,
    "error": ErrorResponse,
}
REQUIRED = [
    "scan_barcode.sesame_crackers", "scan_barcode.ripe", "scan_barcode.noodles_vi",
    "scan_photo.lotion", "scan_photo.front_only", "scan_photo.partial_label",
    "ask.check_vi", "ask.alternatives", "ask.scan_needed",
    "picks.default", "prices.default", "profile_parse.default", "kb_search.default", "health.default",
    "error.not_found", "error.too_blurry", "error.unreadable", "error.rate_limited", "error.internal",
]
LABELS = {
    "RIPE": {"Ripe"}, "EASY": {"Easy does it"}, "SKIP": {"Skip it"},
    "ALLERGY": {"Allergy", "May contain", "Toxic for dogs", "Toxic for cats"},
    "UNSURE": {"Unsure"},
}
NEXT_FOR = {"not_found": "take_photo", "too_blurry": "retake", "unreadable": "retake",
            "rate_limited": "retry", "internal": "retry", "unauthorized": "sign_in"}
SAFE_WORDS = re.compile(r"\bsafe\b|\ban toàn\b|\bsegur[oa]s?\b", re.IGNORECASE)

HOUSEHOLD = Household.model_validate(json.loads((CONTRACTS / "household.example.json").read_text(encoding="utf-8")))
LANG = {m.id: m.language for m in HOUSEHOLD.members}
ORDER = [m.id for m in HOUSEHOLD.members]

JSON_FIXTURES = sorted(p for p in FIXTURES.glob("*.json") if p.name != "index.json")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _scan_results(name: str, body: dict) -> list[ScanResult]:
    prefix = name.split(".")[0]
    if prefix in ("scan_barcode", "scan_photo"):
        return [ScanResult.model_validate(body)]
    if prefix == "ask" and body.get("scan_result"):
        return [ScanResult.model_validate(body["scan_result"])]
    return []


# ----------------------------------------------------------------------------- shapes


def test_required_fixtures_exist():
    have = {p.stem for p in JSON_FIXTURES}
    missing = [n for n in REQUIRED if n not in have]
    assert not missing, f"missing fixtures: {missing}"
    assert (FIXTURES / "explain.sse.txt").exists()


@pytest.mark.parametrize("path", JSON_FIXTURES, ids=lambda p: p.stem)
def test_fixture_validates(path: Path):
    prefix = path.stem.split(".")[0]
    assert prefix in MODELS, f"{path.name}: unknown prefix {prefix!r}; name fixtures <endpoint>.<scenario>.json"
    model = MODELS[prefix]
    body = _load(path)
    parsed = model.model_validate(body)
    # Round trip: the fixture already has every output field (Swift Codable sees all keys).
    assert set(parsed.model_dump(mode="json")) >= set(body)


def test_explain_sse_fixture():
    text = (FIXTURES / "explain.sse.txt").read_text(encoding="utf-8")
    blocks = [b for b in text.split("\n\n") if b.strip()]
    events, names = [], []
    for block in blocks:
        lines = block.splitlines()
        assert all(ln.startswith(("data:", "event:", "id:", ":")) for ln in lines), block
        name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), "message")
        names.append(name)
        data = "\n".join(ln[5:].strip() for ln in lines if ln.startswith("data:"))
        if name == "message":
            events.append(ExplainEvent.model_validate(json.loads(data)))
    assert 3 <= len(events) <= 6
    assert names[-1] == "done" and names.count("done") == 1
    for e in events:
        assert e.language == LANG[e.member_id]
        assert not SAFE_WORDS.search(e.text)
        assert len(e.text.split()) <= 15


def test_scan_result_example_validates():
    sr = ScanResult.model_validate(_load(CONTRACTS / "scan_result.example.json"))
    assert sr.product.key.startswith("off:")


def test_household_example_validates():
    assert ORDER == ["m_linh", "m_ba", "m_mia", "m_leo"]
    assert LANG == {"m_linh": "en", "m_ba": "vi", "m_mia": "en", "m_leo": "es"}


@pytest.mark.parametrize("path", sorted(DEMO.glob("*.json")), ids=lambda p: p.stem)
def test_demo_product_is_raw_product(path: Path):
    raw = RawProduct.model_validate(_load(path))
    assert re.fullmatch(r"(off|obf|fdc):\d{8,14}|photo:[0-9a-f]{12}", raw.key), raw.key
    if raw.code:
        assert raw.key.endswith(raw.code)
        digits = [int(c) for c in raw.code]
        check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:-1])) % 10) % 10
        assert digits[-1] == check, f"{path.name}: bad EAN-13 check digit"
    if raw.model_kb_ids:
        assert len(raw.model_kb_ids) == len(raw.ingredients)


def test_demo_set_is_complete():
    names = {p.stem for p in DEMO.glob("*.json")}
    assert {"sesame_crackers", "mi_tom_noodles", "gummy_candy", "kids_lotion", "kids_body_wash",
            "rice_cakes", "honey_vegan_bar", "front_only_snack"} <= names
    lotion = RawProduct.model_validate(_load(DEMO / "kids_lotion.json"))
    wash = RawProduct.model_validate(_load(DEMO / "kids_body_wash.json"))
    assert (lotion.category, lotion.product_type) == ("cosmetic", "leave_on")
    assert (wash.category, wash.product_type) == ("cosmetic", "rinse_off")
    noodles = RawProduct.model_validate(_load(DEMO / "mi_tom_noodles.json"))
    assert noodles.label_language == "vi" and noodles.nutrition.sodium_dv == 38
    bar = RawProduct.model_validate(_load(DEMO / "honey_vegan_bar.json"))
    assert "Certified Vegan" in bar.certifications_text and "honey" in bar.ingredients
    front = RawProduct.model_validate(_load(DEMO / "front_only_snack.json"))
    assert front.photo_kind == "front_only" and front.matched_by == "name"


# ----------------------------------------------------------------------------- index


def test_index_references_existing_files():
    idx = _load(FIXTURES / "index.json")
    referenced: list[str] = []
    referenced += list(idx["defaults"].values())
    referenced += list(idx["scan_barcode_by_code"].values())
    referenced += [r["fixture"] for r in idx["ask_keywords"]]
    referenced += list(idx["errors"].values())
    for names in idx.get("scenarios", {}).values():
        referenced += names
    missing = sorted({n for n in referenced if not (FIXTURES / n).exists()})
    assert not missing, f"index.json points at missing files: {missing}"
    for rule in idx["ask_keywords"]:
        for pat in rule["patterns"]:
            re.compile(pat)


def test_index_barcodes_match_fixture_codes():
    idx = _load(FIXTURES / "index.json")
    for code, name in idx["scan_barcode_by_code"].items():
        sr = ScanResult.model_validate(_load(FIXTURES / name))
        assert sr.product.code == code, f"{name}: product.code {sr.product.code} != index code {code}"


def test_error_fixtures_codes_and_next():
    idx = _load(FIXTURES / "index.json")
    for code, name in idx["errors"].items():
        err = ErrorResponse.model_validate(_load(FIXTURES / name)).error
        assert err.code == code and code in STATUS
        if code in NEXT_FOR:
            assert err.next == NEXT_FOR[code], f"{name}: next should be {NEXT_FOR[code]}"


# ----------------------------------------------------------------------------- copy rules


@pytest.mark.parametrize("path", JSON_FIXTURES, ids=lambda p: p.stem)
def test_no_safe_word_anywhere(path: Path):
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r'"(text|message|label|note)":\s*"([^"]*)"', text):
        assert not SAFE_WORDS.search(m.group(2)), f"{path.name}: {m.group(2)!r}"


@pytest.mark.parametrize("path", [p for p in JSON_FIXTURES if p.stem.split(".")[0] in ("scan_barcode", "scan_photo", "ask")],
                         ids=lambda p: p.stem)
def test_scan_results_follow_engine_rules(path: Path):
    for sr in _scan_results(path.stem, _load(path)):
        ids = [v.member_id for v in sr.verdicts]
        assert ids == [m for m in ORDER if m in ids], f"{path.name}: verdicts not in family order"
        assert sr.headline.language == HOUSEHOLD.language
        assert len([s for s in re.split(r"(?<=[.?!])\s+", sr.headline.text) if s]) <= 2
        if sr.product.photo_kind == "front_only" or sr.product.data_confidence != "high":
            assert sr.product.less_sure
        for v in sr.verdicts:
            assert v.score is None, f"{path.name}: score must be hidden in fixtures"
            if v.state in LABELS:
                assert v.label in LABELS[v.state], f"{path.name}: {v.state} with label {v.label!r}"
            for r in v.reasons:
                assert r.language == LANG[v.member_id], f"{path.name}: {v.member_id} reason in {r.language}"
            if v.state != "RIPE":
                assert any(r.severity != "note" for r in v.reasons), f"{path.name}: {v.member_id} has no reason"
        states = {v.state for v in sr.verdicts}
        if states == {"RIPE"}:
            assert sr.headline.text.startswith("Ripe for everyone")


def test_ask_fixtures_are_consistent():
    for path in FIXTURES.glob("ask.*.json"):
        ar = AskResponse.model_validate(_load(path))
        assert (ar.next == "result") == (ar.scan_result is not None or ar.picks is not None), path.name
        assert ar.answer.language == ar.language
        assert all(m in ORDER for m in ar.member_ids)
        if ar.intent == "alternatives":
            assert ar.picks is not None and ar.picks.items


def test_picks_fit_everyone():
    picks = PicksResponse.model_validate(_load(FIXTURES / "picks.default.json"))
    assert len(picks.items) == 3
    for it in picks.items:
        assert [v.member_id for v in it.verdicts] == ORDER
        assert {v.state for v in it.verdicts} <= {"RIPE", "EASY"}  # picks must fit everyone
    prices = PricesResponse.model_validate(_load(FIXTURES / "prices.default.json"))
    assert set(prices.prices) == {it.product.key for it in picks.items}


# ----------------------------------------------------------------------------- gen_fixtures


def _fake_normalize(raw, *, extra_hits=None):
    from models.contracts import Product
    from models.domain import NormalizedProduct

    p = Product(key=raw.key, name=raw.name, brand=raw.brand, category=raw.category, product_type=raw.product_type,
                image_url=raw.image_url, source=raw.source, photo_kind=raw.photo_kind,
                data_confidence=raw.data_confidence, less_sure=raw.legibility != "full",
                ingredients=raw.ingredients, label_language=raw.label_language, code=raw.code,
                quantity=raw.quantity, nutrition=raw.nutrition, matched_by=raw.matched_by)
    return NormalizedProduct(product=p, legibility=raw.legibility, kb_version="fake")


def _fake_evaluate(np, household, member_ids=None, *, debug=False):
    from models.contracts import Headline, Verdict
    from models.domain import Evaluation

    vs = [Verdict(member_id=m.id, state="RIPE", label="Ripe", score=100.0) for m in household.select(member_ids)]
    return Evaluation(verdicts=vs, headline=Headline(text="Ripe for everyone. Go for it.", language="en"))


def test_gen_fixtures_with_fake_engine(monkeypatch, capsys):
    """gen_fixtures drives normalize -> run_rules -> build_scan_result and validates every output.

    The brain lane's engine may still be a stub, so a trivial fake engine stands in here; the
    real engine is exercised by running the script (see contracts/README.md)."""
    import pipeline.result as result
    import rules.engine as engine
    import rules.normalize as normalize
    from scripts import gen_fixtures

    monkeypatch.setattr(normalize, "normalize", _fake_normalize)
    monkeypatch.setattr(engine, "evaluate", _fake_evaluate)
    monkeypatch.setattr(result, "evaluate", _fake_evaluate)
    out = gen_fixtures.generate(gen_fixtures.demo_household())
    assert {"scan_barcode.sesame_crackers", "scan_photo.front_only", "ask.check_vi", "picks.default"} <= set(out)
    for name, body in out.items():
        prefix = name.split(".")[0]
        model = ScanResult if name.endswith("scan_result.example") else MODELS[prefix]
        model.model_validate(body)
        for sr in _scan_results(name if prefix in MODELS else "scan_barcode.x", body):
            assert all(v.score is None for v in sr.verdicts)  # hidden outside debug
    assert out["scan_photo.front_only"]["product"]["matched_by"] == "name"
    assert [v["member_id"] for v in out["ask.check_vi"]["scan_result"]["verdicts"]] == ["m_ba"]

    before = {p.name: p.stat().st_mtime_ns for p in FIXTURES.iterdir()}
    assert gen_fixtures.main(["--check"]) in (0, 1)  # check mode never writes
    assert {p.name: p.stat().st_mtime_ns for p in FIXTURES.iterdir()} == before


def test_gen_fixtures_keeps_handwritten_when_engine_is_a_stub(monkeypatch, capsys):
    import rules.normalize as normalize
    from scripts import gen_fixtures

    def stub(raw, *, extra_hits=None):
        raise NotImplementedError("rules.normalize.normalize")

    monkeypatch.setattr(normalize, "normalize", stub)
    before = {p.name: p.stat().st_mtime_ns for p in FIXTURES.iterdir()}
    assert gen_fixtures.main([]) == 0
    assert "keeping the hand-written fixtures" in capsys.readouterr().out
    assert {p.name: p.stat().st_mtime_ns for p in FIXTURES.iterdir()} == before
