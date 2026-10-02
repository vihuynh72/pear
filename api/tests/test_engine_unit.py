"""Brain lane (L4) unit tests: kb loader, matcher, statements, label merge, engine, scoring,
headline, templates, kb search, perf. Pure Python: no network, no Gemini.

    cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_engine_unit.py -q
"""

from __future__ import annotations

import json
import time

import pytest

from models.contracts import Household, Member, Nutrition
from models.domain import Hit, RawProduct
from rules.engine import evaluate, evaluate_many
from rules.kb import get_kb
from rules.normalize import match_text, normalize, split_ingredients
from rules.statements import regions
from rules.templates import BANNED, TEMPLATES, ask_answer, picks_answer, scan_needed_answer, t
from rules.text import join_names, prep
from tests.conftest import demo_household_dict


def hh(**over) -> Household:
    d = demo_household_dict()
    d.update(over)
    return Household.model_validate(d)


def raw(ingredients=None, **kw) -> RawProduct:
    kw.setdefault("key", "test:unit")
    kw.setdefault("source", "photo")
    return RawProduct(ingredients=ingredients or [], **kw)


def verdict(ev, mid):
    return next(v for v in ev.verdicts if v.member_id == mid)


def ids(text: str) -> set[tuple[str, str]]:
    return {(h.kb_id, h.strength) for h in match_text(text)}


def person(mid: str, name: str = "X", **kw) -> Member:
    return Member.model_validate({"id": mid, "name": name, **kw})


# ------------------------------------------------------------------------------- kb


def test_kb_loads_with_version_and_sources():
    kb = get_kb()
    assert len(kb.version) == 10
    assert len(kb.allergen_order) == 17
    for kid, e in kb.entries.items():
        assert e.get("source") in kb.sources, f"{kid} has no valid source"
    for d in kb.diets.values():
        assert d["source"] in kb.sources
    for s in kb.signals:
        assert s["source"] in kb.sources


def test_kb_canonical_aliases():
    kb = get_kb()
    assert kb.canonical("tree_nuts") == "tree_nut"
    assert kb.canonical("dairy") == "milk"
    assert kb.canonical("sulphites") == "sulfite"
    assert kb.canonical("hypertension") == "high_blood_pressure"
    assert kb.canonical("crustaceans") == "shellfish"
    assert kb.is_allergen("eggs") and not kb.is_allergen("gelatin")


def test_prompt_ids_text_and_profile_ids():
    kb = get_kb()
    lines = kb.prompt_ids_text().splitlines()
    assert "sesame: sesame" in lines and "gelatin: gelatin" in lines
    assert not any(line.startswith("neutral.") for line in lines)
    groups = kb.profile_ids()
    assert set(groups) == {"allergies", "conditions", "skin", "diets", "less_of"}
    assert "pregnancy" in groups["conditions"] and "fragrance_sensitivity" in groups["skin"]


@pytest.mark.parametrize("q,lang,expected", [
    ("dairy", "en", "milk"), ("celiac", "en", "gluten"), ("hypertension", "en", "high_blood_pressure"),
    ("đậu phộng", "vi", "peanut"), ("dau phong", "vi", "peanut"), ("cacahuate", "es", "peanut"),
    ("vegan", "en", "vegan"), ("eczema", "es", "eczema"), ("salt", "en", "sodium"), ("presion alta", "es", "high_blood_pressure"),
])
def test_kb_search(q, lang, expected):
    res = get_kb().search(q, language=lang, limit=5)
    assert res and res[0].id == expected, [(r.id, r.score) for r in res]


def test_kb_search_groups_language_and_speed():
    kb = get_kb()
    res = kb.search("se", language="vi", groups=["allergies"])
    assert all(r.group == "allergies" for r in res)
    sesame = kb.search("sesame", language="vi")[0]
    assert sesame.label == "mè" and sesame.labels["es"] == "sésamo"
    t0 = time.perf_counter()
    for _ in range(200):
        kb.search("mi", language="vi")
    assert (time.perf_counter() - t0) / 200 < 0.002


# ------------------------------------------------------------------------------- matcher


@pytest.mark.parametrize("text,expected", [
    ("whey protein", ("milk", "contains")),
    ("sodium caseinate", ("milk", "contains")),
    ("tahini", ("sesame", "contains")),
    ("arachis oil", ("peanut", "contains")),
    ("lòng trắng trứng", ("egg", "contains")),
    ("đậu phộng rang", ("peanut", "contains")),
    ("nước mắm", ("fish", "contains")),
    ("mắm tôm", ("shellfish", "contains")),
    ("ajonjolí", ("sesame", "contains")),
    ("suero de leche", ("milk", "contains")),
    ("almond milk", ("tree_nut", "contains")),
    ("soy milk", ("soy", "contains")),
    ("peanut butter", ("peanut", "contains")),
    ("coconut milk", ("tree_nut", "possible")),
    ("shea butter", ("tree_nut", "possible")),
    ("lecithin", ("soy", "possible")),
    ("E322", ("soy", "possible")),
    ("flour", ("wheat", "possible")),
    ("oats", ("gluten", "possible")),
    ("semolina", ("gluten", "contains")),
    ("malt extract", ("gluten", "contains")),
    ("E 129", ("red_40", "contains")),
    ("E-102", ("yellow_5", "contains")),
    ("INS 110", ("yellow_6", "contains")),
    ("FD&C Red No. 40", ("red_40", "contains")),
    ("Red #40", ("red_40", "contains")),
    ("CI 16035", ("red_40", "contains")),
    ("sodium metabisulfite", ("sulfite", "contains")),
    ("E223", ("sulfite", "contains")),
    ("2-bromo-2-nitropropane-1,3-diol", ("formaldehyde_releasers", "contains")),
    ("Parfum", ("fragrance", "contains")),
    ("prunus amygdalus dulcis oil", ("tree_nut", "contains")),
    ("glucosamine", ("shellfish", "possible")),
    ("dextrose", ("corn", "possible")),
    ("Peanuts", ("peanut", "contains")),
    ("anchovies", ("fish", "contains")),
    ("_MILK_ chocolate", ("milk", "contains")),
])
def test_matcher_finds(text, expected):
    assert expected in ids(text), (text, ids(text))


