"""product_store (memory + Firestore code paths with fake db objects) and embeddings."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from core.clients import Clients
from core.config import get_settings
from models.contracts import Product
from models.domain import NormalizedProduct, RawProduct, StoredProduct
from pipeline import embeddings, product_store
from tests.conftest import FakeGenai, fake_vector


def stored(key: str, *, name: str = "Thing", category: str = "food", category_main: str | None = None,
           embedding: list[float] | None = None) -> StoredProduct:
    source = {"off": "open_food_facts", "obf": "open_beauty_facts", "fdc": "usda_fdc"}.get(key.split(":")[0], "photo")
    raw = RawProduct(key=key, source=source, name=name, category=category, category_main=category_main,
                     ingredients=["water", "salt"])
    np = NormalizedProduct(product=Product(key=key, name=name, source=source, category=category,
                                           ingredients=raw.ingredients),
                           category_main=category_main, kb_version="test")
    return StoredProduct(key=key, source=source, category=category, category_main=category_main,
                         kb_ids=["salt"], kb_version="test", raw=raw, normalized=np, embedding=embedding)


@pytest.fixture(autouse=True)
def _reset():
    product_store.reset_memory()
    embeddings.reset_cache()
    yield
    product_store.reset_memory()
    embeddings.reset_cache()


@pytest.fixture
async def clients():
    s = replace(get_settings(), t_firestore=0.2)
    async with httpx.AsyncClient() as http:
        yield Clients(settings=s, http=http)


# ----------------------------------------------------------------------------- memory store


async def test_memory_put_get_and_code_variants(clients):
    assert await product_store.put(clients, stored("off:0049000028911", name="Diet Coke"))
    got = await product_store.get(clients, "off:0049000028911")
    assert got is not None and got.raw.name == "Diet Coke" and got.fetched_at
    for code in ("049000028911", "0049000028911", "00049000028911"):
        sp = await product_store.get_by_code(clients, code)
        assert sp is not None and sp.key == "off:0049000028911", code
    assert await product_store.get_by_code(clients, "1234567890128") is None
    # copies, not shared references
    got.raw.name = "changed"
    assert (await product_store.get(clients, "off:0049000028911")).raw.name == "Diet Coke"


async def test_code_keys_cover_every_source_and_upce():
    keys = product_store.code_keys("04252614")
    assert keys[0] == "off:04252614"
    assert "off:0042100005264" in keys and "obf:0042100005264" in keys and "fdc:0042100005264" in keys


async def test_memory_find_nearest_and_category(clients):
    await product_store.put(clients, stored("off:1", name="ramen a", category_main="en:instant-noodles",
                                            embedding=[1.0, 0.0, 0.0]))
    await product_store.put(clients, stored("off:2", name="ramen b", category_main="en:instant-noodles",
                                            embedding=[0.8, 0.6, 0.0]))
    await product_store.put(clients, stored("off:3", name="cracker", category_main="en:crackers",
                                            embedding=[0.0, 1.0, 0.0]))
    await product_store.put(clients, stored("obf:4", name="lotion", category="cosmetic", embedding=[1.0, 0.0, 0.0]))
    await product_store.put(clients, stored("off:5", name="no vector", category_main="en:instant-noodles"))
    rows = await product_store.find_nearest(clients, [1.0, 0.0, 0.0], category="food", limit=2)
    assert [sp.key for sp, _ in rows] == ["off:1", "off:2"]
    assert rows[0][1] == pytest.approx(0.0, abs=1e-9) and rows[1][1] == pytest.approx(0.2, abs=1e-9)
    assert all(sp.category == "food" for sp, _ in await product_store.find_nearest(clients, [1, 0, 0], limit=10))
    noodles = await product_store.by_category_main(clients, "en:instant-noodles")
    assert sorted(sp.key for sp in noodles) == ["off:1", "off:2", "off:5"]


async def test_memory_scans_and_prices(clients):
    assert await product_store.log_scan(clients, uid="u1", product_key="off:1", states={"m_mia": "ALLERGY"},
                                        timings_ms={"total": 12}, source="open_food_facts")
    rows = product_store.memory_scans()
    assert rows[0]["uid"] == "u1" and rows[0]["states"] == {"m_mia": "ALLERGY"} and "created_at" in rows[0]
    assert await product_store.get_price(clients, "off:1") is None
    await product_store.put_price(clients, "off:1", {"amount": 3.99, "retailer": "Target"})
    assert (await product_store.get_price(clients, "off:1"))["amount"] == 3.99


# ----------------------------------------------------------------------------- Firestore paths


class _Snap:
    def __init__(self, id_: str, data: dict | None) -> None:
        self.id, self._data, self.exists = id_, data, data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeFirestore:
    """Just enough of firestore.AsyncClient for product_store."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict]] = {}
        self.added: list[tuple[str, dict]] = []
        self.get_all_calls = 0
        self._col = None
        self._key = None

    def collection(self, name):
        db = self

        class Col:
            def document(self, key):
                class Doc:
                    id = key

                    async def get(self):
                        return _Snap(key, db.docs.get(name, {}).get(key))

                    async def set(self, data):
                        db.docs.setdefault(name, {})[key] = data

                return Doc()

            async def add(self, data):
                db.added.append((name, data))

        return Col()

    async def get_all(self, refs):
        self.get_all_calls += 1
        for ref in refs:
            yield await ref.get()


