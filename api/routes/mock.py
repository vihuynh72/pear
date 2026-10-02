"""Mock API: every /v1 endpoint mirrored under /mock/v1, served from contracts/fixtures.

The iOS app builds against these before (and while) the real pipeline works. Zero
credentials, zero network, no pipeline imports: only fixture files on disk.

    GET  /mock/health                      health.default.json
    GET  /mock/v1/kb/search?q=&lang=       kb_search.default.json, filtered by q
    POST /mock/v1/scan/barcode {code}      by code via index.json; unknown code -> 404 not_found
    POST /mock/v1/scan/photo  (multipart)  scan_photo.lotion.json (form fields are ignored)
    POST /mock/v1/ask  (JSON or multipart) keyword match on text (index.json `ask_keywords`)
    POST /mock/v1/picks                    picks.default.json
    POST /mock/v1/prices {product_keys}    prices.default.json, keyed to the keys you send
    POST /mock/v1/explain                  SSE from explain.sse.txt, ~150 ms between events
    POST /mock/v1/profile/parse            profile_parse.default.json
    GET  /mock/fixtures                    index.json + the list of fixture files

Query knobs on every endpoint:
    ?fixture=<name>    serve that file (e.g. scan_photo.front_only), with or without .json
    ?error=<code>      not_found|too_blurry|unreadable|rate_limited|internal|unauthorized|
                       unavailable|bad_request -> that error fixture with the real HTTP status
    ?delay_ms=N        sleep N ms first (max 30000) to test spinners and timeouts
    ?event_ms=N        explain only: gap between SSE events (default 150)

Every JSON response gets a fresh request_id (the middleware's X-Request-ID when present)
and an `X-Pear-Mock-Fixture` header naming the file that was served.

Debugging: `curl -i localhost:8080/mock/v1/scan/barcode -d '{"code":"0000000000017"}'
-H 'content-type: application/json'` and look at X-Pear-Mock-Fixture. Files live in
settings.fixtures_dir (PEAR_FIXTURES_DIR overrides); they are cached in memory and reloaded
when their mtime/size changes, so editing a fixture needs no restart. A broken fixture
returns a 500 `internal` error whose message names the file and the JSON error.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from core.config import get_settings
from core.errors import STATUS
from core.logging import log_event

router = APIRouter(prefix="/mock", tags=["mock"])

MAX_DELAY_MS = 30_000
DEFAULT_EVENT_MS = 150
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_CACHE: dict[Path, tuple[tuple[int, int], Any]] = {}


class MockFixtureError(Exception):
    """A fixture is missing or broken; rendered as an error response by the handlers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ----------------------------------------------------------------------------- loading


def fixtures_dir() -> Path:
    """Where fixtures live. Tests monkeypatch this to point at a temp copy."""
    return Path(get_settings().fixtures_dir)


def _load(path: Path, *, as_json: bool = True) -> Any:
    """Read a file through the mtime/size cache. Raises MockFixtureError on any problem."""
    try:
        st = path.stat()
    except FileNotFoundError as e:
        raise MockFixtureError("not_found", f"Mock fixture {path.name} does not exist in {path.parent}") from e
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if as_json else text
    except json.JSONDecodeError as e:
        raise MockFixtureError("internal", f"Mock fixture {path.name} is not valid JSON: {e}") from e
    _CACHE[path] = (sig, data)
    return data


def _file_name(name: str) -> str:
    """'scan_photo.front_only' -> 'scan_photo.front_only.json'; rejects path tricks."""
    name = name.strip()
    if not _NAME_RE.match(name) or ".." in name:
        raise MockFixtureError("bad_request", f"Bad fixture name {name!r}")
    if not (name.endswith(".json") or name.endswith(".txt")):
        name += ".json"
    return name


def fixture(name: str) -> Any:
    """A deep copy of fixture `name`, safe to mutate."""
    return copy.deepcopy(_load(fixtures_dir() / _file_name(name)))


def fixture_text(name: str) -> str:
    return _load(fixtures_dir() / _file_name(name), as_json=False)


def index() -> dict[str, Any]:
    try:
        data = _load(fixtures_dir() / "index.json")
    except MockFixtureError:
        return {}
    return data if isinstance(data, dict) else {}


def _default(endpoint: str, fallback: str) -> str:
    return index().get("defaults", {}).get(endpoint) or fallback


# ----------------------------------------------------------------------------- responses


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]


