"""POST /v1/explain (plan 7.4, P1): one friendlier line per member, streamed as each is ready.

    explain_stream(clients, product_key=..., household=..., member_ids=None)
      load product (pipeline.products.load_product; missing -> PearError not_found)
      -> rules (same verdicts as the scan) -> one Gemini call PER MEMBER, concurrently
      -> yield ExplainEvent(member_id, language, text) as each completes (asyncio.as_completed)

Every line is guarded before it is sent. It falls back to the verdict's templated reason text
(already in the member's language, from the rules engine) when the model line:
    - contains a banned word (safe, an toàn, seguro, cure, treat, diagnose, doctor-approved)
    - is longer than 25 words, is empty, or is not in the member's language
    - or the Gemini call failed / timed out for that member
So the stream never errors midway: a member always gets a line. Gemini never changes a state.

Debugging
- ?debug=1 is not available on a stream; read the log events instead:
  explain.line (member_id, source=gemini|fallback, why), explain.done (counts, ms).
- curl -N -X POST localhost:8080/v1/explain -H 'content-type: application/json' \
       -d '{"product_key": "off:...", "household": {...}}'
- Tests: tests/test_explain.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from core.clients import Clients
from core.errors import PearError
from core.logging import log_event
from models.contracts import ExplainEvent, Household, Member, Verdict
from models.domain import NormalizedProduct
from pipeline import result as result_mod
from pipeline.gemini import generate_json
from pipeline.prompts import EXPLAIN_SCHEMA, LANGUAGE_NAMES, explain_system, say

MAX_WORDS = 25
BANNED = re.compile(r"(?<!\w)(safe\w*|seguro\w*|segura\w*|cure[sd]?|curing|treat\w*|diagnos\w*|"
                    r"doctor[- ]approved)(?!\w)|an toàn", re.IGNORECASE)

# Vietnamese letters (lowercase). Acute vowels are shared with Spanish, so only the letters
# below in _VI_ONLY prove Vietnamese.
_VI_ALL = set("ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
_VI_ONLY = _VI_ALL - set("áéíóú")
_ES_CHARS = set("ñ¿¡")
_ES_WORDS = {"de", "la", "el", "que", "los", "las", "con", "para", "tiene", "contiene", "es", "un",
             "una", "por", "sin", "puede", "esto", "este", "esta", "lleva", "mejor", "tu", "pero",
             "del", "al", "muy", "hay", "también", "ojo", "cuidado"}
_EN_WORDS = {"the", "and", "has", "have", "contains", "with", "for", "this", "is", "it", "not",
             "may", "of", "to", "in", "your", "you", "but", "skip", "go", "fine", "great", "pick"}


def _products():
    from pipeline import products

    return products


# ----------------------------------------------------------------------------- guard


def _strip_known(text: str, known: list[str]) -> str:
    """Remove product/ingredient words so a foreign brand name doesn't fail the language check."""
    out = text
    for k in sorted({k for k in known if k and len(k) > 1}, key=len, reverse=True):
        out = re.sub(re.escape(k), " ", out, flags=re.IGNORECASE)
    return out


def language_ok(text: str, lang: str, known: list[str] | None = None) -> bool:
    low = _strip_known(text, known or []).casefold()
    words = re.findall(r"[^\W\d_]+", low)
    has_vi_only = any(c in _VI_ONLY for c in low)
    es_score = sum(1 for w in words if w in _ES_WORDS) + (2 if any(c in _ES_CHARS for c in low) else 0)
    en_score = sum(1 for w in words if w in _EN_WORDS)
    if lang == "vi":
        return any(c in _VI_ALL for c in low) and not any(c in _ES_CHARS for c in low)
    if lang == "es":
        return not has_vi_only and es_score >= 1 and es_score >= en_score
    if lang == "en":
        return not has_vi_only and not any(c in _ES_CHARS for c in low) and not (es_score >= 2 and es_score > en_score)
    return True


def guard(text: Any, lang: str, known: list[str] | None = None) -> tuple[bool, str]:
    """(ok, why). Every model line passes this before it reaches the stream."""
    if not isinstance(text, str) or not text.strip():
        return False, "empty"
    if BANNED.search(text):
        return False, "banned_word"
    if len(text.split()) > MAX_WORDS:
        return False, "too_long"
    if not language_ok(text, lang, known):
        return False, "wrong_language"
    return True, "ok"


