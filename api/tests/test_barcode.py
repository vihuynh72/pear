"""POST /v1/scan/barcode end to end + pipeline.products (identify, load, name match, after_scan).

The rules engine (brain lane) is monkeypatched with tiny fakes so these tests verify the data
lane in isolation: fake_normalize flags "sesame" ingredients, fake_evaluate gives ALLERGY to
members allergic to a hit and RIPE to everyone else.
"""

from __future__ import annotations

import sys
import types
from dataclasses import replace

import httpx
import pytest
import respx

from core import background
from core.clients import Clients
from core.config import get_settings
from models.contracts import Headline, Product, Verdict
from models.domain import Evaluation, Hit, NormalizedProduct, RawProduct
from pipeline import embeddings, lookup_off, product_store, products
from tests.conftest import FakeGenai, fake_vector

OFF = "https://world.openfoodfacts.org"
OBF = "https://world.openbeautyfacts.org"
SAL = "https://search.openfoodfacts.org"

CRACKERS = {
    "code": "0012345678905",
    "product_name": "Sesame Crackers",
    "brands": "Example Brand",
    "lang": "en",
    "ingredients_text": "Wheat flour, sesame seeds, salt. May contain peanuts.",
    "allergens_tags": ["en:gluten", "en:sesame-seeds"],
    "traces_tags": ["en:peanuts"],
    "categories_tags": ["en:snacks", "en:crackers"],
    "nutriments": {"sodium_serving": 0.2},
}


def fake_normalize(raw: RawProduct, *, extra_hits: list[Hit] | None = None) -> NormalizedProduct:
    hits = [Hit(kb_id="sesame", matched=i, where="ingredients", ingredient_index=n, position=n)
            for n, i in enumerate(raw.ingredients) if "sesame" in i.lower()]
    product = Product(key=raw.key, name=raw.name, brand=raw.brand, category=raw.category,
                      product_type=raw.product_type, image_url=raw.image_url, source=raw.source,
                      data_confidence=raw.data_confidence, less_sure=raw.legibility != "full",
                      ingredients=raw.ingredients, contains=sorted({h.kb_id for h in hits}),
                      code=raw.code, quantity=raw.quantity, nutrition=raw.nutrition, matched_by=raw.matched_by)
    unmatched = [i for i in raw.ingredients if "sesame" not in i.lower()]
    return NormalizedProduct(product=product, hits=hits + list(extra_hits or []), unmatched=unmatched,
                             legibility=raw.legibility, category_main=raw.category_main, kb_version="test")


def fake_evaluate(np, household, member_ids=None, *, debug=False) -> Evaluation:
    verdicts = []
    for m in household.select(member_ids):
        hit = {a.id for a in m.allergies} & np.kb_ids()
        verdicts.append(Verdict(member_id=m.id, state="ALLERGY" if hit else "RIPE",
                                label="Allergy" if hit else "Ripe", reasons=[], score=100.0))
    return Evaluation(verdicts=verdicts, headline=Headline(text="Not for Mia: contains sesame.",
                                                           language=household.language))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    import pipeline.result as result
    import rules.normalize as rn

    monkeypatch.setattr(rn, "normalize", fake_normalize)
    monkeypatch.setattr(result, "evaluate", fake_evaluate)
    monkeypatch.setattr(products, "_kb_version", lambda: "test")
    product_store.reset_memory()
    embeddings.reset_cache()
    lookup_off.reset_limits()
    products._NEEDS_EMBED.clear()
    yield
    product_store.reset_memory()
    embeddings.reset_cache()
    lookup_off.reset_limits()


@pytest.fixture
def picks_calls(monkeypatch):
    calls: list[str] = []
    mod = types.ModuleType("pipeline.picks")
    mod.start_prefetch = lambda clients, np, household: calls.append(np.product.key)
    monkeypatch.setitem(sys.modules, "pipeline.picks", mod)
    return calls


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


