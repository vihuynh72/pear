"""Smoke test a running Pear API: contracts + latency, against Cloud Run or `make dev`.

    python -m scripts.smoke --url https://pear-api-xxxx.run.app        # from api/
    python -m scripts.smoke --url http://localhost:8080 --mock-only
    python -m scripts.smoke --url $URL --n 5 --barcode 3017620422003 --barcode 0049000028911
    make smoke URL=https://...     (repo root; defaults to http://localhost:8080)

What it checks
  1. GET /health?deep=1           HealthResponse; ok must be true (Firestore/Gemini warn only)
  2. every /mock endpoint         GET /mock/health, /mock/fixtures, /mock/v1/kb/search, POST
                                  scan/barcode (each code in the fixture index + an unknown code),
                                  scan/photo (multipart), ask (JSON + every ask scenario), picks,
                                  prices, profile/parse, explain (SSE), and every ?error=<code>
  3. POST /v1/scan/barcode        N times per barcode (first call is usually the cache miss),
                                  then POST /v1/picks for the scanned product
  4. GET /v1/kb/search            KbSearchResponse
  Optional: --ask (POST /v1/ask, real Gemini), --prices (POST /v1/prices, up to 6 s).

Every JSON body is validated against the Pydantic models in models/contracts.py. Output
models forbid unknown keys, so an extra or renamed field is a failure too. Errors count as
"expected" only where listed (e.g. a barcode OFF doesn't know -> 404 not_found) and their
body must still match ErrorResponse. Exit code 1 on any contract mismatch, unexpected
status or transport error.

Table: client wall ms (includes network) and server ms (body timings_ms.total, else the
Server-Timing header's total) p50/p95 per endpoint.

Debugging: --verbose prints each request with status, X-Request-ID and the first problem;
grep Cloud Logging for that request_id (`make logs`). The check functions take any
httpx.Client, so tests drive them with FastAPI's TestClient (tests/test_smoke_script.py).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

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

API_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = API_DIR.parent
HOUSEHOLD_PATH = REPO_DIR / "contracts" / "household.example.json"
DEMO_CSV = REPO_DIR / "demo" / "products.csv"
# Nutella: the canonical Open Food Facts example product, used when no real code is given.
DEFAULT_BARCODES = ["3017620422003"]
UNKNOWN_MOCK_CODE = "0000000000999"
# A tiny 1x1 grayscale baseline JPEG (SOI..EOI). Only sent to /mock, which ignores content.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f"
    "141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101"
    "011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403"
    "050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a1617"
    "18191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a83"
    "8485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7"
    "d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9"
)
ERROR_CODES = ["not_found", "too_blurry", "unreadable", "rate_limited", "internal",
               "unauthorized", "unavailable", "bad_request"]


# ----------------------------------------------------------------------------- helpers


@dataclass
class Result:
    name: str  # table row, e.g. "POST /v1/scan/barcode"
    status: int | None
    client_ms: float
    server_ms: int | None = None
    problem: str | None = None  # None = passed
    expected_error: str | None = None  # error code that was allowed here
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank-with-interpolation percentile (p in 0..100). None for no values."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    k = (len(vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return float(vals[lo] + (vals[hi] - vals[lo]) * (k - lo))


def validate(model: type[BaseModel], data: Any) -> str | None:
    """None if `data` matches the contract model, else a short readable problem."""
    try:
        model.model_validate(data)
        return None
    except ValidationError as e:
        errs = e.errors()
        parts = [f"{'.'.join(str(x) for x in er.get('loc', ())) or '<root>'}: {er.get('msg')}" for er in errs[:3]]
        more = f" (+{len(errs) - 3} more)" if len(errs) > 3 else ""
        return f"{model.__name__} mismatch: " + "; ".join(parts) + more


_ST_TOTAL = re.compile(r"(?:^|,)\s*total;dur=(\d+(?:\.\d+)?)")


def server_total(resp: httpx.Response, body: Any, *, prefer_header: bool = False) -> int | None:
    """Server time: body timings_ms.total, else the Server-Timing header's `total`.

    prefer_header=True for /mock: fixture bodies carry canned timings_ms, the header is real.
    """
    m = _ST_TOTAL.search(resp.headers.get("server-timing", ""))
    if prefer_header and m:
        return int(float(m.group(1)))
    if isinstance(body, dict):
        tm = body.get("timings_ms")
        if isinstance(tm, dict) and isinstance(tm.get("total"), (int, float)):
            return int(tm["total"])
    return int(float(m.group(1))) if m else None


def parse_sse(text: str) -> tuple[list[dict], str | None]:
    """SSE transcript -> ExplainEvent dicts. Returns (events, problem).

    Only default events (no `event:` line, or `event: message`) are ExplainEvents. Named
    control events such as `event: done` / `event: error` are skipped, as is `data: [DONE]`.
    """
    events: list[dict] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        raw = "\n".join(data_lines).strip()
        if name != "message" or not raw or raw == "[DONE]":
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError as e:
            return events, f"SSE data is not JSON ({e}): {raw[:80]!r}"
        problem = validate(ExplainEvent, ev)
        if problem:
            return events, problem
        events.append(ev)
    if not events:
        return events, "SSE stream had no data: events"
    return events, None


def load_household(path: Path = HOUSEHOLD_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_real_code(code: str) -> bool:
    """Real-looking GTIN: 8-14 digits and not one of the fake 000000000xx demo codes."""
    d = re.sub(r"\D", "", code or "")
    return 8 <= len(d) <= 14 and not d.startswith("000000000")


def load_csv_barcodes(path: Path = DEMO_CSV) -> list[str]:
    """Barcodes from demo/products.csv (column barcode|code|ean|upc), real codes only, deduped."""
    if not path.exists():
        return []
    out: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            code = next((norm[k] for k in ("barcode", "code", "ean", "upc", "gtin") if norm.get(k)), "")
            if is_real_code(code) and code not in out:
                out.append(code)
    return out


# ----------------------------------------------------------------------------- runner


@dataclass
class Smoke:
    """Runs checks through any httpx.Client (real network, or FastAPI's TestClient)."""

    client: httpx.Client
    household: dict = field(default_factory=load_household)
    verbose: bool = False
    results: list[Result] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    health: dict | None = None

    # --- core request helper ---

    def call(self, name: str, method: str, path: str, *, model: type[BaseModel] | None,
             expect_errors: tuple[str, ...] = (), sse: bool = False, **kw: Any) -> tuple[Result, Any]:
        """Send one request, validate it, record a Result. Returns (result, parsed body)."""
        t = time.perf_counter()
        try:
            resp = self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            res = Result(name, None, (time.perf_counter() - t) * 1000, problem=f"transport error: {e!r}")
            return self._record(res, path), None
        ms = (time.perf_counter() - t) * 1000
        rid = resp.headers.get("x-request-id")
        if sse and resp.status_code < 300:
            _, problem = parse_sse(resp.text)
            res = Result(name, resp.status_code, ms, server_total(resp, None), problem, request_id=rid)
            return self._record(res, path), resp.text
        try:
            body = resp.json()
        except ValueError:
            res = Result(name, resp.status_code, ms, server_total(resp, None),
                         f"non-JSON body ({resp.headers.get('content-type')}): {resp.text[:120]!r}",
                         request_id=rid)
            return self._record(res, path), None
        res = Result(name, resp.status_code, ms,
                     server_total(resp, body, prefer_header=path.startswith("/mock")), request_id=rid)
        if resp.status_code >= 300:
            res.problem = self._check_error(resp.status_code, body, expect_errors)
            if res.problem is None:
                res.expected_error = body["error"]["code"]
        elif model is not None:
            res.problem = validate(model, body)
        return self._record(res, path), body

    def _check_error(self, status: int, body: Any, expect_errors: tuple[str, ...]) -> str | None:
        shape = validate(ErrorResponse, body)
        code = body.get("error", {}).get("code") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
        if shape:
            return f"HTTP {status} with a body that is not ErrorResponse: {shape}"
        if code not in expect_errors:
            msg = body["error"].get("message")
            return f"unexpected HTTP {status} {code}: {msg}"
        return None

    def _record(self, res: Result, path: str) -> Result:
        self.results.append(res)
        if self.verbose or not res.ok:
            mark = "ok  " if res.ok else "FAIL"
            extra = f" (expected {res.expected_error})" if res.expected_error else ""
            print(f"  {mark} {res.status} {res.client_ms:7.0f} ms  {path}{extra}"
                  f"  rid={res.request_id or '-'}" + (f"\n       {res.problem}" if res.problem else ""))
        return res

    # --- checks ---

    def check_health(self, *, deep: bool = True, require_ok: bool = True) -> dict | None:
        res, body = self.call("GET /health", "GET", "/health", model=HealthResponse,
                              params={"deep": 1} if deep else None)
        if not res.ok or not isinstance(body, dict):
            return None
        self.health = body
        if not body.get("ok"):
            msg = f"/health ok=false: checks={json.dumps(body.get('checks'), default=str)[:400]}"
            if require_ok:
                res.problem = msg
                print(f"  FAIL {msg}")
            else:
                self.warnings.append(msg)
        if body.get("env") == "cloudrun" and body.get("store") != "firestore":
            self.warnings.append("store is not firestore on Cloud Run (ADC / datastore.user problem)")
        if not str(body.get("gemini", "")).startswith("ok"):
            self.warnings.append(f"gemini: {body.get('gemini')}")
        checks = body.get("checks") or {}
        for name in ("firestore", "gemini"):
            c = checks.get(name)
            if isinstance(c, dict) and not c.get("ok"):
                self.warnings.append(f"deep {name} check failed: {c.get('why')}")
        return body

    def check_mock(self) -> None:
        hh = self.household
        self.call("GET /mock/health", "GET", "/mock/health", model=HealthResponse)
        _, fx = self.call("GET /mock/fixtures", "GET", "/mock/fixtures", model=None)
        index = fx.get("index", {}) if isinstance(fx, dict) else {}
        scenarios = index.get("scenarios", {}) if isinstance(index, dict) else {}

        self.call("GET /mock/v1/kb/search", "GET", "/mock/v1/kb/search",
                  model=KbSearchResponse, params={"q": "pea", "lang": "en"})

        codes = list((index.get("scan_barcode_by_code") or {}).keys()) or ["0000000000017"]
        for code in codes:
            self.call("POST /mock/v1/scan/barcode", "POST", "/mock/v1/scan/barcode", model=ScanResult,
                      json={"code": code, "household": hh})
        self.call("POST /mock/v1/scan/barcode", "POST", "/mock/v1/scan/barcode", model=ScanResult,
                  json={"code": UNKNOWN_MOCK_CODE, "household": hh}, expect_errors=("not_found",))

        photo_fixtures = [None, *scenarios.get("scan_photo", [])]
        for fixture in photo_fixtures:
            self.call("POST /mock/v1/scan/photo", "POST", "/mock/v1/scan/photo", model=ScanResult,
                      params={"fixture": fixture} if fixture else None,
                      files={"image": ("label.jpg", TINY_JPEG, "image/jpeg")},
                      data={"household": json.dumps(hh)})

        ask = {"text": "Can Bà eat these noodles?", "language": "en", "household": hh}
        self.call("POST /mock/v1/ask", "POST", "/mock/v1/ask", model=AskResponse, json=ask)
        for fixture in scenarios.get("ask", []):
            self.call("POST /mock/v1/ask", "POST", "/mock/v1/ask", model=AskResponse, json=ask,
                      params={"fixture": fixture})

        pk = {"product_key": "off:0000000000017", "household": hh}
        self.call("POST /mock/v1/picks", "POST", "/mock/v1/picks", model=PicksResponse, json=pk)
        self.call("POST /mock/v1/prices", "POST", "/mock/v1/prices", model=PricesResponse,
                  json={"product_keys": ["off:0000000000079", "off:0000000000086"], "country": "US"})
        self.call("POST /mock/v1/profile/parse", "POST", "/mock/v1/profile/parse",
                  model=ProfileParseResponse, json={"text": "peanut allergy, vegan", "language": "en"})
        self.call("POST /mock/v1/explain", "POST", "/mock/v1/explain", model=None, sse=True,
                  json=pk, params={"event_ms": 0})

        for code in ERROR_CODES:
            self.call("POST /mock/v1/scan/barcode?error", "POST", "/mock/v1/scan/barcode", model=None,
                      params={"error": code}, json={"code": "0000000000017", "household": hh},
                      expect_errors=(code,))

    def check_kb(self) -> None:
        self.call("GET /v1/kb/search", "GET", "/v1/kb/search", model=KbSearchResponse,
                  params={"q": "pea", "language": "en", "lang": "en"})

    def check_barcodes(self, codes: list[str], n: int = 5, *, picks: bool = True) -> list[str]:
        """Scan each code n times; returns the product keys that came back."""
        keys: list[str] = []
        for code in codes:
            for _ in range(max(n, 1)):
                res, body = self.call("POST /v1/scan/barcode", "POST", "/v1/scan/barcode", model=ScanResult,
                                      json={"code": code, "household": self.household},
                                      expect_errors=("not_found",))
                if res.ok and res.expected_error is None and isinstance(body, dict):
                    key = body["product"]["key"]
                    if key not in keys:
                        keys.append(key)
                elif res.expected_error == "not_found":
                    self.warnings.append(f"barcode {code}: not_found (expected for unknown codes)")
                    break
                elif res.status is None:  # transport error: don't hammer a dead server
                    break
        if picks:
            for key in keys:
                self.call("POST /v1/picks", "POST", "/v1/picks", model=PicksResponse,
                          json={"product_key": key, "household": self.household},
                          expect_errors=("not_found", "unavailable"))
        return keys

    def check_prices(self, keys: list[str]) -> None:
        if keys:
            self.call("POST /v1/prices", "POST", "/v1/prices", model=PricesResponse,
                      json={"product_keys": keys[:3], "country": "US"}, expect_errors=("unavailable",))

    def check_ask(self, last_key: str | None) -> None:
        body = {"text": "Can everyone eat this?", "language": "en", "last_product_key": last_key,
                "household": self.household}
        self.call("POST /v1/ask", "POST", "/v1/ask", model=AskResponse, json=body,
                  expect_errors=("unavailable", "rate_limited"))

    # --- reporting ---

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.ok]

    def table(self) -> str:
        rows: dict[str, list[Result]] = {}
        for r in self.results:
            rows.setdefault(r.name, []).append(r)

        def fmt(v: float | None) -> str:
            return "-" if v is None else f"{v:.0f}"

        head = f"{'endpoint':<36} {'n':>3} {'pass':>4} {'client p50':>10} {'p95':>6} {'server p50':>10} {'p95':>6}"
        lines = [head, "-" * len(head)]
        for name, rs in rows.items():
            cms = [r.client_ms for r in rs]
            sms = [r.server_ms for r in rs if r.server_ms is not None]
            lines.append(f"{name[:36]:<36} {len(rs):>3} {sum(r.ok for r in rs):>4} "
                         f"{fmt(percentile(cms, 50)):>10} {fmt(percentile(cms, 95)):>6} "
                         f"{fmt(percentile(sms, 50)):>10} {fmt(percentile(sms, 95)):>6}")
        return "\n".join(lines)

    def report(self) -> int:
        print()
        print(self.table())
        if self.health:
            h = self.health
            print(f"\nserver: {h.get('service')} {h.get('version')} env={h.get('env')} store={h.get('store')} "
                  f"gemini={h.get('gemini')} kb={h.get('kb_version')} ask={h.get('ask_engine')}")
        for w in self.warnings:
            print(f"WARN {w}")
        fails = self.failures
        for r in fails:
            print(f"FAIL {r.name} [{r.status}] rid={r.request_id or '-'}: {r.problem}")
        print(f"\n{len(self.results) - len(fails)}/{len(self.results)} checks passed"
              + (f", {len(self.warnings)} warning(s)" if self.warnings else ""))
        return 1 if fails else 0


