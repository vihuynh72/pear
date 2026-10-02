"""RawProduct -> NormalizedProduct: every kb hit with where/strength/matched, plus the public Product.

Sources of hits (each becomes models.domain.Hit):
  ingredients     every RawProduct.ingredients string, sub-ingredients included, plus its
                  ingredients_en gloss (hit.matched is then the printed string, not the gloss);
                  ingredients_text too (OFF), deduped against the list
  contains        RawProduct.contains_text      (default strength contains)
  may_contain     RawProduct.may_contain_text   (default strength may_contain)
  tags            OFF allergens_tags (contains), traces_tags (may_contain), additives_tags
                  (E-numbers), ingredients_analysis_tags (palm oil, non-vegan)
  model           label_reader kb_ids (addendum 10 merge rule, see _merge_model_ids)
  enrich          extra_hits from pipeline.normalize_unknown (can only add flags)
A may-contain sentence anywhere ("..., salt. May contain peanuts.") is may_contain, never
contains (rules/statements.py).

Debug:
    from rules.normalize import normalize, match_text
    match_text("Sữa bột, có thể chứa đậu phộng")
    np = normalize(RawProduct(key="test:1", source="photo", ingredients=[...]))
    [(h.kb_id, h.strength, h.matched, h.where) for h in np.hits]; np.unmatched
"""

from __future__ import annotations

import bisect
import re

from core.timing import debug_put, stage
from models.contracts import Product
from models.domain import Hit, NormalizedProduct, RawProduct
from rules.kb import KB, Match, get_kb, vi_mode
from rules.statements import mentions_may_contain
from rules.text import E_NUMBER_RE, e_key, nfc, prep

_PREFIX = re.compile(r"^\s*(?:ingredients?|ingredientes|thành\s+phần|composition|inci)\s*[:：\-]\s*", re.I)


def split_ingredients(text: str | None) -> list[str]:
    """Top-level split on , and ; (parentheses kept together, '1,3-diol' kept whole)."""
    if not text or not text.strip():
        return []
    s = _PREFIX.sub("", nfc(text).strip())
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        sentence_end = ch == "." and depth == 0 and i + 1 < len(s) and s[i + 1].isspace() and not s[i - 1:i].isdigit()
        if (ch in ",;" or sentence_end) and depth == 0:
            prev_digit = i > 0 and s[i - 1].isdigit()
            next_digit = i + 1 < len(s) and s[i + 1].isdigit()
            if not (ch == "," and prev_digit and next_digit):
                out.append("".join(cur))
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur))
    return [p.strip().rstrip(".").strip() for p in out if p.strip(" .\n\t")]


def _hits_from(matches: list[Match], where: str, index: int | None = None,
               matched_override: str | None = None) -> list[Hit]:
    return [
        Hit(kb_id=m.kb_id, matched=matched_override or m.matched, where=where, strength=m.strength,
            ingredient_index=index, position=index)
        for m in matches if not m.neutral and m.kb_id
    ]


def _tag_hits(kb: KB, tags: list[str], strength_default: str, where: str = "tags") -> list[Hit]:
    out: list[Hit] = []
    for tag in tags or []:
        t = (tag or "").strip().lower()
        if not t:
            continue
        targets = kb.lexicon.tag_map.get(t)
        if targets is None:
            body = t.split(":", 1)[-1]
            m = E_NUMBER_RE.fullmatch(body.replace("-", " ").strip())
            if m:
                full, base = e_key(m.group(1), m.group(2))
                targets = kb.lexicon.e_map.get(full) or kb.lexicon.e_map.get(base)
        if targets is None:  # unknown tag: match its text ("en:almond-paste" -> "almond paste")
            body = t.split(":", 1)[-1].replace("-", " ")
            for m in kb.lexicon.scan(body):
                if m.neutral or not m.kb_id:
                    continue
                st = "may_contain" if strength_default == "may_contain" else m.strength
                out.append(Hit(kb_id=m.kb_id, matched=tag, where=where, strength=st))
            continue
        for kid, st in targets.items():
            out.append(Hit(kb_id=kid, matched=tag, where=where,
                           strength="may_contain" if strength_default == "may_contain" else st))
    return out