def _stamp(body: Any, rid: str) -> Any:
    """Put the fresh request id wherever the fixture has a request_id slot (top + nested)."""
    if isinstance(body, dict):
        if "request_id" in body:
            body["request_id"] = rid
        if isinstance(body.get("error"), dict):
            body["error"]["request_id"] = rid
        for key in ("scan_result", "picks"):
            if isinstance(body.get(key), dict) and "request_id" in body[key]:
                body[key]["request_id"] = rid
    return body


def _json(request: Request, body: Any, name: str, status: int = 200) -> JSONResponse:
    rid = _request_id(request)
    headers = {"X-Request-ID": rid, "X-Pear-Mock-Fixture": name}
    return JSONResponse(_stamp(body, rid), status_code=status, headers=headers)


def _error(request: Request, code: str, message: str | None = None) -> JSONResponse:
    """Error fixture for `code` (falls back to a synthesized body) with the real status."""
    name = index().get("errors", {}).get(code) or f"error.{code}.json"
    try:
        body = fixture(name)
    except MockFixtureError:
        body = {"error": {"code": code, "message": message or code, "next": None, "request_id": None}}
        name = "(synthesized)"
    if message:
        body["error"]["message"] = message
    return _json(request, body, name, status=STATUS.get(code, 500))


def _fixture_error(request: Request, e: MockFixtureError) -> JSONResponse:
    log_event("mock.fixture_error", code=e.code, message=e.message)
    if e.code == "not_found":  # a missing fixture is a mock setup problem, not "barcode not found"
        return _error(request, "internal", e.message)
    return _error(request, e.code, e.message)


async def _knobs(request: Request) -> JSONResponse | None:
    """Apply ?delay_ms and ?error. Returns an error response to send, or None."""
    q = request.query_params
    try:
        delay = min(max(int(q.get("delay_ms", "0") or 0), 0), MAX_DELAY_MS)
    except ValueError:
        delay = 0
    if delay:
        await asyncio.sleep(delay / 1000)
    code = (q.get("error") or "").strip()
    if code:
        if code not in STATUS:
            return _error(request, "bad_request", f"Unknown ?error={code}. Use one of: {', '.join(STATUS)}")
        return _error(request, code)
    return None


def _override(request: Request) -> str | None:
    return request.query_params.get("fixture") or None


def _respond(request: Request, default_name: str, *, tweak=None) -> Response:
    """?fixture override or default -> optional tweak(body) -> JSON (no knobs)."""
    name = _override(request) or default_name
    try:
        body = fixture(name)
        file_name = _file_name(name)
    except MockFixtureError as e:
        return _fixture_error(request, e)
    if tweak is not None and not _override(request):
        body = tweak(body)
    return _json(request, body, file_name)


async def _serve(request: Request, default_name: str, *, tweak=None) -> Response:
    """Shared path: knobs (?delay_ms, ?error) then _respond."""
    forced = await _knobs(request)
    if forced is not None:
        return forced
    return _respond(request, default_name, tweak=tweak)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ----------------------------------------------------------------------------- endpoints


@router.get("/health")
async def mock_health(request: Request) -> Response:
    return await _serve(request, _default("health", "health.default.json"))


@router.get("/fixtures")
async def mock_fixtures(request: Request) -> Response:
    files = sorted(p.name for p in fixtures_dir().glob("*") if p.suffix in (".json", ".txt"))
    return JSONResponse({"fixtures_dir": str(fixtures_dir()), "files": files, "index": index()})


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code or "")


def barcode_fixture(code: str) -> str | None:
    """Fixture for a scanned code: exact, digits only, UPC-A (12) -> EAN-13 with a leading 0."""
    table = index().get("scan_barcode_by_code", {})
    d = _digits(code)
    for candidate in (code, d, "0" + d if len(d) == 12 else None):
        if candidate and candidate in table:
            return table[candidate]
    for k, v in table.items():  # also match a table key written without leading zeros
        if d and _digits(k).lstrip("0") == d.lstrip("0"):
            return v
    return None


@router.post("/v1/scan/barcode")
async def mock_scan_barcode(request: Request) -> Response:
    forced = await _knobs(request)
    if forced is not None:
        return forced
    if _override(request):
        return _respond(request, _override(request))
    code = str((await _json_body(request)).get("code") or "").strip()
    if not code:
        return _respond(request, _default("scan_barcode", "scan_barcode.sesame_crackers.json"))
    name = barcode_fixture(code)
    if name is None:
        log_event("mock.barcode_unknown", code=code)
        return _error(request, "not_found")
    return _respond(request, name)


