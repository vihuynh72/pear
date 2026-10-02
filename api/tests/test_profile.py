"""Profile parse tests (pipeline/profile_parse.py, routes/profile.py).

The local pass depends on KB.search, so these tests always use the FakeKB from test_photo.py
(deterministic labels/ids) to verify this lane's logic; test_real_kb_smoke runs against the real
brain-lane kb when it is implemented and is skipped while rules/kb.py is a stub.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_profile.py -q
"""

from __future__ import annotations

import pytest

from tests.test_photo import FakeKB


@pytest.fixture
def env(app_client, fake_genai, monkeypatch):
    from rules import kb

    monkeypatch.setattr(kb, "get_kb", lambda: FakeKB())
    return app_client, fake_genai


def chips_by_id(body: dict) -> dict[str, dict]:
    return {c["id"]: c for c in body["chips"]}


def test_local_only_no_gemini(env):
    client, genai = env
    r = client.post("/v1/profile/parse", json={"text": "peanuts, high blood pressure and eczema", "language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    c = chips_by_id(body)
    assert c["peanut"]["group"] == "allergies" and c["peanut"]["level"] == "strict"
    assert c["high_blood_pressure"]["group"] == "conditions" and c["high_blood_pressure"]["level"] is None
    assert c["eczema"]["group"] == "skin"
    assert c["peanut"]["label"] == "peanut"
    assert body["unmatched"] == []
    assert genai.calls == []


def test_cleanup_and_sensitive_level(env):
    client, genai = env
    r = client.post("/v1/profile/parse", json={"text": "allergic to sesame\nmild lactose intolerance"})
    assert r.status_code == 200, r.text
    c = chips_by_id(r.json())
    assert c["sesame"]["level"] == "strict"
    assert c["lactose"]["level"] == "sensitive"
    assert genai.calls == []


def test_leftovers_go_to_one_gemini_call(env):
    client, genai = env
    genai.on("[pear:profile_parse]", {
        "chips": [
            {"term": "carmine", "group": "diets", "id": "vegan", "level": "none",
             "note": "carmine: insect-based red color, matters for vegans"},
            {"term": "xyzzy", "group": "allergies", "id": "unicorn", "level": "strict", "note": ""},
            {"term": "maní", "group": "allergies", "id": "peanuts", "level": "strict", "note": ""},
        ],
        "unmatched": ["xyzzy"],
    })
    r = client.post("/v1/profile/parse", json={"text": "sesame, carmine, xyzzy, maní", "language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    c = chips_by_id(body)
    assert set(c) == {"sesame", "vegan", "peanut"}  # unicorn dropped (not a kb id); alias resolved
    assert c["vegan"]["group"] == "diets" and c["vegan"]["level"] is None
    assert c["vegan"]["note"] == "carmine: insect-based red color, matters for vegans"
    assert c["peanut"]["level"] == "strict"
    assert body["unmatched"] == ["xyzzy"]
    assert len(genai.calls) == 1
    call = genai.calls[0]
    assert "- carmine" in call["contents"] and "- sesame" not in call["contents"]  # only leftovers
    system = call["config"].system_instruction
    assert "diets: " in system and "vegan (vegan)" in system and "allergies: " in system


def test_gemini_failure_keeps_local_chips(env):
    client, genai = env
    genai.on("[pear:profile_parse]", RuntimeError("boom"))
    r = client.post("/v1/profile/parse", json={"text": "peanut, carmine", "language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["id"] for c in body["chips"]] == ["peanut"]
    assert body["unmatched"] == ["carmine"]


def test_gemini_unavailable_keeps_local_chips(env):
    import main

    client, genai = env
    main.app.state.clients.genai = None
    main.app.state.clients.genai_error = "no creds"
    r = client.post("/v1/profile/parse", json={"text": "eczema, gelatin"})
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["chips"]] == ["eczema"] and r.json()["unmatched"] == ["gelatin"]


def test_labels_in_request_language(env):
    client, genai = env
    r = client.post("/v1/profile/parse", json={"text": "đậu phộng và huyết áp cao", "language": "vi"})
    assert r.status_code == 200, r.text
    c = chips_by_id(r.json())
    assert c["peanut"]["label"] == "đậu phộng" and c["high_blood_pressure"]["label"] == "huyết áp cao"
    assert genai.calls == []


def test_empty_text(env):
    client, genai = env
    r = client.post("/v1/profile/parse", json={"text": "  "})
    assert r.status_code == 200 and r.json()["chips"] == [] and r.json()["unmatched"] == []


def test_split_terms():
    from pipeline.profile_parse import split_terms

    assert split_terms("peanuts, milk and eggs; soy\nsesame y trigo / gluten") == \
        ["peanuts", "milk", "eggs", "soy", "sesame", "trigo", "gluten"]
    assert split_terms("đậu phộng và sữa, Peanuts, peanuts") == ["đậu phộng", "sữa", "Peanuts"]
    assert split_terms("") == []


def test_real_kb_smoke(app_client, fake_genai):
    """Integration with the brain lane's kb (skipped while rules/kb.py is a stub)."""
    from rules.kb import get_kb

    try:
        kb = get_kb()
        kb.profile_ids()
        kb.search("peanut", language="en", limit=3)
    except Exception as e:
        pytest.skip(f"real kb not ready: {e!r}")
    fake_genai.on("[pear:profile_parse]", {"chips": [], "unmatched": []})
    r = app_client.post("/v1/profile/parse", json={"text": "peanut", "language": "en"})
    assert r.status_code == 200, r.text
    assert any(c["id"] == "peanut" and c["group"] == "allergies" for c in r.json()["chips"]), r.json()
