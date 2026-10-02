"""Ask Pear (addendum 3 + 6 + 11): one question in, a templated answer + ScanResult out.

    POST /v1/ask (routes/ask.py)
      JSON  {"text", "language", "last_product_key", "household", "asker_id"}   (primary)
      multipart audio (m4a) + household + language + last_product_key + asker_id (fallback)
        -> answer(clients, req, uid)          engine: settings.ask_engine ("adk" | "direct")
             adk:    agent.graph.run_ask(clients, body, uid)  (same node functions as below)
                     any non-PearError failure or no output -> log ask.adk_fallback, answer_direct
             direct: answer_direct(clients, req, uid)

Node functions (shared with the ADK Workflow; each takes a plain dict body =
AskRequest.model_dump(mode="json") + {"uid": ...}, merged with the understood fields):

    understand(clients, body) -> dict   ONE Gemini call (question_reader, or the audio variant):
                                        language, intent, product_query, member_ids,
                                        refers_to_last_product, transcript, answer_language.
                                        Memoized in body["understood"], so an ADK fallback or a
                                        pre-understood audio question never calls Gemini twice.
    check(clients, body) -> dict        resolve product -> rules -> rules.templates.ask_answer
    alternatives(clients, body) -> dict resolve product -> pipeline.picks.get -> picks_answer
    scan_needed(body) -> dict           next="scan_needed", rules.templates.scan_needed_answer

Code routes on intent (the model never picks a state):
    check / alternatives need a product: last_product_key (pipeline.products.load_product) when
    the question refers to it or names no product; else match_by_name(query=product_query).
    No product -> scan_needed. Unclear -> scan_needed.
Answer language: detected language if en/vi/es, else the asker's language (asker_id or the
first member), else en.

Debugging
- ?debug=1: debug.ask_engine ("adk" | "direct" | "fallback"), debug.adk_error, debug.ask
  (understood fields + how the product was resolved), debug.gemini.question_reader.
- Log events: ask.understood, ask.resolved, ask.adk_fallback.
- Force the direct engine: PEAR_ASK_ENGINE=direct.
- Tests: tests/test_ask.py.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from core.clients import Clients
from core.errors import PearError
from core.logging import log_event
from core.timing import debug_payload, debug_put, request_id, stage, timings
from models.contracts import (
    SUPPORTED_LANGUAGES,
    Answer,
    AskRequest,
    AskResponse,
    Household,
    PicksResponse,
    base_language,
)
from models.domain import Evaluation, NormalizedProduct
from pipeline import result as result_mod
from pipeline.gemini import audio_part, generate_json
from pipeline.prompts import (
    AUDIO_SCHEMA,
    AUDIO_SYSTEM,
    QUESTION_SCHEMA,
    QUESTION_SYSTEM,
    question_context,
    say,
)

INTENTS = ("check", "alternatives", "unclear")
_UNDERSTOOD_KEY = "understood"


# ----------------------------------------------------------------------------- lazy deps


def _products():
    from pipeline import products

    return products


def _picks():
    from pipeline import picks

    return picks


def _templates():
    from rules import templates

    return templates


def _run_ask():
    from agent.graph import run_ask

    return run_ask


async def _maybe_await(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


# ----------------------------------------------------------------------------- body helpers


def _household(body: dict) -> Household:
    hh = body.get("household")
    if isinstance(hh, Household):
        return hh
    return Household.model_validate(hh or {})


def _supported(lang: str | None) -> str | None:
    if not lang:
        return None
    b = base_language(lang)
    return b if b in SUPPORTED_LANGUAGES else None


def _asker(hh: Household, asker_id: str | None):
    if asker_id:
        for m in hh.members:
            if m.id == asker_id:
                return m
    return hh.members[0] if hh.members else None


def answer_language(detected: str | None, hint: str | None, hh: Household, asker_id: str | None) -> str:
    """Detected language if en/vi/es; else (nothing detected) the phone hint; else the asker's."""
    if _supported(detected):
        return _supported(detected)  # type: ignore[return-value]
    if not detected and _supported(hint):
        return _supported(hint)  # type: ignore[return-value]
    asker = _asker(hh, asker_id)
    return asker.language if asker is not None else "en"


def _lang(body: dict, hh: Household) -> str:
    return _supported(body.get("answer_language")) or answer_language(
        body.get("language"), body.get("language"), hh, body.get("asker_id"))


def _transcript(body: dict) -> str:
    t = body.get("transcript") or body.get("text") or ""
    return t.strip() if isinstance(t, str) else str(t)


def _member_ids(body: dict, hh: Household) -> list[str]:
    valid = {m.id for m in hh.members}
    return [m for m in body.get("member_ids") or [] if m in valid]


