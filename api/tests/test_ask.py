"""Ask path tests (pipeline/ask.py, routes/ask.py): JSON text questions, the audio multipart
fallback, intent routing in code, answer language, and the ADK engine with its fallback.

Uses the shared `pear_env` from tests/test_photo.py (fake Gemini, fake data/picks lanes,
real-or-fake brain). The default engine there is "direct"; ADK tests switch it to "adk".

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_ask.py -q
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from core.errors import PearError
from models.contracts import Household, PickItem, PickProduct, PickVerdict
from tests.conftest import demo_household_dict
from tests.test_photo import make_np, pear_env  # noqa: F401  (fixture re-export)

KEY = "off:0123456789012"


def understood(**over) -> dict:
    base = {"language": "en", "intent": "check", "product_query": "", "member_ids": ["m_mia"],
            "refers_to_last_product": True}
    base.update(over)
    return base


@pytest.fixture
def seeded(pear_env):  # noqa: F811  (pytest fixture injection)
    np = make_np(KEY, ["wheat flour", "sesame seeds", "salt"], name="Sesame Crackers", brand="Example")
    pear_env.products.seed(np, "sesame crackers")
    return pear_env


def ask_json(env, text: str, *, last: str | None = KEY, language: str | None = None, asker_id: str | None = None,
             debug: bool = False):
    body = {"text": text, "household": env.household}
    if last:
        body["last_product_key"] = last
    if language:
        body["language"] = language
    if asker_id:
        body["asker_id"] = asker_id
    return env.client.post("/v1/ask" + ("?debug=1" if debug else ""), json=body)


def test_check_with_last_product_key(seeded):
    seeded.genai.on("[pear:question_reader]", understood())
    r = ask_json(seeded, "Can Mia eat this?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "check" and body["next"] == "result"
    assert body["transcript"] == "Can Mia eat this?"
    assert body["member_ids"] == ["m_mia"] and body["language"] == "en"
    assert body["answer"]["language"] == "en" and body["answer"]["text"]
    assert "safe" not in body["answer"]["text"].lower()
    sr = body["scan_result"]
    assert sr["product"]["key"] == KEY
    assert [v["member_id"] for v in sr["verdicts"]] == ["m_mia"]
    assert sr["verdicts"][0]["state"] == "ALLERGY"
    # one Gemini call; the prompt lists members and says a last product exists
    assert len(seeded.genai.calls) == 1
    hay = seeded.genai.calls[0]["haystack"]
    assert "m_ba | bà" in hay and "a product was just scanned: yes" in hay and "can mia eat this?" in hay
    assert seeded.genai.calls[0]["model"] == seeded.clients.settings.model_question
    assert ("load_product", {"key": KEY}) in seeded.products.calls
    assert seeded.picks.prefetched == [KEY]  # identify -> picks prefetch


def test_vietnamese_question_answers_in_vietnamese(seeded):
    seeded.genai.on("[pear:question_reader]", understood(language="vi", member_ids=["m_ba"], product_query="mì"))
    r = ask_json(seeded, "Bà ăn mì này được không?", language="vi")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "vi" and body["answer"]["language"] == "vi"
    assert body["member_ids"] == ["m_ba"]
    assert body["product_query"] == "mì"
    assert body["scan_result"]["product"]["key"] == KEY  # "này" -> last product, no name search
    assert not any(c[0] == "match_by_name" for c in seeded.products.calls)


def test_member_names_are_mapped_to_ids(seeded):
    seeded.genai.on("[pear:question_reader]", understood(member_ids=["Bà", "m_leo", "nobody"]))
    r = ask_json(seeded, "Can grandma and Leo have this?")
    assert r.status_code == 200, r.text
    assert r.json()["member_ids"] == ["m_ba", "m_leo"]


def test_unsupported_language_falls_back_to_asker(seeded):
    seeded.genai.on("[pear:question_reader]", understood(language="fr", member_ids=[]))
    r = ask_json(seeded, "Est-ce que je peux manger ça ?", asker_id="m_leo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "fr" and body["answer"]["language"] == "es"
    assert len(body["scan_result"]["verdicts"]) == 4  # nobody named -> everyone


def test_product_by_name_when_no_last_product(seeded):
    seeded.genai.on("[pear:question_reader]", understood(product_query="sesame crackers",
                                                          refers_to_last_product=False))
    r = ask_json(seeded, "Can Mia eat sesame crackers?", last=None)
    assert r.status_code == 200, r.text
    assert r.json()["scan_result"]["product"]["key"] == KEY
    assert ("match_by_name", {"brand": None, "name": None, "query": "sesame crackers"}) in seeded.products.calls
    assert "a product was just scanned: no" in seeded.genai.calls[0]["haystack"]


def test_check_without_resolvable_product_is_scan_needed(seeded):
    seeded.genai.on("[pear:question_reader]", understood(product_query="unicorn chips",
                                                          refers_to_last_product=False))
    r = ask_json(seeded, "Can Mia eat unicorn chips?", last=None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next"] == "scan_needed" and body["intent"] == "check" and body["scan_result"] is None
    assert body["answer"]["text"]


def test_unclear_is_scan_needed_without_lookups(seeded):
    seeded.genai.on("[pear:question_reader]", understood(intent="unclear", member_ids=[], product_query=""))
    r = ask_json(seeded, "hello pear")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "unclear" and body["next"] == "scan_needed" and body["scan_result"] is None
    assert seeded.products.calls == []


def test_empty_text_is_scan_needed_without_gemini(seeded):
    r = ask_json(seeded, "   ", language="es")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next"] == "scan_needed" and body["answer"]["language"] == "es"
    assert seeded.genai.calls == []


def test_alternatives_uses_picks(seeded):
    seeded.picks.items = [
        PickItem(product=PickProduct(key="off:1", name="Rice Crackers", brand="A"),
                 verdicts=[PickVerdict(member_id="m_mia", state="RIPE", label="Ripe")]),
        PickItem(product=PickProduct(key="off:2", name="Corn Thins", brand="B"),
                 verdicts=[PickVerdict(member_id="m_mia", state="RIPE", label="Ripe")]),
    ]
    seeded.genai.on("[pear:question_reader]", understood(intent="alternatives", member_ids=[]))
    r = ask_json(seeded, "What's a better option?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "alternatives" and body["next"] == "result"
    assert [i["product"]["key"] for i in body["picks"]["items"]] == ["off:1", "off:2"]
    assert "Rice Crackers" in body["answer"]["text"] or "Corn Thins" in body["answer"]["text"]
    assert body["scan_result"]["product"]["key"] == KEY
    assert seeded.picks.get_calls[0]["product_key"] == KEY


def test_alternatives_without_product_is_scan_needed(seeded):
    seeded.genai.on("[pear:question_reader]", understood(intent="alternatives", refers_to_last_product=False,
                                                          product_query=""))
    r = ask_json(seeded, "What should I buy instead?", last=None)
    assert r.status_code == 200, r.text
    assert r.json()["next"] == "scan_needed" and seeded.picks.get_calls == []


def test_audio_multipart_one_call(seeded):
    seeded.genai.on("[pear:question_reader_audio]", {
        "transcript": "Bà ăn cái này được không?", **understood(language="vi", member_ids=["m_ba"])})
    r = seeded.client.post("/v1/ask", files={"audio": ("q.m4a", b"\x00\x00\x00\x20ftypM4A fake", "audio/m4a")},
                           data={"household": json.dumps(seeded.household), "language": "vi",
                                 "last_product_key": KEY, "asker_id": "m_linh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript"] == "Bà ăn cái này được không?"
    assert body["answer"]["language"] == "vi" and body["member_ids"] == ["m_ba"]
    assert body["scan_result"]["product"]["key"] == KEY
    assert len(seeded.genai.calls) == 1
    call = seeded.genai.calls[0]
    audio = [c for c in call["contents"] if not isinstance(c, str)][0]
    assert audio.inline_data.mime_type == "audio/mp4"
    assert call["config"].response_json_schema["required"][0] == "transcript"


def test_audio_empty_transcript_is_unclear(seeded):
    seeded.genai.on("[pear:question_reader_audio]", {"transcript": "", **understood()})
    r = seeded.client.post("/v1/ask", files={"audio": ("q.m4a", b"noise", "audio/x-m4a")},
                           data={"household": json.dumps(seeded.household)})
    assert r.status_code == 200, r.text
    assert r.json()["next"] == "scan_needed"


def test_bad_requests(seeded):
    r = seeded.client.post("/v1/ask", json={"text": "hi"})  # no household
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = seeded.client.post("/v1/ask", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    r = seeded.client.post("/v1/ask", data={"household": json.dumps(seeded.household)},
                           files={"other": ("x.bin", b"x", "application/octet-stream")})
    assert r.status_code == 400


def test_gemini_error_is_contract_error(seeded):
    seeded.genai.on("[pear:question_reader]", RuntimeError("RESOURCE_EXHAUSTED quota"))
    r = ask_json(seeded, "Can Mia eat this?")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


# ---------------------------------------------------------------------------- ADK engine


def use_adk(env, monkeypatch):
    monkeypatch.setattr(env.clients, "settings", dataclasses.replace(env.clients.settings, ask_engine="adk"))


def test_adk_failure_falls_back_with_one_gemini_call(seeded, monkeypatch):
    import agent.graph
    from pipeline import ask

    use_adk(seeded, monkeypatch)

    async def broken_run_ask(clients, body, uid):
        await ask.understand(clients, body)  # the model node ran, then the graph broke
        raise RuntimeError("adk exploded")

    monkeypatch.setattr(agent.graph, "run_ask", broken_run_ask)
    seeded.genai.on("[pear:question_reader]", understood())
    r = ask_json(seeded, "Can Mia eat this?", debug=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["debug"]["ask_engine"] == "fallback" and "adk exploded" in body["debug"]["adk_error"]
    assert body["scan_result"]["verdicts"][0]["member_id"] == "m_mia"
    assert len(seeded.genai.calls) == 1  # understood was memoized, not asked twice


def test_adk_no_output_falls_back(seeded, monkeypatch):
    import agent.graph

    use_adk(seeded, monkeypatch)

    async def empty_run_ask(clients, body, uid):
        return None

    monkeypatch.setattr(agent.graph, "run_ask", empty_run_ask)
    seeded.genai.on("[pear:question_reader]", understood())
    r = ask_json(seeded, "Can Mia eat this?", debug=True)
    assert r.status_code == 200, r.text
    assert r.json()["debug"]["ask_engine"] == "fallback"


def test_adk_pear_error_is_not_retried(seeded, monkeypatch):
    import agent.graph

    use_adk(seeded, monkeypatch)

    async def limited(clients, body, uid):
        raise PearError("rate_limited", "Pear is busy. Try again in a moment.", next="retry")

    monkeypatch.setattr(agent.graph, "run_ask", limited)
    r = ask_json(seeded, "Can Mia eat this?")
    assert r.status_code == 429 and seeded.genai.calls == []


def test_adk_graph_end_to_end(seeded, monkeypatch):
    """The real google-adk Workflow drives the same node functions."""
    pytest.importorskip("google.adk")
    use_adk(seeded, monkeypatch)
    seeded.genai.on("[pear:question_reader]", understood(member_ids=["m_mia", "m_leo"]))
    r = ask_json(seeded, "Can Mia and Leo eat this?", debug=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["debug"]["ask_engine"] == "adk", body["debug"].get("adk_error")
    assert body["next"] == "result" and body["member_ids"] == ["m_mia", "m_leo"]
    assert len(seeded.genai.calls) == 1


# ---------------------------------------------------------------------------- units


def test_answer_language_rules():
    from pipeline.ask import answer_language

    hh = Household.model_validate(demo_household_dict())
    assert answer_language("vi", None, hh, None) == "vi"
    assert answer_language("fr", "vi", hh, "m_leo") == "es"  # detected wins over the hint, then asker
    assert answer_language(None, "es", hh, None) == "es"
    assert answer_language("de", None, hh, None) == "en"  # first member Linh: en
    assert answer_language("de", None, Household(), None) == "en"


def test_audio_mime():
    from pipeline.ask import audio_mime

    assert audio_mime("audio/m4a") == "audio/mp4"
    assert audio_mime("application/octet-stream", "q.m4a") == "audio/mp4"
    assert audio_mime("audio/wav") == "audio/wav"
    assert audio_mime(None) == "audio/mp4"


# ---------------------------------------------------------------------------- integration


def test_check_with_real_products_and_memory_store(pear_env, monkeypatch):  # noqa: F811
    """Real pipeline.products.load_product over the in-memory product_store (data lane)."""
    import asyncio

    from models.domain import RawProduct
    from pipeline import ask, product_store, products
    from rules import normalize

    monkeypatch.setattr(ask, "_products", lambda: products)
    raw = RawProduct(key=KEY, source="open_food_facts", code="0123456789012", name="Sesame Crackers",
                     ingredients=["wheat flour", "sesame seeds", "salt"])
    np = normalize.normalize(raw)
    asyncio.run(product_store.put(pear_env.clients, products.stored_from(raw, np, None)))
    pear_env.genai.on("[pear:question_reader]", understood())
    r = ask_json(pear_env, "Can Mia eat this?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scan_result"]["product"]["key"] == KEY
    assert body["scan_result"]["verdicts"][0]["state"] == "ALLERGY"
