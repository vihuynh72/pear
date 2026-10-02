"""Tests for pipeline/picks.py and POST /v1/picks (lane L3, picks).

Sibling lanes (pipeline.product_store / embeddings / products and the L4 rules engine) are
written in parallel, so every dependency is faked here and injected by monkeypatching the
lazy accessors in pipeline.picks (_store, _embeddings, _products, _kb_version) and
rules.engine.evaluate_many. Embeddings still go through pipeline.gemini.embed_texts into the
fake_genai fixture (deterministic vectors), so the Gemini wrapper is exercised for real.

Run:  cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_picks.py -q
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, replace

import pytest

from core.clients import Clients
from core.config import get_settings
from models.contracts import Headline, Household, Product, Verdict
from models.domain import Evaluation, NormalizedProduct, RawProduct, StoredProduct
from pipeline import gemini, picks, prices
from tests.conftest import demo_household_dict, fake_vector

LABELS = {"RIPE": "Ripe", "EASY": "Easy does it", "SKIP": "Skip it", "ALLERGY": "Allergy",
          "DIET": "Not for diet", "UNSURE": "Not sure"}


# ----------------------------------------------------------------------------- builders


def make_np(key: str, name: str, *, category: str = "food", category_main: str | None = "en:crackers",
            code: str | None = None, ingredients: tuple[str, ...] = ("wheat flour", "salt"),
            brand: str = "Acme") -> NormalizedProduct:
    code = code or key.split(":")[-1]
    product = Product(key=key, name=name, brand=brand, category=category, source="open_food_facts",
                      ingredients=list(ingredients), code=code, quantity="8 oz",
                      image_url=f"https://images.example/{code}.jpg")
    return NormalizedProduct(product=product, category_main=category_main, kb_version="test",
                             legibility="full" if ingredients else "missing")


def emb_text(np: NormalizedProduct) -> str:
    p = np.product
    return f"{p.brand or ''} {p.name or ''}. {np.category_main or ''}. {', '.join(p.ingredients[:8])}"


def make_sp(np: NormalizedProduct, embedding: list[float] | None) -> StoredProduct:
    p = np.product
    raw = RawProduct(key=p.key, source=p.source, code=p.code, name=p.name, brand=p.brand,
                     category=p.category, ingredients=list(p.ingredients),
                     category_main=np.category_main, quantity=p.quantity)
    return StoredProduct(key=p.key, source=p.source, category=p.category, category_main=np.category_main,
                         kb_version=np.kb_version, raw=raw, normalized=np, embedding=embedding)


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / (na * nb) if na and nb else 1.0


# ----------------------------------------------------------------------------- fakes


class FakeStore:
    """Stands in for pipeline.product_store (memory store semantics)."""

    def __init__(self) -> None:
        self.products: dict[str, StoredProduct] = {}
        self.prices: dict[str, dict] = {}
        self.nearest_error: Exception | None = None
        self.nearest_delay = 0.0
        self.nearest_calls = 0
        self.category_calls = 0

    def add(self, np: NormalizedProduct, *, embed: bool = True) -> NormalizedProduct:
        self.products[np.product.key] = make_sp(np, fake_vector(emb_text(np)) if embed else None)
        return np

    async def get(self, clients, key):
        return self.products.get(key)

    async def find_nearest(self, clients, vector, *, category="food", limit=50):
        self.nearest_calls += 1
        if self.nearest_delay:
            await asyncio.sleep(self.nearest_delay)
        if self.nearest_error is not None:
            raise self.nearest_error
        rows = [(sp, cosine_distance(vector, sp.embedding)) for sp in self.products.values()
                if sp.category == category and sp.embedding]
        rows.sort(key=lambda r: r[1])
        return rows[:limit]

    async def by_category_main(self, clients, category_main, *, limit=60):
        self.category_calls += 1
        return [sp for sp in self.products.values() if sp.category_main == category_main][:limit]

    async def get_price(self, clients, key):
        return self.prices.get(key)

    async def put_price(self, clients, key, price):
        self.prices[key] = dict(price)


class FakeEmbeddings:
    """Stands in for pipeline.embeddings; vectors come from fake_genai via pipeline.gemini."""

    def __init__(self) -> None:
        self.calls = 0

    def embedding_text(self, np):
        return emb_text(np)

    async def embed_product(self, clients, np):
        self.calls += 1
        return (await gemini.embed_texts(clients, [emb_text(np)]))[0]

    async def embed_query(self, clients, text):
        self.calls += 1
        return (await gemini.embed_texts(clients, [text], task_type="RETRIEVAL_QUERY"))[0]


class FakeProducts:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.loads: list[str] = []

    async def load_product(self, clients, key):
        self.loads.append(key)
        sp = self.store.products.get(key)
        return sp.normalized if sp else None

    async def match_by_name(self, clients, *, brand=None, name=None, query=None):
        for sp in self.store.products.values():
            if query and query.lower() in (sp.normalized.product.name or "").lower():
                return sp.normalized
        return None


class FakeEngine:
    """rules.engine.evaluate_many with states we control: states[key] = state | {member: state}."""

    def __init__(self) -> None:
        self.states: dict[str, str | dict[str, str]] = {}
        self.calls = 0

    def evaluate_many(self, nps, household, member_ids=None):
        self.calls += 1
        out = []
        for np in nps:
            spec = self.states.get(np.product.key, "RIPE")
            verdicts = []
            for m in household.select(member_ids):
                st = spec.get(m.id, "RIPE") if isinstance(spec, dict) else spec
                verdicts.append(Verdict(member_id=m.id, state=st, label=LABELS[st], score=90.0))
            out.append(Evaluation(verdicts=verdicts, headline=Headline(text="-", language="en")))
        return out


@dataclass
class Env:
    store: FakeStore
    emb: FakeEmbeddings
    products: FakeProducts
    engine: FakeEngine


@pytest.fixture(autouse=True)
def env(monkeypatch) -> Env:
    picks.reset()
    prices.reset()
    e = Env(store=FakeStore(), emb=FakeEmbeddings(), products=None, engine=FakeEngine())  # type: ignore[arg-type]
    e.products = FakeProducts(e.store)
    monkeypatch.setattr(picks, "_store", lambda: e.store)
    monkeypatch.setattr(picks, "_embeddings", lambda: e.emb)
    monkeypatch.setattr(picks, "_products", lambda: e.products)
    monkeypatch.setattr(picks, "_kb_version", lambda: "test")
    monkeypatch.setattr(prices, "_store", lambda: e.store)
    monkeypatch.setattr(prices, "_products", lambda: e.products)
    monkeypatch.setattr("rules.engine.evaluate_many", e.engine.evaluate_many)
    yield e
    picks.reset()
    prices.reset()


def make_clients(fake_genai, **overrides) -> Clients:
    base = {"feature_picks": True, "feature_vector": True, "feature_prices": False}
    base.update(overrides)
    return Clients(settings=replace(get_settings(), **base), http=None, genai=fake_genai)  # type: ignore[arg-type]


@pytest.fixture
def hh() -> Household:
    return Household.model_validate(demo_household_dict())


SCANNED = "off:0000000000100"


def seed_catalog(e: Env) -> NormalizedProduct:
    """Scanned sesame crackers + candidates covering every ranking rule."""
    s = e.store
    scanned = s.add(make_np(SCANNED, "Sesame Crackers"))
    s.add(make_np("off:0000000000201", "Plain Crackers"))                                # same, RIPE
    s.add(make_np("off:0000000000202", "Wheat Crackers"))                                # same, EASY
    s.add(make_np("off:0000000000203", "Rice Crackers Cakes", category_main="en:rice-cakes"))  # other, RIPE
    s.add(make_np("off:0000000000204", "Peanut Crackers"))                               # ALLERGY
    s.add(make_np("off:0000000000205", "Honey Crackers"))                                # DIET
    s.add(make_np("off:0000000000206", "Mystery Crackers"))                              # UNSURE
    s.add(make_np("off:0000000000207", "Salty Crackers"))                                # SKIP
    s.add(make_np("off:0000000000208", "Crackers No List", ingredients=()))              # no ingredients
    s.add(make_np("obf:0000000000100", "Sesame Crackers Copy", code="0000000000100"))  # same code
    e.engine.states.update({
        "off:0000000000202": {"m_ba": "EASY"},
        "off:0000000000204": {"m_mia": "ALLERGY"},
        "off:0000000000205": {"m_leo": "DIET"},
        "off:0000000000206": {"m_mia": "UNSURE"},
        "off:0000000000207": {"m_ba": "SKIP"},
    })
    return scanned


EXPECTED = ["off:0000000000201", "off:0000000000202", "off:0000000000203"]


def keys_of(resp) -> list[str]:
    return [i.product.key for i in resp.items]


# ----------------------------------------------------------------------------- ranking


def test_rank_rules_drop_order_and_scanned_excluded(env, hh):
    scanned = seed_catalog(env)
    sp = env.store.products
    # Distances chosen so distance alone would give the WRONG order: the rules must win.
    pairs = [(sp[SCANNED], 0.0), (sp["obf:0000000000100"], 0.01), (sp["off:0000000000203"], 0.02),
             (sp["off:0000000000204"], 0.03), (sp["off:0000000000202"], 0.05),
             (sp["off:0000000000205"], 0.06), (sp["off:0000000000206"], 0.07),
             (sp["off:0000000000207"], 0.08), (sp["off:0000000000208"], 0.09),
             (sp["off:0000000000201"], 0.40)]
    diag: dict = {}
    items = picks.rank(scanned, pairs, hh, diag)
    assert [i["product"].key for i in items] == EXPECTED
    # same category + all RIPE first; same-category EASY beats other-category RIPE
    assert [(i["same_category"], i["all_ripe"]) for i in items] == [(True, True), (True, False), (False, True)]
    dropped = diag["dropped"]
    assert dropped["scanned_or_duplicate"] == 2
    assert dropped["no_ingredients"] == 1
    for st in ("ALLERGY", "DIET", "UNSURE", "SKIP"):
        assert dropped[st] == 1
    # every verdict for every member travels with the pick
    assert {v.member_id for v in items[1]["verdicts"]} == {"m_linh", "m_ba", "m_mia", "m_leo"}
    assert {v.member_id: v.state for v in items[1]["verdicts"]}["m_ba"] == "EASY"


def test_rank_distance_breaks_ties_and_top3(env, hh):
    scanned = env.store.add(make_np(SCANNED, "Sesame Crackers"))
    nps = [env.store.add(make_np(f"off:00000000003{i:02d}", f"Cracker {i}")) for i in range(6)]
    sp = env.store.products
    dists = [0.6, 0.2, 0.5, 0.1, 0.4, 0.3]
    items = picks.rank(scanned, [(sp[n.product.key], d) for n, d in zip(nps, dists, strict=True)], hh)
    assert [i["distance"] for i in items] == [0.1, 0.2, 0.3]


def test_rank_drops_non_food_and_budget(env, hh):
    scanned = env.store.add(make_np(SCANNED, "Sesame Crackers"))
    lotion = env.store.add(make_np("obf:0000000000900", "Crackers Lotion", category="cosmetic"))
    many = [env.store.add(make_np(f"off:00000000004{i:02d}", f"Cracker {i}")) for i in range(50)]
    hh5 = hh.model_copy(update={"members": [*hh.members, hh.members[0].model_copy(update={"id": "m_5"})]})
    sp = env.store.products
    diag: dict = {}
    items = picks.rank(scanned, [(sp[lotion.product.key], 0.0)] + [(sp[n.product.key], 0.5) for n in many],
                       hh5, diag)
    assert len(items) == 3 and all(i["product"].category == "food" for i in items)
    assert diag["dropped"]["not_food"] == 1
    assert diag["evaluated"] == 50
    assert diag["rank_ms"] < 100  # addendum 7 target: 50 candidates x 5 members


# ----------------------------------------------------------------------------- prefetch + get


async def test_prefetch_then_get_is_prefetched_and_fast(env, fake_genai, hh):
    scanned = seed_catalog(env)
    clients = make_clients(fake_genai)
    t = time.perf_counter()
    picks.start_prefetch(clients, scanned, hh)
    assert time.perf_counter() - t < 0.05  # returns at once
    picks.start_prefetch(clients, scanned, hh)  # duplicate start reuses the task
    assert len(picks.INFLIGHT) == 1
    await next(iter(picks.INFLIGHT.values()))

    t = time.perf_counter()
    resp = await picks.get(clients, SCANNED, demo_household_dict())  # dict household -> same key
    assert time.perf_counter() - t < 0.05
    assert resp.prefetched is True
    assert keys_of(resp) == EXPECTED
    assert set(resp.timings_ms) >= {"wait", "total"} and resp.timings_ms["wait"] < 50
    assert resp.items[0].product.name == "Plain Crackers"
    assert resp.items[0].product.image_url == "https://images.example/0000000000201.jpg"
    assert env.products.loads == []  # no cold load
    # scanned product was stored with an embedding -> no embed call needed
    assert env.emb.calls == 0 and env.store.nearest_calls == 1

    st = picks.stats()
    assert st["count"] == 1 and st["done"] == 1 and st["newest"][0]["items"] == EXPECTED
    assert st["newest"][0]["path"] == "vector"


async def test_vector_path_embeds_via_gemini_when_not_stored(env, fake_genai, hh):
    seed_catalog(env)
    scanned = env.store.products.pop(SCANNED).normalized  # fresh scan, not stored yet
    clients = make_clients(fake_genai)
    result = await picks.compute(clients, scanned, hh)
    assert result["path"] == "vector"
    assert result["diag"]["query_vector"] == "embedded"
    assert fake_genai.embed_calls and fake_genai.embed_calls[0]["model"] == clients.settings.model_embedding
    assert [i["product"].key for i in result["items"]] == EXPECTED


async def test_start_prefetch_ignores_non_food_and_flag_off(env, fake_genai, hh):
    env.store.add(make_np(SCANNED, "Sesame Crackers"))
    lotion = make_np("obf:0000000000900", "Lotion", category="cosmetic")
    picks.start_prefetch(make_clients(fake_genai), lotion, hh)
    picks.start_prefetch(make_clients(fake_genai, feature_picks=False),
                         env.store.products[SCANNED].normalized, hh)
    assert picks.INFLIGHT == {}


async def test_cold_get_then_reuse(env, fake_genai, hh):
    seed_catalog(env)
    clients = make_clients(fake_genai)
    first = await picks.get(clients, SCANNED, hh)
    assert first.prefetched is False
    assert keys_of(first) == EXPECTED
    assert env.products.loads == [SCANNED]
    second = await picks.get(clients, SCANNED, hh)
    assert second.prefetched is True and keys_of(second) == EXPECTED
    assert env.engine.calls == 1  # computed once


async def test_member_ids_filter_chips_but_picks_fit_everyone(env, fake_genai, hh):
    seed_catalog(env)
    resp = await picks.get(make_clients(fake_genai), SCANNED, hh, ["m_mia"])
    assert keys_of(resp) == EXPECTED  # still dropped ALLERGY/DIET/... for the others
    assert all([v.member_id for v in it.verdicts] == ["m_mia"] for it in resp.items)


async def test_cold_get_missing_product_is_empty(env, fake_genai, hh):
    resp = await picks.get(make_clients(fake_genai), "off:404", hh)
    assert resp.items == [] and resp.prefetched is False


async def test_cold_get_store_error_is_empty(env, fake_genai, hh, monkeypatch):
    async def boom(clients, key):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(env.products, "load_product", boom)
    resp = await picks.get(make_clients(fake_genai), SCANNED, hh)
    assert resp.items == [] and resp.prefetched is False


async def test_vector_failure_falls_back_to_category(env, fake_genai, hh):
    scanned = seed_catalog(env)
    env.store.nearest_error = RuntimeError("FAILED_PRECONDITION: vector index not ready")
    result = await picks.compute(make_clients(fake_genai), scanned, hh)
    assert result["path"] == "category"
    assert "index not ready" in result["diag"]["vector_skip"]
    assert env.store.category_calls == 1
    # category_main fallback only sees en:crackers -> the rice cakes are not candidates
    assert [i["product"].key for i in result["items"]] == EXPECTED[:2]


async def test_vector_empty_falls_back_and_flag_off_skips_vector(env, fake_genai, hh):
    scanned = seed_catalog(env)
    for sp in env.store.products.values():
        sp.embedding = None  # index has nothing yet
    clients = make_clients(fake_genai)
    result = await picks.compute(clients, scanned, hh)
    assert result["path"] == "category" and result["diag"]["vector_skip"] == "no neighbours"

    env.store.nearest_calls = 0
    result = await picks.compute(make_clients(fake_genai, feature_vector=False), scanned, hh)
    assert result["path"] == "category" and env.store.nearest_calls == 0


async def test_vector_none_fit_tries_category_shelf(env, fake_genai, hh):
    scanned = seed_catalog(env)
    env.engine.states["off:0000000000203"] = {"m_mia": "ALLERGY"}
    for key in ("off:0000000000201", "off:0000000000202"):  # fitting ones are not in the index
        env.store.products[key].embedding = None
    result = await picks.compute(make_clients(fake_genai), scanned, hh)
    assert result["path"] == "vector+category"
    assert [i["product"].key for i in result["items"]] == EXPECTED[:2]


async def test_no_category_main_and_no_vector_is_empty(env, fake_genai, hh):
    scanned = make_np(SCANNED, "Mystery", category_main=None)
    env.store.nearest_error = RuntimeError("down")
    result = await picks.compute(make_clients(fake_genai), scanned, hh)
    assert result["path"] == "none" and result["items"] == []


async def test_timeout_returns_empty_then_task_finishes(env, fake_genai, hh):
    seed_catalog(env)
    env.store.nearest_delay = 0.4
    clients = make_clients(fake_genai, t_picks_wait=0.05)
    t = time.perf_counter()
    resp = await picks.get(clients, SCANNED, hh)
    assert time.perf_counter() - t < 0.3
    assert resp.items == [] and resp.prefetched is False
    assert picks.stats()["running"] == 1  # shielded: the computation keeps going
    await next(iter(picks.INFLIGHT.values()))
    resp = await picks.get(clients, SCANNED, hh)
    assert resp.prefetched is True and keys_of(resp) == EXPECTED


async def test_failed_compute_is_empty_and_retried(env, fake_genai, hh, monkeypatch):
    seed_catalog(env)
    clients = make_clients(fake_genai)
    real = env.engine.evaluate_many

    def broken(*a, **k):
        raise RuntimeError("engine bug")

    monkeypatch.setattr("rules.engine.evaluate_many", broken)
    resp = await picks.get(clients, SCANNED, hh)
    assert resp.items == []
    assert picks.stats()["failed"] == 1
    monkeypatch.setattr("rules.engine.evaluate_many", real)
    resp = await picks.get(clients, SCANNED, hh)  # failed task is not reused
    assert keys_of(resp) == EXPECTED and resp.prefetched is False


async def test_prices_started_for_each_pick(env, fake_genai, hh, monkeypatch):
    scanned = seed_catalog(env)
    started: list[dict] = []
    monkeypatch.setattr(prices, "start", lambda clients, p: started.append(p))
    await picks.compute(make_clients(fake_genai, feature_prices=True), scanned, hh)
    assert [p["key"] for p in started] == EXPECTED
    assert started[0] == {"key": "off:0000000000201", "name": "Plain Crackers", "brand": "Acme",
                          "quantity": "8 oz", "code": "0000000000201"}
    started.clear()
    picks.reset()
    await picks.compute(make_clients(fake_genai, feature_prices=False), scanned, hh)
    assert started == []


async def test_get_flag_off_is_empty(env, fake_genai, hh):
    seed_catalog(env)
    resp = await picks.get(make_clients(fake_genai, feature_picks=False), SCANNED, hh)
    assert resp.items == [] and resp.prefetched is False and picks.INFLIGHT == {}


async def test_for_question(env, fake_genai, hh):
    seed_catalog(env)
    clients = make_clients(fake_genai)
    resp = await picks.for_question(clients, {"last_product_key": SCANNED, "household": demo_household_dict()})
    assert resp is not None and keys_of(resp) == EXPECTED
    by_name = await picks.for_question(clients, {"product_query": "sesame crackers",
                                                 "household": demo_household_dict()})
    assert by_name is not None and keys_of(by_name) == EXPECTED
    assert await picks.for_question(clients, {"household": demo_household_dict()}) is None


def test_hh_hash_stable():
    d = demo_household_dict()
    assert picks.hh_hash(d) == picks.hh_hash(Household.model_validate(d))
    d2 = demo_household_dict()
    d2["members"][0]["diets"] = ["vegan"]
    assert picks.hh_hash(d) != picks.hh_hash(d2)


def test_inflight_trimmed_to_newest(monkeypatch):
    monkeypatch.setattr(picks, "MAX_INFLIGHT", 3)

    class Done:
        def done(self):
            return True

    for i in range(5):
        picks._register(f"k{i}", Done())  # type: ignore[arg-type]
    assert list(picks.INFLIGHT) == ["k2", "k3", "k4"]


async def test_with_real_memory_product_store(env, fake_genai, hh, monkeypatch):
    """Interface check against the data lane's memory store (skipped until it imports)."""
    store_mod = pytest.importorskip("pipeline.product_store")
    store_mod.reset_memory()
    seed_catalog(env)
    clients = make_clients(fake_genai)
    for sp in env.store.products.values():
        assert await store_mod.put(clients, sp)
    monkeypatch.setattr(picks, "_store", lambda: store_mod)
    result = await picks.compute(clients, env.store.products[SCANNED].normalized, hh)
    assert result["path"] == "vector" and [i["product"].key for i in result["items"]] == EXPECTED
    for sp in env.store.products.values():  # no vectors -> category_main fallback
        await store_mod.put(clients, sp.model_copy(update={"embedding": None}))
    result = await picks.compute(clients, env.store.products[SCANNED].normalized, hh)
    assert result["path"] == "category" and [i["product"].key for i in result["items"]] == EXPECTED[:2]
    store_mod.reset_memory()