# ----------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m scripts.smoke", description=__doc__.split("\n")[0])
    p.add_argument("--url", default="http://localhost:8080", help="base URL (default %(default)s)")
    p.add_argument("--n", type=int, default=5, help="scans per barcode (default %(default)s)")
    p.add_argument("--barcode", action="append", default=[], help="barcode to scan (repeatable)")
    p.add_argument("--mock-only", action="store_true", help="only /health and /mock endpoints")
    p.add_argument("--no-mock", action="store_true", help="skip the /mock endpoints")
    p.add_argument("--ask", action="store_true", help="also POST /v1/ask (real Gemini call)")
    p.add_argument("--prices", action="store_true", help="also POST /v1/prices (up to 6 s)")
    p.add_argument("--no-deep", action="store_true", help="GET /health without ?deep=1")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request timeout s")
    p.add_argument("--token", default=None, help="Firebase ID token (Authorization: Bearer)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def pick_barcodes(cli: list[str]) -> tuple[list[str], str]:
    if cli:
        return cli, "--barcode"
    csv_codes = load_csv_barcodes()
    if csv_codes:
        return csv_codes, str(DEMO_CSV.relative_to(REPO_DIR))
    return list(DEFAULT_BARCODES), "default (no real codes in demo/products.csv)"


def run(smoke: Smoke, args: argparse.Namespace) -> int:
    print("health")
    smoke.check_health(deep=not args.no_deep)
    if smoke.results and smoke.results[0].status is None:
        print(f"server unreachable at {smoke.client.base_url}; is it running? (make dev / make url)")
        return smoke.report()
    if not args.no_mock:
        print("mock")
        smoke.check_mock()
    if not args.mock_only:
        codes, where = pick_barcodes(args.barcode)
        print(f"barcode x{args.n}: {', '.join(codes)}  [{where}]")
        keys = smoke.check_barcodes(codes, args.n)
        print("kb")
        smoke.check_kb()
        if args.prices:
            smoke.check_prices(keys)
        if args.ask:
            smoke.check_ask(keys[0] if keys else None)
    return smoke.report()


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = parse_args(argv)
    if client is not None:
        return run(Smoke(client, verbose=args.verbose), args)
    headers = {"User-Agent": "pear-smoke/1"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    print(f"smoke: {args.url}")
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=args.timeout, headers=headers,
                      follow_redirects=True) as c:
        return run(Smoke(c, verbose=args.verbose), args)


if __name__ == "__main__":
    sys.exit(main())