def _intent(body: dict) -> str:
    i = body.get("intent")
    return i if i in INTENTS else "unclear"


def _response(body: dict, hh: Household, *, intent: str, next: str, text: str, lang: str,
              member_ids: list[str], scan_result: Any = None,
              picks: PicksResponse | None = None) -> dict:
    resp = AskResponse(
        transcript=_transcript(body),
        language=base_language(body.get("language")) if body.get("language") else lang,
        intent=intent,  # type: ignore[arg-type]
        product_query=(body.get("product_query") or None),
        member_ids=member_ids,
        next=next,  # type: ignore[arg-type]
        answer=Answer(text=text, language=lang),
        scan_result=scan_result,
        picks=picks,
        timings_ms=timings(),
        request_id=request_id(),
    )
    return resp.model_dump(mode="json")


def make_body(req: AskRequest, uid: str, *, transcript: str | None = None,
              understood: dict | None = None) -> dict:
    """The plain dict the node functions (and agent.graph) operate on."""
    body = req.model_dump(mode="json")
    body["uid"] = uid
    if transcript:
        body["transcript"] = transcript
    if understood:
        body[_UNDERSTOOD_KEY] = understood
    return body


# ----------------------------------------------------------------------------- understand


def _map_members(raw_ids: Any, hh: Household) -> list[str]:
    """Model member ids -> household ids (also accepts names), deduped, family order kept."""
    by_id = {m.id: m.id for m in hh.members}
    by_name = {m.name.casefold(): m.id for m in hh.members}
    out: list[str] = []
    for x in raw_ids if isinstance(raw_ids, list) else []:
        if not isinstance(x, str):
            continue
        mid = by_id.get(x.strip()) or by_name.get(x.strip().casefold())
        if mid and mid not in out:
            out.append(mid)
    return out


def _clean_understood(data: dict, body: dict, hh: Household, *, audio: bool) -> dict:
    detected = base_language(data.get("language")) if isinstance(data.get("language"), str) and data.get("language").strip() else None
    transcript = (data.get("transcript") if audio else body.get("text")) or ""
    transcript = transcript.strip() if isinstance(transcript, str) else ""
    pq = data.get("product_query")
    pq = pq.strip() if isinstance(pq, str) and pq.strip() else None
    intent = data.get("intent") if data.get("intent") in INTENTS else "unclear"
    if audio and not transcript:
        intent = "unclear"
    lang = answer_language(detected, body.get("language"), hh, body.get("asker_id"))
    return {
        "transcript": transcript,
        "language": detected or _supported(body.get("language")) or lang,
        "answer_language": lang,
        "intent": intent,
        "product_query": pq,
        "member_ids": _map_members(data.get("member_ids"), hh),
        "refers_to_last_product": bool(data.get("refers_to_last_product")),
        # Drop audio bytes from anything merged downstream (ADK events, logs).
        "audio": None,
        "audio_mime": None,
    }


async def understand(clients: Clients, body: dict) -> dict:
    """question_reader: ONE Gemini call (text or audio). Memoized in body["understood"]."""
    memo = body.get(_UNDERSTOOD_KEY)
    if isinstance(memo, dict) and memo.get("intent"):
        return memo
    hh = _household(body)
    s = clients.settings
    audio: bytes | None = body.get("audio") if isinstance(body.get("audio"), (bytes, bytearray)) else None
    text = (body.get("text") or "").strip() if isinstance(body.get("text"), str) else ""
    ctx = question_context(hh, asker_id=body.get("asker_id"), has_last_product=bool(body.get("last_product_key")),
                           language_hint=body.get("language"), question=None if audio else text)
    if audio is not None:
        data = await generate_json(clients, call="question_reader_audio", model=s.model_question,
                                   contents=[audio_part(bytes(audio), body.get("audio_mime") or "audio/mp4"), ctx],
                                   schema=AUDIO_SCHEMA, system=AUDIO_SYSTEM, timeout_s=s.t_audio,
                                   max_output_tokens=512)
    elif text:
        data = await generate_json(clients, call="question_reader", model=s.model_question, contents=ctx,
                                   schema=QUESTION_SCHEMA, system=QUESTION_SYSTEM, timeout_s=s.t_question,
                                   max_output_tokens=384)
    else:  # nothing to read: no model call
        data = {"intent": "unclear", "member_ids": []}
    out = _clean_understood(data if isinstance(data, dict) else {}, body, hh, audio=audio is not None)
    body[_UNDERSTOOD_KEY] = out
    log_event("ask.understood", intent=out["intent"], language=out["language"],
              answer_language=out["answer_language"], member_ids=out["member_ids"],
              has_query=bool(out["product_query"]), refers_last=out["refers_to_last_product"],
              audio=audio is not None)
    debug_put("ask", {k: v for k, v in out.items() if k not in ("audio", "audio_mime")})
    return out