# ----------------------------------------------------------------------------- route


def test_route_cold_then_prefetched_with_debug_header(env, app_client):
    seed_catalog(env)
    body = {"product_key": SCANNED, "household": demo_household_dict()}
    r = app_client.post("/v1/picks?debug=1", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["prefetched"] is False
    assert [i["product"]["key"] for i in data["items"]] == EXPECTED
    assert data["items"][0]["verdicts"][0] == {"member_id": "m_linh", "state": "RIPE", "label": "Ripe"}
    assert set(data["items"][0]["product"]) == {"key", "name", "brand", "image_url", "category"}
    assert "wait" in data["timings_ms"] and "total" in data["timings_ms"] and data["request_id"]
    assert '"path": "vector"' in r.headers["x-pear-debug"]
    r2 = app_client.post("/v1/picks", json=body)
    assert r2.status_code == 200 and r2.json()["prefetched"] is True
    assert "x-pear-debug" not in r2.headers


def test_route_flag_off_and_bad_request(env, app_client):
    seed_catalog(env)
    clients = app_client.app.state.clients
    clients.settings = replace(clients.settings, feature_picks=False)
    r = app_client.post("/v1/picks?debug=1", json={"product_key": SCANNED, "household": demo_household_dict()})
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["prefetched"] is False
    assert "feature_picks off" in r.headers["x-pear-debug"]
    r = app_client.post("/v1/picks", json={"household": demo_household_dict()})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