@router.post("/v1/scan/photo")
async def mock_scan_photo(request: Request) -> Response:
    # Multipart fields (image, front_image, household, member_ids) are accepted and ignored.
    return await _serve(request, _default("scan_photo", "scan_photo.lotion.json"))


async def _ask_input(request: Request) -> tuple[str, bool]:
    """(text, had_audio) from a JSON AskRequest or the multipart audio fallback."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        try:
            form = await request.form()
        except Exception:
            return "", False
        text = form.get("text")
        return (text if isinstance(text, str) else ""), form.get("audio") is not None
    body = await _json_body(request)
    return str(body.get("text") or ""), False


def ask_fixture(text: str, had_audio: bool = False) -> str:
    """Keyword routing from index.json `ask_keywords` (first match wins)."""
    low = (text or "").lower()
    if low.strip():
        for rule in index().get("ask_keywords", []):
            for pat in rule.get("patterns", []):
                try:
                    if re.search(pat, low):
                        return rule["fixture"]
                except re.error:
                    continue
    elif had_audio:
        return _default("ask_audio", "ask.check_vi.json")
    return _default("ask", "ask.scan_needed.json")


@router.post("/v1/ask")
async def mock_ask(request: Request) -> Response:
    text, had_audio = await _ask_input(request)

    def tweak(body: dict) -> dict:
        if text.strip() and "transcript" in body:
            body["transcript"] = text.strip()  # echo what the app sent
        return body

    return await _serve(request, _override(request) or ask_fixture(text, had_audio), tweak=tweak)


@router.post("/v1/picks")
async def mock_picks(request: Request) -> Response:
    return await _serve(request, _default("picks", "picks.default.json"))


@router.post("/v1/prices")
async def mock_prices(request: Request) -> Response:
    keys = (await _json_body(request)).get("product_keys")

    def tweak(body: dict) -> dict:
        if isinstance(keys, list) and keys:
            known = body.get("prices", {})
            body["prices"] = {str(k): known.get(str(k)) for k in keys}  # unknown keys -> null
        return body

    return await _serve(request, _default("prices", "prices.default.json"), tweak=tweak)


@router.post("/v1/profile/parse")
async def mock_profile_parse(request: Request) -> Response:
    return await _serve(request, _default("profile_parse", "profile_parse.default.json"))


@router.get("/v1/kb/search")
async def mock_kb_search(request: Request) -> Response:
    q = (request.query_params.get("q") or "").strip()
    lang = (request.query_params.get("lang") or request.query_params.get("language") or "en")[:2].lower()
    try:
        limit = int(request.query_params.get("limit") or 0)
    except ValueError:
        limit = 0

    def tweak(body: dict) -> dict:
        body["query"] = q
        out = []
        for s in body.get("suggestions", []):
            labels = s.get("labels") or {}
            hay = " ".join([s.get("id", ""), s.get("label", ""), *labels.values()]).lower()
            if q and q.lower() not in hay:
                continue
            s["label"] = labels.get(lang) or s.get("label")
            out.append(s)
        body["suggestions"] = out[:limit] if limit > 0 else out
        return body

    return await _serve(request, _default("kb_search", "kb_search.default.json"), tweak=tweak)


def _sse_blocks(text: str) -> list[str]:
    """Split an SSE transcript into events (blank-line separated), keeping each block intact."""
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n"))]
    return [b for b in blocks if b.strip()]


def _block_member(block: str) -> str | None:
    for line in block.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip()).get("member_id")
            except Exception:
                return None
    return None


@router.post("/v1/explain")
async def mock_explain(request: Request) -> Response:
    forced = await _knobs(request)
    if forced is not None:
        return forced
    name = _file_name(_override(request) or _default("explain", "explain.sse.txt"))
    try:
        blocks = _sse_blocks(fixture_text(name))
    except MockFixtureError as e:
        return _fixture_error(request, e)
    wanted = (await _json_body(request)).get("member_ids")
    if isinstance(wanted, list) and wanted:
        blocks = [b for b in blocks if _block_member(b) in (None, *wanted)]
    try:
        gap = min(max(int(request.query_params.get("event_ms", DEFAULT_EVENT_MS)), 0), 5000) / 1000
    except ValueError:
        gap = DEFAULT_EVENT_MS / 1000

    async def stream() -> AsyncIterator[str]:
        for i, block in enumerate(blocks):
            if i:
                await asyncio.sleep(gap)
            yield block + "\n\n"

    rid = _request_id(request)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Request-ID": rid, "X-Pear-Mock-Fixture": name})