# ----------------------------------------------------------------------------- resolve


async def resolve_product(clients: Clients, body: dict) -> tuple[NormalizedProduct | None, str]:
    """(product, how): how is last_product | name | none."""
    last = body.get("last_product_key")
    query = body.get("product_query")
    refers = bool(body.get("refers_to_last_product"))
    np: NormalizedProduct | None = None
    how = "none"
    if last and (refers or not query):
        with stage("resolve_last"):
            np = await _products().load_product(clients, last)
        how = "last_product" if np is not None else "none"
    if np is None and query:
        with stage("resolve_name"):
            np = await _products().match_by_name(clients, query=query)
        how = "name" if np is not None else "none"
    log_event("ask.resolved", how=how, product_key=np.product.key if np else None,
              last_product_key=last, has_query=bool(query))
    debug_put("ask_resolve", {"how": how, "product_key": np.product.key if np else None})
    return np, how


def _start_prefetch(clients: Clients, np: NormalizedProduct, hh: Household) -> None:
    if np.product.category != "food":
        return
    try:
        _picks().start_prefetch(clients, np, hh)
    except Exception as e:
        log_event("ask.prefetch_failed", level=logging.WARNING, why=repr(e)[:300])


def _ask_answer_text(ev: Evaluation, hh: Household, member_ids: list[str] | None, lang: str,
                     name: str | None) -> str:
    try:
        text = _templates().ask_answer(ev, hh, member_ids, lang, name)
        if isinstance(text, str) and text.strip():
            return text
    except Exception as e:
        log_event("templates.error", level=logging.WARNING, key="ask_answer", why=repr(e)[:200])
    return ev.headline.text


def _scan_needed_text(lang: str) -> str:
    try:
        text = _templates().scan_needed_answer(lang)
        if isinstance(text, str) and text.strip():
            return text
    except Exception as e:
        log_event("templates.error", level=logging.WARNING, key="scan_needed_answer", why=repr(e)[:200])
    return say("ask.scan_needed", lang, default="I'm not sure which product you mean. Scan it and ask again.")


def _picks_text(names: list[str], lang: str) -> str:
    try:
        text = _templates().picks_answer(names, lang)
        if isinstance(text, str) and text.strip():
            return text
    except Exception as e:
        log_event("templates.error", level=logging.WARNING, key="picks_answer", why=repr(e)[:200])
    if not names:
        return say("ask.picks_none", lang, default="I couldn't find a better pick for everyone yet.")
    return say("ask.picks_some", lang, default="Better picks for everyone: {names}.", names=", ".join(names))


# ----------------------------------------------------------------------------- nodes


async def check(clients: Clients, body: dict) -> dict:
    """intent=check: resolve -> rules -> templated answer + ScanResult. No Gemini."""
    hh = _household(body)
    np, _how = await resolve_product(clients, body)
    if np is None:
        return scan_needed({**body, "intent": "check"})
    lang = _lang(body, hh)
    ids = _member_ids(body, hh)
    ev = result_mod.run_rules(np, hh, ids or None)
    _start_prefetch(clients, np, hh)
    text = _ask_answer_text(ev, hh, ids or None, lang, np.product.name)
    sr = result_mod.build_scan_result(np, hh, ids or None, evaluation=ev)
    return _response(body, hh, intent="check", next="result", text=text, lang=lang,
                     member_ids=[v.member_id for v in ev.verdicts], scan_result=sr)


async def _get_picks(clients: Clients, np: NormalizedProduct, hh: Household,
                     ids: list[str] | None) -> PicksResponse:
    picks = _picks()
    try:
        try:
            out = await picks.get(clients, np.product.key, hh, ids, np=np)
        except TypeError:  # older signature without np=
            out = await picks.get(clients, np.product.key, hh, ids)
        if isinstance(out, dict):
            out = PicksResponse.model_validate(out)
        return out
    except PearError:
        raise
    except Exception as e:
        log_event("ask.picks_failed", level=logging.WARNING, why=repr(e)[:300])
        return PicksResponse(items=[], prefetched=False, timings_ms={}, request_id=request_id())


