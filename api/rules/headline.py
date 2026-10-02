"""Headline (plan 8.6): most serious clause first, then "Fine for ..." for RIPE members, max two
sentences, in the owner's language (en/vi/es), names joined with a localized "and".

    Not for Mia: contains sesame. Fine for Linh, Bà and Leo.
    Couldn't confirm for Mia. Retake the ingredients?
    Not vegan for Leo: gelatin.
    Easy does it for Bà: high sodium.
    Ripe for everyone. Go for it.

Debug: build_headline(ev.verdicts, household.members, "vi", category="food", source="photo")
"""

from __future__ import annotations

from models.contracts import Headline, Member, Verdict
from rules.templates import cap, diet_phrase, short_reason, t
from rules.text import distinct_spans, join_names

ORDER = {"ALLERGY": 0, "UNSURE": 1, "DIET": 2, "SKIP": 3, "EASY": 4, "RIPE": 5}


def _diet_clause(group: list[tuple[Verdict, Member | None]], names: str, lang: str) -> str:
    from rules.kb import get_kb

    kb = get_kb()
    firsts = [next((r for r in v.reasons if r.code.startswith("diet.")), None) for v, _m in group]
    codes = {r.code if r else None for r in firsts}
    items_set = {tuple(r.matched) if r else () for r in firsts}
    if len(group) > 1 and (len(codes) > 1 or len(items_set) > 1):
        return t("headline.diet_many", lang, names=names)
    r = firsts[0]
    if r is None:
        return t("headline.diet_many", lang, names=names)
    parts = r.code.split(".")
    did = parts[1] if len(parts) > 1 else ""
    kind = parts[2] if len(parts) > 2 else "not"
    diet = kb.diets.get(did, {})
    items = ", ".join(distinct_spans(r.matched))
    if kind == "check":
        return t("headline.diet_check", lang, names=names, diet=(diet.get("label") or {}).get(lang, did), items=items)
    label = (diet.get("maybe_label" if kind == "maybe" else "not_label") or {}).get(lang) or diet_phrase(r, lang)
    key = "headline.diet" if items else "headline.diet_noitems"
    return t(key, lang, names=names, label=label, label_lower=label[:1].lower() + label[1:], items=items)


def _clause(state: str, group: list[tuple[Verdict, Member | None]], lang: str, verb: str) -> str:
    names = join_names([m.name if m else v.member_id for v, m in group], lang)
    if state == "UNSURE":
        return t("headline.unsure", lang, names=names)
    if state == "DIET":
        return _diet_clause(group, names, lang)
    reasons = {short_reason(v, m, lang) for v, m in group}
    same = len(reasons) == 1 and next(iter(reasons))
    key = {"ALLERGY": "headline.allergy", "SKIP": "headline.skip", "EASY": "headline.easy"}[state]
    if same:
        return t(key, lang, names=names, reason=next(iter(reasons)), verb=verb)
    return t(key + "_many", lang, names=names, verb=verb)


def build_headline(verdicts: list[Verdict], members: list[Member], language: str, *,
                   category: str | None = None, source: str | None = None) -> Headline:
    lang = language if language in ("en", "vi", "es") else "en"
    if not verdicts:
        return Headline(text=t("headline.none", lang), language=lang)
    by_id = {m.id: m for m in members}
    groups: dict[str, list[tuple[Verdict, Member | None]]] = {}
    for v in verdicts:
        groups.setdefault(v.state, []).append((v, by_id.get(v.member_id)))
    serious = sorted((s for s in groups if s != "RIPE"), key=ORDER.__getitem__)
    if not serious:
        return Headline(text=t("headline.all_ripe", lang), language=lang)
    verb = t("verb.food" if category == "food" else "verb.other", lang)
    first = serious[0]
    sentences = [_clause(first, groups[first], lang, verb)]
    ripe = groups.get("RIPE", [])
    if first == "UNSURE":  # the one step that resolves it beats reassurance from a partial read
        sentences.append(t("headline.retake" if source == "photo" else "headline.snap", lang))
    elif ripe:
        sentences.append(t("headline.fine", lang, names=join_names([m.name if m else v.member_id for v, m in ripe], lang)))
    elif len(serious) > 1:
        sentences.append(_clause(serious[1], groups[serious[1]], lang, verb))
    text = " ".join(cap(s.strip()) for s in sentences[:2] if s.strip())
    return Headline(text=text, language=lang)
