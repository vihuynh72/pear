"""Explain stream tests (pipeline/explain.py, routes/explain.py): SSE framing, one Gemini call
per member, per-line guard (banned words, length, language), and a failing member falling back
to the templated reason without breaking the stream.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_explain.py -q
"""

from __future__ import annotations

import json

import pytest

from tests.test_photo import make_np, pear_env  # noqa: F401  (fixture re-export)

KEY = "off:0123456789012"


def parse_sse(text: str) -> tuple[list[dict], list[str]]:
    """-> (data events, event names) from a text/event-stream body."""
    data, names = [], []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        name = next((ln[len("event: "):] for ln in lines if ln.startswith("event: ")), None)
        payload = next(ln[len("data: "):] for ln in lines if ln.startswith("data: "))
        if name:
            names.append(name)
        else:
            data.append(json.loads(payload))
    return data, names


@pytest.fixture
def seeded(pear_env):  # noqa: F811  (pytest fixture injection)
    np = make_np(KEY, ["wheat flour", "sesame seeds", "salt"], name="Sesame Crackers", brand="Example")
    pear_env.products.seed(np)
    return pear_env


def _line_for(model, contents, config):
    if "Family member: Mia" in contents:
        raise RuntimeError("model down for this one")
    if "Family member: Bà" in contents:
        return {"text": "Bà ơi, bánh này có muối, ăn ít thôi nhé."}
    if "Family member: Leo" in contents:
        return {"text": "Leo, this one is safe for you."}  # banned word -> fallback
    return {"text": "Linh, nothing in these crackers bugs your skin."}


def test_stream_one_line_per_member_with_fallbacks(seeded):
    seeded.genai.on("[pear:explain]", _line_for)
    r = seeded.client.post("/v1/explain", json={"product_key": KEY, "household": seeded.household})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    events, names = parse_sse(r.text)
    assert names == ["done"] and r.text.rstrip().endswith("data: {}")
    by = {e["member_id"]: e for e in events}
    assert set(by) == {"m_linh", "m_ba", "m_mia", "m_leo"} and len(events) == 4
    assert by["m_ba"]["text"] == "Bà ơi, bánh này có muối, ăn ít thôi nhé." and by["m_ba"]["language"] == "vi"
    assert by["m_linh"]["text"].startswith("Linh,")
    # Mia's call failed: templated reason from the verdict (names the allergen)
    assert "sesame" in by["m_mia"]["text"].lower() and by["m_mia"]["language"] == "en"
    # Leo's line said "safe": replaced by the templated line in Spanish/English fallback
    assert "safe" not in by["m_leo"]["text"].lower() and by["m_leo"]["language"] == "es"
    assert len(seeded.genai.calls) == 4  # one call per member
    assert all(c["model"] == seeded.clients.settings.model_text for c in seeded.genai.calls)


def test_member_ids_filter(seeded):
    seeded.genai.on("[pear:explain]", {"text": "Mia, these have sesame seeds, so skip them."})
    r = seeded.client.post("/v1/explain", json={"product_key": KEY, "household": seeded.household,
                                                "member_ids": ["m_mia"]})
    assert r.status_code == 200, r.text
    events, _ = parse_sse(r.text)
    assert [e["member_id"] for e in events] == ["m_mia"]
    assert events[0]["text"] == "Mia, these have sesame seeds, so skip them."


def test_missing_product_is_json_404(seeded):
    r = seeded.client.post("/v1/explain", json={"product_key": "off:404", "household": seeded.household})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert seeded.genai.calls == []


def test_feature_off_is_503(seeded, monkeypatch):
    import dataclasses

    monkeypatch.setattr(seeded.clients, "settings",
                        dataclasses.replace(seeded.clients.settings, feature_explain=False))
    r = seeded.client.post("/v1/explain", json={"product_key": KEY, "household": seeded.household})
    assert r.status_code == 503


def test_guard_rules():
    from pipeline.explain import guard

    assert guard("Mia, these crackers have sesame seeds. Skip them.", "en") == (True, "ok")
    assert guard("Totally safe for Mia.", "en")[1] == "banned_word"
    assert guard("Bà ơi, món này an toàn.", "vi")[1] == "banned_word"
    assert guard("Leo, es seguro para ti.", "es")[1] == "banned_word"
    assert guard("This may treat eczema.", "en")[1] == "banned_word"
    assert guard(" ".join(["word"] * 26), "en")[1] == "too_long"
    assert guard("", "en")[1] == "empty"
    assert guard("Grandma, this has a lot of salt.", "vi")[1] == "wrong_language"
    assert guard("Leo, esta galleta tiene gelatina.", "es") == (True, "ok")
    assert guard("Leo, this cookie has gelatin.", "es")[1] == "wrong_language"
    assert guard("Bà ơi, mì này hơi mặn.", "en")[1] == "wrong_language"
    # a Vietnamese product name inside an English line is fine
    assert guard("Linh, Mì Hảo Hảo has a lot of sodium.", "en", ["Mì Hảo Hảo"]) == (True, "ok")
