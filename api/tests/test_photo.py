"""Photo path tests (pipeline/photo_gemini.py, routes/scan_photo.py) + the shared GEMINI-lane
test environment (`pear_env`), imported by test_ask.py and test_explain.py.

Isolation
- Gemini: tests/conftest.py fake_genai, matched on the "[pear:<call>]" tag of each system prompt.
- Data / picks lanes (pipeline.products, pipeline.product_store, pipeline.picks): replaced by
  the small fakes below through each module's lazy accessor (_products / _picks / _store).
- Brain lane (rules.kb / rules.normalize / rules.engine / rules.templates): the real code is
  used when it works; any function that is still a stub (raises) is replaced by a tiny fake
  with the same contract. Set PEAR_TEST_FAKE_RULES=1 to force the fakes.
  `pear_env.rules.faked` lists which ones were faked in this run.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_photo.py -q
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from types import SimpleNamespace
from typing import Any

import pytest

from models.contracts import (
    Headline,
    Household,
    PickItem,
    PicksResponse,
    Product,
    Reason,
    Verdict,
)
from models.domain import Evaluation, Hit, NormalizedProduct, RawProduct
from tests.conftest import demo_household_dict

# ============================================================================ fake brain

FAKE_LEX: dict[str, dict[str, Any]] = {
    "peanut": {"group": "allergies", "allergen": True, "terms": ["peanut", "groundnut", "đậu phộng", "cacahuete"],
               "label": {"en": "peanut", "vi": "đậu phộng", "es": "cacahuete"}},
    "sesame": {"group": "allergies", "allergen": True, "terms": ["sesame", "tahini", "mè", "vừng", "sésamo"],
               "label": {"en": "sesame", "vi": "mè", "es": "sésamo"}},
    "milk": {"group": "allergies", "allergen": True, "terms": ["milk", "whey", "casein", "sữa", "leche"],
             "label": {"en": "milk", "vi": "sữa", "es": "leche"}},
    "wheat": {"group": "allergies", "allergen": True, "terms": ["wheat", "bột mì", "trigo"],
              "label": {"en": "wheat", "vi": "lúa mì", "es": "trigo"}},
    "soy": {"group": "allergies", "allergen": True, "terms": ["soy", "soybean", "đậu nành", "soja"],
            "label": {"en": "soy", "vi": "đậu nành", "es": "soja"}},
    "lactose": {"group": "allergies", "allergen": True, "terms": ["lactose"],
                "label": {"en": "lactose", "vi": "lactose", "es": "lactosa"}},
    "tree_nut": {"group": "allergies", "allergen": True, "terms": ["almond", "cashew", "walnut"],
                 "label": {"en": "tree nuts", "vi": "hạt cây", "es": "frutos secos"}},
    "gelatin": {"group": None, "allergen": False, "terms": ["gelatin", "gelatine"],
                "label": {"en": "gelatin", "vi": "gelatin", "es": "gelatina"}},
    "carmine": {"group": None, "allergen": False, "terms": ["carmine", "e120", "cochineal"],
                "label": {"en": "carmine", "vi": "carmine", "es": "carmín"}},
    "high_blood_pressure": {"group": "conditions", "allergen": False, "terms": [],
                            "label": {"en": "high blood pressure", "vi": "huyết áp cao", "es": "presión alta"}},
    "prediabetes": {"group": "conditions", "allergen": False, "terms": [],
                    "label": {"en": "prediabetes", "vi": "tiền tiểu đường", "es": "prediabetes"}},
    "eczema": {"group": "skin", "allergen": False, "terms": [],
               "label": {"en": "eczema", "vi": "chàm", "es": "eccema"}},
    "fragrance_sensitivity": {"group": "skin", "allergen": False, "terms": [],
                              "label": {"en": "fragrance sensitivity", "vi": "nhạy cảm hương liệu",
                                        "es": "sensibilidad a fragancias"}},
    "vegan": {"group": "diets", "allergen": False, "terms": [],
              "label": {"en": "vegan", "vi": "thuần chay", "es": "vegano"}},
    "no_pork": {"group": "diets", "allergen": False, "terms": [],
                "label": {"en": "no pork", "vi": "không thịt heo", "es": "sin cerdo"}},
    "sodium": {"group": "less_of", "allergen": False, "terms": [],
               "label": {"en": "sodium", "vi": "muối", "es": "sodio"}},
    "sugar": {"group": "less_of", "allergen": False, "terms": [],
              "label": {"en": "sugar", "vi": "đường", "es": "azúcar"}},
}
FAKE_ALIASES = {"tree_nuts": "tree_nut", "dairy": "milk", "peanuts": "peanut"}
VEGAN_FORBIDDEN = {"gelatin", "carmine", "milk"}


class FakeKB:
    version = "fake-kb-1"

    def ids(self) -> list[str]:
        return list(FAKE_LEX)

    def canonical(self, kb_id: str) -> str:
        return FAKE_ALIASES.get(kb_id, kb_id)

    def is_allergen(self, kb_id: str) -> bool:
        return bool(FAKE_LEX.get(self.canonical(kb_id), {}).get("allergen"))

    def label(self, kb_id: str, language: str) -> str:
        e = FAKE_LEX.get(self.canonical(kb_id))
        return (e["label"].get(language) or e["label"]["en"]) if e else kb_id

    def prompt_ids_text(self) -> str:
        return "\n".join(f"{k}: {v['label']['en']}" for k, v in FAKE_LEX.items())

    def profile_ids(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {g: [] for g in ("allergies", "conditions", "skin", "diets", "less_of")}
        for k, v in FAKE_LEX.items():
            if v["group"]:
                out[v["group"]].append(k)
        return out

    def search(self, q: str, language: str = "en", limit: int = 10, groups: list[str] | None = None):
        from models.contracts import KbSuggestion

        ql = q.casefold().strip()
        hits = []
        for k, v in FAKE_LEX.items():
            if not v["group"] or (groups and v["group"] not in groups):
                continue
            labels = [x.casefold() for x in v["label"].values()]
            if ql in labels or ql.rstrip("s") in labels:
                score = 1.0
            elif any(ql in x or x in ql for x in labels):
                score = 0.6
            else:
                continue
            hits.append(KbSuggestion(group=v["group"], id=k, label=v["label"].get(language, v["label"]["en"]),
                                     labels=v["label"], score=score))
        return sorted(hits, key=lambda s: -s.score)[:limit]


def _term_hits(text: str, where: str, strength: str, index: int | None = None) -> list[Hit]:
    out: list[Hit] = []
    low = text.casefold()
    for kid, e in FAKE_LEX.items():
        for term in e["terms"]:
            m = re.search(rf"(?<!\w){re.escape(term)}", low)
            if m:
                out.append(Hit(kb_id=kid, matched=text[m.start():m.end()], where=where, strength=strength,
                               ingredient_index=index, position=index))
                break
    return out


def fake_normalize(raw: RawProduct, *, extra_hits: list[Hit] | None = None) -> NormalizedProduct:
    hits: list[Hit] = []
    unmatched: list[str] = []
    for i, ing in enumerate(raw.ingredients):
        found = _term_hits(ing, "ingredients", "contains", i)
        have = {h.kb_id for h in found}
        for kid in (raw.model_kb_ids[i] if i < len(raw.model_kb_ids) else []):
            if kid in FAKE_LEX and kid not in have and (FAKE_LEX[kid]["allergen"] or not found):
                found.append(Hit(kb_id=kid, matched=ing, where="model", strength="contains", ingredient_index=i))
        hits += found
        if not found:
            unmatched.append(ing)
    if raw.contains_text:
        hits += _term_hits(raw.contains_text, "contains", "contains")
    if raw.may_contain_text:
        hits += _term_hits(raw.may_contain_text, "may_contain", "may_contain")
    hits += list(extra_hits or [])
    allergens = [h for h in hits if FAKE_LEX.get(h.kb_id, {}).get("allergen")]
    product = Product(
        key=raw.key, name=raw.name, brand=raw.brand, category=raw.category, product_type=raw.product_type,
        source=raw.source, photo_kind=raw.photo_kind, data_confidence=raw.data_confidence,
        less_sure=raw.legibility != "full" or raw.matched_by == "name",
        ask_for_ingredients=raw.matched_by == "name", ingredients=raw.ingredients,
        contains=sorted({h.kb_id for h in allergens if h.strength != "may_contain"}),
        may_contain=sorted({h.kb_id for h in allergens if h.strength == "may_contain"}),
        label_language=raw.label_language, code=raw.code, nutrition=raw.nutrition, matched_by=raw.matched_by,
    )
    return NormalizedProduct(product=product, hits=hits, unmatched=unmatched, legibility=raw.legibility,
                             kb_version=FakeKB.version)


LABELS = {"RIPE": "Ripe", "ALLERGY": "Allergy", "UNSURE": "Unsure", "DIET": "Not vegan"}


def fake_evaluate(np: NormalizedProduct, household: Household, member_ids: list[str] | None = None,
                  *, debug: bool = False) -> Evaluation:
    verdicts: list[Verdict] = []
    for m in household.select(member_ids):
        state, reasons = "RIPE", []
        for a in m.allergies:
            aid = FAKE_ALIASES.get(a.id, a.id)
            for h in (h for h in np.hits if h.kb_id == aid):
                if h.strength == "may_contain" and a.level != "strict":
                    reasons.append(Reason(code=f"allergen.{aid}.trace_note", text=f"May contain {aid}",
                                          language=m.language, severity="note", matched=[h.matched]))
                    continue
                suffix = {"may_contain": ".may_contain", "possible": ".possible"}.get(h.strength, "")
                state = "ALLERGY"
                reasons.append(Reason(code=f"allergen.{aid}{suffix}", text=f"Contains {aid} ({h.matched})",
                                      language=m.language, severity="hard", matched=[h.matched]))
            if state != "ALLERGY" and a.level == "strict" and np.legibility in ("partial", "missing"):
                state = "UNSURE"
                reasons.append(Reason(code="confidence.unsure", text="Couldn't read the whole label",
                                      language=m.language, severity="hard"))
        if state == "RIPE" and "vegan" in m.diets:
            bad = [h for h in np.hits if h.kb_id in VEGAN_FORBIDDEN]
            if bad:
                state = "DIET"
                reasons.append(Reason(code="diet.vegan", text=f"Not vegan: {bad[0].matched}",
                                      language=m.language, severity="hard", matched=[bad[0].matched]))
        verdicts.append(Verdict(member_id=m.id, state=state, label=LABELS[state], reasons=reasons, score=100.0))
    names = {m.id: m.name for m in household.members}
    bad = [v for v in verdicts if v.state != "RIPE"]
    text = "Ripe for everyone. Go for it." if not bad else f"Not for {names[bad[0].member_id]}."
    return Evaluation(verdicts=verdicts, headline=Headline(text=text, language=household.language))


def fake_t(key: str, language: str, default: str | None = None, **kw: Any) -> str:
    try:
        return (default or key).format(**kw)
    except Exception:
        return default or key


def fake_ask_answer(ev: Evaluation, household: Household, member_ids, language: str, product_name) -> str:
    return f"[{language}] {ev.headline.text}"


def fake_scan_needed_answer(language: str) -> str:
    return {"vi": "Quét sản phẩm trước nhé.", "es": "Escanéalo primero."}.get(language, "Scan it first.")


def fake_picks_answer(names: list[str], language: str) -> str:
    return f"[{language}] Try: " + ", ".join(names) if names else f"[{language}] No picks yet."


def _works(fn) -> bool:
    if os.environ.get("PEAR_TEST_FAKE_RULES") == "1":
        return False
    try:
        fn()
        return True
    except Exception:
        return False


def install_rules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Use the real brain where it works; fake whatever is still a stub."""
    import pipeline.result as result_mod
    from rules import engine, kb, normalize, templates

    faked: list[str] = []
    if not _works(lambda: kb.get_kb().prompt_ids_text() and kb.get_kb().profile_ids()):
        fkb = FakeKB()
        monkeypatch.setattr(kb, "get_kb", lambda: fkb)
        faked.append("kb")
    probe = RawProduct(key="photo:probe", source="photo", ingredients=["peanuts", "salt"])
    if not _works(lambda: normalize.normalize(probe)):
        monkeypatch.setattr(normalize, "normalize", fake_normalize)
        faked.append("normalize")
    hh = Household.model_validate(demo_household_dict())
    if not _works(lambda: engine.evaluate(normalize.normalize(probe), hh)):
        monkeypatch.setattr(engine, "evaluate", fake_evaluate)
        monkeypatch.setattr(result_mod, "evaluate", fake_evaluate)
        faked.append("engine")
    if not _works(lambda: templates.t("x.y", "en", default="d")):
        monkeypatch.setattr(templates, "t", fake_t)
        faked.append("t")
    ev = Evaluation(verdicts=[], headline=Headline(text="h", language="en"))
    for name, fake, probe_fn in (
        ("ask_answer", fake_ask_answer, lambda: templates.ask_answer(ev, hh, None, "en", "x")),
        ("scan_needed_answer", fake_scan_needed_answer, lambda: templates.scan_needed_answer("en")),
        ("picks_answer", fake_picks_answer, lambda: templates.picks_answer(["a"], "en")),
    ):
        if not _works(probe_fn):
            monkeypatch.setattr(templates, name, fake)
            faked.append(name)
    return SimpleNamespace(faked=faked)