@pytest.mark.parametrize("text,not_expected", [
    ("cocoa butter", "milk"),
    ("peanut butter", "milk"),
    ("coconut milk", "milk"),
    ("rice milk", "milk"),
    ("cream of tartar", "milk"),
    ("nutmeg", "tree_nut"),
    ("water chestnut", "tree_nut"),
    ("butternut squash", "tree_nut"),
    ("buckwheat", "wheat"),
    ("eggplant", "egg"),
    ("peanut-free", "peanut"),
    ("gluten-free", "gluten"),
    ("dairy-free", "milk"),
    ("nuez moscada", "tree_nut"),
    ("trigo sarraceno", "wheat"),
    ("bơ ca cao", "milk"),
    ("sunflower lecithin", "soy"),
    ("cetearyl alcohol", "alcohol"),
    ("indigo carmine", "carmine"),
    ("mì chính", "wheat"),
])
def test_neutral_traps(text, not_expected):
    assert not_expected not in {k for k, _s in ids(text)}, (text, ids(text))


def test_neutral_never_hides_real_allergen():
    assert ("tree_nut", "contains") in ids("unsweetened almond milk")
    assert ("soy", "contains") in ids("organic soy milk")
    assert ("gluten", "possible") in ids("oat milk")
    assert ("oats" and ("gluten", "possible")) in ids("gluten free oats")
    assert ("milk", "contains") in ids("lactose-free milk")


def test_matched_is_exact_printed_substring():
    text = "Ingredients: Sữa BỘT (12%), Arachis Oil, FD&C Red No. 40, Fragrance (Parfum)"
    for h in match_text(text):
        assert h.matched in prep(text)[0] or h.matched in text, h.matched
    assert {h.matched for h in match_text(text)} >= {"Sữa BỘT", "Arachis Oil", "FD&C Red No. 40", "Fragrance (Parfum)"}


def test_split_ingredients_keeps_parentheses_and_chemicals():
    parts = split_ingredients("Ingredients: chocolate (sugar, cocoa butter, milk), 2-bromo-2-nitropropane-1,3-diol; salt.")
    assert parts == ["chocolate (sugar, cocoa butter, milk)", "2-bromo-2-nitropropane-1,3-diol", "salt"]


# ------------------------------------------------------------------------------- statements


@pytest.mark.parametrize("text,allergen", [
    ("Wheat flour, sugar. May contain peanuts.", "peanut"),
    ("may contain traces of tree nuts", "tree_nut"),
    ("Processed in a facility that also processes milk.", "milk"),
    ("Made on shared equipment with sesame", "sesame"),
    ("Có thể chứa đậu phộng", "peanut"),
    ("Sản xuất trên dây chuyền có chế biến sữa", "milk"),
    ("Puede contener trazas de cacahuete", "peanut"),
    ("Contiene trazas de huevo", "egg"),
    ("Elaborado en una planta que también procesa soja", "soy"),
])
def test_may_contain_statements(text, allergen):
    found = {(h.kb_id, h.strength) for h in match_text(text)}
    assert (allergen, "may_contain") in found, found
    assert (allergen, "contains") not in found


def test_contains_then_may_contain_in_one_text():
    found = ids("Contains: wheat, milk. May contain: almonds.")
    assert ("milk", "contains") in found and ("wheat", "contains") in found
    assert ("tree_nut", "may_contain") in found and ("tree_nut", "contains") not in found


def test_regions_reset_at_sentence_end():
    norm = prep("may contain nuts. milk, sugar")[0]
    reg = regions(norm)
    assert reg.at(norm.index("nuts")) == "may_contain"
    assert reg.at(norm.index("milk")) == "contains"


def test_may_contain_inside_ingredient_list_is_not_contains():
    np = normalize(raw(["oats", "sugar", "may contain traces of peanuts"], source="open_food_facts"))
    assert np.product.may_contain == ["peanut"] or "peanut" in np.product.may_contain
    assert "peanut" not in np.product.contains
    np2 = normalize(raw([], source="open_food_facts", ingredients_text="oats, sugar, salt. May contain milk and soy."))
    assert "milk" in np2.product.may_contain and "milk" not in np2.product.contains
    assert np2.product.ingredients[:2] == ["oats", "sugar"]


# ------------------------------------------------------------------------------- normalize


VN_NOODLES = ("Vắt mì: bột mì (lúa mì), dầu cọ, muối, đường, nước mắm. Sản phẩm có chứa: lúa mì, cá. "
              "Có thể chứa đậu phộng, mè, tôm.")