def found(product: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": product["code"], "status": "success", "product": product})


def missing() -> httpx.Response:
    return httpx.Response(404, json={"status": "failure", "result": {"id": "product_not_found"}})


def drain(app_client) -> None:
    app_client.portal.call(background.drain, 5.0)


# ----------------------------------------------------------------------------- route


def test_scan_barcode_miss_then_cache_hit(app_client, household_dict, mock, picks_calls):
    off = mock.get(f"{OFF}/api/v3/product/0012345678905").mock(return_value=found(CRACKERS))
    obf = mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())

    r = app_client.post("/v1/scan/barcode?debug=1", json={"code": "012345678905", "household": household_dict})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["key"] == "off:0012345678905"
    assert body["product"]["source"] == "open_food_facts"
    assert body["product"]["ingredients"] == ["Wheat flour", "sesame seeds", "salt"]
    states = {v["member_id"]: v["state"] for v in body["verdicts"]}
    assert states == {"m_linh": "RIPE", "m_ba": "RIPE", "m_mia": "ALLERGY", "m_leo": "RIPE"}
    assert "lookup" in body["timings_ms"] and "total" in body["timings_ms"]
    assert body["debug"]["cache"]["hit"] is False and body["debug"]["cached"] is False
    assert body["request_id"]
    assert picks_calls == ["off:0012345678905"]
    assert off.call_count == 1

    drain(app_client)
    sp = product_store._MEM["products"]["off:0012345678905"]
    assert sp.embedding and len(sp.embedding) == 768 and sp.kb_ids == ["sesame"]
    assert sp.category_main == "en:crackers" and sp.raw.may_contain_text == "May contain peanuts"
    scans = product_store.memory_scans()
    assert scans and scans[-1]["product_key"] == "off:0012345678905" and scans[-1]["states"]["m_mia"] == "ALLERGY"

    calls_before = (off.call_count, obf.call_count, len(mock.calls))
    r2 = app_client.post("/v1/scan/barcode?debug=1", json={"code": "0012345678905", "household": household_dict})
    assert r2.status_code == 200
    assert r2.json()["debug"]["cache"]["hit"] is True
    assert "lookup" not in r2.json()["timings_ms"]
    assert (off.call_count, obf.call_count, len(mock.calls)) == calls_before  # zero HTTP calls
    assert r2.json()["verdicts"][2]["score"] == 100.0  # debug shows the score
    r3 = app_client.post("/v1/scan/barcode", json={"code": "0012345678905", "household": household_dict})
    assert all(v["score"] is None for v in r3.json()["verdicts"])  # hidden without debug


def test_scan_barcode_not_found(app_client, household_dict, mock, picks_calls):
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=missing())
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    r = app_client.post("/v1/scan/barcode", json={"code": "4006381333931", "household": household_dict})
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["next"] == "take_photo"
    assert err["message"] == "We couldn't find this barcode. Snap the ingredients?"
    assert picks_calls == []


def test_scan_barcode_rate_limited(app_client, household_dict, mock, picks_calls):
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=httpx.Response(429))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=httpx.Response(429))
    r = app_client.post("/v1/scan/barcode", json={"code": "4006381333931", "household": household_dict})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited" and r.json()["error"]["next"] == "retry"


def test_scan_barcode_bad_code(app_client, household_dict, mock):
    r = app_client.post("/v1/scan/barcode", json={"code": "12", "household": household_dict})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_cosmetic_scan_skips_picks(app_client, household_dict, mock, picks_calls):
    lotion = {"code": "3600523614929", "product_name": "Baby Lotion", "lang": "en",
              "ingredients_text": "Aqua, Parfum", "categories_tags": ["en:body-lotions"]}
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=missing())
    mock.get(f"{OBF}/api/v3/product/3600523614929").mock(return_value=found(lotion))
    r = app_client.post("/v1/scan/barcode", json={"code": "3600523614929", "household": household_dict})
    assert r.status_code == 200, r.text
    assert r.json()["product"]["category"] == "cosmetic" and r.json()["product"]["product_type"] == "leave_on"
    assert picks_calls == []