# ============================================================================ fake data lane


class FakeProducts:
    def __init__(self) -> None:
        self.by_key: dict[str, NormalizedProduct] = {}
        self.by_name: dict[str, NormalizedProduct] = {}
        self.calls: list[tuple[str, dict]] = []

    def seed(self, np: NormalizedProduct, *names: str) -> NormalizedProduct:
        self.by_key[np.product.key] = np
        for n in names:
            self.by_name[n.casefold()] = np
        return np

    async def load_product(self, clients, key: str) -> NormalizedProduct | None:
        self.calls.append(("load_product", {"key": key}))
        return self.by_key.get(key)

    async def match_by_name(self, clients, *, brand=None, name=None, query=None) -> NormalizedProduct | None:
        self.calls.append(("match_by_name", {"brand": brand, "name": name, "query": query}))
        for k in (query, name, f"{brand} {name}" if brand and name else None, brand):
            if k and k.casefold() in self.by_name:
                return self.by_name[k.casefold()]
        return None

    def after_scan(self, clients, *, raw, np, household, uid, evaluation, cached) -> None:
        self.calls.append(("after_scan", {"raw": raw, "np": np, "uid": uid, "cached": cached}))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakePicks:
    def __init__(self) -> None:
        self.prefetched: list[str] = []
        self.get_calls: list[dict] = []
        self.items: list[PickItem] = []

    def start_prefetch(self, clients, np, household) -> None:
        self.prefetched.append(np.product.key)

    async def get(self, clients, product_key, household, member_ids=None, *, np=None) -> PicksResponse:
        self.get_calls.append({"product_key": product_key, "member_ids": member_ids, "np": np})
        return PicksResponse(items=self.items, prefetched=True, timings_ms={"wait": 1})