def test_derived_list_keeps_may_contain_sentence():
    np = normalize(raw([], source="open_food_facts", ingredients_text=VN_NOODLES))
    assert np.product.contains == ["wheat", "fish", "gluten"]
    assert np.product.may_contain == ["peanut", "shellfish", "sesame"]
    assert "Có thể chứa đậu phộng" in np.product.ingredients and "mè" in np.product.ingredients


def test_list_item_split_from_may_contain_statement_is_reconciled_with_text():
    items = ["bột mì", "dầu cọ", "Có thể chứa đậu phộng", "mè", "tôm"]
    np = normalize(raw(items, source="open_food_facts", ingredients_text=VN_NOODLES))
    assert "sesame" in np.product.may_contain and "sesame" not in np.product.contains
    # ...but a real ingredient mention is never softened
    np = normalize(raw(["sesame oil", "sugar"], ingredients_text="sesame oil, sugar. May contain sesame."))
    assert np.product.contains == ["sesame"]
    np = normalize(raw(["mè", "đường"], ingredients_text="mè, đường"))
    assert np.product.contains == ["sesame"]


def test_tags_contains_and_traces():
    np = normalize(raw(["sugar"], source="open_food_facts", allergens_tags=["en:milk"], traces_tags=["en:nuts"],
                       additives_tags=["en:e129", "en:e322i"], labels_tags=["en:vegan", "en:organic"],
                       analysis_tags=["en:palm-oil"]))
    kinds = {(h.kb_id, h.strength, h.where) for h in np.hits}
    assert ("milk", "contains", "tags") in kinds
    assert ("tree_nut", "may_contain", "tags") in kinds
    assert ("red_40", "contains", "tags") in kinds
    assert ("soy", "possible", "tags") in kinds
    assert ("palm_oil", "contains", "tags") in kinds
    assert np.certifications == ["vegan", "organic"]


def test_label_merge_rule_model_adds_allergens_never_removes():
    r = raw(["Thai text A", "bột mì", "cocoa butter", "mystery thing"],
            model_kb_ids=[["peanut"], [], ["milk"], ["gelatin"]], label_language="th")
    np = normalize(r)
    by = {(h.kb_id, h.where) for h in np.hits}
    assert ("peanut", "model") in by                    # model adds an allergen the kb couldn't read
    assert ("wheat", "ingredients") in by               # kb hit kept even though model said []
    assert ("milk", "model") in by                      # allergen from model counts even on a kb-neutral string
    assert ("gelatin", "model") in by                   # non-allergen counts: kb matched nothing there
    assert "mystery thing" not in np.unmatched          # the model mapped it


def test_label_merge_non_allergen_ignored_where_kb_matched():
    np = normalize(raw(["sugar"], model_kb_ids=[["palm_oil"]]))
    assert "palm_oil" not in np.kb_ids()


def test_model_may_contain_item_stays_may_contain():
    np = normalize(raw(["rice", "may contain xyz"], model_kb_ids=[[], ["sesame"]]))
    assert np.product.may_contain == ["sesame"] and np.product.contains == []


def test_gloss_matches_with_printed_text():
    np = normalize(raw(["ถั่วลิสง"], ingredients_en=["peanuts"], label_language="th"))
    h = next(h for h in np.hits if h.kb_id == "peanut")
    assert h.matched == "ถั่วลิสง"


def test_extra_hits_only_add():
    np = normalize(raw(["mystery"]), extra_hits=[Hit(kb_id="sesame_seeds", matched="mystery", where="enrich")])
    assert "sesame" in np.product.contains


def test_product_flags():
    np = normalize(raw([], legibility="full", source="open_food_facts"))
    assert np.legibility == "missing" and np.product.less_sure and np.product.ask_for_ingredients
    np = normalize(raw(["rice"], matched_by="name", data_confidence="medium", photo_kind="front_only"))
    assert np.product.less_sure and np.product.ask_for_ingredients
    np = normalize(raw(["rice"]))
    assert not np.product.less_sure and not np.product.ask_for_ingredients
    assert np.kb_version == get_kb().version


# ------------------------------------------------------------------------------- scoring examples (plan 8.4)


def test_example_ba_noodles_sodium_75_easy():
    ev = evaluate(normalize(raw(["bột mì", "muối"], nutrition=Nutrition(sodium_dv=38))), hh(), ["m_ba"])
    v = verdict(ev, "m_ba")
    assert (v.state, v.score, v.label) == ("EASY", 75.0, "Easy does it")
    assert v.reasons[0].code == "signal.sodium" and "38% nhu cầu mỗi ngày" in v.reasons[0].text
    assert v.reasons[0].language == "vi"


def test_example_ba_noodles_plus_sugar_50_skip():
    ev = evaluate(normalize(raw(["bột mì", "muối"], nutrition=Nutrition(sodium_dv=38, added_sugars_dv=25))), hh(), ["m_ba"])
    v = verdict(ev, "m_ba")
    assert (v.state, v.score) == ("SKIP", 50.0)


def test_example_mia_leave_on_lotion_35_skip():
    np = normalize(raw(["water", "methylisothiazolinone", "fragrance"], category="cosmetic", product_type="leave_on"))
    v = verdict(evaluate(np, hh(), ["m_mia"]), "m_mia")
    assert (v.state, v.score) == ("SKIP", 35.0)


