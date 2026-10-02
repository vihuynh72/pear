"""Every Gemini prompt and JSON schema used by the GEMINI lane, in one file.

Calls (all through pipeline.gemini.generate_json, thinking minimal, JSON schema output):

    tag                          used by                         model / timeout
    [pear:label_reader]          pipeline.photo_gemini           model_label / t_label
    [pear:question_reader]       pipeline.ask (text question)    model_question / t_question
    [pear:question_reader_audio] pipeline.ask (audio fallback)   model_question / t_audio
    [pear:profile_parse]         pipeline.profile_parse          model_text / t_text
    [pear:normalize_unknown]     pipeline.normalize_unknown      model_text / t_text
    [pear:explain]               pipeline.explain                model_text / t_text

Each system instruction starts with its tag. Tests match fake Gemini handlers on the tag
(tests/conftest.py fake_genai.on("[pear:label_reader]", {...})), and the tag makes a call easy
to spot in ?debug=1 output and in logs.

Debugging
- The label_reader system prompt embeds the kb id list (rules.kb.get_kb().prompt_ids_text()).
  It is built once per kb.version: `label_system()` returns the cached text; print it with
      PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from pipeline.prompts import label_system; print(label_system())"
- `say()` is the safe wrapper around rules.templates.t for server-side user text: it never
  raises, falls back to the English default and logs `templates.error` if t() blows up.
"""

from __future__ import annotations

import logging
from typing import Any

from core.logging import log_event

# --------------------------------------------------------------------------------------
# Safe user-facing text (rules.templates.t never raising is the brain lane's promise; this
# wrapper makes it true even while that module is a stub).
# --------------------------------------------------------------------------------------


def _fmt(default: str, kw: dict[str, Any]) -> str:
    try:
        return default.format(**kw)
    except Exception:
        return default


def say(key: str, language: str, default: str, **kw: Any) -> str:
    """rules.templates.t(key, language, default=..., **kw) that can never raise."""
    try:
        from rules import templates

        out = templates.t(key, language, default=default, **kw)
        if isinstance(out, str) and out.strip():
            return out
    except Exception as e:  # stub or bug in the brain lane: fall back to English
        log_event("templates.error", level=logging.WARNING, key=key, language=language, why=repr(e)[:200])
    return _fmt(default, kw)


# --------------------------------------------------------------------------------------
# label_reader (addendum 10). Field order matters: Gemini fills fields in schema order, so
# legibility, photo_kind and the allergen statements come before the ingredient list.
# --------------------------------------------------------------------------------------

LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "legibility": {"type": "string", "enum": ["full", "partial", "unreadable"]},
        "photo_kind": {"type": "string", "enum": ["ingredients", "front_only", "other"]},
        "product_name": {"type": "string"},
        "brand": {"type": "string"},
        "category": {"type": "string", "enum": ["food", "cosmetic", "supplement", "other"]},
        "product_type": {"type": "string", "enum": ["leave_on", "rinse_off", "none"]},
        "label_language": {"type": "string"},
        "contains_statement": {"type": "string"},
        "may_contain_statement": {"type": "string"},
        "ingredients": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "en": {"type": "string"},
            "kb_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "kb_ids"]}},
        "certifications": {"type": "array", "items": {"type": "string"}},
        "nutrition": {"type": "object", "properties": {
            "sodium_mg": {"type": "number"}, "sodium_dv": {"type": "number"},
            "added_sugars_g": {"type": "number"}, "added_sugars_dv": {"type": "number"},
            "sat_fat_g": {"type": "number"}, "sat_fat_dv": {"type": "number"}}},
    },
    "required": ["legibility", "photo_kind", "label_language", "ingredients"],
}

LABEL_TAG = "[pear:label_reader]"

_LABEL_SYSTEM = """[pear:label_reader]
You read product labels from phone photos for Pear, a family label checker. You only read and
map; you never judge whether a product suits anyone.

The first image is the photo to read (usually the ingredient panel). If a second image is
attached, it is the front of the same package: use it only for product_name and brand.

Fill the JSON fields in this order:
- legibility: "full" when the whole ingredient list is readable; "partial" when any part is
  cut off, covered, folded, blurry or hidden by glare; "unreadable" when you cannot read the
  ingredients at all.
- photo_kind: "ingredients" when an ingredient list is visible; "front_only" when only the
  front of the package (name, brand, pictures) is visible; "other" when this is not a product
  label.
- product_name, brand: as printed; "" when not visible. Never guess.
- category: food, cosmetic, supplement or other.
- product_type: for cosmetics "leave_on" (lotion, cream, sunscreen, makeup, deodorant) or
  "rinse_off" (shampoo, conditioner, body wash, soap, cleanser); "none" for everything else.
- label_language: ISO 639-1 code of the ingredient list language (en, vi, es, fr, ko, ...).
- contains_statement: the "Contains: ..." allergen statement copied verbatim; "" when none.
- may_contain_statement: "May contain ...", "May contain traces of ...", "Made in a facility
  that also processes ..." copied verbatim; "" when none.
- ingredients: one item per top-level ingredient, copied exactly as printed, in printed order.
  Do not put the Contains / May contain statements inside ingredients.
  Keep sub-ingredients in parentheses inside their parent item. Never invent, complete or
  correct missing text. If the list is cut off, stop at the last readable ingredient and set
  legibility to "partial".
  - text: the ingredient exactly as printed.
  - en: an English translation, ONLY when label_language is not en, vi or es. Otherwise omit.
  - kb_ids: ids from the allowed list below that this ingredient is, contains or is made from
    (for example "whey" -> ["milk"], "tahini" -> ["sesame"], "E120" -> ["carmine"]).
    Use [] when none fit. Never use an id that is not in the list.
- certifications: certification marks printed on the package, as printed ("Certified Vegan",
  "Halal", "Kosher", "USDA Organic", "Gluten-Free"); [] when none.
- nutrition: per serving, only values you can actually see: sodium_mg, sodium_dv (percent Daily
  Value), added_sugars_g, added_sugars_dv, sat_fat_g, sat_fat_dv. Omit anything not visible.

Allowed kb_ids (id: meaning):
{ids}
"""