class FakeStore:
    def __init__(self) -> None:
        self.scans: list[dict] = []

    async def log_scan(self, clients, **kw) -> None:
        self.scans.append(kw)


def make_np(key: str, ingredients: list[str], *, name: str = "Sesame Crackers", brand: str = "Example",
            source: str = "open_food_facts", legibility: str = "full", matched_by: str = "barcode",
            contains_text: str | None = None, category: str = "food") -> NormalizedProduct:
    from rules import normalize

    raw = RawProduct(key=key, source=source, name=name, brand=brand, ingredients=ingredients,
                     legibility=legibility, matched_by=matched_by, contains_text=contains_text,
                     category=category, data_confidence="medium" if matched_by == "name" else "high")
    np = normalize.normalize(raw)
    if matched_by == "name":  # what pipeline.products.match_by_name promises
        np = np.model_copy(update={"product": np.product.model_copy(update={
            "less_sure": True, "ask_for_ingredients": True, "data_confidence": "medium", "matched_by": "name"})})
    return np


@pytest.fixture
def pear_env(app_client, fake_genai, monkeypatch):
    import main
    from pipeline import ask, explain, photo_gemini

    rules = install_rules(monkeypatch)
    products, picks, store = FakeProducts(), FakePicks(), FakeStore()
    for mod in (photo_gemini, ask, explain):
        monkeypatch.setattr(mod, "_products", lambda: products)
    for mod in (photo_gemini, ask):
        monkeypatch.setattr(mod, "_picks", lambda: picks)
    monkeypatch.setattr(photo_gemini, "_store", lambda: store)
    clients = main.app.state.clients
    monkeypatch.setattr(clients, "settings", dataclasses.replace(clients.settings, ask_engine="direct"))
    return SimpleNamespace(client=app_client, genai=fake_genai, products=products, picks=picks, store=store,
                           rules=rules, clients=clients, household=demo_household_dict())


