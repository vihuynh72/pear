"""Debug routes (/debug/*) on the real app.

Other lanes' modules are imported lazily by routes/debug.py, so these tests swap in tiny
fake modules (sys.modules) or monkeypatch the function the handler calls. That verifies the
debug router in isolation, whatever state the data/brain/gemini lanes are in. One test
also runs /debug/evaluate against the real rules engine and skips if it is still a stub.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from models.contracts import Headline, Product, Verdict
from models.domain import Evaluation, Hit, NormalizedProduct, RawProduct, StoredProduct
from routes import debug

API = Path(__file__).resolve().parents[1]


def _fake_module(monkeypatch, name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _np(raw: RawProduct) -> NormalizedProduct:
    product = Product(key=raw.key, name=raw.name, source=raw.source, ingredients=raw.ingredients)
    hits = [Hit(kb_id="sesame", matched="sesame seeds", where="ingredients", ingredient_index=1)]
    return NormalizedProduct(product=product, hits=hits, kb_version="test")


def _ev(np, household, member_ids=None, *, debug=False) -> Evaluation:
    vs = [Verdict(member_id=m.id, state="RIPE", label="Ripe", score=97.5) for m in household.select(member_ids)]
    return Evaluation(verdicts=vs, headline=Headline(text="Ripe for everyone. Go for it.", language="en"),
                      debug={"called_with_debug": debug})


def test_config_and_routes(app_client):
    r = app_client.get("/debug/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["store"] == "memory"
    assert body["clients"]["store"] == "memory"
    assert "router_errors" in body and "kb" in body
    paths = {row["path"] for row in app_client.get("/debug/routes").json()["routes"]}
    assert {"/debug/evaluate", "/debug/metrics", "/mock/v1/ask", "/health"} <= paths


def test_kb_match(app_client, monkeypatch):
    import rules.normalize as normalize

    def fake_match(text, language=None):
        assert text == "tahini" and language == "en"
        return [Hit(kb_id="sesame", matched="tahini", where="ingredients")]

    monkeypatch.setattr(normalize, "match_text", fake_match)
    r = app_client.get("/debug/kb/match", params={"text": "tahini", "lang": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["hits"][0]["kb_id"] == "sesame"


def test_kb_match_stub_is_503(app_client, monkeypatch):
    import rules.normalize as normalize

    def stub(text, language=None):
        raise NotImplementedError("rules.normalize.match_text")

    monkeypatch.setattr(normalize, "match_text", stub)
    r = app_client.get("/debug/kb/match", params={"text": "x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"


def test_evaluate_with_fake_engine(app_client, monkeypatch, household_dict):
    import rules.engine as engine
    import rules.normalize as normalize

    monkeypatch.setattr(normalize, "normalize", lambda raw, extra_hits=None: _np(raw))
    monkeypatch.setattr(engine, "evaluate", _ev)
    product = {"key": "test:1", "source": "photo", "name": "Test", "ingredients": ["rice", "sesame seeds"]}
    r = app_client.post("/debug/evaluate", json={"product": product, "household": household_dict,
                                                 "member_ids": ["m_mia"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["origin"] == "body"
    assert body["normalized"]["hits"][0]["matched"] == "sesame seeds"
    assert body["evaluation"]["verdicts"][0]["score"] == 97.5  # scores are never hidden here
    assert body["evaluation"]["debug"] == {"called_with_debug": True}
    assert body["summary"] == {"m_mia": {"state": "RIPE", "label": "Ripe", "score": 97.5, "codes": []}}
    assert "normalize" in body["timings_ms"] and "rules" in body["timings_ms"]


def test_evaluate_demo_product_defaults_to_demo_household(app_client, monkeypatch):
    import rules.engine as engine
    import rules.normalize as normalize

    monkeypatch.setattr(normalize, "normalize", lambda raw, extra_hits=None: _np(raw))
    monkeypatch.setattr(engine, "evaluate", _ev)
    r = app_client.post("/debug/evaluate", json={"demo": "sesame_crackers"})
    assert r.status_code == 200, r.text
    assert r.json()["origin"] == "demo:sesame_crackers"
    assert set(r.json()["summary"]) == {"m_linh", "m_ba", "m_mia", "m_leo"}
    names = app_client.get("/debug/demo_products").json()["names"]
    assert "sesame_crackers" in names and "mi_tom_noodles" in names


def test_evaluate_errors(app_client):
    assert app_client.post("/debug/evaluate", json={}).status_code == 400
    assert app_client.post("/debug/evaluate", json={"demo": "nope"}).status_code == 404
    assert app_client.post("/debug/evaluate", content=b"not json",
                           headers={"content-type": "application/json"}).status_code == 400


def test_evaluate_real_engine_if_ready(app_client):
    r = app_client.post("/debug/evaluate", json={"demo": "sesame_crackers"})
    if r.status_code == 503:
        pytest.skip(f"rules engine not ready: {r.json()['error']['message']}")
    assert r.status_code == 200, r.text
    summary = r.json()["summary"]
    assert summary["m_mia"]["state"] == "ALLERGY"
    assert "allergen.sesame" in summary["m_mia"]["codes"]


def test_product_from_store(app_client, monkeypatch):
    raw = RawProduct(key="off:0000000000017", source="open_food_facts", name="Sesame Crackers")
    stored = StoredProduct(key=raw.key, source=raw.source, raw=raw, embedding=[0.1] * 768)

    async def get(clients, key):
        return stored if key == raw.key else None

    async def get_price(clients, key):
        return {"amount": 2.5}

    _fake_module(monkeypatch, "pipeline.product_store", get=get, get_price=get_price)
    r = app_client.get(f"/debug/product/{raw.key}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["raw"]["name"] == "Sesame Crackers"
    assert body["product"]["embedding"]["dim"] == 768
    assert body["price"] == {"amount": 2.5}
    assert len(app_client.get(f"/debug/product/{raw.key}?full=1").json()["product"]["embedding"]) == 768
    assert app_client.get("/debug/product/off:404").status_code == 404


def test_evaluate_by_product_key(app_client, monkeypatch):
    import rules.engine as engine
    import rules.normalize as normalize

    raw = RawProduct(key="off:0000000000017", source="open_food_facts", name="Sesame Crackers")

    async def get(clients, key):
        return StoredProduct(key=raw.key, source=raw.source, raw=raw) if key == raw.key else None

    _fake_module(monkeypatch, "pipeline.product_store", get=get)
    monkeypatch.setattr(normalize, "normalize", lambda r, extra_hits=None: _np(r))
    monkeypatch.setattr(engine, "evaluate", _ev)
    r = app_client.post("/debug/evaluate", json={"product_key": raw.key})
    assert r.status_code == 200, r.text
    assert r.json()["origin"] == f"store:{raw.key}"


def test_off_raw(app_client, monkeypatch):
    async def fetch_raw_json(clients, code):
        return {"off": {"status": 1, "code": code}, "obf": None}

    _fake_module(monkeypatch, "pipeline.lookup_off", fetch_raw_json=fetch_raw_json)
    r = app_client.get("/debug/off/0000000000017")
    assert r.status_code == 200
    assert r.json()["responses"]["off"]["code"] == "0000000000017"


def test_missing_module_is_503(app_client, monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.lookup_off", None)  # import now raises ImportError
    r = app_client.get("/debug/off/123")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "unavailable"


def test_background_and_inflight(app_client, monkeypatch):
    assert "running" in app_client.get("/debug/background").json()
    _fake_module(monkeypatch, "pipeline.picks", stats=lambda: {"inflight": 2})
    _fake_module(monkeypatch, "pipeline.prices", stats=lambda: {"inflight": 1})
    body = app_client.get("/debug/inflight").json()
    assert body["picks"] == {"inflight": 2} and body["prices"] == {"inflight": 1}


def test_inflight_survives_broken_module(app_client, monkeypatch):
    def boom():
        raise RuntimeError("not ready")

    _fake_module(monkeypatch, "pipeline.picks", stats=boom)
    body = app_client.get("/debug/inflight").json()
    assert "error" in body["picks"]


def test_label_reader(app_client, monkeypatch):
    seen = {}

    async def read_label(clients, image, front=None):
        seen["image"], seen["front"] = image, front
        return {"legibility": "full", "photo_kind": "ingredients", "label_language": "en", "ingredients": []}

    _fake_module(monkeypatch, "pipeline.photo_gemini", read_label=read_label)
    r = app_client.post("/debug/label_reader",
                        files={"image": ("l.jpg", b"\xff\xd8 back", "image/jpeg"),
                               "front_image": ("f.jpg", b"\xff\xd8 front", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["label"]["legibility"] == "full"
    assert seen == {"image": b"\xff\xd8 back", "front": b"\xff\xd8 front"}


def test_metrics_from_request_log(app_client):
    debug.METRICS.clear()
    for _ in range(5):
        assert app_client.post("/mock/v1/picks", json={}).status_code == 200
    app_client.post("/mock/v1/scan/barcode", json={"code": "4006381333931"})  # 404
    body = app_client.get("/debug/metrics").json()
    picks = body["paths"]["/mock/v1/picks"]
    assert picks["count"] == 5 and picks["p50_ms"] is not None and picks["p95_ms"] >= picks["p50_ms"]
    assert body["paths"]["/mock/v1/scan/barcode"]["errors"] == 1
    app_client.get("/debug/metrics?reset=1")
    assert debug.METRICS.summary()["requests"] <= 1  # only the reset request itself, logged after


def test_metrics_record_and_percentiles():
    m = debug._Metrics(maxlen=3)
    for ms in (10, 20, 30, 40):
        m.record("/v1/x", ms, 200, {"lookup": ms // 2})
    s = m.summary()
    assert s["requests"] == 3  # ring buffer keeps the newest 3
    assert s["paths"]["/v1/x"]["p50_ms"] == 30 and s["paths"]["/v1/x"]["p95_ms"] == 40
    assert s["paths"]["/v1/x"]["stages"]["lookup"]["p50_ms"] == 15
    assert debug._pct([], 50) is None and debug._pct([7], 95) == 7
    debug.record("/v1/y", "not a number")  # never raises


def test_debug_routes_off_when_disabled():
    env = {**os.environ, "PEAR_DEBUG_ROUTES": "0", "PYTHONDONTWRITEBYTECODE": "1", "PEAR_STORE": "memory"}
    code = "import routes.debug as d; print(len(d.router.routes))"
    out = subprocess.run([sys.executable, "-c", code], cwd=API, env=env, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0"


def test_demo_household_loads():
    hh = debug.demo_household()
    assert [m["id"] for m in hh["members"]] == ["m_linh", "m_ba", "m_mia", "m_leo"]
    assert json.dumps(hh)
