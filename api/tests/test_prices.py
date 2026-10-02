"""Tests for pipeline/prices.py and POST /v1/prices (lane L3, P2 price finder).

The store and product loader belong to the data lane (still in progress), so they are faked
by monkeypatching pipeline.prices._store / _products. Gemini goes through the real
pipeline.gemini.generate_raw into the fake_genai fixture, which can return grounded or
ungrounded responses (grounded_urls=...).

Run:  cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_prices.py -q
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.clients import Clients
from core.config import get_settings
from models.contracts import Price, Product
from models.domain import NormalizedProduct
from pipeline import prices

MATCH = "Find the current US retail price"
KEY = "off:0856069005155"
PRODUCT = {"key": KEY, "name": "Almond Flour Crackers Fine Ground Sea Salt", "brand": "Simple Mills",
           "quantity": "4.25 oz", "code": "0856069005155"}


class FakeStore:
    def __init__(self) -> None:
        self.prices: dict[str, dict] = {}
        self.gets = 0
        self.puts = 0

    async def get_price(self, clients, key):
        self.gets += 1
        return self.prices.get(key)

    async def put_price(self, clients, key, price):
        self.puts += 1
        self.prices[key] = dict(price)


class FakeProducts:
    def __init__(self) -> None:
        self.catalog: dict[str, NormalizedProduct] = {}
        self.loads: list[str] = []

    def add(self, p: dict) -> None:
        prod = Product(key=p["key"], name=p["name"], brand=p.get("brand"), quantity=p.get("quantity"),
                       code=p.get("code"), source="open_food_facts")
        self.catalog[p["key"]] = NormalizedProduct(product=prod)

    async def load_product(self, clients, key):
        self.loads.append(key)
        return self.catalog.get(key)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    prices.reset()
    store, products = FakeStore(), FakeProducts()
    products.add(PRODUCT)
    monkeypatch.setattr(prices, "_store", lambda: store)
    monkeypatch.setattr(prices, "_products", lambda: products)
    yield store, products
    prices.reset()


def make_clients(fake_genai, **overrides) -> Clients:
    base = {"feature_prices": True}
    base.update(overrides)
    return Clients(settings=replace(get_settings(), **base), http=None, genai=fake_genai)  # type: ignore[arg-type]


def grounded(fake_genai, text="PRICE|Target|3.99|8 oz"):
    return fake_genai.on(MATCH, text, grounded_urls=["https://www.target.com/p/x"])


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ----------------------------------------------------------------------------- parsing


def test_parse_price_text():
    assert prices.parse_price_text("PRICE|Target|3.99|8 oz") == ("Target", 3.99, "8 oz")
    assert prices.parse_price_text("PRICE|Walmart|$4.68|4.25 oz") == ("Walmart", 4.68, "4.25 oz")
    two = "Here you go\nPRICE|Target|0.10|3 oz\nPRICE|Albertsons|0.49|3 oz\n"
    assert prices.parse_price_text(two) == ("Albertsons", 0.49, "3 oz")  # first IN RANGE line
    assert prices.parse_price_text("PRICE|Costco|150.00|case") is None  # over 100
    assert prices.parse_price_text("PRICE|Target|3,99|8 oz") is None  # not digits and a dot
    assert prices.parse_price_text("NONE") is None
    assert prices.parse_price_text("") is None


def test_prompt_matches_addendum():
    text = prices.build_prompt(PRODUCT)
    assert text.splitlines() == [
        "Find the current US retail price for this exact product and package size.",
        "Product: Simple Mills Almond Flour Crackers Fine Ground Sea Salt, 4.25 oz. UPC 0856069005155.",
        "Search US grocery, drugstore and big-box retailers.",
        "Reply with at most 2 lines, each exactly:",
        "PRICE|<retailer>|<price in USD, digits and a dot only>|<package size>",
        "If you can't find this exact product and size, reply exactly: NONE",
    ]
    assert "Product: Crackers, size not listed. UPC unknown." in prices.build_prompt({"key": "k", "name": "Crackers"})


# ----------------------------------------------------------------------------- find_price


async def test_find_price_grounded(fake_genai):
    grounded(fake_genai)
    clients = make_clients(fake_genai)
    p = await prices.find_price(clients, PRODUCT)
    assert p is not None
    assert p["amount"] == 3.99 and p["currency"] == "USD" and p["retailer"] == "Target"
    assert p["size"] == "8 oz" and p["source"] == "google_search"
    assert p["source_url"] == "https://www.target.com/p/x" and p["source_title"] == "www.target.com"
    assert p["checked_at"].endswith("Z")
    assert datetime.now(UTC) - datetime.fromisoformat(p["checked_at"].replace("Z", "+00:00")) < timedelta(minutes=1)
    Price.model_validate(p)  # contract-shaped
    call = fake_genai.calls[0]
    cfg = call["config"]
    assert call["model"] == clients.settings.model_price
    assert cfg.tools and cfg.tools[0].google_search is not None
    assert cfg.response_mime_type is None and cfg.response_json_schema is None  # grounding needs text out
    assert cfg.max_output_tokens == 512
    assert "UPC 0856069005155" in call["contents"]


async def test_ungrounded_answer_is_dropped(fake_genai):
    fake_genai.on(MATCH, "PRICE|Target|3.99|8 oz")  # no grounding chunks
    assert await prices.find_price(make_clients(fake_genai), PRODUCT) is None


async def test_none_reply_and_out_of_range_are_dropped(fake_genai):
    grounded(fake_genai, "NONE")
    assert await prices.find_price(make_clients(fake_genai), PRODUCT) is None
    fake_genai.handlers.clear()
    grounded(fake_genai, "PRICE|Target|0.10|8 oz")
    assert await prices.find_price(make_clients(fake_genai), PRODUCT) is None


async def test_gemini_error_returns_none(fake_genai):
    fake_genai.on(MATCH, RuntimeError("RESOURCE_EXHAUSTED"))
    assert await prices.find_price(make_clients(fake_genai), PRODUCT) is None


async def test_source_prefers_retailer_chunk(fake_genai):
    fake_genai.on(MATCH, "PRICE|Walmart|3.97|8.5 oz",
                  grounded_urls=["https://www.target.com/p/a", "https://www.walmart.com/ip/b"])
    p = await prices.find_price(make_clients(fake_genai), PRODUCT)
    assert p["source_url"] == "https://www.walmart.com/ip/b"


# ----------------------------------------------------------------------------- cache + for_keys


async def test_cache_hit_within_24h_skips_gemini(fakes, fake_genai):
    store, products = fakes
    store.prices[KEY] = {"amount": 4.99, "currency": "USD", "retailer": "Target", "size": "4.25 oz",
                         "source_title": "target.com", "source_url": "https://t", "search_entry_html": None,
                         "checked_at": iso(datetime.now(UTC) - timedelta(hours=23)),
                         "source": "google_search", "product_key": KEY}  # extra field is ignored
    resp = await prices.for_keys(make_clients(fake_genai), [KEY])
    assert resp.prices[KEY] is not None and resp.prices[KEY].amount == 4.99
    assert fake_genai.calls == [] and products.loads == []  # cache read before any product load
    assert prices.stats()["counters"]["cache_hits"] == 1


async def test_stale_cache_refreshes_and_writes_back(fakes, fake_genai):
    store, products = fakes
    store.prices[KEY] = {"amount": 4.99, "currency": "USD", "retailer": "Target",
                         "checked_at": iso(datetime.now(UTC) - timedelta(hours=25)), "source": "google_search"}
    grounded(fake_genai)
    resp = await prices.for_keys(make_clients(fake_genai), [KEY])
    assert resp.prices[KEY].amount == 3.99
    assert len(fake_genai.calls) == 1 and products.loads == [KEY]
    assert store.prices[KEY]["amount"] == 3.99 and store.puts == 1


async def test_ungrounded_is_null_and_not_cached(fakes, fake_genai):
    store, _ = fakes
    fake_genai.on(MATCH, "PRICE|Target|3.99|8 oz")
    resp = await prices.for_keys(make_clients(fake_genai), [KEY])
    assert resp.prices == {KEY: None}
    assert store.puts == 0


async def test_start_then_for_keys_reuses_inflight(fakes, fake_genai):
    _, products = fakes
    grounded(fake_genai)
    clients = make_clients(fake_genai)
    prices.start(clients, PRODUCT)
    prices.start(clients, PRODUCT)
    assert len(prices.PRICE_INFLIGHT) == 1
    resp = await prices.for_keys(clients, [KEY, "off:missing", KEY])
    assert list(resp.prices) == [KEY, "off:missing"]
    assert resp.prices[KEY].retailer == "Target" and resp.prices["off:missing"] is None
    assert products.loads == ["off:missing"]  # the started key never needed a load
    again = await prices.for_keys(clients, [KEY])
    assert again.prices[KEY].amount == 3.99
    assert len(fake_genai.calls) == 1  # reused, not looked up twice
    assert set(resp.timings_ms) == {"wait", "total"} and resp.request_id is not None
    st = prices.stats()
    assert st["found"] == 1 and st["none"] == 1 and st["count"] == 2


async def test_for_keys_waits_at_most_t_price(fakes, fake_genai, monkeypatch):
    store, _ = fakes

    async def slow(clients, p, timeout_s=None):
        await asyncio.sleep(0.5)
        return {"amount": 2.5, "currency": "USD", "retailer": "Walmart", "size": "3 oz",
                "source_title": "walmart.com", "source_url": "https://w", "search_entry_html": None,
                "checked_at": iso(datetime.now(UTC)), "source": "google_search"}

    monkeypatch.setattr(prices, "find_price", slow)
    clients = make_clients(fake_genai, t_price=0.05)
    t = time.perf_counter()
    resp = await prices.for_keys(clients, [KEY])
    assert time.perf_counter() - t < 0.4
    assert resp.prices == {KEY: None}
    await prices.PRICE_INFLIGHT[KEY]  # it keeps running in the background and caches
    assert store.prices[KEY]["amount"] == 2.5
    resp = await prices.for_keys(clients, [KEY])
    assert resp.prices[KEY].amount == 2.5


async def test_flag_off_and_non_us(fakes, fake_genai):
    clients = make_clients(fake_genai, feature_prices=False)
    prices.start(clients, PRODUCT)
    assert prices.PRICE_INFLIGHT == {}
    resp = await prices.for_keys(clients, [KEY])
    assert resp.prices == {KEY: None}
    resp = await prices.for_keys(make_clients(fake_genai), [KEY], country="VN")
    assert resp.prices == {KEY: None}
    assert fake_genai.calls == []


async def test_with_real_memory_product_store(fake_genai, monkeypatch):
    """Interface check against the data lane's memory price cache (skipped until it imports)."""
    store_mod = pytest.importorskip("pipeline.product_store")
    store_mod.reset_memory()
    monkeypatch.setattr(prices, "_store", lambda: store_mod)
    grounded(fake_genai)
    clients = make_clients(fake_genai)
    resp = await prices.for_keys(clients, [KEY])
    assert resp.prices[KEY].amount == 3.99
    assert (await store_mod.get_price(clients, KEY))["retailer"] == "Target"
    prices.reset()  # forget in-process tasks: the next answer must come from the store cache
    resp = await prices.for_keys(clients, [KEY])
    assert resp.prices[KEY].amount == 3.99 and len(fake_genai.calls) == 1
    store_mod.reset_memory()


# ----------------------------------------------------------------------------- route


def test_route_prices(fakes, app_client, fake_genai):
    grounded(fake_genai)
    r = app_client.post("/v1/prices?debug=1", json={"product_keys": [KEY, "off:0222222222222"], "country": "US"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["prices"]["off:0222222222222"] is None
    got = data["prices"][KEY]
    assert got["amount"] == 3.99 and got["retailer"] == "Target" and got["source"] == "google_search"
    assert set(got) == set(Price.model_fields)
    assert "wait" in data["timings_ms"] and "total" in data["timings_ms"]
    assert "price_finder" in r.headers["x-pear-debug"]


def test_route_prices_flag_off(fakes, app_client, fake_genai):
    clients = app_client.app.state.clients
    clients.settings = replace(clients.settings, feature_prices=False)
    r = app_client.post("/v1/prices?debug=1", json={"product_keys": [KEY]})
    assert r.status_code == 200
    assert r.json()["prices"] == {KEY: None}
    assert "feature_prices off" in r.headers["x-pear-debug"]
    assert fake_genai.calls == []