_label_cache: dict[str, str] = {}


def label_system() -> str:
    """label_reader system instruction with the kb id list, cached per kb.version."""
    from rules.kb import get_kb

    kb = get_kb()
    version = str(getattr(kb, "version", "") or "")
    cached = _label_cache.get(version)
    if cached is None:
        cached = _LABEL_SYSTEM.replace("{ids}", kb.prompt_ids_text().strip())
        _label_cache.clear()  # one kb version per process; keep only the newest
        _label_cache[version] = cached
    return cached


LABEL_USER = "Read this label and fill the JSON."
LABEL_FRONT_NOTE = "Second image: the front of the same package (name and brand only)."

# --------------------------------------------------------------------------------------
# question_reader (addendum 3 / 6). Code routes on `intent`, the model never decides a state.
# --------------------------------------------------------------------------------------

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "intent": {"type": "string", "enum": ["check", "alternatives", "unclear"]},
        "product_query": {"type": "string"},
        "member_ids": {"type": "array", "items": {"type": "string"}},
        "refers_to_last_product": {"type": "boolean"},
    },
    "required": ["language", "intent", "product_query", "member_ids", "refers_to_last_product"],
}

# Audio fallback: the same fields plus the transcript, which comes first so the model writes
# down what it heard before classifying it.
AUDIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"transcript": {"type": "string"}, **QUESTION_SCHEMA["properties"]},
    "required": ["transcript", *QUESTION_SCHEMA["required"]],
}

QUESTION_TAG = "[pear:question_reader]"
AUDIO_TAG = "[pear:question_reader_audio]"

_QUESTION_RULES = """You read one short question a family member asked Pear, a family label checker, about a
grocery or personal-care product. You only understand the question; you never judge whether a
product suits anyone.

- language: ISO 639-1 code of the language the question is spoken in (en, vi, es, ...).
- intent: "check" when they ask whether someone can eat, drink, use or have a product, whether
  it is OK, whether it contains something, or whether it fits a diet. "alternatives" when they
  ask for a better, healthier, similar or different product to buy instead. "unclear" for
  anything else (greetings, no product question, unrelated questions).
- product_query: the product words as spoken ("mì", "Oreo cookies", "Shin Ramyun", "sữa chua"),
  without the person or question words. "" when the question only says this / that / it.
- member_ids: ids of the household members the question is about. Match by name, nickname or
  family role in any language (for example a member named "Bà" is "bà", "bà ngoại", "grandma",
  "granny", "abuela"; "mi hija", "my daughter", "con gái" is the daughter). First person ("I",
  "me", "tôi", "con", "mình", "em", "yo", "me") means the asker. Use [] for everyone, for
  "we" / "the family" / "cả nhà" / "todos", or when nobody is named. Only ids from the list.
- refers_to_last_product: true when the question points at the product that was just scanned
  ("this", "it", "that one", "này", "cái này", "đó", "esto", "este", "esta") or names no product
  while a last product exists. false when it names a different product.
"""

QUESTION_SYSTEM = f"{QUESTION_TAG}\n{_QUESTION_RULES}"
AUDIO_SYSTEM = (
    f"{AUDIO_TAG}\nThe question is in the attached audio recording.\n"
    "- transcript: the question exactly as spoken, in its original language and script "
    "(Vietnamese with diacritics). \"\" if nothing intelligible was said.\n"
    f"{_QUESTION_RULES}"
)