def test_example_mia_rinse_off_wash_77_5_easy():
    np = normalize(raw(["water", "methylisothiazolinone"], category="cosmetic", product_type="rinse_off"))
    v = verdict(evaluate(np, hh(), ["m_mia"]), "m_mia")
    assert (v.state, v.score) == ("EASY", 77.5)


def test_example_linh_rinse_off_shampoo_90_ripe_note():
    np = normalize(raw(["aqua", "parfum"], category="cosmetic", product_type="rinse_off"))
    v = verdict(evaluate(np, hh(), ["m_linh"]), "m_linh")
    assert (v.state, v.score) == ("RIPE", 90.0)
    assert v.reasons and v.reasons[0].code == "signal.fragrance" and v.reasons[0].severity == "note"


def test_leniency_one_medium_issue_stays_easy():
    m = person("m_k", "Kim", conditions=["kidney_potassium_limit"])
    np = normalize(raw(["potassium chloride"], nutrition=Nutrition(sodium_dv=45)))
    # 30 (potassium) + 40 (sodium 1.6x, kidney) -> 30 -> two real issues -> SKIP
    v = evaluate(np, Household(members=[m])).verdicts[0]
    assert v.state == "SKIP"
    m2 = person("m_j", "Jo", less_of=["sodium"])
    v2 = evaluate(normalize(raw(["salt"], nutrition=Nutrition(sodium_dv=200))), Household(members=[m2])).verdicts[0]
    assert v2.state in ("RIPE", "EASY")  # one less-of goal never reaches SKIP


def test_leniency_clamps_single_issue():
    from rules.scoring import Penalty, tier

    p = Penalty(signal={"id": "x"}, weight=30, amount=1.6, context=1.0, ptype=1.0, penalty=48)
    assert tier(52, [p]) == ("EASY", True)
    p45 = Penalty(signal={"id": "y"}, weight=45, amount=1.0, context=1.0, ptype=1.0, penalty=45)
    assert tier(55, [p45]) == ("SKIP", False)


def test_amount_factor_bands():
    from rules.scoring import amount_factor

    assert [amount_factor(x) for x in (None, 4.9, 5, 19.9, 20, 39.9, 40, 120)] == [0, 0, 0.3, 0.3, 1.0, 1.0, 1.6, 1.6]


def test_dyes_follow_member_dyes_on_and_pregnancy():
    np = normalize(raw(["sugar", "Red 40"]))
    ev = evaluate(np, hh(), ["m_mia", "m_linh"])
    assert any(r.code == "signal.dyes" for r in verdict(ev, "m_mia").reasons)
    assert not any(r.code == "signal.dyes" for r in verdict(ev, "m_linh").reasons)
    preg = person("m_p", "Pat", pregnant=True)
    v = evaluate(normalize(raw(["water", "retinol"], category="cosmetic", product_type="leave_on")), Household(members=[preg])).verdicts[0]
    assert v.state == "SKIP" and v.reasons[0].code == "signal.retinoids" and "doctor" in v.reasons[0].text


# ------------------------------------------------------------------------------- allergies + precedence


def test_strict_may_contain_is_allergy_with_may_contain_label():
    ev = evaluate(normalize(raw(["rice"], may_contain_text="May contain peanuts")), hh(), ["m_mia"])
    v = verdict(ev, "m_mia")
    assert (v.state, v.label) == ("ALLERGY", "May contain")
    assert v.reasons[0].code == "allergen.peanut.may_contain" and v.reasons[0].text == "May contain peanuts"


def test_sensitive_levels():
    sam = person("m_sam", "Sam", allergies=[{"id": "milk", "level": "sensitive"}])
    house = Household(members=[sam])
    v = evaluate(normalize(raw(["flour", "whey"])), house).verdicts[0]
    assert v.state == "ALLERGY" and v.reasons[0].code == "allergen.milk"
    v = evaluate(normalize(raw(["rice"], may_contain_text="may contain milk")), house).verdicts[0]
    assert v.state == "RIPE" and any(r.code == "allergen.milk.trace_note" and r.severity == "note" for r in v.reasons)
    v = evaluate(normalize(raw(["rice"], legibility="partial")), house).verdicts[0]
    assert v.state == "RIPE" and any(r.code == "confidence.less_sure" for r in v.reasons)
    assert not any(r.code.endswith(".sensitive") for r in v.reasons)


def test_strict_partial_or_missing_is_unsure_never_ripe():
    for leg, ings in (("partial", ["rice"]), ("missing", [])):
        ev = evaluate(normalize(raw(ings, legibility=leg)), hh())
        mia = verdict(ev, "m_mia")
        assert mia.state == "UNSURE" and mia.label == "Unsure" and mia.reasons[0].code == "confidence.unsure"
        assert verdict(ev, "m_leo").state == "RIPE"


def test_precedence_allergy_over_everything():
    np = normalize(raw(["sesame", "gelatin", "Red 40", "fragrance"], legibility="partial", nutrition=Nutrition(sodium_dv=50)))
    m = person("m_x", "Max", allergies=[{"id": "sesame"}, {"id": "peanut"}], diets=["vegan"], conditions=["high_blood_pressure"])
    v = evaluate(np, Household(members=[m])).verdicts[0]
    assert v.state == "ALLERGY"
    sev = [r.severity for r in v.reasons]
    assert sev == sorted(sev, key={"hard": 0, "soft": 1, "note": 2}.__getitem__)
    codes = [r.code for r in v.reasons]
    assert "confidence.unsure" in codes and "diet.vegan" in codes and "signal.sodium" in codes


