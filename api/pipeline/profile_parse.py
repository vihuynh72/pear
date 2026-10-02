"""POST /v1/profile/parse: free text about a person -> profile chips to confirm (plan 5.2.8, 8.8).

    "peanuts, mild lactose intolerance, carmine, high blood pressure"
      1. split into terms (commas, semicolons, newlines, "and", "or", "và", "hoặc", "y", "o")
      2. local pass: rules.kb.get_kb().search(term, groups=profile groups). A confident match
         (the term IS a kb label / id, after light cleanup like "allergic to", plural s) becomes
         a chip with no Gemini call.
      3. leftovers only -> ONE Gemini call (profile_parse prompt with KB.profile_ids()).
         Ids not in the kb are dropped. Example: "carmine" -> diets/vegan with the note
         "carmine: insect-based red color, matters for vegans" for the user to confirm.
      4. Gemini unavailable / failed -> local chips, leftovers in `unmatched`, no error.
    Labels come from KB.label(id, language). The user confirms every chip in the app.

Debugging
- ?debug=1: debug.profile = {terms, local, leftovers, gemini_dropped, gemini_error}.
- Log event: profile.parsed (counts only; the text itself is never logged).
- Tests: tests/test_profile.py.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.clients import Clients
from core.errors import PearError
from core.logging import log_event
from core.timing import debug_put, request_id, stage, timings
from models.contracts import SUPPORTED_LANGUAGES, ProfileChip, ProfileParseResponse, base_language
from pipeline.gemini import generate_json
from pipeline.prompts import PROFILE_GROUPS, PROFILE_SCHEMA, profile_system

MAX_TERMS = 30
MAX_TERM_CHARS = 80

_SPLIT = re.compile(r"[,;\n\r/•·]+|\s+(?:and|or|và|hoặc|y|o|&|\+)\s+", re.IGNORECASE)
# Filler around the real term ("allergic to peanuts" -> "peanuts", "peanut allergy" -> "peanut").
_PREFIXES = (
    "i am allergic to", "i'm allergic to", "im allergic to", "allergic to", "allergy to",
    "allergies to", "an allergy to", "i have", "i am", "i'm", "my", "has", "have",
    "dị ứng với", "dị ứng", "bị", "tôi bị", "soy alérgico a", "soy alérgica a", "alérgico a",
    "alérgica a", "alergia al", "alergia a la", "alergia a", "alergia", "tengo",
)
_SUFFIXES = ("allergy", "allergies", "intolerance", "intolerant", "allergic")
_SENSITIVE = re.compile(r"\b(mild|mildly|slight|sensitive|sensitivity|intoleran\w*|ingredients only|"
                        r"nhẹ|không dung nạp|leve|intolerancia)\b", re.IGNORECASE)


def _kb():
    from rules.kb import get_kb

    return get_kb()


# ----------------------------------------------------------------------------- local pass


def split_terms(text: str) -> list[str]:
    out: list[str] = []
    for part in _SPLIT.split(text or ""):
        term = part.strip(" \t.!?:\"'()[]-").strip()
        if term and term.casefold() not in {t.casefold() for t in out}:
            out.append(term[:MAX_TERM_CHARS])
    return out[:MAX_TERMS]


def _clean(term: str) -> str:
    t = term.casefold().strip()
    changed = True
    while changed:
        changed = False
        for p in _PREFIXES:
            if t.startswith(p + " "):
                t, changed = t[len(p) + 1:].strip(), True
        for s in _SUFFIXES:
            if t.endswith(" " + s):
                t, changed = t[: -len(s) - 1].strip(), True
    for w in ("mild ", "mildly ", "slight ", "severe "):
        if t.startswith(w):
            t = t[len(w):].strip()
    return t


def _forms(s: str) -> set[str]:
    s = s.casefold().strip().replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    out = {s}
    if s.endswith("es") and len(s) > 4:
        out.add(s[:-2])
    if s.endswith("s") and len(s) > 3:
        out.add(s[:-1])
    return out


def _is_confident(candidate: str, sug: Any) -> bool:
    """The term is this kb entry (label in any language, or the id), not just similar."""
    names = [sug.id, sug.label, *(sug.labels or {}).values()]
    want = _forms(candidate)
    return any(want & _forms(n) for n in names if isinstance(n, str) and n)


def _level(group: str, term: str, given: str | None = None) -> str | None:
    if group != "allergies":
        return None
    if given in ("strict", "sensitive"):
        return given
    return "sensitive" if _SENSITIVE.search(term) else "strict"


def _local_match(kb: Any, term: str, lang: str) -> ProfileChip | None:
    for cand in dict.fromkeys([term.casefold().strip(), _clean(term)]):
        if not cand:
            continue
        try:
            sugs = kb.search(cand, language=lang, limit=5, groups=list(PROFILE_GROUPS))
        except Exception as e:
            log_event("profile.kb_search_failed", level=logging.WARNING, why=repr(e)[:200])
            return None
        for sug in sugs:
            if sug.group in PROFILE_GROUPS and _is_confident(cand, sug):
                return ProfileChip(group=sug.group, id=sug.id, level=_level(sug.group, term),
                                   label=_label(kb, sug.id, lang, sug.label), note=None)
    return None


def _label(kb: Any, kb_id: str, lang: str, default: str | None = None) -> str | None:
    try:
        return kb.label(kb_id, lang) or default
    except Exception:
        return default


# ----------------------------------------------------------------------------- Gemini pass


def _allowed(kb: Any) -> dict[str, set[str]]:
    ids = kb.profile_ids() or {}
    return {g: set(ids.get(g) or []) for g in PROFILE_GROUPS}


def _ids_text(kb: Any, allowed: dict[str, set[str]]) -> str:
    lines: list[str] = []
    for g in PROFILE_GROUPS:
        parts = [f"{i} ({_label(kb, i, 'en', i)})" for i in sorted(allowed.get(g) or [])]
        lines.append(f"{g}: {', '.join(parts) if parts else '(none)'}")
    return "\n".join(lines)


def _canonical(kb: Any, kb_id: str) -> str:
    try:
        return kb.canonical(kb_id)
    except Exception:
        return kb_id


async def _gemini_pass(clients: Clients, kb: Any, leftovers: list[str], lang: str,
                       diag: dict) -> tuple[list[ProfileChip], list[str]]:
    allowed = _allowed(kb)
    s = clients.settings
    contents = "Terms:\n" + "\n".join(f"- {t}" for t in leftovers)
    data = await generate_json(clients, call="profile_parse", model=s.model_text, contents=contents,
                               schema=PROFILE_SCHEMA, system=profile_system(lang, _ids_text(kb, allowed)),
                               timeout_s=s.t_text, max_output_tokens=768)
    chips: list[ProfileChip] = []
    covered: set[str] = set()
    dropped: list[dict] = []
    data = data if isinstance(data, dict) else {}
    for c in data.get("chips") or []:
        if not isinstance(c, dict):
            continue
        group = c.get("group")
        kb_id = _canonical(kb, str(c.get("id") or "").strip())
        if group not in allowed or kb_id not in allowed[group]:
            dropped.append({"group": group, "id": c.get("id")})
            continue
        term = str(c.get("term") or "").strip()
        note = str(c.get("note") or "").strip() or None
        chips.append(ProfileChip(group=group, id=kb_id, level=_level(group, term, c.get("level")),
                                 label=_label(kb, kb_id, lang), note=note))
        if term:
            covered.add(term.casefold())
    diag["gemini_dropped"] = dropped
    said = {str(u).strip().casefold() for u in (data.get("unmatched") or []) if str(u).strip()}
    sent = {t.casefold() for t in leftovers}
    if covered <= sent:  # chip terms echo our terms: anything not covered is unmatched
        unmatched = [t for t in leftovers if t.casefold() not in covered]
    else:  # the model reworded terms: trust its unmatched list, limited to what we sent
        unmatched = [t for t in leftovers if t.casefold() in said]
    return chips, unmatched


# ----------------------------------------------------------------------------- entry


def _dedupe(chips: list[ProfileChip]) -> list[ProfileChip]:
    seen: set[tuple[str, str]] = set()
    out: list[ProfileChip] = []
    for c in chips:
        if (c.group, c.id) not in seen:
            seen.add((c.group, c.id))
            out.append(c)
    return out


async def parse_profile(clients: Clients, text: str, language: str | None) -> ProfileParseResponse:
    lang = base_language(language) if language else "en"
    lang = lang if lang in SUPPORTED_LANGUAGES else "en"
    kb = _kb()
    terms = split_terms(text)
    chips: list[ProfileChip] = []
    leftovers: list[str] = []
    with stage("profile_local"):
        for term in terms:
            chip = _local_match(kb, term, lang)
            if chip is not None:
                chips.append(chip)
            else:
                leftovers.append(term)
    diag: dict[str, Any] = {"terms": len(terms), "local": len(chips), "leftovers": len(leftovers)}
    unmatched = list(leftovers)
    if leftovers:
        try:
            more, unmatched = await _gemini_pass(clients, kb, leftovers, lang, diag)
            chips.extend(more)
        except PearError as e:  # Gemini down / slow: local chips still help
            diag["gemini_error"] = e.code
            log_event("profile.gemini_failed", level=logging.WARNING, code=e.code)
        except Exception as e:
            diag["gemini_error"] = repr(e)[:200]
            log_event("profile.gemini_failed", level=logging.WARNING, why=repr(e)[:200])
    chips = _dedupe(chips)
    log_event("profile.parsed", terms=len(terms), local=diag["local"], chips=len(chips),
              unmatched=len(unmatched), gemini=bool(leftovers))
    debug_put("profile", diag)
    return ProfileParseResponse(chips=chips, unmatched=unmatched, timings_ms=timings(),
                                request_id=request_id())
