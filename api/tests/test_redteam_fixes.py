"""Regression tests for the 2026-10-02 red-team fixes that are not plain rules cases
(tests/cases/redteam.json holds those): Firestore doc shape for photo products, one embedding
per text on batches, OFF line breaks kept, folded / hyphen / bare-INS lexicon passes."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from core.clients import Clients
from core.config import get_settings
from pipeline import gemini, product_store
from pipeline.lookup_off import map_off_product
from pipeline.photo_gemini import label_to_raw
from pipeline.products import normalize_raw, stored_from
from rules.kb import get_kb
from rules.normalize import normalize


def _photo_raw():
    return label_to_raw({"legibility": "full", "photo_kind": "ingredients", "label_language": "en",
                         "ingredients": [{"text": "wheat flour", "kb_ids": ["wheat"]},
                                         {"text": "sesame seeds", "kb_ids": ["sesame"]},
                                         {"text": "salt", "kb_ids": []}]})


def test_photo_doc_has_no_nested_arrays_and_round_trips():
    raw = _photo_raw()
    doc = product_store.to_doc(stored_from(raw, normalize_raw(raw), None))
    doc.pop("updated_at", None)
    product_store._assert_firestore_shape(doc)  # raises on a list inside a list
    back = product_store.from_doc(doc)
    assert back is not None and back.raw.model_kb_ids == [["wheat"], ["sesame"], []]


def test_memory_put_enforces_firestore_shape():
    raw = _photo_raw()
    clients = Clients(settings=get_settings(), http=None, genai=None)  # type: ignore[arg-type]
    assert asyncio.run(product_store.put(clients, stored_from(raw, normalize_raw(raw), None)))


def test_embed_texts_one_request_per_text(fake_genai):
    clients = Clients(settings=replace(get_settings()), http=None, genai=fake_genai)  # type: ignore[arg-type]
    vecs = asyncio.run(gemini.embed_texts(clients, ["a", "b", "c"]))
    assert len(vecs) == 3 and len({tuple(v) for v in vecs}) == 3
    assert [c["texts"] for c in fake_genai.embed_calls] and all(len(c["texts"]) == 1 for c in fake_genai.embed_calls)


def test_off_ingredient_text_keeps_line_breaks():
    raw = map_off_product({"code": "0000000000017", "lang": "en",
                           "ingredients_text": "May contain traces of peanuts\nIngredients: sugar, wheat flour"})
    np = normalize(raw)
    assert "wheat" in np.product.contains and "peanut" in np.product.may_contain


def test_lexicon_passes_only_add_hits():
    lex = get_kb().lexicon
    ids = lambda s: {m.kb_id for m in lex.scan(s) if m.kb_id}  # noqa: E731
    assert "peanut" in ids("Pea-\nnut butter")
    assert "sulfite" in ids("Preservative (220)")
    assert "egg" in ids("Bột mi, trung, duong")
    assert "sesame" not in ids("nước, đường, me")  # well-accented vi: 'me' is tamarind
    assert "peanut" in ids("Contains: milk, peanuts\nFree from artificial colors")
    assert {m.matched for m in lex.scan("sugar, (E322i)") if m.kb_id == "soy"} == {"E322i"}