def test_precedence_unsure_over_diet_over_skip():
    m = person("m_x", "Max", allergies=[{"id": "peanut"}], diets=["vegan"])
    assert evaluate(normalize(raw(["gelatin"], legibility="partial")), Household(members=[m])).verdicts[0].state == "UNSURE"
    m2 = person("m_y", "Yan", diets=["vegan"], conditions=["high_blood_pressure"], less_of=["sugar"])
    v = evaluate(normalize(raw(["gelatin"], nutrition=Nutrition(sodium_dv=50, added_sugars_dv=50))), Household(members=[m2])).verdicts[0]
    assert v.state == "DIET" and v.label == "Not vegan"


def test_database_strict_gets_check_label_note():
    v = verdict(evaluate(normalize(raw(["rice"], source="open_food_facts")), hh(), ["m_mia"]), "m_mia")
    assert v.state == "RIPE" and any(r.code == "confidence.check_label" for r in v.reasons)
    v = verdict(evaluate(normalize(raw(["rice"], source="photo")), hh(), ["m_mia"]), "m_mia")
    assert not any(r.code == "confidence.check_label" for r in v.reasons)


def test_matched_by_name_notes():
    np = normalize(raw(["rice", "sesame"], source="open_food_facts", matched_by="name", data_confidence="medium"))
    ev = evaluate(np, hh())
    assert verdict(ev, "m_mia").state == "ALLERGY"
    assert any(r.code == "confidence.matched_by_name" for r in verdict(ev, "m_leo").reasons)


# ------------------------------------------------------------------------------- diets + certs (8.5)


def test_diet_states_and_certs():
    leo = ["m_leo"]
    v = verdict(evaluate(normalize(raw(["sugar", "gelatin"])), hh(), leo), "m_leo")
    assert (v.state, v.label, v.reasons[0].code, v.reasons[0].matched) == ("DIET", "Not vegan", "diet.vegan", ["gelatin"])
    assert v.reasons[0].text == "No es vegano: gelatin"
    v = verdict(evaluate(normalize(raw(["sugar", "natural flavors"])), hh(), leo), "m_leo")
    assert (v.state, v.label) == ("DIET", "Maybe not vegan")
    v = verdict(evaluate(normalize(raw(["sugar", "natural flavors"], certifications_text=["Certified Vegan"])), hh(), leo), "m_leo")
    assert v.state == "RIPE" and v.reasons[0].code == "cert.vegan"
    v = verdict(evaluate(normalize(raw(["sugar", "honey"], certifications_text=["Vegan"])), hh(), leo), "m_leo")
    assert (v.state, v.label, v.reasons[0].code) == ("DIET", "Check", "diet.vegan.check")
    v = verdict(evaluate(normalize(raw(["sugar"], labels_tags=["en:vegan"])), hh(), leo), "m_leo")
    assert v.state == "RIPE" and v.reasons[0].code == "cert.vegan"


def test_diet_labels_are_chip_words():
    for d in get_kb().diets.values():
        assert d["not_label"]["en"].startswith("Not "), d["id"]
        assert d["maybe_label"]["en"].startswith("Maybe not "), d["id"]


# ------------------------------------------------------------------------------- pets, meds, supplements


def test_pets_meds_supplements():
    dog = Member.model_validate({"id": "m_dog", "name": "Rex", "type": "pet", "species": "dog"})
    v = evaluate(normalize(raw(["xylitol", "mint"])), Household(members=[dog])).verdicts[0]
    assert (v.state, v.label, v.reasons[0].code) == ("ALLERGY", "Toxic for dogs", "pet.xylitol")
    cat = Member.model_validate({"id": "m_cat", "name": "Mo", "type": "pet", "species": "cat"})
    assert evaluate(normalize(raw(["macadamia nuts"])), Household(members=[cat])).verdicts[0].state == "RIPE"
    gran = person("m_g", "Gran", medications=["Atorvastatin 20mg"])
    v = evaluate(normalize(raw(["grapefruit juice"])), Household(members=[gran])).verdicts[0]
    assert v.state == "RIPE" and v.reasons[0].code == "med.grapefruit_statins" and "pharmacist" in v.reasons[0].text
    v = evaluate(normalize(raw(["fish oil", "gelatin"], category="supplement", nutrition=Nutrition(sodium_dv=90))),
                 hh(), ["m_ba", "m_leo"])
    assert verdict(v, "m_ba").state == "RIPE" and any(r.code == "supplement.scope" for r in verdict(v, "m_ba").reasons)
    assert verdict(v, "m_leo").state == "DIET"


# ------------------------------------------------------------------------------- headline (8.6)


def test_headline_formats_three_languages():
    np = normalize(raw(["wheat flour", "sesame seeds", "salt"]))
    assert evaluate(np, hh()).headline.text == "Not for Mia: contains sesame. Fine for Linh, Bà and Leo."
    ev = evaluate(np, hh(owner_language="vi"))
    assert ev.headline.text == "Không dùng cho Mia: có mè. Ổn với Linh, Bà và Leo." and ev.headline.language == "vi"
    ev = evaluate(np, hh(owner_language="es"))
    assert ev.headline.text == "No es para Mia: contiene sésamo. Bien para Linh, Bà y Leo."