# ============================================================================ photo tests

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # magic bytes are enough for the fake model


def label(**over: Any) -> dict:
    base = {
        "legibility": "full", "photo_kind": "ingredients", "product_name": "Sesame Crackers",
        "brand": "Example Brand", "category": "food", "product_type": "none", "label_language": "en",
        "contains_statement": "Contains: wheat, sesame.", "may_contain_statement": "",
        "ingredients": [
            {"text": "wheat flour", "kb_ids": ["wheat"]},
            {"text": "sesame seeds", "kb_ids": ["sesame"]},
            {"text": "salt", "kb_ids": []},
        ],
        "certifications": [],
        "nutrition": {"sodium_mg": 230},
    }
    base.update(over)
    return base


def post_photo(env, *, front: bytes | None = None, member_ids: str | None = None, debug: bool = False):
    files = {"image": ("label.jpg", JPEG, "image/jpeg")}
    if front is not None:
        files["front_image"] = ("front.jpg", front, "image/jpeg")
    data = {"household": json.dumps(env.household)}
    if member_ids is not None:
        data["member_ids"] = member_ids
    return env.client.post("/v1/scan/photo" + ("?debug=1" if debug else ""), files=files, data=data)


def verdict(body: dict, member_id: str) -> dict:
    return next(v for v in body["verdicts"] if v["member_id"] == member_id)