# ----------------------------------------------------------------------------- lines


def fallback_line(verdict: Verdict, member: Member) -> str:
    """The rules engine's own templated reason (member's language), or a neutral line."""
    for r in verdict.reasons:
        if r.text and r.text.strip():
            return r.text.strip()
    return say("explain.no_reasons", member.language, default="Nothing on this label flags for {name}.",
               name=member.name)


def _prompt(np: NormalizedProduct, verdict: Verdict, member: Member, lang: str) -> str:
    reasons = [f"- {r.text} (matched: {', '.join(r.matched) or 'n/a'}; {r.severity})" for r in verdict.reasons[:5]]
    return "\n".join([
        f"Product: {' '.join(x for x in (np.product.brand, np.product.name) if x) or 'this product'}",
        f"Family member: {member.name} ({member.age_group})",
        f"Pear's verdict for them: {verdict.state} ({verdict.label})",
        "Reasons from Pear's rules:",
        *(reasons or ["- none: nothing on the label flags for them"]),
        f"Write the line in {LANGUAGE_NAMES.get(lang, 'English')}.",
    ])


def _known_words(np: NormalizedProduct, verdict: Verdict, member: Member) -> list[str]:
    words = [np.product.name or "", np.product.brand or "", member.name]
    for r in verdict.reasons:
        words += list(r.matched)
    return words


async def _member_line(clients: Clients, np: NormalizedProduct, verdict: Verdict,
                       member: Member) -> ExplainEvent:
    lang = member.language
    s = clients.settings
    text, source, why = None, "gemini", "ok"
    try:
        data = await generate_json(clients, call="explain", model=s.model_text,
                                   contents=_prompt(np, verdict, member, lang), schema=EXPLAIN_SCHEMA,
                                   system=explain_system(lang), timeout_s=s.t_text, max_output_tokens=256)
        cand = (data.get("text") if isinstance(data, dict) else None) or ""
        ok, why = guard(cand.strip(), lang, _known_words(np, verdict, member))
        if ok:
            text = cand.strip()
    except PearError as e:
        why = f"gemini_{e.code}"
    except Exception as e:  # never break the stream
        why = f"error:{type(e).__name__}"
    if text is None:
        text, source = fallback_line(verdict, member), "fallback"
    log_event("explain.line", member_id=member.id, source=source, why=why, language=lang)
    return ExplainEvent(member_id=member.id, language=lang, text=text)


async def _load(clients: Clients, product_key: str, language: str) -> NormalizedProduct:
    np = await _products().load_product(clients, product_key)
    if np is None:
        raise PearError("not_found", say("error.product_missing", language,
                                         default="We couldn't find that product. Scan it again?"),
                        next="take_photo", detail={"product_key": product_key})
    return np


async def explain_stream(clients: Clients, *, product_key: str, household: Household | dict,
                         member_ids: list[str] | None = None) -> AsyncIterator[ExplainEvent]:
    """Yield one ExplainEvent per member as soon as each line is ready (raises only before the
    first yield: not_found for a missing product)."""
    hh = household if isinstance(household, Household) else Household.model_validate(household or {})
    t0 = time.perf_counter()
    np = await _load(clients, product_key, hh.language)
    ev = result_mod.run_rules(np, hh, member_ids)
    members = {m.id: m for m in hh.members}
    by_task = {asyncio.create_task(_member_line(clients, np, v, members[v.member_id])): v
               for v in ev.verdicts if v.member_id in members}
    tasks = list(by_task)
    sent = 0
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                event = await fut
            except Exception as e:  # _member_line already catches; belt and braces
                log_event("explain.task_failed", level=logging.WARNING, why=repr(e)[:200])
                continue
            sent += 1
            yield event
        # Any task whose result we lost above still gets its templated line.
        for t, v in by_task.items():
            if t.done() and not t.cancelled() and t.exception() is not None:
                m = members[v.member_id]
                sent += 1
                yield ExplainEvent(member_id=m.id, language=m.language, text=fallback_line(v, m))
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        log_event("explain.done", product_key=product_key, members=len(tasks), sent=sent,
                  ms=int((time.perf_counter() - t0) * 1000))