def test_headline_variants():
    assert evaluate(normalize(raw(["water"])), hh()).headline.text == "Ripe for everyone. Go for it."
    assert evaluate(normalize(raw(["water"])), hh(owner_language="vi")).headline.text == "Hợp với cả nhà. Cứ chọn nhé!"
    assert evaluate(normalize(raw(["rice"], legibility="partial")), hh()).headline.text.startswith("Couldn't confirm for Mia.")
    assert evaluate(normalize(raw(["rice"], legibility="partial")), hh(), ["m_mia"]).headline.text == \
        "Couldn't confirm for Mia. Retake the ingredients?"
    assert evaluate(normalize(raw(["sugar", "gelatin"])), hh(), ["m_leo", "m_linh"]).headline.text == \
        "Not vegan for Leo: gelatin. Fine for Linh."
    ev = evaluate(normalize(raw(["water", "methylisothiazolinone"], category="cosmetic", product_type="leave_on")), hh(), ["m_mia"])
    assert ev.headline.text == "Skip it for Mia: skin allergen."
    ev = evaluate(normalize(raw(["bột mì"], nutrition=Nutrition(sodium_dv=38))), hh(), ["m_ba"])
    assert ev.headline.text == "Easy does it for Bà: high sodium."
    assert evaluate(normalize(raw(["water"])), Household(members=[])).headline.text


def test_headline_at_most_two_sentences_and_never_safe():
    np = normalize(raw(["sesame", "gelatin"], legibility="partial", nutrition=Nutrition(sodium_dv=40)))
    for lang in ("en", "vi", "es"):
        text = evaluate(np, hh(owner_language=lang)).headline.text
        assert sum(text.count(c) for c in ".?!") <= 2, text
        for w in BANNED:
            assert w not in text.lower()


def test_join_names():
    assert join_names(["Linh", "Bà", "Leo"], "en") == "Linh, Bà and Leo"
    assert join_names(["Linh", "Leo"], "vi") == "Linh và Leo"
    assert join_names(["Ana", "Inés"], "es") == "Ana e Inés"
    assert join_names(["Ana", "Leo"], "es") == "Ana y Leo"


# ------------------------------------------------------------------------------- templates


def test_t_fallbacks():
    assert t("headline.all_ripe", "vi-VN") == "Hợp với cả nhà. Cứ chọn nhé!"
    assert t("headline.all_ripe", "fr") == "Ripe for everyone. Go for it."
    assert t("nope.key", "vi", default="Hi {name}", name="Bà") == "Hi Bà"
    assert t("nope.key", "en") == "nope.key"
    assert t("headline.fine", "en", wrong=1) == "Fine for {names}."


def test_templates_complete_and_no_banned_words():
    for key, tr in TEMPLATES.items():
        assert set(tr) == {"en", "vi", "es"}, key
        for lang, s in tr.items():
            low = s.lower()
            for w in BANNED:
                assert w not in low, (key, lang, s)
    kb = get_kb()
    for n in kb.meds:
        for s in n["text"].values():
            assert not any(w in s.lower() for w in BANNED)


def test_ask_answer_vietnamese_example():
    ev = evaluate(normalize(raw(["mì", "muối"], nutrition=Nutrition(sodium_dv=38))), hh())
    assert ask_answer(ev, hh(), ["m_ba"], "vi", "Mì") == "Bà nên ăn ít thôi: nhiều muối (38% nhu cầu mỗi ngày)."
    assert ask_answer(ev, hh(), ["m_leo"], "es", None).startswith("Le va bien a Leo")
    ev2 = evaluate(normalize(raw(["sesame"])), hh())
    assert ask_answer(ev2, hh(), ["m_mia"], "en", None) == "Not for Mia: contains sesame."
    assert "Mia" in ask_answer(ev2, hh(), None, "en", None)
    assert scan_needed_answer("vi").startswith("Pear")
    assert picks_answer(["A", "B", "C"], "en") == "Try A, B and C. They fit everyone."
    assert picks_answer([], "es").startswith("Todavía")


# ------------------------------------------------------------------------------- purity + perf


def test_evaluate_is_deterministic_and_pure():
    np = normalize(raw(["wheat flour", "sesame", "gelatin", "Red 40"], nutrition=Nutrition(sodium_dv=30)))
    before = np.model_dump_json()
    a = evaluate(np, hh(), debug=True)
    b = evaluate(np, hh(), debug=True)
    assert a.model_dump() == b.model_dump()
    assert np.model_dump_json() == before
    assert all(v.score is not None for v in a.verdicts)
    assert a.debug["members"]["m_ba"]["penalties"]
    assert evaluate(np, hh()).debug is None


def _five_member_household() -> Household:
    d = demo_household_dict()
    d["members"].append({"id": "m_pat", "name": "Pat", "pregnant": True, "allergies": [{"id": "milk", "level": "sensitive"}],
                         "conditions": ["kidney_disease"], "diets": ["halal"], "less_of": ["palm_oil"]})
    return Household.model_validate(d)


