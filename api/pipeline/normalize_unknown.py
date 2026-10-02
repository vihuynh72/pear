"""Unknown-term enrichment (plan 8.8): map ingredient strings the kb matcher missed to kb ids
with ONE batched Gemini call. Background only (pipeline.products.after_scan), never on the
request path; the photo path already maps ids inside label_reader (addendum 2).

    hits = await map_unknown(clients, ["lait écrémé", "E120", "water"])
      -> [Hit(kb_id="milk", matched="lait écrémé", where="enrich", strength="contains"), ...]
    np2 = await enrich_product(clients, raw, np)   # normalize(raw, extra_hits=hits) or None

Filters (the model can only ADD flags, and only confident, valid ones):
    confidence >= 0.6, kb_id in rules.kb.get_kb().ids() (aliases resolved with KB.canonical)
    allergens: strength "possible" unless confidence >= 0.9, then "contains"
    everything else: strength "contains"

Debugging
- Log events: enrich.mapped (counts), enrich.failed (why). Nothing here raises to the caller of
  enrich_product; map_unknown raises PearError like any Gemini call.
- PEAR_FEATURE_ENRICH=0 turns enrich_product into a no-op.
- Tests: tests/test_unknown.py.
"""

from __future__ import annotations

import logging
from typing import Any

from core.clients import Clients
from core.logging import log_event
from core.timing import stage
from models.domain import Hit, NormalizedProduct, RawProduct
from pipeline.gemini import generate_json
from pipeline.prompts import UNKNOWN_SCHEMA, unknown_system

MIN_CONFIDENCE = 0.6
CONTAINS_CONFIDENCE = 0.9
MAX_STRINGS = 40  # ~25 output tokens each; keeps the JSON well under max_output_tokens
MAX_CHARS = 120


def _kb():
    from rules.kb import get_kb

    return get_kb()


def _normalize(raw: RawProduct, extra_hits: list[Hit]) -> NormalizedProduct:
    from rules import normalize as normalize_mod

    return normalize_mod.normalize(raw, extra_hits=extra_hits)


def _unique(strings: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in strings:
        if not isinstance(s, str):
            continue
        t = s.strip()[:MAX_CHARS]
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out[:MAX_STRINGS]


def _confidence(v: Any) -> float:
    try:
        c = float(v)
    except (TypeError, ValueError):
        return 0.0
    return c / 100.0 if 1.0 < c <= 100.0 else c  # tolerate a 0-100 answer


def _to_hit(kb: Any, valid: set[str], text: str, kb_id: Any, conf: float) -> Hit | None:
    if not isinstance(kb_id, str) or not kb_id.strip() or kb_id.strip().lower() == "unknown":
        return None
    if conf < MIN_CONFIDENCE:
        return None
    try:
        cid = kb.canonical(kb_id.strip())
    except Exception:
        cid = kb_id.strip()
    if cid not in valid:
        return None
    try:
        allergen = bool(kb.is_allergen(cid))
    except Exception:
        allergen = False
    strength = "contains" if (not allergen or conf >= CONTAINS_CONFIDENCE) else "possible"
    return Hit(kb_id=cid, matched=text, where="enrich", strength=strength)


async def map_unknown(clients: Clients, strings: list[str]) -> list[Hit]:
    """ONE batched Gemini call: strings -> confident, valid kb hits (where="enrich")."""
    items = _unique(strings)
    if not items:
        return []
    kb = _kb()
    valid = set(kb.ids())
    s = clients.settings
    contents = "Ingredient strings (index: text):\n" + "\n".join(f"{i}: {t}" for i, t in enumerate(items))
    data = await generate_json(clients, call="normalize_unknown", model=s.model_text, contents=contents,
                               schema=UNKNOWN_SCHEMA, system=unknown_system(), timeout_s=s.t_text,
                               max_output_tokens=2048)
    rows = data.get("items") if isinstance(data, dict) else None
    hits: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    by_text = {t.casefold(): t for t in items}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        text = items[idx] if isinstance(idx, int) and 0 <= idx < len(items) else \
            by_text.get(str(row.get("text") or "").strip().casefold())
        if text is None:
            continue  # the model answered about a string we never sent
        hit = _to_hit(kb, valid, text, row.get("kb_id"), _confidence(row.get("confidence")))
        if hit is not None and (hit.kb_id, text) not in seen:
            seen.add((hit.kb_id, text))
            hits.append(hit)
    log_event("enrich.mapped", strings=len(items), answered=len(rows or []), kept=len(hits))
    return hits


def _place(hits: list[Hit], raw: RawProduct) -> list[Hit]:
    """Point each hit at the ingredient it came from (for highlights and position rules)."""
    index = {s.strip().casefold(): i for i, s in enumerate(raw.ingredients)}
    out: list[Hit] = []
    for h in hits:
        i = index.get(h.matched.strip().casefold())
        out.append(h.model_copy(update={"ingredient_index": i, "position": i}) if i is not None else h)
    return out


async def enrich_product(clients: Clients, raw: RawProduct, np: NormalizedProduct) -> NormalizedProduct | None:
    """Re-normalize with Gemini's extra hits for np.unmatched. None when nothing changes."""
    if not clients.settings.feature_enrich or not np.unmatched:
        return None
    if getattr(clients, "genai", None) is None:  # no Gemini (no ADC): skip quietly, /health says why
        return None
    try:
        with stage("enrich"):
            hits = await map_unknown(clients, list(np.unmatched))
            if not hits:
                return None
            return _normalize(raw, _place(hits, raw))
    except Exception as e:  # background job: log and move on
        log_event("enrich.failed", level=logging.WARNING, product_key=np.product.key, why=repr(e)[:300])
        return None
