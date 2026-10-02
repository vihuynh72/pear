"""Mock API (/mock/v1/*): shapes, selection rules, knobs, SSE, hot reload, and /v1 twins.

Most tests run the mock router on a bare FastAPI app (no lifespan, no other lanes), so
they verify routes/mock.py in isolation. `test_every_v1_route_has_a_mock_twin` uses the
real main.app to make sure nothing the app can call is missing under /mock.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from models.contracts import (
    AskResponse,
    ErrorResponse,
    ExplainEvent,
    HealthResponse,
    KbSearchResponse,
    PicksResponse,
    PricesResponse,
    ProfileParseResponse,
    ScanResult,
)
from routes import mock

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "contracts" / "fixtures"
HOUSEHOLD = json.loads((REPO / "contracts" / "household.example.json").read_text(encoding="utf-8"))

# Every /v1 endpoint the app can call (agreed interface), as (method, path).
V1_ENDPOINTS = {
    ("POST", "/v1/scan/barcode"), ("POST", "/v1/scan/photo"), ("POST", "/v1/ask"),
    ("POST", "/v1/profile/parse"), ("POST", "/v1/explain"), ("GET", "/v1/kb/search"),
    ("POST", "/v1/picks"), ("POST", "/v1/prices"),
}


def _mock_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mock.router)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_mock_app())


def _photo_files() -> dict:
    return {"image": ("label.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")}


# ----------------------------------------------------------------------------- shapes


def test_health(client):
    r = client.get("/mock/health")
    assert r.status_code == 200
    HealthResponse.model_validate(r.json())


@pytest.mark.parametrize("code,fixture_name,headline_part", [
    ("0000000000017", "scan_barcode.sesame_crackers.json", "Mia"),
    ("0000000000062", "scan_barcode.ripe.json", "Ripe for everyone"),
    ("0000000000024", "scan_barcode.noodles_vi.json", "Leo"),
    ("000000000017", "scan_barcode.sesame_crackers.json", "Mia"),  # UPC-A form -> EAN-13
])
def test_barcode_by_code(client, code, fixture_name, headline_part):
    r = client.post("/mock/v1/scan/barcode", json={"code": code, "household": HOUSEHOLD})
    assert r.status_code == 200, r.text
    assert r.headers["x-pear-mock-fixture"] == fixture_name
    sr = ScanResult.model_validate(r.json())
    assert headline_part in sr.headline.text
    assert sr.request_id and sr.request_id == r.headers["x-request-id"]
    assert all(v.score is None for v in sr.verdicts)


def test_barcode_unknown_is_404_not_found(client):
    r = client.post("/mock/v1/scan/barcode", json={"code": "4006381333931", "household": HOUSEHOLD})
    assert r.status_code == 404
    err = ErrorResponse.model_validate(r.json())
    assert err.error.code == "not_found" and err.error.next == "take_photo"
    assert err.error.request_id == r.headers["x-request-id"]


def test_barcode_without_code_uses_default(client):
    r = client.post("/mock/v1/scan/barcode", json={"household": HOUSEHOLD})
    assert r.status_code == 200
    ScanResult.model_validate(r.json())


@pytest.mark.parametrize("fixture_name", ["scan_photo.lotion", "scan_photo.front_only", "scan_photo.partial_label"])
def test_photo_multipart(client, fixture_name):
    q = "" if fixture_name == "scan_photo.lotion" else f"?fixture={fixture_name}"
    r = client.post(f"/mock/v1/scan/photo{q}", files=_photo_files(),
                    data={"household": json.dumps(HOUSEHOLD), "member_ids": '["m_mia"]'})
    assert r.status_code == 200, r.text
    assert r.headers["x-pear-mock-fixture"] == fixture_name + ".json"
    sr = ScanResult.model_validate(r.json())
    if fixture_name != "scan_photo.lotion":
        assert sr.product.less_sure


def test_photo_front_only_and_partial_flags(client):
    fo = ScanResult.model_validate(client.post("/mock/v1/scan/photo?fixture=scan_photo.front_only",
                                               files=_photo_files()).json())
    assert fo.product.photo_kind == "front_only" and fo.product.ask_for_ingredients
    assert fo.product.matched_by == "name"
    pl = ScanResult.model_validate(client.post("/mock/v1/scan/photo?fixture=scan_photo.partial_label",
                                               files=_photo_files()).json())
    assert {v.member_id: v.state for v in pl.verdicts}["m_mia"] == "UNSURE"


@pytest.mark.parametrize("text,expected", [
    ("Bà ăn mì này được không?", "ask.check_vi.json"),
    ("Can Bà have these noodles?", "ask.check_vi.json"),
    ("What can we get instead?", "ask.alternatives.json"),
    ("¿Hay otra opción para toda la familia?", "ask.alternatives.json"),
    ("Có loại nào khác không?", "ask.alternatives.json"),
    ("Can Mia have this?", "ask.scan_needed.json"),
])
def test_ask_json_keywords(client, text, expected):
    body = {"text": text, "language": "vi", "household": HOUSEHOLD, "last_product_key": "off:0000000000024"}
    r = client.post("/mock/v1/ask", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["x-pear-mock-fixture"] == expected
    ar = AskResponse.model_validate(r.json())
    assert ar.transcript == text  # the mock echoes what the app sent
    assert ar.request_id == r.headers["x-request-id"]
    if ar.scan_result is not None:
        assert ar.scan_result.request_id == ar.request_id
    if expected == "ask.alternatives.json":
        assert ar.picks is not None and len(ar.picks.items) == 3


def test_ask_multipart_audio_fallback(client):
    r = client.post("/mock/v1/ask", files={"audio": ("q.m4a", b"fake m4a", "audio/mp4")},
                    data={"household": json.dumps(HOUSEHOLD), "language": "vi", "asker_id": "m_linh"})
    assert r.status_code == 200, r.text
    assert r.headers["x-pear-mock-fixture"] == "ask.check_vi.json"
    ar = AskResponse.model_validate(r.json())
    assert ar.language == "vi" and ar.next == "result" and ar.scan_result is not None


def test_ask_multipart_text_field(client):
    r = client.post("/mock/v1/ask", data={"text": "what else could we buy instead", "household": json.dumps(HOUSEHOLD)})
    assert r.status_code == 200
    assert r.headers["x-pear-mock-fixture"] == "ask.alternatives.json"


def test_picks_prices_profile_kb(client):
    body = {"product_key": "off:0000000000017", "household": HOUSEHOLD}
    picks = PicksResponse.model_validate(client.post("/mock/v1/picks", json=body).json())
    assert len(picks.items) == 3 and picks.prefetched
    keys = [it.product.key for it in picks.items]

    pr = client.post("/mock/v1/prices", json={"product_keys": keys + ["off:9999999999994"], "country": "US"})
    prices = PricesResponse.model_validate(pr.json())
    assert list(prices.prices) == keys + ["off:9999999999994"]
    assert prices.prices["off:9999999999994"] is None
    assert prices.prices[keys[0]] is not None and prices.prices[keys[0]].amount > 0

    pp = client.post("/mock/v1/profile/parse", json={"text": "peanuts, sesame, eczema", "language": "en"})
    ProfileParseResponse.model_validate(pp.json())

    kb = KbSearchResponse.model_validate(client.get("/mock/v1/kb/search", params={"q": "ses"}).json())
    assert kb.query == "ses" and [s.id for s in kb.suggestions] == ["sesame"]
    kb_vi = KbSearchResponse.model_validate(client.get("/mock/v1/kb/search",
                                                       params={"q": "đậu", "lang": "vi"}).json())
    assert {s.id for s in kb_vi.suggestions} >= {"peanut", "soy"}
    assert all(s.label == s.labels["vi"] for s in kb_vi.suggestions)
    everything = KbSearchResponse.model_validate(client.get("/mock/v1/kb/search").json())
    assert len(everything.suggestions) >= 10


# ----------------------------------------------------------------------------- knobs


@pytest.mark.parametrize("code,status", [
    ("rate_limited", 429), ("not_found", 404), ("too_blurry", 422), ("unreadable", 422),
    ("internal", 500), ("unauthorized", 401), ("unavailable", 503), ("bad_request", 400),
])
def test_forced_errors(client, code, status):
    r = client.post(f"/mock/v1/scan/photo?error={code}", files=_photo_files())
    assert r.status_code == status
    err = ErrorResponse.model_validate(r.json())
    assert err.error.code == code
    assert err.error.request_id == r.headers["x-request-id"]


def test_forced_error_on_get_and_sse(client):
    assert client.get("/mock/v1/kb/search?error=rate_limited").status_code == 429
    r = client.post("/mock/v1/explain?error=internal", json={})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal"


def test_unknown_error_code_is_bad_request(client):
    r = client.post("/mock/v1/picks?error=nope", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_delay_ms(client):
    t = time.perf_counter()
    r = client.post("/mock/v1/picks?delay_ms=250", json={})
    assert r.status_code == 200
    assert time.perf_counter() - t >= 0.24


def test_fixture_override_and_bad_names(client):
    r = client.post("/mock/v1/scan/barcode?fixture=scan_barcode.ripe", json={"code": "0000000000017"})
    assert r.headers["x-pear-mock-fixture"] == "scan_barcode.ripe.json"
    assert "Ripe for everyone" in r.json()["headline"]["text"]
    assert client.post("/mock/v1/picks?fixture=../secrets", json={}).status_code == 400
    missing = client.post("/mock/v1/picks?fixture=picks.nope", json={})
    assert missing.status_code == 500 and "picks.nope.json" in missing.json()["error"]["message"]


def test_fresh_request_id_each_time(client):
    a = client.post("/mock/v1/picks", json={}).json()["request_id"]
    b = client.post("/mock/v1/picks", json={}).json()["request_id"]
    assert a and b and a != b


# ----------------------------------------------------------------------------- SSE


def _parse_sse(text: str) -> tuple[list[ExplainEvent], list[str]]:
    events, names = [], []
    for block in [b for b in text.split("\n\n") if b.strip()]:
        lines = block.splitlines()
        name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), "message")
        data = "\n".join(ln[5:].strip() for ln in lines if ln.startswith("data:"))
        names.append(name)
        if name == "message":
            events.append(ExplainEvent.model_validate(json.loads(data)))
    return events, names


def test_explain_sse(client):
    t = time.perf_counter()
    r = client.post("/mock/v1/explain", json={"product_key": "off:0000000000017", "household": HOUSEHOLD})
    elapsed = time.perf_counter() - t
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events, names = _parse_sse(r.text)
    assert len(events) >= 3 and names[-1] == "done"
    assert {e.member_id for e in events} == {"m_linh", "m_ba", "m_mia", "m_leo"}
    assert {e.language for e in events} == {"en", "vi", "es"}
    assert elapsed >= 0.15 * (len(names) - 1) - 0.05


def test_explain_sse_member_filter_and_fast(client):
    r = client.post("/mock/v1/explain?event_ms=0", json={"product_key": "x", "member_ids": ["m_mia"]})
    events, names = _parse_sse(r.text)
    assert [e.member_id for e in events] == ["m_mia"] and names[-1] == "done"


# ----------------------------------------------------------------------------- hot reload


def test_fixture_edit_is_picked_up_without_restart(client, tmp_path, monkeypatch):
    for p in FIXTURES.iterdir():
        shutil.copy(p, tmp_path / p.name)
    monkeypatch.setattr(mock, "fixtures_dir", lambda: tmp_path)
    assert client.post("/mock/v1/picks", json={}).json()["prefetched"] is True

    path = tmp_path / "picks.default.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["prefetched"] = False
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    assert client.post("/mock/v1/picks", json={}).json()["prefetched"] is False

    # index.json edits too: map a new barcode
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    idx["scan_barcode_by_code"]["4006381333931"] = "scan_barcode.ripe.json"
    (tmp_path / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    r = client.post("/mock/v1/scan/barcode", json={"code": "4006381333931"})
    assert r.status_code == 200 and r.headers["x-pear-mock-fixture"] == "scan_barcode.ripe.json"


def test_broken_fixture_reports_file(client, tmp_path, monkeypatch):
    for p in FIXTURES.iterdir():
        shutil.copy(p, tmp_path / p.name)
    (tmp_path / "picks.default.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(mock, "fixtures_dir", lambda: tmp_path)
    r = client.post("/mock/v1/picks", json={})
    assert r.status_code == 500 and "picks.default.json" in r.json()["error"]["message"]


# ----------------------------------------------------------------------------- twins


def _routes(app) -> set[tuple[str, str]]:
    """(method, path) for every route, flattening included routers (FastAPI >= 0.141)."""
    try:
        from fastapi.routing import iter_route_contexts
        items = list(iter_route_contexts(app.routes))
    except ImportError:  # older FastAPI: included routes are flat APIRoutes
        items = [r for r in app.routes if isinstance(r, APIRoute)]
    out = set()
    for route in items:
        for m in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            out.add((m, route.path))
    return out


def test_mock_router_covers_every_agreed_v1_endpoint():
    have = _routes(_mock_app())
    missing = {(m, p) for m, p in V1_ENDPOINTS if (m, "/mock" + p) not in have}
    assert not missing, f"mock is missing {missing}"
    assert ("GET", "/mock/health") in have


def test_every_v1_route_has_a_mock_twin():
    import main

    have = _routes(main.app)
    v1 = {(m, p) for m, p in have if p.startswith("/v1/")}
    missing = sorted((m, p) for m, p in v1 if (m, "/mock" + p) not in have)
    assert not missing, f"/v1 routes without a /mock twin: {missing}"
    assert "routes.mock" not in main.app.state.router_errors


def test_mock_through_main_app_uses_middleware_request_id():
    """Full app (middleware + exception handlers): the client's X-Request-ID flows into the body."""
    import main

    with TestClient(main.app) as c:
        r = c.post("/mock/v1/scan/barcode", json={"code": "0000000000017", "household": HOUSEHOLD},
                   headers={"X-Request-ID": "ios-test-123"})
        assert r.status_code == 200 and r.headers["x-request-id"] == "ios-test-123"
        assert r.json()["request_id"] == "ios-test-123"
        err = c.post("/mock/v1/scan/barcode", json={"code": "123"}, headers={"X-Request-ID": "ios-404"})
        assert err.status_code == 404 and err.json()["error"]["request_id"] == "ios-404"
        assert c.get("/mock/health").status_code == 200
        ev = c.post("/mock/v1/explain?event_ms=0", json={})
        assert ev.status_code == 200 and "event: done" in ev.text