def test_perf_evaluate_under_20ms_for_five():
    house = _five_member_household()
    np = normalize(raw(["wheat flour", "sugar", "palm oil", "milk powder", "soy lecithin", "salt", "Red 40", "natural flavors",
                        "sodium phosphate", "may contain peanuts"], nutrition=Nutrition(sodium_dv=30, added_sugars_dv=22)))
    evaluate(np, house)
    t0 = time.perf_counter()
    for _ in range(50):
        evaluate(np, house)
    per = (time.perf_counter() - t0) / 50
    assert per < 0.020, f"{per * 1000:.2f} ms"


def test_perf_evaluate_many_50x5_under_100ms():
    house = _five_member_household()
    nps = [normalize(raw([f"ingredient {i}", "sugar", "wheat flour", "salt", "palm oil", "natural flavor"],
                         key=f"test:{i}", nutrition=Nutrition(sodium_dv=i % 45))) for i in range(50)]
    evaluate_many(nps, house)
    t0 = time.perf_counter()
    out = evaluate_many(nps, house)
    dt = time.perf_counter() - t0
    assert len(out) == 50 and all(len(e.verdicts) == 5 for e in out)
    assert dt < 0.100, f"{dt * 1000:.1f} ms"


def test_perf_normalize_fast():
    r = raw(["Wheat flour (wheat flour, niacin, reduced iron)", "sugar", "palm oil", "cocoa butter", "milk powder",
             "soy lecithin", "salt", "natural flavor", "may contain traces of peanuts and tree nuts"],
            ingredients_text="Wheat flour, sugar, palm oil, cocoa butter, milk powder, soy lecithin, salt, natural flavor.")
    normalize(r)
    t0 = time.perf_counter()
    for _ in range(50):
        normalize(r)
    assert (time.perf_counter() - t0) / 50 < 0.010


# ------------------------------------------------------------------------------- route + exports