def test_broken_picks_module_never_breaks_a_scan(app_client, household_dict, mock, monkeypatch):
    mod = types.ModuleType("pipeline.picks")

    def boom(*a, **kw):
        raise RuntimeError("picks broke")

    mod.start_prefetch = boom
    monkeypatch.setitem(sys.modules, "pipeline.picks", mod)
    mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=found(CRACKERS))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    r = app_client.post("/v1/scan/barcode", json={"code": "0012345678905", "household": household_dict})
    assert r.status_code == 200


def test_firestore_read_failure_falls_back_to_lookup(app_client, household_dict, mock, picks_calls):
    class Broken:
        def collection(self, name):
            raise RuntimeError("firestore down")

        def get_all(self, refs):
            raise RuntimeError("firestore down")

    app_client.app.state.clients.db = Broken()
    try:
        mock.get(url__startswith=f"{OFF}/api/v3/product/").mock(return_value=found(CRACKERS))
        mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
        r = app_client.post("/v1/scan/barcode", json={"code": "0012345678905", "household": household_dict})
        assert r.status_code == 200, r.text
        assert r.json()["product"]["key"] == "off:0012345678905"
        drain(app_client)
    finally:
        app_client.app.state.clients.db = None


# ----------------------------------------------------------------------------- products module


@pytest.fixture
async def clients():
    fake = FakeGenai()
    s = replace(get_settings(), t_off=0.6, feature_fdc=False, usda_api_key=None)
    async with httpx.AsyncClient() as http:
        yield Clients(settings=s, http=http, genai=fake)


async def test_stale_kb_version_is_renormalized(clients, monkeypatch):
    raw = lookup_off.map_off_product(CRACKERS, "off")
    np = fake_normalize(raw).model_copy(update={"kb_version": "old"})
    enrich_hit = Hit(kb_id="natural_flavors", matched="salt", where="enrich")
    np = np.model_copy(update={"hits": np.hits + [enrich_hit]})
    await product_store.put(clients, products.stored_from(raw, np, None))
    raw2, np2, cached = await products.identify_barcode(clients, "012345678905")
    assert cached and np2.kb_version == "test"
    assert any(h.where == "enrich" for h in np2.hits)  # enrichment survives re-normalization
    await background.drain(3.0)
    sp = await product_store.get(clients, raw.key)
    assert sp.kb_version == "test" and sp.embedding


async def test_load_product_store_then_refetch(clients, mock):
    raw = lookup_off.map_off_product(CRACKERS, "off")
    await product_store.put(clients, products.stored_from(raw, fake_normalize(raw), None))
    assert (await products.load_product(clients, raw.key)).product.name == "Sesame Crackers"
    product_store.reset_memory()
    route = mock.get(f"{OFF}/api/v3/product/0012345678905").mock(return_value=found(CRACKERS))
    np = await products.load_product(clients, "off:0012345678905")
    assert np is not None and np.product.key == "off:0012345678905" and route.call_count == 1
    assert await products.load_product(clients, "photo:abcdef123456") is None
    await background.drain(3.0)
    assert await product_store.get(clients, "off:0012345678905") is not None


async def test_match_by_name_vector_path(clients):
    raw = lookup_off.map_off_product(CRACKERS, "off")
    np = fake_normalize(raw)
    # Store the product with the same vector the query will get, so distance ~0.
    vec = fake_vector("Example Brand Sesame Crackers")
    await product_store.put(clients, products.stored_from(raw, np, vec))
    m = await products.match_by_name(clients, brand="Example Brand", name="Sesame Crackers")
    assert m is not None and m.product.key == raw.key
    assert m.product.less_sure and m.product.ask_for_ingredients
    assert m.product.data_confidence == "medium" and m.product.matched_by == "name"
    # the store keeps the unmarked version
    assert not (await product_store.get(clients, raw.key)).normalized.product.less_sure


async def test_match_by_name_search_path_reads_full_record(clients, mock):
    mock.get(f"{SAL}/search").mock(return_value=httpx.Response(200, json={"hits": [
        {"code": "0099999999999", "product_name": "Unrelated Soup", "brands": ["Other"]},
        {"code": "0012345678905", "product_name": "Sesame Crackers", "brands": ["Example Brand"]},
    ]}))
    full = mock.get(f"{OFF}/api/v3/product/0012345678905").mock(return_value=found(CRACKERS))
    m = await products.match_by_name(clients, brand="Example Brand", name="Sesame Crackers")
    assert m is not None and m.product.key == "off:0012345678905" and full.call_count == 1
    assert m.product.ingredients == ["Wheat flour", "sesame seeds", "salt"]
    assert m.product.less_sure and m.product.matched_by == "name"
    await background.drain(3.0)
    assert await product_store.get(clients, "off:0012345678905") is not None


