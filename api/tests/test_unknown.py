"""normalize_unknown tests: one batched Gemini call, confidence / kb-id filters, allergen
strength, and enrich_product re-normalizing with the extra hits (background-only helper).

The kb is the FakeKB from test_photo.py and rules.normalize.normalize is replaced by a recorder,
so this verifies the mapping logic in isolation from the brain lane.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_unknown.py -q
"""

from __future__ import annotations

import dataclasses

import pytest

from core.clients import Clients
from core.config import get_settings
from models.contracts import Product
from models.domain import NormalizedProduct, RawProduct
from tests.test_photo import FakeKB


@pytest.fixture
def clients(fake_genai, monkeypatch):
    from rules import kb

    monkeypatch.setattr(kb, "get_kb", lambda: FakeKB())
    return Clients(settings=get_settings(), http=None, genai=fake_genai)


ANSWER = {"items": [
    {"index": 0, "text": "lait écrémé", "kb_id": "milk", "confidence": 0.95},
    {"index": 1, "text": "E120", "kb_id": "carmine", "confidence": 0.8},
    {"index": 2, "text": "water", "kb_id": "unknown", "confidence": 0.99},
    {"index": 3, "text": "huile végétale", "kb_id": "peanut", "confidence": 0.7},
    {"index": 3, "text": "huile végétale", "kb_id": "dragon_fruit_oil", "confidence": 0.99},
    {"index": 9, "text": "never sent", "kb_id": "milk", "confidence": 0.99},
    {"index": 4, "text": "lécithine", "kb_id": "soy", "confidence": 0.3},
    {"index": 4, "text": "lécithine", "kb_id": "dairy", "confidence": 60},
]}
STRINGS = ["lait écrémé", "E120", "water", "huile végétale", "lécithine", "lait écrémé"]


async def test_map_unknown_filters(clients, fake_genai):
    from pipeline.normalize_unknown import map_unknown

    fake_genai.on("[pear:normalize_unknown]", ANSWER)
    hits = await map_unknown(clients, STRINGS)
    got = {(h.kb_id, h.matched, h.strength) for h in hits}
    assert got == {
        ("milk", "lait écrémé", "contains"),     # allergen, >= 0.9
        ("carmine", "E120", "contains"),         # not an allergen
        ("peanut", "huile végétale", "possible"),  # allergen, 0.6 <= c < 0.9
        ("milk", "lécithine", "possible"),       # alias dairy -> milk, 60 read as 0.6
    }
    assert all(h.where == "enrich" for h in hits)
    assert len(fake_genai.calls) == 1  # one batched call, duplicates sent once
    contents = fake_genai.calls[0]["contents"]
    assert contents.count("lait écrémé") == 1 and "4: lécithine" in contents
    assert "milk: milk" in fake_genai.calls[0]["config"].system_instruction


async def test_map_unknown_empty_makes_no_call(clients, fake_genai):
    from pipeline.normalize_unknown import map_unknown

    assert await map_unknown(clients, ["", "  "]) == []
    assert fake_genai.calls == []


def _np(unmatched: list[str]) -> NormalizedProduct:
    return NormalizedProduct(product=Product(key="off:1", source="open_food_facts"), unmatched=unmatched)


async def test_enrich_product_renormalizes_with_hits(clients, fake_genai, monkeypatch):
    from pipeline import normalize_unknown

    seen = {}

    def recorder(raw, extra_hits):
        seen["hits"] = extra_hits
        return _np([])

    monkeypatch.setattr(normalize_unknown, "_normalize", recorder)
    fake_genai.on("[pear:normalize_unknown]", {"items": [{"index": 0, "kb_id": "milk", "confidence": 0.92}]})
    raw = RawProduct(key="off:1", source="open_food_facts", ingredients=["sugar", "Lait écrémé"])
    out = await normalize_unknown.enrich_product(clients, raw, _np(["Lait écrémé"]))
    assert out is not None
    (hit,) = seen["hits"]
    assert hit.kb_id == "milk" and hit.ingredient_index == 1 and hit.position == 1


async def test_enrich_product_noops(clients, fake_genai):
    from pipeline.normalize_unknown import enrich_product

    raw = RawProduct(key="off:1", source="open_food_facts", ingredients=["salt"])
    assert await enrich_product(clients, raw, _np([])) is None  # nothing unmatched: no call
    fake_genai.on("[pear:normalize_unknown]", RuntimeError("down"))
    assert await enrich_product(clients, raw, _np(["mystery"])) is None  # failure: logged, None
    off = dataclasses.replace(clients.settings, feature_enrich=False)
    assert await enrich_product(Clients(settings=off, http=None, genai=fake_genai), raw, _np(["x"])) is None
    assert len(fake_genai.calls) == 1