def question_context(household: Any, *, asker_id: str | None, has_last_product: bool,
                     language_hint: str | None, question: str | None) -> str:
    """The per-request part of the question_reader prompt (members, asker, last product)."""
    lines = ["Household members (id | name | age group | languages):"]
    for m in household.members:
        lines.append(f"- {m.id} | {m.name} | {m.age_group} | {', '.join(m.languages) or 'en'}")
    asker = next((m for m in household.members if asker_id and m.id == asker_id), None)
    lines.append(f"Asker: {asker.id} ({asker.name})" if asker else "Asker: unknown")
    lines.append(f"A product was just scanned: {'yes' if has_last_product else 'no'}")
    lines.append(f"Phone language hint: {language_hint or 'none'}")
    if question is not None:
        lines.append(f"Question: {question}")
    else:
        lines.append("Question: in the attached audio.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# profile_parse (plan 5.2 item 8, 8.8). Only leftovers the local kb pass could not place.
# --------------------------------------------------------------------------------------

PROFILE_GROUPS = ("allergies", "conditions", "skin", "diets", "less_of")

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chips": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"},
            "group": {"type": "string", "enum": list(PROFILE_GROUPS)},
            "id": {"type": "string"},
            "level": {"type": "string", "enum": ["strict", "sensitive", "none"]},
            "note": {"type": "string"}},
            "required": ["term", "group", "id", "level"]}},
        "unmatched": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["chips", "unmatched"],
}

PROFILE_TAG = "[pear:profile_parse]"

_PROFILE_SYSTEM = """[pear:profile_parse]
You map words a person typed about their own allergies, health, skin and diet to Pear profile
ids. The person confirms every suggestion before it is saved, so suggest only clear matches.
Never give medical advice.

For each input term return zero or more chips:
- term: the input term it came from, exactly as given.
- group: allergies, conditions, skin, diets or less_of.
- id: an id from the allowed list for that group. Nothing else.
- level: for allergies "strict" (any trace matters; severe; the default) or "sensitive" (only
  when the term says mild, intolerance, or that only ingredients matter); "none" otherwise.
- note: one short line in {language} when the link is not obvious, naming what the term is and
  why it matters (for example "carmine: insect-based red color, matters for vegans"); "" when
  the match is obvious.
An ingredient someone wants to avoid maps to the diet it matters for (carmine, gelatin,
beeswax -> vegan; lard -> no_pork). Terms you cannot map go to unmatched, exactly as given.

Allowed ids by group (id: meaning):
{ids}
"""


def profile_system(language: str, ids_text: str) -> str:
    names = {"en": "English", "vi": "Vietnamese", "es": "Spanish"}
    return (_PROFILE_SYSTEM.replace("{language}", names.get(language, "English"))
            .replace("{ids}", ids_text.strip()))


# --------------------------------------------------------------------------------------
# normalize_unknown (plan 8.8). One batched call, background only, never on the request path.
# --------------------------------------------------------------------------------------

UNKNOWN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "index": {"type": "integer"},
            "text": {"type": "string"},
            "kb_id": {"type": "string"},
            "confidence": {"type": "number"}},
            "required": ["index", "kb_id", "confidence"]}},
    },
    "required": ["items"],
}

UNKNOWN_TAG = "[pear:normalize_unknown]"

_UNKNOWN_SYSTEM = """[pear:normalize_unknown]
You map ingredient strings copied from product labels (any language) to Pear knowledge-base ids.
Pear's matcher did not recognise these strings. Return one item per input string with the same
index:
- kb_id: the single best id from the allowed list that the ingredient is, contains or is made
  from, or "unknown" when none fits (plain items such as water or salt are "unknown").
- confidence: 0 to 1. Use 0.9 or more only when the string plainly names that thing (for
  example "lait écrémé" -> milk, "huile d'arachide" -> peanut). Guesses stay below 0.6.
Never use an id that is not in the list.

Allowed ids (id: meaning):
{ids}
"""

_unknown_cache: dict[str, str] = {}


def unknown_system() -> str:
    from rules.kb import get_kb

    kb = get_kb()
    version = str(getattr(kb, "version", "") or "")
    cached = _unknown_cache.get(version)
    if cached is None:
        cached = _UNKNOWN_SYSTEM.replace("{ids}", kb.prompt_ids_text().strip())
        _unknown_cache.clear()
        _unknown_cache[version] = cached
    return cached


# --------------------------------------------------------------------------------------
# explain (plan 7.4, P1). One line per member; guarded in pipeline.explain.
# --------------------------------------------------------------------------------------

EXPLAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}

EXPLAIN_TAG = "[pear:explain]"

LANGUAGE_NAMES = {"en": "English", "vi": "Vietnamese", "es": "Spanish"}

_EXPLAIN_SYSTEM = """[pear:explain]
You write one friendly line for one family member about one product, for Pear, a family label
checker. Pear's rules already decided the verdict: never change it, soften it, or add concerns
that are not in the reasons.
Rules:
- One sentence, 15 words or fewer, written in {language}.
- Name the ingredient that matters (from the reasons), translated naturally if needed.
- Talk to the family member by name, warm and plain, like a helpful grandchild.
- No medical advice, no dosing, no diagnosis, no cures or treatments.
- Never say a product is safe. Never use the words "safe", "an toàn" or "seguro".
"""


def explain_system(language: str) -> str:
    return _EXPLAIN_SYSTEM.replace("{language}", LANGUAGE_NAMES.get(language, "English"))