def _merge_model_ids(kb: KB, raw: RawProduct, hits: list[Hit], kb_matched: list[bool]) -> list[Hit]:
    """Addendum 10: allergen ids = union(kb, model); other model ids only where the kb matched nothing."""
    out: list[Hit] = []
    unknown: list[str] = []
    for i, ids in enumerate(raw.model_kb_ids or []):
        if i >= len(raw.ingredients) or not ids:
            continue
        ing = (raw.ingredients[i] or "").strip()
        here = {h.kb_id for h in hits if h.ingredient_index == i}
        seg = "may_contain" if mentions_may_contain(prep(ing)[0]) else "contains"
        for mid in ids:
            cid = kb.canonical(mid)
            if cid not in kb.entries or kb.entries[cid].get("neutral"):
                unknown.append(mid)
                continue
            if kb.is_allergen(cid):
                for aid in [cid, *[kb.canonical(x) for x in kb.allergens[cid].get("implies", [])]]:
                    if aid in here:
                        continue
                    here.add(aid)
                    out.append(Hit(kb_id=aid, matched=ing or mid, where="model", strength=seg,
                                   ingredient_index=i, position=i))
            elif not kb_matched[i] and cid not in here:
                here.add(cid)
                out.append(Hit(kb_id=cid, matched=ing or mid, where="model", strength=seg,
                               ingredient_index=i, position=i))
    if unknown:
        debug_put("model_kb_ids_unknown", sorted(set(unknown)))
    return out


def _reconcile_with_text(list_hits: list[Hit], text_hits: list[Hit], raw: RawProduct | None = None,
                         kb: KB | None = None) -> list[Hit]:
    """A list item like "mè" split out of "Có thể chứa đậu phộng, mè" is not an ingredient.

    Downgrade an item hit to may_contain ONLY when the full printed text mentions that kb id
    exclusively inside may-contain statements and with the same word, AND the item is that bare
    word (1-3 tokens, "mè" / "tree nuts"), AND the label reader did not tag the item with that
    allergen. A real ingredient ("Wheat flour", "sữa bột") is never softened.
    """
    by_id: dict[str, list[Hit]] = {}
    for h in text_hits:
        by_id.setdefault(h.kb_id, []).append(h)
    model_rows = (raw.model_kb_ids or []) if raw is not None else []
    items = raw.ingredients if raw is not None else []
    out: list[Hit] = []
    for h in list_hits:
        th = by_id.get(h.kb_id)
        if (h.ingredient_index is not None and h.strength != "may_contain" and th
                and all(x.strength == "may_contain" for x in th)
                and h.matched.casefold() in {x.matched.casefold() for x in th}):
            i = h.ingredient_index
            item = (items[i] if i < len(items) else "") or ""
            bare = " ".join(item.casefold().split()) == " ".join(h.matched.casefold().split()) \
                and len(item.split()) <= 3
            model_ids = {kb.canonical(x) for x in model_rows[i]} if (kb and i < len(model_rows)) else set()
            if kb:
                for x in list(model_ids):
                    if kb.is_allergen(x):
                        model_ids.update(kb.canonical(y) for y in kb.allergens[x].get("implies", []))
            if bare and h.kb_id not in model_ids:
                h = h.model_copy(update={"strength": "may_contain"})
        out.append(h)
    return out