async def test_match_by_name_no_match(clients, mock):
    mock.get(f"{SAL}/search").mock(return_value=httpx.Response(200, json={"hits": [
        {"code": "0099999999999", "product_name": "Unrelated Soup", "brands": ["Other"]}]}))
    assert await products.match_by_name(clients, name="Sesame Crackers") is None
    assert await products.match_by_name(clients) is None


async def test_after_scan_enriches_and_logs(clients, monkeypatch):
    seen: list[str] = []

    async def enrich_product(c, raw, np):
        seen.append(np.product.key)
        return np.model_copy(update={"hits": np.hits + [Hit(kb_id="natural_flavors", matched="salt",
                                                            where="enrich")]})

    mod = types.ModuleType("pipeline.normalize_unknown")
    mod.enrich_product = enrich_product
    monkeypatch.setitem(sys.modules, "pipeline.normalize_unknown", mod)
    raw = lookup_off.map_off_product(CRACKERS, "off")
    np = fake_normalize(raw)
    ev = fake_evaluate(np, _household())
    products.after_scan(clients, raw=raw, np=np, household=_household(), uid="u1", evaluation=ev, cached=False)
    await background.drain(3.0)
    sp = await product_store.get(clients, raw.key)
    assert seen == [raw.key] and "natural_flavors" in sp.kb_ids and sp.embedding
    assert product_store.memory_scans()[-1]["uid"] == "u1"
    # cached + already embedded: no save, still a scan log
    product_store._MEM["products"][raw.key].normalized = None  # would be visible if re-saved
    products.after_scan(clients, raw=raw, np=np, household=None, uid="u2", evaluation=ev, cached=True)
    await background.drain(3.0)
    assert product_store._MEM["products"][raw.key].normalized is None
    assert product_store.memory_scans()[-1]["uid"] == "u2" and seen == [raw.key]


def _household():
    from models.contracts import Household
    from tests.conftest import demo_household_dict

    return Household.model_validate(demo_household_dict())


# ----------------------------------------------------------------------------- live


@pytest.mark.live
async def test_live_identify_nutella():
    s = get_settings()
    async with httpx.AsyncClient(http2=True, headers={"User-Agent": s.user_agent}) as http:
        clients = Clients(settings=replace(s, t_off=5.0, feature_fdc=False), http=http)
        raw, np, cached = await products.identify_barcode(clients, "3017624010701")
    assert raw.key == "off:3017624010701" and not cached and np.product.ingredients


async def test_cached_record_without_ingredients_is_rechecked(clients, mock, monkeypatch):
    bare = {"code": "0050428335246", "product_name": "Sesame sticks", "lang": "en"}
    raw = lookup_off.map_off_product(bare, "off").model_copy(update={"fetched_at": "2026-10-01T00:00:00Z"})
    await product_store.put(clients, products.stored_from(raw, fake_normalize(raw), None))
    fixed = {**bare, "ingredients_text": "Wheat flour, sesame seeds, salt"}
    route = mock.get(f"{OFF}/api/v3/product/0050428335246").mock(return_value=found(fixed))
    mock.get(url__startswith=f"{OBF}/api/v3/product/").mock(return_value=missing())
    raw2, np2, cached = await products.identify_barcode(clients, "050428335246")
    assert not cached and route.call_count == 1 and raw2.ingredients == ["Wheat flour", "sesame seeds", "salt"]
    # a recent record is trusted (no new lookup)
    recent = raw.model_copy(update={"fetched_at": lookup_off._now_iso()})
    await product_store.put(clients, products.stored_from(recent, fake_normalize(recent), None))
    _, _, cached = await products.identify_barcode(clients, "050428335246")
    assert cached and route.call_count == 1
