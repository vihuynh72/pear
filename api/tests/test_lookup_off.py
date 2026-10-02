"""Open Food Facts / Open Beauty Facts / USDA FDC adapters (respx-mocked, no network)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import httpx
import pytest
import respx

from core.clients import Clients
from core.config import get_settings
from pipeline import lookup_fdc, lookup_off
from pipeline.lookup_off import extract_statements, map_off_product, split_ingredients

OFF = "https://world.openfoodfacts.org"
OBF = "https://world.openbeautyfacts.org"
SAL = "https://search.openfoodfacts.org"
FDC = "https://api.nal.usda.gov/fdc/v1"

RAMEN = {
    "code": "0070662010013",
    "product_name": "Top Ramen - Soy Sauce Flavor",
    "brands": "Nissin, Nissin Top Ramen",
    "quantity": "85 g",
    "lang": "en",
    "image_front_url": "https://images.example/front.jpg",
    "ingredients_text": "ENRICHED FLOUR (WHEAT FLOUR, NIACIN), VEGETABLE OIL (PALM OIL, RICE BRAN OIL), SALT, "
                        "DEHYDRATED SOY SAUCE (MALTODEXTRIN, SALT, WHEAT, SOYBEAN), ONION POWDER. SUGAR",
    "ingredients": [
        {"id": "en:flour", "text": "ENRICHED FLOUR", "ingredients": [
            {"id": "en:wheat-flour", "text": "WHEAT FLOUR"}, {"id": "en:e375", "text": "NIACIN"}]},
        {"id": "en:vegetable-oil", "text": "VEGETABLE OIL", "ingredients": [
            {"id": "en:palm-oil", "text": "PALM OIL"}, {"id": "en:rice-bran-oil", "text": "RICE BRAN OIL"}]},
        {"id": "en:salt", "text": "SALT"},
        {"id": "en:soy-sauce", "text": "SOY SAUCE", "ingredients": [
            {"text": "MALTODEXTRIN"}, {"text": "SALT"}, {"text": "WHEAT"}, {"text": "SOYBEAN"}]},
        {"id": "en:onion", "text": "ONION"},
        {"id": "en:sugar", "text": "SUGAR"},
    ],
    "allergens_tags": ["en:gluten", "en:soybeans"],
    "traces_tags": ["en:peanuts", "en:milk"],
    "additives_tags": ["en:e375"],
    "ingredients_analysis_tags": ["en:palm-oil", "en:vegan-status-unknown"],
    "labels_tags": [],
    "categories_tags": ["en:plant-based-foods", "en:noodles", "en:instant-noodles", "en:instant-noodle-soups"],
    "nutriments": {"sodium_100g": 1.9059, "sodium_serving": 1.62, "saturated-fat_serving": 6,
                   "saturated-fat_100g": 7.06, "added-sugars_serving": 0, "sugars_serving": 0},
    "serving_size": "85g",
    "serving_quantity": 85,
}

NUTELLA = {
    "code": "3017624010701",
    "product_name": "Nutella",
    "product_name_en": "Nutella",
    "brands": "Ferrero",
    "lang": "de",
    "ingredients_text": "sugar, palm oil, hazelnuts, skimmed milk powder, fat reduced cocoa, emulsifier, vanillin",
    "ingredients_text_en": "sugar, palm oil, hazelnuts (13%), skimmed milk powder (8.7%), fat reduced cocoa (7.4%), "
                           "emulsifier: lecithins (soya), vanillin",
    "ingredients": [{"text": "sugar"}, {"text": "palm oil"}, {"text": "hazelnuts"}, {"text": "emulsifier"}],
    "allergens_tags": ["en:nuts"],
    "traces_tags": ["en:milk", "en:nuts", "en:soybeans"],
    "categories_tags": ["en:breakfasts", "en:spreads", "en:hazelnut-spreads", "de:Other"],
    "nutriments": {"sodium_100g": 0.043, "sugars_100g": 56.3, "saturated-fat_100g": 10.6, "added-sugars_100g": 0},
    "nutrition_data_per": "100g",
}

LOTION = {
    "code": "3600523614929",
    "product_name": "Baby Lotion",
    "brands": "Example Baby",
    "lang": "en",
    "ingredients_text": "Aqua, Glycerin, Parfum, Methylisothiazolinone",
    "categories_tags": ["en:body", "en:body-lotions"],
}


def found(product: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": product["code"], "status": "success", "product": product})


def missing(code: str = "x") -> httpx.Response:
    return httpx.Response(404, json={"code": code, "status": "failure",
                                     "result": {"id": "product_not_found"}})


@pytest.fixture(autouse=True)
def _reset_limits():
    lookup_off.reset_limits()
    yield
    lookup_off.reset_limits()


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


@pytest.fixture
async def clients():
    s = replace(get_settings(), t_off=0.6, t_off_max=1.0, feature_fdc=False, usda_api_key=None)
    async with httpx.AsyncClient() as http:
        yield Clients(settings=s, http=http)


# ----------------------------------------------------------------------------- text helpers


def test_split_ingredients_respects_parentheses_and_periods():
    text = ("Ingredients: Enriched flour (wheat flour, niacin, reduced iron), vegetable oil (palm oil; rice bran oil), "
            "salt 2.5%, spices [pepper, garlic]. Onion powder, and sugar.")
    assert split_ingredients(text) == [
        "Enriched flour (wheat flour, niacin, reduced iron)",
        "vegetable oil (palm oil; rice bran oil)",
        "salt 2.5%",
        "spices [pepper, garlic]",
        "Onion powder",
        "sugar",
    ]


def test_extract_statements_moves_contains_and_may_contain():
    text = "Wheat flour, sugar, sesame seeds. Contains: wheat, sesame. May contain peanuts and tree nuts."
    body, contains, may = extract_statements(text)
    assert body == "Wheat flour, sugar, sesame seeds"
    assert contains == ["Contains: wheat, sesame"]
    assert may == ["May contain peanuts and tree nuts"]


def test_extract_statements_leaves_ingredient_prefixes_alone():
    text = ("vegetable oil (contains one or more of the following: canola, palm), "
            "CONTAINS LESS THAN 2% OF SALT, spice")
    body, contains, may = extract_statements(text)
    assert body == text and contains == [] and may == []
    body, contains, may = extract_statements("sugar, cocoa. Made in a facility that also processes milk")
    assert body == "sugar, cocoa" and may == ["Made in a facility that also processes milk"]


# ----------------------------------------------------------------------------- mapping


def test_map_structured_ingredients_and_serving_nutrition():
    raw = map_off_product(RAMEN, "off")
    assert raw.key == "off:0070662010013"
    assert raw.source == "open_food_facts" and raw.category == "food" and raw.matched_by == "barcode"
    assert raw.brand == "Nissin" and raw.name == "Top Ramen - Soy Sauce Flavor"
    assert raw.ingredients == [
        "ENRICHED FLOUR (WHEAT FLOUR, NIACIN)",
        "VEGETABLE OIL (PALM OIL, RICE BRAN OIL)",
        "SALT",
        "SOY SAUCE (MALTODEXTRIN, SALT, WHEAT, SOYBEAN)",
        "ONION",
        "SUGAR",
    ]
    assert raw.ingredients_text == RAMEN["ingredients_text"]
    assert raw.allergens_tags == ["en:gluten", "en:soybeans"]
    assert raw.traces_tags == ["en:peanuts", "en:milk"]
    assert raw.category_main == "en:instant-noodle-soups"
    assert raw.legibility == "full" and raw.data_confidence == "high"
    n = raw.nutrition
    assert n is not None and n.basis == "serving"
    assert n.sodium_mg == 1620.0
    assert n.sodium_dv == pytest.approx(70.4, abs=0.1)
    assert n.sat_fat_g == 6 and n.sat_fat_dv == 30.0
    assert n.added_sugars_g == 0 and n.added_sugars_dv == 0


def test_map_text_when_label_language_not_supported_and_100g_basis():
    raw = map_off_product(NUTELLA, "off")
    # Structured list was parsed from the poorer `de` text: use the English text instead.
    assert "emulsifier: lecithins (soya)" in raw.ingredients
    assert raw.ingredients[2] == "hazelnuts (13%)"
    assert raw.category_main == "en:hazelnut-spreads"
    n = raw.nutrition
    assert n.basis == "100g" and n.sodium_mg == 43.0 and n.sodium_dv == pytest.approx(1.9, abs=0.1)
    assert n.sugars_g == 56.3 and n.sat_fat_dv == 53.0


def test_map_text_split_without_structured_and_salt_fallback():
    p = {"code": "012345678905", "product_name": "Crackers", "lang": "en",
         "ingredients_text": "Wheat flour, sesame seeds (12%), salt. Contains: wheat, sesame.",
         "nutriments": {"salt_serving": 0.5, "caffeine_serving": 0.08}}
    raw = map_off_product(p, "off")
    assert raw.key == "off:0012345678905"
    assert raw.ingredients == ["Wheat flour", "sesame seeds (12%)", "salt"]
    assert raw.contains_text == "Contains: wheat, sesame"
    assert raw.nutrition.sodium_mg == 200.0 and raw.nutrition.caffeine_mg == 80.0


def test_missing_ingredients_is_low_confidence():
    raw = map_off_product({"code": "0049000028911", "product_name": "Diet Coke"}, "off")
    assert raw.ingredients == [] and raw.legibility == "missing" and raw.data_confidence == "low"
    assert raw.nutrition is None


def test_obf_cosmetic_product_type():
    raw = map_off_product(LOTION, "obf")
    assert raw.key == "obf:3600523614929" and raw.source == "open_beauty_facts"
    assert raw.category == "cosmetic" and raw.product_type == "leave_on" and raw.nutrition is None
    sh = map_off_product({**LOTION, "product_name": "Kids Shampoo", "categories_tags": ["en:shampoos"]}, "obf")
    assert sh.product_type == "rinse_off"


# ----------------------------------------------------------------------------- lookup_barcode


async def test_upca_is_looked_up_as_ean13(mock, clients):
    off = mock.get(f"{OFF}/api/v3/product/0070662010013").mock(return_value=found(RAMEN))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    raw = await lookup_off.lookup_barcode(clients, "070662010013")  # 12-digit UPC-A from VisionKit
    assert raw is not None and raw.key == "off:0070662010013"
    assert off.call_count == 1
    assert "fields=" in str(off.calls[0].request.url) and "nutriments" in str(off.calls[0].request.url)


async def test_upce_tries_expanded_code(mock, clients):
    # UPC-E 04252614 expands to UPC-A 042100005264 -> EAN-13 0042100005264
    short = mock.get(f"{OFF}/api/v3/product/04252614").mock(return_value=missing())
    full = mock.get(f"{OFF}/api/v3/product/0042100005264").mock(
        return_value=found({**RAMEN, "code": "0042100005264"}))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    raw = await lookup_off.lookup_barcode(clients, "04252614")
    assert raw is not None and raw.key == "off:0042100005264"
    assert short.call_count == 1 and full.call_count == 1


async def test_race_obf_fast_off_slow_beyond_cap(mock, clients):
    async def slow(request):
        await asyncio.sleep(3)
        return found(RAMEN)

    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(side_effect=slow)
    mock.get(f"{OBF}/api/v3/product/3600523614929").mock(return_value=found(LOTION))
    t0 = time.perf_counter()
    raw = await lookup_off.lookup_barcode(clients, "3600523614929")
    elapsed = time.perf_counter() - t0
    assert raw is not None and raw.key == "obf:3600523614929" and raw.category == "cosmetic"
    assert elapsed < 1.2  # cap 0.6 s, never the 3 s OFF answer


async def test_race_off_wins_when_both_hit(mock, clients):
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=found(RAMEN))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=found({**LOTION, "code": RAMEN["code"]}))
    raw = await lookup_off.lookup_barcode(clients, RAMEN["code"])
    assert raw.key.startswith("off:")


async def test_both_slow_returns_none_within_cap(mock, clients):
    async def slow(request):
        await asyncio.sleep(3)
        return found(RAMEN)

    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(side_effect=slow)
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(side_effect=slow)
    t0 = time.perf_counter()
    assert await lookup_off.lookup_barcode(clients, RAMEN["code"]) is None
    assert time.perf_counter() - t0 < 1.5  # hard cap t_off_max=1.0, never the 3 s answers


async def test_off_slower_than_soft_cap_is_still_a_hit(mock, clients):
    """Cold TLS to OFF: an answer after t_off (0.6) but before t_off_max (1.0) is a hit, not a 404."""
    async def cold(request):
        await asyncio.sleep(0.8)
        return found(RAMEN)

    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(side_effect=cold)
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    raw = await lookup_off.lookup_barcode(clients, RAMEN["code"])
    assert raw is not None and raw.key == f"off:{RAMEN['code']}"


async def test_off_definitive_miss_does_not_wait_for_hard_cap(mock, clients):
    """OFF says "not found" fast and OBF is slow: give up at the soft cap (0.6 s), as before."""
    async def slow(request):
        await asyncio.sleep(3)
        return found(LOTION)

    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=missing())
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(side_effect=slow)
    t0 = time.perf_counter()
    assert await lookup_off.lookup_barcode(clients, RAMEN["code"]) is None
    assert time.perf_counter() - t0 < 0.9


async def test_both_miss_falls_back_to_fdc(mock, clients):
    clients.settings = replace(clients.settings, feature_fdc=True, usda_api_key="test-key")
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=missing())
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    fdc = mock.get(f"{FDC}/foods/search").mock(return_value=httpx.Response(200, json={"foods": [
        {"fdcId": 1, "gtinUpc": "000000000000", "description": "OTHER"},
        {"fdcId": 2, "gtinUpc": "070662010013", "description": "TOP RAMEN CHICKEN", "brandOwner": "NISSIN FOODS",
         "ingredients": "ENRICHED FLOUR (WHEAT FLOUR, NIACIN), PALM OIL, SALT. CONTAINS: WHEAT, SOY.",
         "servingSize": 43.0, "servingSizeUnit": "g",
         "foodNutrients": [{"nutrientNumber": "307", "unitName": "MG", "value": 2000.0},
                           {"nutrientNumber": "606", "unitName": "G", "value": 10.0},
                           {"nutrientNumber": "539", "unitName": "G", "value": 2.0}]},
    ]}))
    raw = await lookup_off.lookup_barcode(clients, "070662010013")
    assert raw is not None and raw.key == "fdc:0070662010013" and raw.source == "usda_fdc"
    assert raw.name == "Top Ramen Chicken" and raw.brand == "Nissin Foods"
    assert raw.ingredients == ["ENRICHED FLOUR (WHEAT FLOUR, NIACIN)", "PALM OIL", "SALT"]
    assert raw.contains_text == "CONTAINS: WHEAT, SOY"
    assert raw.nutrition.basis == "serving" and raw.nutrition.sodium_mg == 860.0
    assert raw.nutrition.sat_fat_g == 4.3
    q = fdc.calls[0].request.url.params
    assert q["query"] == "070662010013" and q["dataType"] == "Branded" and q["api_key"] == "test-key"


async def test_rate_limited_everywhere_raises_throttled(mock, clients):
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=httpx.Response(429))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=httpx.Response(429))
    with pytest.raises(lookup_off.LookupThrottled):
        await lookup_off.lookup_barcode(clients, RAMEN["code"])


async def test_limiter_skips_instead_of_hammering(clients, monkeypatch):
    monkeypatch.setattr(lookup_off, "READS_PER_MIN", 2)
    lim = lookup_off._limiter(OFF, "read")
    assert await lim.acquire() and await lim.acquire()
    assert not await lim.acquire()  # full window, interactive: skip at once
    assert lim.snapshot()["throttled"] == 1


# ----------------------------------------------------------------------------- search


async def test_search_by_name_search_a_licious(mock, clients):
    route = mock.get(f"{SAL}/search").mock(return_value=httpx.Response(200, json={"hits": [
        {"code": "0070662010013", "product_name": "Top Ramen - Soy Sauce Flavor", "brands": ["Nissin"]},
    ], "count": 1}))
    out = await lookup_off.search_by_name(clients, "top ramen soy sauce", brand="Nissin", limit=3)
    assert [r.key for r in out] == ["off:0070662010013"] and out[0].brand == "Nissin"
    assert route.calls[0].request.url.params["q"] == "Nissin top ramen soy sauce"


async def test_search_by_name_falls_back_to_search_pl(mock, clients):
    mock.get(f"{SAL}/search").mock(return_value=httpx.Response(503))
    legacy = mock.get(f"{OFF}/cgi/search.pl").mock(return_value=httpx.Response(200, json={
        "count": 1, "products": [{"code": "0070662010020", "product_name": "Top Ramen Beef Flavor", "brands": "Nissin"}]}))
    out = await lookup_off.search_by_name(clients, "top ramen beef")
    assert [r.key for r in out] == ["off:0070662010020"]
    assert legacy.calls[0].request.url.params["search_terms"] == "top ramen beef"


async def test_search_category_us_products(mock, clients):
    route = mock.get(f"{OFF}/api/v2/search").mock(return_value=httpx.Response(200, json={
        "count": 2, "products": [RAMEN, {**RAMEN, "code": "0041789001017"}]}))
    out = await lookup_off.search_category(clients, "en:instant-noodles", page=2, page_size=100)
    assert len(out) == 2
    p = route.calls[0].request.url.params
    assert p["categories_tags"] == "en:instant-noodles" and p["countries_tags"] == "en:united-states"
    assert p["page"] == "2" and p["page_size"] == "100"


async def test_fetch_raw_json_reports_both_sources(mock, clients):
    mock.get(f"{OFF}/api/v3/product/3017624010701").mock(return_value=found(NUTELLA))
    mock.get(f"{OBF}/api/v3/product/3017624010701").mock(return_value=missing())
    blob = await lookup_off.fetch_raw_json(clients, "3017624010701")
    assert blob["off"]["3017624010701"]["status"] == 200
    assert blob["off"]["3017624010701"]["json"]["product"]["product_name"] == "Nutella"
    assert blob["obf"]["3017624010701"]["status"] == 404


async def test_fdc_without_key_is_skipped(clients):
    assert await lookup_fdc.lookup_gtin(clients, "070662010013") is None


# ----------------------------------------------------------------------------- live


@pytest.mark.live
async def test_live_nutella_from_off():
    s = get_settings()
    async with httpx.AsyncClient(http2=True, headers={"User-Agent": s.user_agent}) as http:
        clients = Clients(settings=replace(s, t_off=5.0, feature_fdc=False), http=http)
        raw = await lookup_off.lookup_barcode(clients, "3017624010701")
    assert raw is not None and raw.key == "off:3017624010701"
    assert raw.name and "nutella" in raw.name.lower()
    assert raw.ingredients and any("hazelnut" in i.lower() for i in raw.ingredients)
    assert raw.nutrition is not None and raw.nutrition.sodium_mg is not None