def _dedupe(hits: list[Hit]) -> list[Hit]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Hit] = []
    for h in hits:
        key = (h.kb_id, h.strength, h.matched.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


_NEGATED_CERT = re.compile(r"(?:\b(?:not|non|no|never|without)|không|no\s+apt[oa])[\s\-]+(?:\w+[\s\-]+){0,3}$", re.I)


def _cert_matches(kb: KB, text: str) -> set[str]:
    """Cert ids in a label text, minus negated ones ("Not vegan", "May not be suitable for vegans")."""
    out: set[str] = set()
    folded = nfc(text).casefold()
    for m in kb.cert_lexicon.scan(text):
        if m.kb_id and not _NEGATED_CERT.search(folded[:m.orig]):
            out.add(m.kb_id)
    return out


def _certifications(kb: KB, raw: RawProduct) -> list[str]:
    found: set[str] = set()
    for tag in raw.labels_tags or []:
        t = (tag or "").strip().lower()
        targets = kb.cert_lexicon.tag_map.get(t)
        body = t.split(":", 1)[-1]
        if body.startswith(("non-", "not-", "no-")):
            continue
        if targets:
            found.update(targets)
        elif t:
            found.update(_cert_matches(kb, body.replace("-", " ")))
    for text in raw.certifications_text or []:
        found.update(_cert_matches(kb, text or ""))
    return [c for c in kb.cert_order if c in found]


def normalize(raw: RawProduct, *, extra_hits: list[Hit] | None = None) -> NormalizedProduct:
    """RawProduct -> NormalizedProduct (public Product + kb hits). Pure, deterministic, ~ms.

    extra_hits: additive hits from normalize_unknown enrichment (can only add flags).
    """
    kb = get_kb()
    with stage("kb_match"):
        derived = not any((s or "").strip() for s in raw.ingredients)
        # Product-level hint for the diacritic-folded pass: one-syllable unaccented vi words
        # ("trung", "sua") fire as possible hits when the label is Vietnamese.
        vi = vi_mode(raw.ingredients_text, raw.contains_text, raw.may_contain_text, *raw.ingredients,
                     label_language=raw.label_language)
        ingredients = split_ingredients(raw.ingredients_text) if derived else [(s or "").strip() for s in raw.ingredients]
        hits: list[Hit] = []
        kb_matched = [False] * len(ingredients)
        if derived and ingredients:
            # One sentence-aware scan of the printed text ("Có thể chứa đậu phộng, mè" keeps mè
            # may_contain); each match is mapped back to the split item it falls in.
            text = nfc(raw.ingredients_text or "")
            starts: list[int] = []
            cursor = 0
            for ing in ingredients:
                at = text.find(ing, cursor)
                at = cursor if at < 0 else at
                starts.append(at)
                cursor = at + len(ing)
            for m in kb.lexicon.scan(text, vi=vi):
                i = max(0, bisect.bisect_right(starts, m.orig) - 1)
                kb_matched[i] = True
                hits.extend(_hits_from([m], "ingredients", i))
        for i, ing in enumerate([] if derived else ingredients):
            if not ing:
                continue
            ms = kb.lexicon.scan(ing, vi=vi)
            kb_matched[i] = bool(ms)
            hits.extend(_hits_from(ms, "ingredients", i))
            gloss = raw.ingredients_en[i] if (not derived and i < len(raw.ingredients_en)) else None
            if gloss and gloss.strip() and gloss.strip().casefold() != ing.casefold():
                gms = kb.lexicon.scan(gloss)
                kb_matched[i] = kb_matched[i] or bool(gms)
                hits.extend(_hits_from(gms, "ingredients", i, matched_override=ing))
        if not derived and raw.ingredients_text:
            text_hits = _hits_from(kb.lexicon.scan(raw.ingredients_text, vi=vi), "ingredients")
            hits = _reconcile_with_text(hits, text_hits, raw, kb)
            hits.extend(text_hits)
        if raw.contains_text:
            hits.extend(_hits_from(kb.lexicon.scan(raw.contains_text, "contains", vi=vi), "contains"))
        if raw.may_contain_text:
            hits.extend(_hits_from(kb.lexicon.scan(raw.may_contain_text, "may_contain", vi=vi), "may_contain"))
        hits.extend(_tag_hits(kb, raw.allergens_tags, "contains"))
        hits.extend(_tag_hits(kb, raw.traces_tags, "may_contain"))
        hits.extend(_tag_hits(kb, raw.additives_tags, "contains"))
        for tag in raw.analysis_tags or []:
            for kid, st in kb.analysis_map.get((tag or "").strip().lower(), {}).items():
                hits.append(Hit(kb_id=kid, matched=tag, where="tags", strength=st))
        if raw.model_kb_ids and not derived:
            hits.extend(_merge_model_ids(kb, raw, hits, kb_matched))
        for h in extra_hits or []:
            cid = kb.canonical(h.kb_id)
            hits.append(h if cid == h.kb_id else h.model_copy(update={"kb_id": cid}))
        has_list = any(ingredients)
        if not has_list and raw.name:
            # No list to read: obvious allergens / toxins in the product name are possible hits
            # ("Peanut Butter Cups"), never contains, so they don't land in Product.contains.
            for m in kb.lexicon.scan(raw.name, vi=vi):
                if m.neutral or not m.kb_id:
                    continue
                hits.append(Hit(kb_id=m.kb_id, matched=m.matched, where="name",
                                strength="may_contain" if m.strength == "may_contain" else "possible"))
        hits = _dedupe(hits)

        model_rows = raw.model_kb_ids if not derived else []
        unmatched = [
            ing for i, ing in enumerate(ingredients)
            if ing and not kb_matched[i] and not (i < len(model_rows) and model_rows[i])
        ]

        legibility = raw.legibility
        if not has_list and legibility in ("full", "partial"):
            legibility = "missing"
        if raw.photo_kind in ("front_only", "other") and legibility == "full":
            legibility = "partial"  # a front-of-pack photo is never a full ingredient list

        # Product.contains = allergens in the ingredients / "Contains:" / allergens tags.
        # Product.may_contain = ONLY allergens from a may-contain statement or traces tags (what the
        # label itself warns about). "possible" ingredient-level hits (oats -> gluten, lecithin -> soy)
        # are in neither list; they reach the members who have that allergy as allergen.<id>.possible.
        strengths: dict[str, set[str]] = {}
        for h in hits:
            if kb.is_allergen(h.kb_id):
                strengths.setdefault(h.kb_id, set()).add(h.strength)
        contains = [a for a in kb.allergen_order if "contains" in strengths.get(a, ())]
        may_contain = [a for a in kb.allergen_order
                       if "may_contain" in strengths.get(a, ()) and "contains" not in strengths[a]]
        certs = _certifications(kb, raw)

        less_sure = (legibility in ("partial", "missing", "unreadable") or raw.data_confidence != "high"
                     or raw.matched_by == "name")
        ask_for_ingredients = legibility == "missing" or raw.photo_kind == "front_only" or raw.matched_by == "name"
        product = Product(
            key=raw.key, name=raw.name, brand=raw.brand, category=raw.category, product_type=raw.product_type,
            image_url=raw.image_url, source=raw.source, photo_kind=raw.photo_kind,
            data_confidence=raw.data_confidence, less_sure=less_sure, ask_for_ingredients=ask_for_ingredients,
            ingredients=[s for s in ingredients if s], contains=contains, may_contain=may_contain,
            certifications=certs, label_language=raw.label_language, code=raw.code, quantity=raw.quantity,
            nutrition=raw.nutrition, matched_by=raw.matched_by,
        )
    return NormalizedProduct(
        product=product, hits=hits, unmatched=unmatched, legibility=legibility, certifications=certs,
        analysis_tags=list(raw.analysis_tags or []), category_main=raw.category_main, kb_version=kb.version,
    )


def match_text(text: str, language: str | None = None) -> list[Hit]:
    """Match free text against the lexicon (used by /debug/kb/match and profile parsing)."""
    return _dedupe(_hits_from(get_kb().lexicon.scan(text or ""), "ingredients"))