def test_full_label_allergy_one_gemini_call(pear_env):
    pear_env.genai.on("[pear:label_reader]", label())
    r = post_photo(pear_env)
    assert r.status_code == 200, r.text
    body = r.json()
    p = body["product"]
    assert p["source"] == "photo" and p["key"].startswith("photo:") and p["photo_kind"] == "ingredients"
    assert p["ingredients"] == ["wheat flour", "sesame seeds", "salt"]
    mia = verdict(body, "m_mia")
    assert mia["state"] == "ALLERGY"
    assert any(r["code"].startswith("allergen.sesame") for r in mia["reasons"])
    assert verdict(body, "m_leo")["state"] != "ALLERGY"
    assert all(v["score"] is None for v in body["verdicts"])  # hidden without ?debug=1
    assert "safe" not in body["headline"]["text"].lower()
    # exactly one Gemini call, with the agreed settings
    assert len(pear_env.genai.calls) == 1
    call = pear_env.genai.calls[0]
    cfg = call["config"]
    assert cfg.max_output_tokens == 1024
    assert cfg.response_mime_type == "application/json"
    assert list(cfg.response_json_schema["properties"])[:2] == ["legibility", "photo_kind"]
    assert "[pear:label_reader]" in cfg.system_instruction and "sesame" in cfg.system_instruction
    assert call["model"] == pear_env.clients.settings.model_label
    # background hooks: picks prefetch for food, after_scan with the text-only raw product
    assert pear_env.picks.prefetched == [p["key"]]
    after = [c for c in pear_env.products.calls if c[0] == "after_scan"]
    assert len(after) == 1 and after[0][1]["cached"] is False and after[0][1]["raw"].source == "photo"