async def alternatives(clients: Clients, body: dict) -> dict:
    """intent=alternatives: resolve -> pipeline.picks.get -> templated picks answer."""
    hh = _household(body)
    np, _how = await resolve_product(clients, body)
    if np is None:
        return scan_needed({**body, "intent": "alternatives"})
    lang = _lang(body, hh)
    ids = _member_ids(body, hh)
    picks = await _get_picks(clients, np, hh, ids or None)
    names = [it.product.name or it.product.brand for it in picks.items if (it.product.name or it.product.brand)]
    text = _picks_text(names, lang)
    ev = result_mod.run_rules(np, hh, ids or None)
    sr = result_mod.build_scan_result(np, hh, ids or None, evaluation=ev)
    return _response(body, hh, intent="alternatives", next="result", text=text, lang=lang,
                     member_ids=[v.member_id for v in ev.verdicts], scan_result=sr, picks=picks)


def scan_needed(body: dict) -> dict:
    """intent unclear, or no product resolvable: ask the user to scan it."""
    hh = _household(body)
    lang = _lang(body, hh)
    return _response(body, hh, intent=_intent(body), next="scan_needed", text=_scan_needed_text(lang),
                     lang=lang, member_ids=_member_ids(body, hh))


# ----------------------------------------------------------------------------- entry points


def _finish(resp: AskResponse) -> AskResponse:
    return resp.model_copy(update={"timings_ms": timings(), "request_id": request_id(),
                                   "debug": debug_payload()})


async def _route(clients: Clients, merged: dict) -> dict:
    intent = _intent(merged)
    if intent == "check":
        return await check(clients, merged)
    if intent == "alternatives":
        return await alternatives(clients, merged)
    return scan_needed(merged)


async def answer_direct(clients: Clients, req: AskRequest, uid: str, *, transcript: str | None = None,
                        understood: dict | None = None) -> AskResponse:
    """FastAPI-only engine: understand (unless given) -> route on intent in code."""
    body = make_body(req, uid, transcript=transcript, understood=understood)
    got = await understand(clients, body)
    merged = {**body, **got}
    out = await _route(clients, merged)
    return _finish(AskResponse.model_validate(out))


async def answer(clients: Clients, req: AskRequest, uid: str, *, transcript: str | None = None,
                 understood: dict | None = None) -> AskResponse:
    """settings.ask_engine == "adk": ADK Workflow, falling back to answer_direct on failure."""
    if clients.settings.ask_engine != "adk":
        debug_put("ask_engine", "direct")
        return await answer_direct(clients, req, uid, transcript=transcript, understood=understood)
    body = make_body(req, uid, transcript=transcript, understood=understood)
    try:
        out = await _maybe_await(_run_ask()(clients, body, uid))
        if out is None:
            raise RuntimeError("ADK workflow returned no output")
        resp = AskResponse.model_validate(out)
    except PearError:
        # A contract error (Gemini timeout, rate limit, ...) would repeat in the direct engine
        # and cost a second model call; surface it as is.
        debug_put("ask_engine", "adk")
        raise
    except Exception as e:
        why = repr(e)[:300]
        log_event("ask.adk_fallback", level=logging.WARNING, why=why)
        debug_put("ask_engine", "fallback")
        debug_put("adk_error", why)
        memo = body.get(_UNDERSTOOD_KEY) if isinstance(body.get(_UNDERSTOOD_KEY), dict) else understood
        return await answer_direct(clients, req, uid, transcript=transcript, understood=memo)
    debug_put("ask_engine", "adk")
    return _finish(resp)


def audio_mime(content_type: str | None, filename: str | None = None) -> str:
    """Upload content type -> Gemini audio MIME. m4a (AAC in MP4) -> audio/mp4."""
    ct = (content_type or "").split(";")[0].strip().lower()
    name = (filename or "").lower()
    if ct in ("audio/m4a", "audio/x-m4a", "audio/mp4", "audio/aac", "audio/mp4a-latm", "video/mp4") \
            or name.endswith((".m4a", ".mp4", ".aac")):
        return "audio/mp4"
    if ct.startswith("audio/"):
        return ct  # wav, mpeg, ogg, webm, flac: Gemini accepts these as sent
    return "audio/mp4"  # application/octet-stream or missing: the app records m4a


async def answer_audio(clients: Clients, *, audio: bytes, mime: str, household: Household,
                       language: str | None, last_product_key: str | None, asker_id: str | None,
                       uid: str) -> AskResponse:
    """Audio fallback: one Gemini call transcribes + understands, then the same routing."""
    req = AskRequest(text="", language=language, last_product_key=last_product_key or None,
                     household=household, asker_id=asker_id or None)
    body = make_body(req, uid)
    body["audio"], body["audio_mime"] = audio, mime
    got = await understand(clients, body)
    transcript = got.get("transcript") or ""
    req = req.model_copy(update={"text": transcript})
    return await answer(clients, req, uid, transcript=transcript or None, understood=got)
