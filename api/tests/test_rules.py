"""Spec-driven test suite for the rules engine (plan 8.3-8.7, addendum 10, kb/README.md).

These cases come from the SPEC, not from the engine code, so they work as an independent
check on the brain lane.

What this runs
    Every case in tests/cases/*.json (format: tests/cases/README.md) goes through the real
    brain: RawProduct -> rules.normalize.normalize -> rules.engine.evaluate(np, hh, ids, debug=True).
    The household is the demo family (contracts/household.example.json) filtered by the case's
    "members" (in the listed order; omit = whole family), then "extra_members" (full Member
    objects), with "owner_language" (default "en").

    Per-case expectations (test_case), all optional:
        expect.<member_id>: state | label | codes_include | codes_exclude | matched_include |
                            score_min | score_max                     (cases/README.md)
        Extra keys this runner also reads (they add to the README format):
            state_in          a list of allowed states, for when the spec allows more than one
            notes_include     codes that must be present with severity "note"
            reasons_language  every reason of this member must be in this language
        Case level:
            headline_contains / headline_not_contains   case-insensitive, NFC substrings
            headline_first_name   the first member name in the headline (the most serious clause leads)
            headline_max_sentences
            member_ids            passed to evaluate() (the "Who's this for?" toggle)
            expect_member_ids     exact verdict member ids, in order
            product_overrides     fields set on np.product after normalize(), the way
                                  pipeline.products.match_by_name marks less_sure / ask_for_ingredients
            why                   free text, ignored

    Code matching
        codes_include / notes_include: exact or prefix ("signal." = any signal;
            "allergen.peanut" also accepts "allergen.peanut.may_contain"). Use the full code when
            the strength matters ("allergen.peanut.may_contain"). A "*.trace_note" code is a note,
            not an allergen hit, so it only matches when the pattern names it in full.
        codes_exclude: exact, or a prefix only when the pattern ends with "." or "*"
            ("allergen." = no allergen code at all; "allergen.milk" = only that exact code).
        matched_include: every word must be a substring (case-insensitive, NFC) of some
            reason.matched entry of that member's verdict.

    Checks that run on EVERY case (each is its own test, so a failure names the rule):
        test_no_banned_words           no "safe" / "an toàn" / "seguro" in the headline or any reason
        test_strict_never_ripe_unsure_label   strict allergy + partial / missing / name-matched list -> never RIPE
        test_verdict_order             verdicts in household order (after the member_ids filter)
        test_deterministic             same input twice -> identical normalize() and evaluate() output
        test_languages                 reasons in the member's first language, headline in the owner's
        test_state_consistency         chip label fits the state, a reason code explains the state,
                                       score tier fits the state, reasons sorted hard -> soft -> note

How to debug
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_rules.py -q
    ... -m allergen                         # the no-missed-allergen gate (plan 8.7)
    ... -k tahini                           # one case; ids are "<file>::<name>"
    ... -k "test_case and diets.json"       # one file, expectations only
    A failure prints every verdict on one line each:
        member STATE 'label' score=.. | code(severity)[matched], ...   + headline + legibility
    If rules.normalize / rules.engine still raise NotImplementedError, every engine test fails with
    "still a NotImplementedError stub". The checks that don't need the engine (case files are
    well formed, runner helpers work) still pass.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from models.contracts import Household, Reason, Verdict
from models.domain import Evaluation, NormalizedProduct, RawProduct

CASES_DIR = Path(__file__).resolve().parent / "cases"
HOUSEHOLD_PATH = Path(__file__).resolve().parents[2] / "contracts" / "household.example.json"

CASE_KEYS = {
    "name", "tags", "product", "members", "extra_members", "owner_language", "member_ids",
    "product_overrides", "expect", "expect_member_ids", "headline_contains",
    "headline_not_contains", "headline_first_name", "headline_max_sentences", "why",
}
EXPECT_KEYS = {
    "state", "state_in", "label", "codes_include", "codes_exclude", "notes_include",
    "matched_include", "score_min", "score_max", "reasons_language",
}
STATES = {"RIPE", "EASY", "SKIP", "ALLERGY", "DIET", "UNSURE"}
STATE_LABELS = {"RIPE": {"Ripe"}, "EASY": {"Easy does it"}, "SKIP": {"Skip it"}, "UNSURE": {"Unsure"},
                "ALLERGY": {"Allergy", "May contain", "Toxic for dogs", "Toxic for cats", "Toxic for pets"}}
SEVERITY_RANK = {"hard": 0, "soft": 1, "note": 2}
EXPECTED_FILES = {
    "allergens_en.json", "allergens_vi_es.json", "allergens_cosmetics_inci.json",
    "allergens_statements_tags.json", "neutral_traps.json", "confidence.json", "diets.json",
    "certifications.json", "scoring.json", "headline.json", "pets_meds_supplements.json",
}
MIN_CASES = 80
# Plan 8.6 / kb README 4.6: never the word "safe" (or its vi / es translations), in any form.
BANNED = [
    ("safe", re.compile(r"safe", re.IGNORECASE)),
    ("an toàn", re.compile(r"(?<!\w)an toàn", re.IGNORECASE)),
    ("seguro", re.compile(r"segur[oa]s?\b", re.IGNORECASE)),
]
UNCERTAIN_LEGIBILITY = {"partial", "missing"}


# ----------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class Case:
    file: str
    index: int
    data: dict[str, Any]
    error: str | None = None  # set when the file could not be parsed

    @property
    def id(self) -> str:
        return f"{self.file}::{self.data.get('name', f'#{self.index}')}"

    @property
    def tags(self) -> list[str]:
        return list(self.data.get("tags") or [])


def load_cases() -> list[Case]:
    cases: list[Case] = []
    for path in sorted(CASES_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            items = doc["cases"]
        except (ValueError, KeyError, TypeError) as exc:
            cases.append(Case(path.name, 0, {"name": "<unreadable file>"}, error=f"{type(exc).__name__}: {exc}"))
            continue
        cases.extend(Case(path.name, i, c) for i, c in enumerate(items))
    return cases


CASES = load_cases()


def params(cases: list[Case]) -> list[Any]:
    return [pytest.param(c, id=c.id, marks=[pytest.mark.allergen] if "allergen" in c.tags else [])
            for c in cases]


def demo_household() -> dict[str, Any]:
    return json.loads(HOUSEHOLD_PATH.read_text(encoding="utf-8"))


def build_household(data: dict[str, Any]) -> Household:
    demo = demo_household()
    by_id = {m["id"]: m for m in demo["members"]}
    wanted = data.get("members")
    members = list(demo["members"]) if wanted is None else [by_id[i] for i in wanted]
    members = copy.deepcopy(members + list(data.get("extra_members") or []))
    return Household.model_validate({
        "household_id": "hh_test",
        "owner_language": data.get("owner_language", demo.get("owner_language", "en")),
        "member_limit": max(5, len(members)),
        "members": members,
    })


def build_raw(data: dict[str, Any]) -> RawProduct:
    return RawProduct.model_validate(data["product"])


# ----------------------------------------------------------------------------- running


@dataclass
class Outcome:
    raw: RawProduct
    np: NormalizedProduct
    ev: Evaluation
    household: Household
    member_ids: list[str] | None


def run_case(case: Case) -> Outcome:
    """normalize + evaluate one case from scratch (no caching)."""
    if case.error:
        pytest.fail(f"{case.file}: cannot parse the case file: {case.error}", pytrace=False)
    from rules.engine import evaluate
    from rules.normalize import normalize

    household = build_household(case.data)
    raw = build_raw(case.data)
    member_ids = case.data.get("member_ids")
    np = normalize(raw)
    overrides = case.data.get("product_overrides")
    if overrides:
        np = np.model_copy(update={"product": np.product.model_copy(update=overrides)})
    ev = evaluate(np, household, member_ids, debug=True)
    return Outcome(raw=raw, np=np, ev=ev, household=household, member_ids=member_ids)


_CACHE: dict[str, Outcome | Exception] = {}


def outcome(case: Case) -> Outcome:
    """Cached run_case; stub errors become one clear failure message."""
    if case.id not in _CACHE:
        try:
            _CACHE[case.id] = run_case(case)
        except Exception as exc:  # noqa: BLE001 - re-raised below, per test
            _CACHE[case.id] = exc
    result = _CACHE[case.id]
    if isinstance(result, NotImplementedError):
        pytest.fail(f"{case.id}: the brain is still a NotImplementedError stub ({result}).", pytrace=False)
    if isinstance(result, Exception):
        raise result
    return result


# ----------------------------------------------------------------------------- matching helpers


def fold(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").casefold()


def codes_of(v: Verdict) -> list[str]:
    return [r.code for r in v.reasons]


def code_included(codes: list[str], pattern: str) -> bool:
    """Exact or prefix match; a ".trace_note" (a note, not an allergen hit) only matches when named."""
    prefix = pattern.rstrip("*")
    return any(c == pattern or (c.startswith(prefix) and not c.endswith(".trace_note")) for c in codes)


def codes_excluded_hits(codes: list[str], pattern: str) -> list[str]:
    if pattern.endswith((".", "*")):
        prefix = pattern.rstrip("*")
        return [c for c in codes if c.startswith(prefix)]
    return [c for c in codes if c == pattern]


def matched_words(v: Verdict) -> list[str]:
    return [m for r in v.reasons for m in r.matched]


def describe_verdict(v: Verdict) -> str:
    reasons = ", ".join(f"{r.code}({r.severity},{r.language}){r.matched}" for r in v.reasons) or "-"
    return f"{v.member_id} {v.state} '{v.label}' score={v.score} | {reasons}"


def describe(out: Outcome) -> str:
    lines = [describe_verdict(v) for v in out.ev.verdicts]
    lines.append(f"headline[{out.ev.headline.language}]: {out.ev.headline.text!r}")
    lines.append(f"legibility raw={out.raw.legibility} np={out.np.legibility} "
                 f"kb_ids={sorted(out.np.kb_ids())} unmatched={out.np.unmatched}")
    return "\n    ".join(lines)


def check_member(v: Verdict, exp: dict[str, Any]) -> list[str]:
    """All the ways verdict `v` misses expectation `exp` (empty list = pass)."""
    fails: list[str] = []
    codes = codes_of(v)
    if "state" in exp and v.state != exp["state"]:
        fails.append(f"state {v.state} != expected {exp['state']}")
    if "state_in" in exp and v.state not in exp["state_in"]:
        fails.append(f"state {v.state} not in {exp['state_in']}")
    if "label" in exp and v.label != exp["label"]:
        fails.append(f"label {v.label!r} != expected {exp['label']!r}")
    for pat in exp.get("codes_include", []):
        if not code_included(codes, pat):
            fails.append(f"missing code {pat!r} (have {codes})")
    for pat in exp.get("codes_exclude", []):
        hits = codes_excluded_hits(codes, pat)
        if hits:
            fails.append(f"unexpected code(s) {hits} (excluded by {pat!r})")
    notes = [r.code for r in v.reasons if r.severity == "note"]
    for pat in exp.get("notes_include", []):
        if not code_included(notes, pat):
            fails.append(f"missing note {pat!r} (notes {notes})")
    words = [fold(m) for m in matched_words(v)]
    for word in exp.get("matched_include", []):
        if not any(fold(word) in w for w in words):
            fails.append(f"no reason.matched contains {word!r} (matched {matched_words(v)})")
    if "score_min" in exp or "score_max" in exp:
        if v.score is None:
            fails.append("score is None (evaluate(debug=True) must fill it for graded members)")
        else:
            if "score_min" in exp and v.score < exp["score_min"]:
                fails.append(f"score {v.score} < score_min {exp['score_min']}")
            if "score_max" in exp and v.score > exp["score_max"]:
                fails.append(f"score {v.score} > score_max {exp['score_max']}")
    if "reasons_language" in exp:
        if not v.reasons:
            fails.append(f"no reasons to check for language {exp['reasons_language']}")
        bad = [f"{r.code}:{r.language}" for r in v.reasons if r.language != exp["reasons_language"]]
        if bad:
            fails.append(f"reasons not in {exp['reasons_language']}: {bad}")
    return fails


def first_name_in(headline: str, names: list[str]) -> str | None:
    text = fold(headline)
    found = []
    for name in names:
        m = re.search(rf"(?<!\w){re.escape(fold(name))}(?!\w)", text)
        if m:
            found.append((m.start(), name))
    return min(found)[1] if found else None


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?。])\s+", text.strip()) if s.strip()])


def check_headline(data: dict[str, Any], ev: Evaluation, household: Household) -> list[str]:
    fails: list[str] = []
    text = ev.headline.text
    for sub in data.get("headline_contains", []):
        if fold(sub) not in fold(text):
            fails.append(f"headline lacks {sub!r}")
    for sub in data.get("headline_not_contains", []):
        if fold(sub) in fold(text):
            fails.append(f"headline must not contain {sub!r}")
    if "headline_first_name" in data:
        names = [m.name for m in household.members]
        first = first_name_in(text, names)
        if first != data["headline_first_name"]:
            fails.append(f"first name in headline is {first!r}, expected {data['headline_first_name']!r}")
    if "headline_max_sentences" in data and count_sentences(text) > data["headline_max_sentences"]:
        fails.append(f"headline has {count_sentences(text)} sentences > {data['headline_max_sentences']}")
    return fails


def banned_hits(text: str) -> list[str]:
    return [word for word, rx in BANNED if rx.search(unicodedata.normalize("NFC", text or ""))]


def label_is_uncertain(raw: RawProduct, np: NormalizedProduct) -> bool:
    """Partial / missing ingredient list, or a product matched by name (plan 8.5, 8.7.3)."""
    no_list = not raw.ingredients and not (raw.ingredients_text or "").strip()
    return (raw.legibility in UNCERTAIN_LEGIBILITY or np.legibility in UNCERTAIN_LEGIBILITY or no_list
            or raw.matched_by == "name" or np.product.matched_by == "name" or raw.photo_kind == "front_only")


def state_problems(v: Verdict) -> list[str]:
    """Spec-level consistency of one verdict (labels: kb README 3; tiers: plan 8.4)."""
    fails: list[str] = []
    codes = codes_of(v)
    allowed = STATE_LABELS.get(v.state)
    if allowed is not None and v.label not in allowed:
        fails.append(f"label {v.label!r} is not a {v.state} chip ({sorted(allowed)})")
    # "Not vegan / Maybe not vegan / Check"; the no_* diets read "Not pork-free" / "Maybe not pork-free"
    # (full list: kb/README.md section 3).
    if v.state == "DIET" and not (v.label.startswith(("Not ", "Maybe not ")) or v.label == "Check"):
        fails.append(f"label {v.label!r} is not a DIET chip (Not x / Maybe not x / Check)")
    if v.state != "RIPE" and not v.reasons:
        fails.append(f"{v.state} without any reason")
    drivers = {
        "ALLERGY": lambda c: (c.startswith("allergen.") and not c.endswith(".trace_note")) or c.startswith("pet."),
        "UNSURE": lambda c: c == "confidence.unsure",
        "DIET": lambda c: c.startswith("diet."),
        "SKIP": lambda c: c.startswith("signal."),
        "EASY": lambda c: c.startswith("signal."),
    }
    if v.state in drivers and not any(drivers[v.state](c) for c in codes):
        fails.append(f"no reason code drives {v.state} (codes {codes})")
    graded = v.state in ("EASY", "SKIP") or any(c.startswith("signal.") for c in codes)
    if graded and v.score is None:
        fails.append("graded member without a score (debug=True must fill Verdict.score)")
    if v.score is not None:
        if not 0 <= v.score <= 100:
            fails.append(f"score {v.score} outside 0..100")
        if v.state == "RIPE" and v.score < 85:
            fails.append(f"RIPE with score {v.score} < 85")
        if v.state == "EASY" and v.score >= 85:
            fails.append(f"EASY with score {v.score} >= 85")
        if v.state == "SKIP" and v.score >= 60:
            fails.append(f"SKIP with score {v.score} >= 60")
    ranks = [SEVERITY_RANK.get(r.severity, 9) for r in v.reasons]
    if ranks != sorted(ranks):
        fails.append(f"reasons not sorted hard -> soft -> note: {[r.severity for r in v.reasons]}")
    return fails


# ----------------------------------------------------------------------------- case-file checks (no engine)


def test_case_inventory() -> None:
    files = {c.file for c in CASES}
    assert not (EXPECTED_FILES - files), f"missing case files: {sorted(EXPECTED_FILES - files)}"
    assert len(CASES) >= MIN_CASES, f"only {len(CASES)} cases, need at least {MIN_CASES}"
    ids = [c.id for c in CASES]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate case ids: {dupes}"
    allergen_cases = [c for c in CASES if "allergen" in c.tags]
    assert len(allergen_cases) >= 40, f"allergen gate has only {len(allergen_cases)} cases"


@pytest.mark.parametrize("case", params(CASES))
def test_case_is_well_formed(case: Case) -> None:
    assert case.error is None, case.error
    data = case.data
    assert data.get("name"), "case needs a name"
    unknown = set(data) - CASE_KEYS
    assert not unknown, f"unknown case keys {sorted(unknown)} (typo?)"
    raw = build_raw(data)
    assert raw.key and raw.source
    household = build_household(data)
    selected = [m.id for m in household.select(data.get("member_ids"))]
    expect = data.get("expect") or {}
    assert expect or data.get("headline_contains") or data.get("expect_member_ids"), "case asserts nothing"
    for member_id, exp in expect.items():
        assert member_id in selected, f"expect.{member_id} is not in the evaluated members {selected}"
        assert not (set(exp) - EXPECT_KEYS), f"unknown expect keys {sorted(set(exp) - EXPECT_KEYS)}"
        assert "state" in exp or "state_in" in exp or exp.get("codes_include") or exp.get("notes_include"), \
            f"expect.{member_id} asserts nothing about the state or codes"
        for st in [exp.get("state")] + list(exp.get("state_in", [])):
            assert st is None or st in STATES, f"bad state {st!r}"
        if "score_min" in exp and "score_max" in exp:
            assert exp["score_min"] <= exp["score_max"]
        for key in ("codes_include", "codes_exclude", "notes_include", "matched_include"):
            assert isinstance(exp.get(key, []), list), f"{key} must be a list"
    if "expect_member_ids" in data:
        assert data["expect_member_ids"] == selected, "expect_member_ids disagrees with household.select()"


# ----------------------------------------------------------------------------- runner self-checks (no engine)


def _verdict(state: str, label: str, reasons: list[tuple[str, str, list[str]]], score: float | None = None,
             language: str = "en") -> Verdict:
    return Verdict(member_id="m_x", state=state, label=label, score=score,
                   reasons=[Reason(code=c, text=f"text for {c}", language=language, severity=s, matched=m)
                            for c, s, m in reasons])


def test_runner_helpers_detect_mismatches() -> None:
    v = _verdict("ALLERGY", "May contain", [("allergen.peanut.may_contain", "hard", ["Peanuts"]),
                                            ("confidence.check_label", "note", [])])
    ok = {"state": "ALLERGY", "label": "May contain", "codes_include": ["allergen.peanut.may_contain", "allergen.peanut"],
          "codes_exclude": ["allergen.peanut", "allergen.sesame."], "notes_include": ["confidence."],
          "matched_include": ["peanut"], "reasons_language": "en"}
    assert check_member(v, ok) == []
    bad = {"state": "RIPE", "label": "Allergy", "codes_include": ["allergen.sesame"],
           "codes_exclude": ["allergen."], "notes_include": ["allergen.peanut.may_contain"],
           "matched_include": ["tahini"], "score_min": 10, "reasons_language": "vi"}
    fails = check_member(v, bad)
    assert len(fails) == 8, fails
    graded = _verdict("EASY", "Easy does it", [("signal.sodium", "soft", ["salt"])], score=75)
    assert check_member(graded, {"state": "EASY", "score_min": 74.5, "score_max": 75.5}) == []
    assert len(check_member(graded, {"score_min": 76, "score_max": 80})) == 1
    assert check_member(_verdict("RIPE", "Ripe", [], language="vi"), {"state": "RIPE"}) == []


def test_runner_invariant_helpers() -> None:
    assert banned_hits("This is SAFE for Mia") == ["safe"]
    assert banned_hits("Unsafe") == ["safe"]
    assert banned_hits("Món này an toàn cho Mia") == ["an toàn"]
    assert banned_hits("Es seguro para Leo") == ["seguro"]
    assert banned_hits("Not for Mia: contains sesame. Fine for Linh and Leo.") == []
    assert banned_hits("Hoàn toàn bình thường") == []
    assert first_name_in("Not for Mia: contains sesame. Fine for Linh and Leo.", ["Linh", "Mia", "Leo"]) == "Mia"
    assert first_name_in("Easy does it for Bà: high sodium.", ["Linh", "Bà"]) == "Bà"
    assert count_sentences("Not for Mia: contains sesame. Fine for Linh and Leo.") == 2
    assert state_problems(_verdict("SKIP", "Skip it", [("signal.mit", "soft", ["MIT"])], score=55)) == []
    assert state_problems(_verdict("SKIP", "Skip it", [("signal.mit", "soft", ["MIT"])], score=77.5))
    assert state_problems(_verdict("RIPE", "Ripe", [("signal.fragrance", "note", ["Fragrance"])], score=90)) == []
    assert state_problems(_verdict("ALLERGY", "Allergy", [("allergen.milk.trace_note", "note", ["milk"])]))
    assert state_problems(_verdict("DIET", "Not vegan", [("cert.vegan", "note", []), ("diet.vegan", "hard", [])]))
    assert state_problems(_verdict("DIET", "Not vegan", [("diet.vegan", "hard", ["gelatin"])])) == []
    hh = build_household({"members": ["m_leo", "m_mia"], "extra_members": [{"id": "m_x", "name": "X"}]})
    assert [m.id for m in hh.members] == ["m_leo", "m_mia", "m_x"]
    assert build_household({}).language == "en" and len(build_household({}).members) == 4


def test_stub_error_surfaces_as_clear_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A NotImplementedError from the brain must fail loudly, never pass or skip."""
    import rules.engine
    import rules.normalize

    def stub(*_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("rules.engine.evaluate")

    monkeypatch.setattr(rules.normalize, "normalize", stub)
    monkeypatch.setattr(rules.engine, "evaluate", stub)
    case = Case("selftest.json", 0, {"name": "stub", "product": {"key": "t:1", "source": "photo"},
                                     "expect": {"m_mia": {"state": "RIPE"}}})
    _CACHE.pop(case.id, None)
    with pytest.raises(pytest.fail.Exception, match="NotImplementedError stub"):
        outcome(case)
    _CACHE.pop(case.id, None)


# ----------------------------------------------------------------------------- failure collectors (pure)


def expectation_failures(data: dict[str, Any], out: Outcome) -> list[str]:
    by_id = {v.member_id: v for v in out.ev.verdicts}
    fails: list[str] = []
    for member_id, exp in (data.get("expect") or {}).items():
        v = by_id.get(member_id)
        if v is None:
            fails.append(f"{member_id}: no verdict")
            continue
        fails += [f"{member_id}: {f}" for f in check_member(v, exp)]
    if "expect_member_ids" in data:
        got = [v.member_id for v in out.ev.verdicts]
        if got != data["expect_member_ids"]:
            fails.append(f"verdict member ids {got} != {data['expect_member_ids']}")
    return fails + check_headline(data, out.ev, out.household)


def banned_failures(out: Outcome) -> list[str]:
    fails = [f"headline: {w!r} in {out.ev.headline.text!r}" for w in banned_hits(out.ev.headline.text)]
    for v in out.ev.verdicts:
        for r in v.reasons:
            fails += [f"{v.member_id} {r.code}: {w!r} in {r.text!r}" for w in banned_hits(r.text)]
    return fails


def strict_ripe_failures(out: Outcome) -> list[str]:
    if not label_is_uncertain(out.raw, out.np):
        return []
    members = {m.id: m for m in out.household.members}
    return [f"{v.member_id}: strict allergy shown RIPE on a partial / missing / name-matched list"
            for v in out.ev.verdicts
            if v.state == "RIPE" and v.member_id in members
            and any(a.level == "strict" for a in members[v.member_id].allergies)]


def order_failures(out: Outcome) -> list[str]:
    expected = [m.id for m in out.household.select(out.member_ids)]
    got = [v.member_id for v in out.ev.verdicts]
    return [] if got == expected else [f"verdict order {got} != household order {expected}"]


def language_failures(out: Outcome) -> list[str]:
    members = {m.id: m for m in out.household.members}
    fails = []
    if out.ev.headline.language != out.household.language:
        fails.append(f"headline language {out.ev.headline.language} != owner {out.household.language}")
    for v in out.ev.verdicts:
        want = members[v.member_id].language if v.member_id in members else "en"
        fails += [f"{v.member_id} {r.code}: {r.language} != {want}" for r in v.reasons if r.language != want]
    return fails


def consistency_failures(out: Outcome) -> list[str]:
    return [f"{v.member_id}: {p}" for v in out.ev.verdicts for p in state_problems(v)]


def report(fails: list[str], out: Outcome) -> str:
    return "\n  " + "\n  ".join(fails) + "\n  got:\n    " + describe(out)


# ----------------------------------------------------------------------------- end-to-end self-check (fake brain)


def _fake_outcome(data: dict[str, Any], verdicts: list[Verdict], headline: str, language: str = "en") -> Outcome:
    from models.contracts import Headline, Product

    raw = build_raw(data)
    np = NormalizedProduct(product=Product(key=raw.key, source=raw.source), legibility=raw.legibility)
    return Outcome(raw=raw, np=np, ev=Evaluation(verdicts=verdicts, headline=Headline(text=headline, language=language)),
                   household=build_household(data), member_ids=data.get("member_ids"))


def _v(member_id: str, state: str, label: str, reasons: list[tuple[str, str, list[str]]] = (),
       language: str = "en", score: float | None = None) -> Verdict:
    return Verdict(member_id=member_id, state=state, label=label, score=score,
                   reasons=[Reason(code=c, text=f"{c} text", language=language, severity=s, matched=m)
                            for c, s, m in reasons])


def test_runner_end_to_end_with_fake_brain() -> None:
    """A spec-correct fake result passes every collector; a broken one is caught by each."""
    data = next(c.data for c in CASES if c.id == "allergens_en.json::tahini triggers strict sesame for Mia only")
    good = _fake_outcome(data, [
        _v("m_linh", "RIPE", "Ripe"),
        _v("m_mia", "ALLERGY", "Allergy", [("allergen.sesame", "hard", ["tahini"]), ("confidence.check_label", "note", [])]),
        _v("m_leo", "RIPE", "Ripe", language="es"),
    ], "Not for Mia: contains sesame. Fine for Linh and Leo.")
    for collector in (banned_failures, strict_ripe_failures, order_failures, language_failures, consistency_failures):
        assert collector(good) == [], collector.__name__
    assert expectation_failures(data, good) == []

    bad = _fake_outcome(data, [
        _v("m_mia", "RIPE", "Allergy", [("allergen.sesame.trace_note", "note", ["sesame"])], language="vi"),
        _v("m_linh", "RIPE", "Ripe"),
        _v("m_leo", "DIET", "Vegan?", [("signal.x", "note", []), ("diet.vegan", "hard", [])], language="es"),
    ], "Safe for Linh.", language="vi")
    assert banned_failures(bad) and order_failures(bad) and language_failures(bad) and consistency_failures(bad)
    # Mia: state, allergen.sesame (a trace note doesn't count), matched tahini; Leo: state; headline lacks Mia
    assert len(expectation_failures(data, bad)) == 5, expectation_failures(data, bad)

    partial = dict(data, product=dict(data["product"], legibility="partial"))
    unsure_ripe = _fake_outcome(partial, [_v("m_linh", "RIPE", "Ripe"), _v("m_mia", "RIPE", "Ripe"),
                                          _v("m_leo", "RIPE", "Ripe", language="es")], "Ripe for everyone. Go for it.")
    assert strict_ripe_failures(unsure_ripe) == ["m_mia: strict allergy shown RIPE on a partial / missing / name-matched list"]


# ----------------------------------------------------------------------------- engine: expectations


@pytest.mark.parametrize("case", params(CASES))
def test_case(case: Case) -> None:
    out = outcome(case)
    fails = expectation_failures(case.data, out)
    assert not fails, report(fails, out)


# ----------------------------------------------------------------------------- engine: invariants on every case


@pytest.mark.parametrize("case", params(CASES))
def test_no_banned_words(case: Case) -> None:
    out = outcome(case)
    fails = banned_failures(out)
    assert not fails, report(fails, out)


@pytest.mark.parametrize("case", params(CASES))
def test_strict_never_ripe_unsure_label(case: Case) -> None:
    out = outcome(case)
    fails = strict_ripe_failures(out)
    assert not fails, report(fails, out)


@pytest.mark.parametrize("case", params(CASES))
def test_verdict_order(case: Case) -> None:
    out = outcome(case)
    fails = order_failures(out)
    assert not fails, report(fails, out)


@pytest.mark.parametrize("case", params(CASES))
def test_deterministic(case: Case) -> None:
    first = outcome(case)
    second = run_case(case)
    assert second.np.model_dump() == first.np.model_dump(), "normalize() is not deterministic"
    assert second.ev.model_dump(exclude={"debug"}) == first.ev.model_dump(exclude={"debug"}), \
        "evaluate() is not deterministic" + report([], first)


@pytest.mark.parametrize("case", params(CASES))
def test_languages(case: Case) -> None:
    out = outcome(case)
    fails = language_failures(out)
    assert not fails, report(fails, out)


@pytest.mark.parametrize("case", params(CASES))
def test_state_consistency(case: Case) -> None:
    out = outcome(case)
    fails = consistency_failures(out)
    assert not fails, report(fails, out)