class BrokenFirestore:
    def __init__(self, hang: bool = False) -> None:
        self.hang = hang

    async def _fail(self, *a, **kw):
        if self.hang:
            await asyncio.sleep(5)
        raise RuntimeError("firestore down")

    def collection(self, name):
        return self

    def document(self, key):
        return self

    def where(self, *a, **kw):
        return self

    def limit(self, n):
        return self

    def find_nearest(self, **kw):
        return self

    def get(self):
        return self._fail()

    def set(self, data):
        return self._fail()

    def add(self, data):
        return self._fail()

    async def get_all(self, refs):
        await self._fail()
        yield None  # pragma: no cover


async def test_firestore_roundtrip_vector_and_get_all(clients):
    from google.cloud.firestore_v1.vector import Vector

    db = FakeFirestore()
    clients.db = db
    sp = stored("off:0070662010013", name="Top Ramen", category_main="en:instant-noodles", embedding=[0.1, 0.2])
    assert await product_store.put(clients, sp)
    doc = db.docs["products"]["off:0070662010013"]
    assert isinstance(doc["embedding"], Vector) and doc["category"] == "food"
    assert doc["category_main"] == "en:instant-noodles" and doc["kb_ids"] == ["salt"]
    got = await product_store.get_by_code(clients, "070662010013")
    assert db.get_all_calls == 1  # one round trip for every key spelling
    assert got is not None and got.key == "off:0070662010013" and got.embedding == pytest.approx([0.1, 0.2])
    await product_store.log_scan(clients, uid="u", product_key=sp.key, states={}, timings_ms={}, source="x")
    assert db.added and db.added[0][0] == "scans" and "created_at" in db.added[0][1]


@pytest.mark.parametrize("hang", [False, True])
async def test_firestore_failure_is_a_miss(clients, hang):
    clients.db = BrokenFirestore(hang=hang)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await product_store.get(clients, "off:1") is None
    assert await product_store.get_by_code(clients, "070662010013") is None
    assert await product_store.find_nearest(clients, [1.0, 0.0]) == []
    assert await product_store.by_category_main(clients, "en:instant-noodles") == []
    assert await product_store.get_price(clients, "off:1") is None
    assert await product_store.put(clients, stored("off:1")) is False
    assert await product_store.log_scan(clients, uid="u", product_key="off:1", states={}, timings_ms={},
                                        source="x") is False
    assert await product_store.put_price(clients, "off:1", {"amount": 1}) is False
    assert loop.time() - t0 < 3.0  # each call capped by t_firestore (0.2 s)


# ----------------------------------------------------------------------------- embeddings


def _np(brand="Nissin", name="Top Ramen Chicken", cat="en:instant-noodles", ingredients=None) -> NormalizedProduct:
    ingr = ingredients if ingredients is not None else [f"i{n}" for n in range(10)]
    return NormalizedProduct(product=Product(key="off:1", name=name, brand=brand, source="open_food_facts",
                                             ingredients=ingr), category_main=cat)


def test_embedding_text_format():
    assert embeddings.embedding_text(_np()) == (
        "Nissin Top Ramen Chicken. instant noodles. i0, i1, i2, i3, i4, i5, i6, i7")
    assert embeddings.embedding_text(_np(brand=None, cat=None, ingredients=[])) == "Top Ramen Chicken"


async def test_embed_product_and_query_with_fake_gemini(clients):
    fake = FakeGenai()
    clients.genai = fake
    v = await embeddings.embed_product(clients, _np())
    assert v is not None and len(v) == clients.settings.embedding_dim
    assert v == fake_vector(embeddings.embedding_text(_np()))
    q = await embeddings.embed_query(clients, "top ramen")
    assert q is not None
    # cached: the same product again makes no new call
    await embeddings.embed_product(clients, _np())
    assert len(fake.embed_calls) == 2


async def test_concurrent_embeds_share_one_call(clients):
    fake = FakeGenai()
    clients.genai = fake
    a, b = await asyncio.gather(embeddings.embed_product(clients, _np()), embeddings.embed_product(clients, _np()))
    assert a == b and len(fake.embed_calls) == 1


async def test_embed_failure_returns_none(clients):
    clients.genai = None
    clients.genai_error = "no ADC"
    assert await embeddings.embed_product(clients, _np()) is None
    assert await embeddings.embed_query(clients, "x") is None
    assert embeddings.stats()["failed"] >= 2