def test_route_kb_search(app_client):
    r = app_client.get("/v1/kb/search", params={"q": "cacahuate", "lang": "es"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "cacahuate" and body["suggestions"][0]["id"] == "peanut"
    assert body["suggestions"][0]["label"] == "cacahuate"
    r = app_client.get("/v1/kb/search", params={"q": "e", "group": "skin,diets", "limit": 3})
    assert r.status_code == 200 and len(r.json()["suggestions"]) <= 3
    assert all(s["group"] in ("skin", "diets") for s in r.json()["suggestions"])
    r = app_client.get("/v1/kb/search", params={"q": "x", "group": "bogus"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


async def test_route_kb_search_bad_group():
    # The route itself raises the contract error (independent of main.py's handler).
    from core.errors import PearError
    from routes.kb import kb_search

    with pytest.raises(PearError) as e:
        await kb_search(q="x", lang="en", limit=5, group="bogus")
    assert e.value.code == "bad_request" and e.value.status == 400


def test_exports_match_kb():
    from pathlib import Path

    from scripts.export_kb_index import build

    data = build()
    assert data["version"] == get_kb().version
    assert {g for g in data["groups"]} == {"allergies", "conditions", "skin", "diets", "less_of"}
    peanut = next(o for o in data["groups"]["allergies"] if o["id"] == "peanut")
    assert peanut["labels"] == {"en": "peanut", "vi": "đậu phộng", "es": "cacahuate"} and "maní" in peanut["synonyms"]
    ids_file = Path(__file__).resolve().parents[1] / "kb" / "kb_ids.txt"
    assert ids_file.read_text(encoding="utf-8").strip() == get_kb().prompt_ids_text().strip()
    idx = Path(__file__).resolve().parents[2] / "contracts" / "kb_index.json"
    assert json.loads(idx.read_text(encoding="utf-8"))["version"] == get_kb().version


# ------------------------------------------------------------------------------- integrator 2026-10-02


def test_chip_labels_documented():
    """kb/README.md section 3 + contracts/README.md section 6 list every Verdict.label exactly."""
    import re
    from pathlib import Path

    from rules.engine import chip_labels

    api = Path(__file__).resolve().parents[1]
    readme = (api / "kb" / "README.md").read_text(encoding="utf-8")
    section = readme.split("Chip labels (`verdicts[].label`)", 1)[1].split("\n\n", 2)[1]
    documented: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] in ("RIPE", "EASY", "SKIP", "ALLERGY", "DIET", "UNSURE"):
            documented.setdefault(cells[0], set()).update(re.findall(r"`([^`]+)`", cells[1]))
    engine = {k: set(v) for k, v in chip_labels().items()}
    assert documented == engine
    contracts = (api.parent / "contracts" / "README.md").read_text(encoding="utf-8")
    for label in set().union(*engine.values()):
        assert label in contracts, f"contracts/README.md section 6 is missing chip label {label!r}"


def _reasons(np, member: dict, language: str = "en"):
    hh = Household.model_validate({"owner_language": language, "members": [{"id": "m1", "name": "Ana", **member}]})
    ev = evaluate(np, hh)
    return ev.verdicts[0], ev.headline.text


def test_every_signal_reason_names_the_ingredient():
    np = normalize(raw(["Water", "Glycerin", "Methylisothiazolinone"], category="cosmetic", product_type="leave_on"))
    v, _ = _reasons(np, {"skin": ["eczema"]})
    texts = {r.code: r.text for r in v.reasons}
    assert texts["signal.methylisothiazolinone"] == "Skin allergen: Methylisothiazolinone"
    np = normalize(raw(["sugar", "Red 40"]))
    v, _ = _reasons(np, {"age_group": "kid"})
    assert {r.code: r.text for r in v.reasons}["signal.dyes"] == "Synthetic dyes (Red 40)"
    np = normalize(raw(["Water", "Fragrance"], category="cosmetic", product_type="leave_on"))
    v, _ = _reasons(np, {"skin": ["fragrance_sensitivity"]})
    assert {r.code: r.text for r in v.reasons}["signal.fragrance"] == "Fragrance"
    for code_text in [r.text for r in v.reasons]:
        assert not code_text.rstrip().endswith((":", "()"))


def test_overlapping_spans_are_shown_once_longest_first():
    np = normalize(raw(["wheat flour", "sesame seeds", "salt"], contains_text="Contains: wheat, sesame."))
    v, head = _reasons(np, {"allergies": [{"id": "sesame", "level": "strict"}]})
    r = next(r for r in v.reasons if r.code == "allergen.sesame")
    assert r.text == "Contains sesame (sesame seeds)"
    assert "sesame seeds" in r.matched  # highlights keep every printed span
    np = normalize(raw(["bột mì", "bột tôm (2%)"], contains_text="Sản phẩm có chứa: lúa mì, tôm."))
    v, head = _reasons(np, {"diets": ["vegan"]})
    r = next(r for r in v.reasons if r.code == "diet.vegan")
    assert r.text == "Not vegan: bột tôm" and head == "Not vegan for Ana: bột tôm."


def test_product_may_contain_is_only_label_warnings():
    np = normalize(raw(["rolled oats", "natural flavors", "tinh bột", "lecithin"], may_contain_text="May contain milk."))
    assert np.product.may_contain == ["milk", "lactose"]  # milk implies lactose; no gluten/celery/corn/soy
    assert np.product.contains == []
    # possible hits still reach the member who has that allergy, at both levels
    for level in ("strict", "sensitive"):
        for aid, word in (("gluten", "rolled oats"), ("celery", "natural flavors"), ("corn", "tinh bột"), ("soy", "lecithin")):
            v, _ = _reasons(np, {"allergies": [{"id": aid, "level": level}]})
            assert v.state == "ALLERGY", (aid, level)
            r = next(r for r in v.reasons if r.code == f"allergen.{aid}.possible")
            assert word in r.text and r.severity == "hard"
    # an allergen that is both possible and warned about is a may-contain on the product card
    np = normalize(raw(["flour", "sugar"], may_contain_text="May contain wheat."))
    assert "wheat" in np.product.may_contain and "wheat" not in np.product.contains
    # contains wins over may-contain
    np = normalize(raw(["milk", "sugar"], may_contain_text="May contain milk."))
    assert np.product.contains[:1] == ["milk"] and "milk" not in np.product.may_contain


def test_named_non_corn_starch_is_not_possible_corn():
    for s in ["tinh bột khoai mì", "tinh bột sắn", "tinh bột gạo", "rice starch", "pea starch", "tapioca starch",
              "almidón de tapioca", "almidón de papa", "potato starch"]:
        assert "corn" not in {h.kb_id for h in match_text(s)}, s
    for s in ["starch", "modified food starch", "tinh bột", "tinh bột biến tính", "almidón"]:
        assert ("corn", "possible") in {(h.kb_id, h.strength) for h in match_text(s)}, s
    assert "root_vegetables" in {h.kb_id for h in match_text("tinh bột khoai mì")}
    assert {"wheat", "gluten"} <= {h.kb_id for h in match_text("tinh bột mì")}
    for s in ["natural flavor", "natural flavors", "natural flavouring"]:
        assert ("celery", "possible") in {(h.kb_id, h.strength) for h in match_text(s)}, s


def test_every_lane_template_key_is_translated():
    """Keys other lanes call with an English default (say / tr / t(..., default=)) exist in en/vi/es."""
    import re
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    pat = re.compile(r"\b(?:say|tr|t)\(\s*\"([a-z_]+\.[a-z_.]+)\"[^)]*?default=", re.S)
    used: set[str] = set()
    for folder in ("pipeline", "routes", "agent", "core"):
        for f in (api / folder).glob("*.py"):
            used.update(pat.findall(f.read_text(encoding="utf-8")))
    assert {"error.barcode_not_found", "error.too_blurry", "explain.no_reasons", "ask.picks_some"} <= used
    for key in sorted(used):
        assert key in TEMPLATES, f"template {key!r} has only an English default"
        assert all(TEMPLATES[key].get(lg, "").strip() for lg in ("en", "vi", "es")), key


def test_ribonucleotides_are_a_vegan_maybe_and_settled_by_a_cert():
    np = normalize(raw(["wheat flour", "salt", "disodium inosinate", "E627"]))
    v, head = _reasons(np, {"diets": ["vegan"]})
    assert v.state == "DIET" and v.label == "Maybe not vegan"
    assert "disodium inosinate" in next(r for r in v.reasons if r.code == "diet.vegan.maybe").text
    np = normalize(raw(["wheat flour", "disodium guanylate"], labels_tags=["en:vegan"]))
    v, _ = _reasons(np, {"diets": ["vegan"]})
    assert v.state == "RIPE" and [r.code for r in v.reasons] == ["cert.vegan"]
    assert "disodium guanylate" in v.reasons[0].text.casefold()