def test_front_image_is_sent_too(pear_env):
    pear_env.genai.on("[pear:label_reader]", label())
    r = post_photo(pear_env, front=JPEG)
    assert r.status_code == 200, r.text
    contents = pear_env.genai.calls[0]["contents"]
    parts = [c for c in contents if not isinstance(c, str)]
    assert len(parts) == 2  # ingredient panel + front
    assert parts[0].inline_data.mime_type == "image/jpeg"


def test_partial_label_strict_allergy_never_ripe(pear_env):
    pear_env.genai.on("[pear:label_reader]", label(
        legibility="partial", contains_statement="",
        ingredients=[{"text": "sugar", "kb_ids": []}, {"text": "corn syrup", "kb_ids": []}]))
    r = post_photo(pear_env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert verdict(body, "m_mia")["state"] in ("UNSURE", "ALLERGY")
    assert body["product"]["less_sure"] is True


def test_member_ids_filter(pear_env):
    pear_env.genai.on("[pear:label_reader]", label())
    r = post_photo(pear_env, member_ids='["m_mia"]')
    assert r.status_code == 200, r.text
    assert [v["member_id"] for v in r.json()["verdicts"]] == ["m_mia"]


def test_too_blurry_is_422_retake(pear_env):
    pear_env.genai.on("[pear:label_reader]", label(legibility="unreadable", ingredients=[], product_name="",
                                                   brand="", contains_statement=""))
    r = post_photo(pear_env)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "too_blurry" and err["next"] == "retake"
    assert err["message"] == "Too blurry. Retake?"
    assert not any(c[0] == "after_scan" for c in pear_env.products.calls)


def test_not_a_label_is_unreadable(pear_env):
    pear_env.genai.on("[pear:label_reader]", label(photo_kind="other", ingredients=[], product_name="",
                                                   brand="", contains_statement=""))
    r = post_photo(pear_env)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unreadable" and r.json()["error"]["next"] == "retake"


def test_front_only_matched_by_name(pear_env):
    np = make_np("off:0070662404027", ["enriched wheat flour", "palm oil", "salt"], name="Top Ramen",
                 brand="Nissin", matched_by="name")
    pear_env.products.seed(np, "Nissin Top Ramen")
    pear_env.genai.on("[pear:label_reader]", label(photo_kind="front_only", ingredients=[], legibility="full",
                                                   product_name="Top Ramen", brand="Nissin",
                                                   contains_statement=""))
    r = post_photo(pear_env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["key"] == "off:0070662404027"
    assert body["product"]["less_sure"] is True and body["product"]["ask_for_ingredients"] is True
    assert body["product"]["matched_by"] == "name"
    assert ("match_by_name", {"brand": "Nissin", "name": "Top Ramen", "query": None}) in pear_env.products.calls
    assert not any(c[0] == "after_scan" for c in pear_env.products.calls)  # never saved as a photo product
    assert pear_env.picks.prefetched == ["off:0070662404027"]


def test_front_only_no_match_is_404_take_photo(pear_env):
    pear_env.genai.on("[pear:label_reader]", label(photo_kind="front_only", ingredients=[],
                                                   product_name="Imaginary Crisps", brand="Nobody",
                                                   contains_statement=""))
    r = post_photo(pear_env)
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["next"] == "take_photo"
    assert err["message"] == "Snap the ingredients."


def test_missing_image_and_household_are_400(pear_env):
    r = pear_env.client.post("/v1/scan/photo", data={"household": json.dumps(pear_env.household)})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = pear_env.client.post("/v1/scan/photo", files={"image": ("l.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = pear_env.client.post("/v1/scan/photo", files={"image": ("l.txt", b"hello", "text/plain")},
                             data={"household": json.dumps(pear_env.household)})
    assert r.status_code == 400


def test_gemini_unavailable_is_503(pear_env):
    pear_env.clients.genai = None
    pear_env.clients.genai_error = "no creds"
    r = post_photo(pear_env)
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"


def test_debug_shows_label_reader(pear_env):
    pear_env.genai.on("[pear:label_reader]", label())
    r = post_photo(pear_env, debug=True)
    assert r.status_code == 200, r.text
    dbg = r.json()["debug"]
    assert dbg["label"]["ingredients"] == 3 and "gemini.label_reader" in dbg


# ---------------------------------------------------------------------------- unit: mapping


def test_label_to_raw_mapping(monkeypatch):
    from pipeline import photo_gemini
    from rules import kb

    monkeypatch.setattr(kb, "get_kb", lambda: FakeKB())
    data = label(label_language="vi", product_type="none", nutrition={"sodium_mg": 1150, "added_sugars_g": 10,
                                                                      "sat_fat_g": 2, "sat_fat_dv": 11},
                 certifications=["Halal"], may_contain_statement="Có thể chứa đậu phộng",
                 ingredients=[{"text": "bột mì", "en": "wheat flour", "kb_ids": ["wheat", "made_up_id"]},
                              {"text": "  dầu mè ", "kb_ids": ["sesame"]}])
    raw = photo_gemini.label_to_raw(data)
    assert raw.source == "photo" and raw.matched_by == "photo" and raw.key.startswith("photo:")
    assert raw.ingredients == ["bột mì", "dầu mè"]
    assert raw.ingredients_en == [None, None]  # vi label: no English gloss
    assert raw.model_kb_ids == [["wheat"], ["sesame"]]  # ids not in the kb are dropped
    assert raw.product_type is None and raw.label_language == "vi"
    assert raw.data_confidence == "high" and raw.legibility == "full"
    assert raw.may_contain_text == "Có thể chứa đậu phộng" and raw.certifications_text == ["Halal"]
    n = raw.nutrition
    assert n.sodium_dv == 50.0 and n.added_sugars_dv == 20.0 and n.sat_fat_dv == 11.0
    # same label -> same key; partial -> medium confidence
    assert photo_gemini.label_to_raw(data).key == raw.key
    assert photo_gemini.label_to_raw({**data, "legibility": "partial"}).data_confidence == "medium"
    fr = photo_gemini.label_to_raw({**data, "label_language": "fr"})
    assert fr.ingredients_en == ["wheat flour", None]


def test_clean_label_is_defensive():
    from pipeline.photo_gemini import clean_label

    out = clean_label({"ingredients": ["sugar", {"text": ""}, {"text": "salt", "kb_ids": [1, "x"]}],
                       "legibility": "unreadable", "nutrition": {"sodium_mg": "120"}})
    assert [i["text"] for i in out["ingredients"]] == ["sugar", "salt"]
    assert out["legibility"] == "partial"
    assert out["photo_kind"] == "ingredients"
    assert out["nutrition"] == {"sodium_mg": 120.0}
    assert clean_label(None)["legibility"] == "unreadable"


def test_sniff_image_mime():
    from pipeline.photo_gemini import sniff_image_mime

    assert sniff_image_mime(b"\xff\xd8\xff\xe0xx") == "image/jpeg"
    assert sniff_image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert sniff_image_mime(b"\x00\x00\x00\x18ftypheic....") == "image/heic"


def test_photo_after_scan_saves_text_only_product(pear_env, monkeypatch):
    """Real pipeline.products.after_scan: the photo product lands in the memory store, text only."""
    import asyncio
    import time

    from pipeline import photo_gemini, product_store, products

    monkeypatch.setattr(photo_gemini, "_products", lambda: products)
    monkeypatch.setattr(photo_gemini, "_store", lambda: product_store)
    pear_env.genai.on("[pear:label_reader]", label())
    r = post_photo(pear_env)
    assert r.status_code == 200, r.text
    key = r.json()["product"]["key"]
    stored = None
    for _ in range(40):  # background save runs on the app loop after the response
        stored = asyncio.run(product_store.get(pear_env.clients, key))
        if stored is not None:
            break
        time.sleep(0.05)
    assert stored is not None and stored.source == "photo"
    assert stored.raw.ingredients == ["wheat flour", "sesame seeds", "salt"]
    dumped = json.dumps(stored.model_dump(mode="json"), ensure_ascii=False)
    assert "\\xff\\xd8" not in dumped and "image" not in stored.raw.model_dump()  # never the image
