"""scripts/smoke.py checks, driven in-process (no network).

Smoke takes any httpx.Client; FastAPI's TestClient is one, so these tests run the same
validation code against the real app (memory store, fake Gemini) that `make smoke` runs
against Cloud Run. Pure helpers (percentile, Server-Timing parsing, SSE, CSV) and the
pass/fail logic are covered with httpx.MockTransport so they don't depend on other lanes.

Debug a failure: run `python -m scripts.smoke --url http://localhost:8080 --mock-only -v`
against `make dev` to see each request with its status and problem.
"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts import dev_info, smoke


def _mock_available(client) -> bool:
    import main

    return "routes.mock" not in main.app.state.router_errors


# ----------------------------------------------------------------------------- in-process app


def test_health_validates_against_contract(app_client, fake_genai):
    fake_genai.on("Reply with: ok", "ok")
    s = smoke.Smoke(app_client)
    body = s.check_health(deep=True, require_ok=False)
    assert s.failures == [], [r.problem for r in s.failures]
    assert body is not None and body["store"] == "memory"
    assert "checks" in body and "firestore" in body["checks"]
    assert s.results[0].status == 200


def test_mock_endpoints_match_contracts(app_client):
    if not _mock_available(app_client):
        pytest.skip("routes.mock failed to import (contracts lane still writing)")
    s = smoke.Smoke(app_client)
    s.check_mock()
    assert s.failures == [], "\n".join(f"{r.name} [{r.status}]: {r.problem}" for r in s.failures)
    names = {r.name for r in s.results}
    for expected in ("GET /mock/health", "POST /mock/v1/scan/barcode", "POST /mock/v1/scan/photo",
                     "POST /mock/v1/ask", "POST /mock/v1/picks", "POST /mock/v1/prices",
                     "POST /mock/v1/profile/parse", "POST /mock/v1/explain", "GET /mock/v1/kb/search"):
        assert expected in names
    # The unknown barcode and every ?error= knob came back as *expected* errors.
    expected_errors = {r.expected_error for r in s.results if r.expected_error}
    assert set(smoke.ERROR_CODES) <= expected_errors
    # Mock JSON responses carry the middleware's Server-Timing total.
    assert any(r.server_ms is not None for r in s.results)


def test_main_mock_only_exit_code(app_client, fake_genai, capsys):
    if not _mock_available(app_client):
        pytest.skip("routes.mock failed to import (contracts lane still writing)")
    fake_genai.on("Reply with: ok", "ok")
    import main as app_main

    rc = smoke.main(["--mock-only"], client=app_client)
    out = capsys.readouterr().out
    assert "endpoint" in out and "client p50" in out
    healthy = app_main.app.state.router_errors == {} or not any(
        v["required"] for v in app_main.app.state.router_errors.values())
    if healthy:
        assert rc == 0, out
    else:  # another lane's required router is broken right now: smoke must say so
        assert rc == 1 and "ok=false" in out


# ----------------------------------------------------------------------------- pass/fail logic


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pear.test")


def _health_body(**over) -> dict:
    body = {"ok": True, "service": "pear-api", "version": "0.3.0+abc", "env": "local", "region": "us-central1",
            "store": "memory", "gemini": "ok", "auth": "optional", "ask_engine": "adk", "kb_version": "1",
            "uptime_s": 3, "features": {"picks": True}}
    body.update(over)
    return body


def test_contract_mismatch_is_a_failure():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_health_body(surprise_field=1),
                              headers={"Server-Timing": "cache_get;dur=3, total;dur=12"})

    s = smoke.Smoke(_client(handler), household={})
    s.check_health(deep=False)
    assert len(s.failures) == 1
    assert "HealthResponse mismatch" in s.failures[0].problem and "surprise_field" in s.failures[0].problem
    assert s.results[0].server_ms == 12
    assert s.report() == 1


def test_health_not_ok_fails_only_when_required():
    def handler(req):
        return httpx.Response(200, json=_health_body(ok=False, checks={"router_errors": {"routes.ask": {}}}))

    strict = smoke.Smoke(_client(handler), household={})
    strict.check_health(deep=False)
    assert strict.failures and "ok=false" in strict.failures[0].problem
    lax = smoke.Smoke(_client(handler), household={})
    lax.check_health(deep=False, require_ok=False)
    assert lax.failures == [] and lax.warnings


def test_expected_vs_unexpected_errors():
    def handler(req):
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "No product", "next": "take_photo",
                                                   "request_id": "r1"}}, headers={"X-Request-ID": "r1"})

    s = smoke.Smoke(_client(handler), household={})
    res, _ = s.call("x", "POST", "/v1/scan/barcode", model=None, json={}, expect_errors=("not_found",))
    assert res.ok and res.expected_error == "not_found" and res.request_id == "r1"
    res, _ = s.call("x", "POST", "/v1/scan/barcode", model=None, json={})
    assert not res.ok and "unexpected HTTP 404 not_found" in res.problem


def test_error_with_bad_shape_and_non_json_fail():
    def handler(req):
        if req.url.path == "/html":
            return httpx.Response(502, text="<html>Bad gateway</html>", headers={"content-type": "text/html"})
        return httpx.Response(500, json={"detail": "boom"})

    s = smoke.Smoke(_client(handler), household={})
    res, _ = s.call("x", "GET", "/json", model=None, expect_errors=("internal",))
    assert not res.ok and "not ErrorResponse" in res.problem
    res, _ = s.call("y", "GET", "/html", model=None)
    assert not res.ok and "non-JSON" in res.problem


def test_transport_error_is_recorded():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    s = smoke.Smoke(_client(handler), household={})
    res, body = s.call("x", "GET", "/health", model=None)
    assert body is None and not res.ok and "transport error" in res.problem


def test_barcode_check_collects_keys_and_calls_picks():
    scan = json.loads((smoke.REPO_DIR / "contracts" / "fixtures" / "scan_barcode.ripe.json").read_text()) \
        if (smoke.REPO_DIR / "contracts" / "fixtures" / "scan_barcode.ripe.json").exists() else None
    if scan is None:
        pytest.skip("contracts/fixtures/scan_barcode.ripe.json missing")
    seen: list[str] = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/v1/scan/barcode":
            return httpx.Response(200, json=scan)
        return httpx.Response(200, json={"items": [], "prefetched": False, "timings_ms": {"total": 4}})

    s = smoke.Smoke(_client(handler), household={"members": []})
    keys = s.check_barcodes(["3017620422003"], n=3)
    assert keys == [scan["product"]["key"]]
    assert seen.count("/v1/scan/barcode") == 3 and seen.count("/v1/picks") == 1
    assert s.failures == [], [r.problem for r in s.failures]


# ----------------------------------------------------------------------------- pure helpers


def test_percentile():
    assert smoke.percentile([], 50) is None
    assert smoke.percentile([7], 95) == 7
    assert smoke.percentile([1, 2, 3, 4], 50) == 2.5
    assert smoke.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_server_total_prefers_body_then_header():
    r = httpx.Response(200, headers={"Server-Timing": "lookup;dur=400, total;dur=610"})
    assert smoke.server_total(r, {"timings_ms": {"total": 99}}) == 99
    assert smoke.server_total(r, {"timings_ms": {}}) == 610
    assert smoke.server_total(httpx.Response(200), None) is None
    # /mock bodies carry canned timings; the header is the real server time.
    assert smoke.server_total(r, {"timings_ms": {"total": 99}}, prefer_header=True) == 610


def test_parse_sse():
    good = ('data: {"member_id": "m_mia", "language": "en", "text": "Hi"}\n\n'
            'data: [DONE]\n\nevent: done\ndata: {}\n\n')
    events, problem = smoke.parse_sse(good)
    assert problem is None and len(events) == 1
    _, problem = smoke.parse_sse('data: {"member_id": "m_mia"}\n\n')
    assert problem and "ExplainEvent" in problem
    _, problem = smoke.parse_sse("event: ping\n\n")
    assert problem == "SSE stream had no data: events"


def test_csv_barcodes_real_codes_only(tmp_path):
    p = tmp_path / "products.csv"
    p.write_text("barcode,path,expected\n0000000000017,barcode,ALLERGY\n3017620422003,barcode,EASY\n"
                 ",photo,UNSURE\n3017620422003,barcode,EASY\n049000028911,barcode,SKIP\n", encoding="utf-8")
    assert smoke.load_csv_barcodes(p) == ["3017620422003", "049000028911"]
    assert smoke.load_csv_barcodes(tmp_path / "missing.csv") == []


def test_pick_barcodes_cli_wins():
    assert smoke.pick_barcodes(["123456789012"]) == (["123456789012"], "--barcode")
    codes, where = smoke.pick_barcodes([])
    assert codes and where


def test_dev_info_render():
    text = dev_info.render(8080, ips=["192.168.1.20"])
    assert "http://192.168.1.20:8080/health" in text
    assert "http://localhost:8080/docs" in text and "store=" in text
    assert "No LAN IPv4" in dev_info.render(8080, ips=[])
