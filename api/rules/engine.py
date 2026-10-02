"""Rules engine: NormalizedProduct x Household -> one Verdict per member + headline. Pure, no I/O.

Order of checks per member (plan 8.3-8.7, kb/README.md section 4):
  1. pets: ASPCA toxins for the species -> ALLERGY "Toxic for dogs/cats"
  2. allergies: strict -> contains/possible/may_contain all ALLERGY ("May contain" label when
     only may-contain); sensitive ("Ingredients only") -> contains/possible ALLERGY, may-contain
     is a trace_note. Strict + partial/missing list + allergen not found -> UNSURE.
  3. diets with certifications (8.5): Not X / Maybe not X / Check / cert note
  4. graded signals (rules/scoring.py) -> EASY / SKIP / RIPE (+notes); supplements skip this
  5. notes: meds (never change state), supplement scope, less sure / matched by name, check label
Precedence ALLERGY > UNSURE > DIET > SKIP > EASY > RIPE. Reasons sorted hard -> soft -> note.
Verdict.score is always the computed score for graded people (result.py hides it unless debug).

Debug:
    ev = evaluate(np, household, debug=True); ev.debug["members"]["m_mia"]
    [(v.member_id, v.state, v.label, [r.code for r in v.reasons]) for v in ev.verdicts]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models.contracts import Allergy, Household, Member, Reason, Verdict
from models.domain import Evaluation, Hit, NormalizedProduct
from rules.headline import build_headline
from rules.kb import KB, get_kb
from rules.scoring import Penalty, compute_penalties, tier
from rules.templates import species_word, t
from rules.text import distinct_spans, fold_search, join_names

_SEV = {"hard": 0, "soft": 1, "note": 2}
_GROUP = {"allergy": 0, "pet": 0, "unsure": 1, "diet": 2, "signal": 3, "cert": 4, "med": 5, "confidence": 6, "scope": 7}
_RANK = {"contains": 0, "possible": 1, "may_contain": 2}
_INGEST = ("food", "supplement")


@dataclass
class ProductCtx:
    np: NormalizedProduct
    hits: list[Hit]
    by_id: dict[str, list[Hit]]
    certs: set[str]
    category: str
    product_type: str | None
    source: str
    legibility: str
    matched_by: str | None
    data_confidence: str
    folded_text: str

    @classmethod
    def build(cls, np: NormalizedProduct) -> ProductCtx:
        by_id: dict[str, list[Hit]] = {}
        for h in np.hits:
            by_id.setdefault(h.kb_id, []).append(h)
        p = np.product
        return cls(np=np, hits=list(np.hits), by_id=by_id, certs=set(np.certifications or p.certifications),
                   category=p.category, product_type=p.product_type, source=p.source, legibility=np.legibility,
                   matched_by=p.matched_by, data_confidence=p.data_confidence,
                   folded_text=" " + fold_search(" , ".join(p.ingredients)) + " ")


@dataclass
class _R:
    sev: str
    group: str
    order: float
    reason: Reason


@dataclass
class _MemberEval:
    reasons: list[_R] = field(default_factory=list)
    allergy_hard: bool = False
    allergy_labels: list[str] = field(default_factory=list)
    unsure: bool = False
    diet_label: str | None = None
    diet_rank: int = 9
    graded: str = "RIPE"
    score: float | None = None
    penalties: list[Penalty] = field(default_factory=list)
    lenient: bool = False

    def add(self, sev: str, group: str, code: str, text: str, lang: str, matched: list[str] | None = None,
            source: str | None = None, order: float = 0.0) -> None:
        self.reasons.append(_R(sev, group, order, Reason(code=code, text=text, language=lang, severity=sev,
                                                          matched=matched or [], source=source)))


def _printed(hits: list[Hit], limit: int = 6, *, tag_fallback: bool = False) -> list[str]:
    """Exact printed substrings for highlights. Tags are not printed text, so they only show
    (humanized: "en:sesame-seeds" -> "sesame seeds") when nothing printed matched."""
    out: list[str] = []
    seen: set[str] = set()
    for h in hits:
        if h.where == "tags" or not h.matched:
            continue
        k = h.matched.casefold()
        if k not in seen:
            seen.add(k)
            out.append(h.matched)
        if len(out) >= limit:
            break
    if not out and tag_fallback:
        for h in hits:
            if h.where == "tags" and h.matched:
                word = h.matched.split(":", 1)[-1].replace("-", " ").strip()
                if word and word.casefold() not in seen:
                    seen.add(word.casefold())
                    out.append(word)
    return out


def _items(kb: KB, hits: list[Hit], lang: str, limit: int = 3) -> str:
    printed = distinct_spans(_printed(hits))[:limit]
    if printed:
        return ", ".join(printed)
    labels = list(dict.fromkeys(kb.label(h.kb_id, lang) for h in hits))
    return ", ".join(labels[:limit])


def _detail(kb: KB, aid: str, printed: list[str], lang: str) -> str:
    if not printed:
        return ""
    names = {kb.label(aid, lg).casefold() for lg in ("en", "vi", "es")} | {kb.label_many(aid, lg).casefold() for lg in ("en", "vi", "es")}
    shown = distinct_spans(printed)[:3]  # "sesame seeds, sesame" -> "sesame seeds"
    if len(shown) == 1 and shown[0].casefold().strip("_ ") in names:
        return ""
    return f" ({', '.join(shown)})"


# ---------------------------------------------------------------------- checks


def _check_pet(ctx: ProductCtx, m: Member, me: _MemberEval, kb: KB, lang: str) -> None:
    species = (m.species or "").strip().lower()
    toxins = [x for x in kb.pet_toxins if species in x.get("species", [])]
    if species not in ("dog", "cat"):
        toxins = list(kb.pet_toxins)
    for i, tox in enumerate(toxins):
        hits = [h for k in tox["kb_ids"] for h in ctx.by_id.get(k, []) if h.strength != "may_contain"]
        if not hits:
            continue
        me.allergy_hard = True
        label = PET_LABELS.get(species, PET_LABEL_OTHER)
        if label not in me.allergy_labels:
            me.allergy_labels.insert(0, label)
        me.add("hard", "pet", f"pet.{tox['id']}",
               t("reason.pet", lang, species=species_word(species, lang), items=_items(kb, hits, lang)),
               lang, _printed(hits), tox.get("source"), order=i)


def _plural_variants(word: str) -> set[str]:
    out = {word}
    for w in list(out):
        if w.endswith("ies") and len(w) > 4:
            out.add(w[:-3] + "y")
        if w.endswith("es") and len(w) > 3:
            out.add(w[:-2])
        if w.endswith("s") and len(w) > 3:
            out.add(w[:-1])
    for w in list(out):
        if w.endswith("y") and len(w) > 2:
            out.add(w[:-1] + "ies")
        out.add(w + "s")
        out.add(w + "es")
    return out


def _custom_hit(needle: str, folded_text: str) -> str | None:
    """Literal check for an allergy Pear has no kb entry for, with singular/plural folding on the
    last word ("strawberry" finds "strawberries", "peaches" finds "peach puree")."""
    if not needle:
        return None
    head, _, last = needle.rpartition(" ")
    for v in sorted(_plural_variants(last), key=len, reverse=True):
        phrase = f"{head} {v}" if head else v
        m = re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded_text)
        if m:
            return m.group(0)
    return None


def _allergy_hits(ctx: ProductCtx, kb: KB, aid: str) -> list[Hit]:
    return ctx.by_id.get(aid, [])


def _check_allergies(ctx: ProductCtx, m: Member, me: _MemberEval, kb: KB, lang: str) -> list[str]:
    """Returns strict allergen ids with no hit (for the UNSURE rule)."""
    unfound_strict: list[str] = []
    allergies = list(m.allergies)
    have = {kb.canonical(a.id) for a in allergies}
    for c in [*m.conditions, *m.skin]:  # "celiac disease" typed as a health problem -> strict gluten
        cid = kb.canonical(c)
        if cid in kb.allergens and cid not in have:
            have.add(cid)
            allergies.append(Allergy(id=cid))
    for order, a in enumerate(allergies):
        aid = kb.canonical(a.id)
        strict = a.level != "sensitive"
        known = aid in kb.entries
        if not known:
            word = a.id.replace("_", " ")
            found = _custom_hit(fold_search(word), ctx.folded_text)
            if found:
                me.allergy_hard = True
                me.allergy_labels.append("Allergy")
                me.add("hard", "allergy", f"allergen.{aid}",
                       t("reason.allergen.contains", lang, allergen=word, detail=f" ({found})"), lang,
                       [found], None, order=order)
            elif strict:
                # Pear has no kb entry for it: a strict allergy is never Ripe on a literal miss.
                me.unsure = True
                me.add("hard", "unsure", "confidence.unsure",
                       t("reason.allergen.unknown", lang, allergen=word), lang, [], None, order=order)
            else:
                me.add("note", "allergy", f"allergen.{aid}.unknown",
                       t("reason.allergen.unknown", lang, allergen=word), lang, [], None, order=order)
            continue
        hits = _allergy_hits(ctx, kb, aid)
        by_strength: dict[str, list[Hit]] = {"contains": [], "possible": [], "may_contain": []}
        for h in hits:
            by_strength[h.strength].append(h)
        source = kb.source_of(aid)
        label = kb.label(aid, lang)
        many = kb.label_many(aid, lang)
        if by_strength["contains"] or by_strength["possible"]:
            kind = "contains" if by_strength["contains"] else "possible"
            hs = by_strength[kind]
            printed = _printed(hs, tag_fallback=True)
            code = f"allergen.{aid}" if kind == "contains" else f"allergen.{aid}.possible"
            me.allergy_hard = True
            me.allergy_labels.append("Allergy")
            me.add("hard", "allergy", code,
                   t(f"reason.allergen.{kind}", lang, allergen=label, detail=_detail(kb, aid, printed, lang)),
                   lang, printed, source, order=_RANK[kind] * 1000 + order)
        elif by_strength["may_contain"]:
            hs = by_strength["may_contain"]
            printed = _printed(hs, tag_fallback=True)
            if strict:
                me.allergy_hard = True
                me.allergy_labels.append("May contain")
                me.add("hard", "allergy", f"allergen.{aid}.may_contain",
                       t("reason.allergen.may_contain", lang, allergen=many, detail=_detail(kb, aid, printed, lang)),
                       lang, printed, source, order=2000 + order)
            else:
                me.add("note", "allergy", f"allergen.{aid}.trace_note",
                       t("reason.allergen.trace_note", lang, allergen=many), lang, printed, source, order=order)
        elif strict:
            unfound_strict.append(aid)
    return unfound_strict


def _check_unsure(ctx: ProductCtx, me: _MemberEval, kb: KB, lang: str, unfound_strict: list[str]) -> None:
    by_name = ctx.matched_by == "name"
    if not unfound_strict or (ctx.legibility not in ("partial", "missing", "unreadable") and not by_name):
        return
    me.unsure = True
    names = join_names([kb.label(a, lang) if a in kb.entries else a.replace("_", " ") for a in unfound_strict], lang)
    if ctx.legibility == "partial":
        key = "reason.unsure.partial"
    elif ctx.legibility in ("missing", "unreadable"):
        key = "reason.unsure.missing"
    else:  # a name match may be a different variant of the product: never Ripe for a strict allergy
        key = "reason.unsure.name"
    me.add("hard", "unsure", "confidence.unsure", t(key, lang, allergens=names), lang, [], None)


def _check_diets(ctx: ProductCtx, m: Member, me: _MemberEval, kb: KB, lang: str) -> None:
    for order, d in enumerate(m.diets):
        did = kb.canonical(d)
        diet = kb.diets.get(did)
        if diet is None:
            continue
        forbidden = set(diet.get("forbidden", []))
        maybe_ids = set(diet.get("maybe", []))
        skip = set(diet.get("ingest_only", [])) if ctx.category not in _INGEST else set()
        definite: list[Hit] = []
        maybes: list[Hit] = []
        for h in ctx.hits:
            if h.kb_id in skip or h.strength == "may_contain":
                continue
            if h.kb_id in forbidden:
                (definite if h.strength == "contains" else maybes).append(h)
            elif h.kb_id in maybe_ids:
                maybes.append(h)
        certified = bool(set(diet.get("cert_ids", [])) & ctx.certs)
        dlabel = (diet.get("label") or {}).get(lang, did)
        not_label = (diet.get("not_label") or {})
        maybe_label = (diet.get("maybe_label") or {})
        src = diet.get("source")
        if certified:
            if definite:
                me.add("hard", "diet", f"diet.{did}.check",
                       t("reason.diet.check", lang, diet=dlabel, items=_items(kb, definite, lang)), lang,
                       _printed(definite), src, order=order)
                if me.diet_rank > 1:
                    me.diet_label, me.diet_rank = "Check", 1
            elif maybes:
                items = _items(kb, maybes, lang)
                me.add("note", "cert", f"cert.{did}",
                       t("reason.cert.settled", lang, items=items[:1].upper() + items[1:],
                         assumed=(diet.get("assumed") or {}).get(lang, dlabel), diet=dlabel), lang,
                       _printed(maybes), src, order=order)
            else:
                me.add("note", "cert", f"cert.{did}", t("reason.cert.ok", lang, diet=dlabel), lang, [], src, order=order)
            continue
        if definite:
            me.add("hard", "diet", f"diet.{did}",
                   t("reason.diet.not", lang, label=not_label.get(lang, f"Not {did}"), items=_items(kb, definite, lang)),
                   lang, _printed(definite), src, order=order)
            if me.diet_rank > 0:
                me.diet_label, me.diet_rank = not_label.get("en", f"Not {did}"), 0
        elif maybes:
            me.add("soft", "diet", f"diet.{did}.maybe",
                   t("reason.diet.maybe", lang, label=maybe_label.get(lang, f"Maybe not {did}"), items=_items(kb, maybes, lang)),
                   lang, _printed(maybes), src, order=order)
            if me.diet_rank > 2:
                me.diet_label, me.diet_rank = maybe_label.get("en", f"Maybe not {did}"), 2


def _signal_text(kb: KB, p: Penalty, lang: str) -> str:
    sid = p.sid
    if p.signal.get("nutrient") in ("sodium", "added_sugars", "sat_fat") and p.dv is not None and not p.hits:
        level = "some" if p.amount < 1.0 else ("high" if p.amount < 1.6 else "very_high")
        key = {"sat_fat": "saturated_fat"}.get(p.signal["nutrient"], p.signal["nutrient"])
        return t(f"reason.signal.{key}.{level}", lang, dv=int(round(p.dv)))
    items = _items(kb, p.hits, lang) if p.hits else (f"{int(p.mg)} mg" if p.mg else "")
    key, default = f"reason.signal.{sid}", sid.replace("_", " ").capitalize() + " ({items})"
    bare = _tidy(t(key, lang, default=default, items=""))
    if items and fold_search(items) == fold_search(bare):
        items = ""  # the text already names it: "Fragrance (Fragrance)" -> "Fragrance"
    if not items:
        return bare
    return _tidy(t(key, lang, default=default, items=items))


def _tidy(text: str) -> str:
    """Drop an empty "({items})" / ": {items}" slot so a reason never ends in "()" or ":"."""
    return text.replace(" ()", "").replace("(). ", "").strip().rstrip(":").strip()


def _check_unknown_conditions(m: Member, me: _MemberEval, kb: KB, lang: str) -> None:
    """A custom health problem / skin issue Pear has no rule for gets a note, never silence."""
    known = {o["id"] for g in ("conditions", "skin", "less_of") for o in kb.options.get(g, [])}
    for i, c in enumerate([*m.conditions, *m.skin]):
        cid = kb.canonical(c)
        if cid in known or cid in kb.allergens or cid in kb.diets or cid in ("pregnant", "pregnancy"):
            continue
        me.add("note", "confidence", f"condition.{cid}.unknown",
               t("reason.condition.unknown", lang, condition=c.replace("_", " ")), lang, [], None, order=i)


def _check_signals(ctx: ProductCtx, m: Member, me: _MemberEval, kb: KB, lang: str) -> None:
    _check_unknown_conditions(m, me, kb, lang)
    conds = {kb.canonical(c) for c in m.conditions}
    if "kidney_potassium_limit" in conds:
        conds.add("kidney_disease")
    skin = {kb.canonical(s) for s in m.skin}
    less_of = {kb.canonical(x) for x in m.less_of}
    pens = compute_penalties(kb.signals, member=m, conds=conds, skin=skin, less_of=less_of, category=ctx.category,
                             product_type=ctx.product_type, nutrition=ctx.np.product.nutrition, hits_by_id=ctx.by_id)
    score = max(0.0, 100.0 - sum(p.penalty for p in pens))
    state, lenient = tier(score, pens)
    me.score = round(score, 2)
    me.graded = state
    me.penalties = pens
    me.lenient = lenient
    sev = "note" if state == "RIPE" else "soft"
    for i, p in enumerate(pens):
        me.add(sev, "signal", f"signal.{p.sid}", _signal_text(kb, p, lang), lang, _printed(p.hits),
               p.signal.get("source"), order=i)


def _check_meds(ctx: ProductCtx, m: Member, me: _MemberEval, kb: KB, lang: str) -> None:
    if not m.medications:
        return
    meds = " " + " ".join(fold_search(x) for x in m.medications) + " "
    for i, note in enumerate(kb.meds):
        if not any(re.search(rf"(?<![a-z0-9]){re.escape(fold_search(term))}", meds) for term in note.get("med_terms", [])):
            continue
        hits = [h for k in note.get("kb_ids", []) for h in ctx.by_id.get(k, []) if h.strength != "may_contain"]
        if hits:
            text = (note.get("text") or {}).get(lang) or (note.get("text") or {}).get("en", "")
            me.add("note", "med", f"med.{note['id']}", text, lang, _printed(hits), note.get("source"), order=i)


def _confidence_notes(ctx: ProductCtx, m: Member, me: _MemberEval, lang: str) -> None:
    if ctx.matched_by == "name":
        me.add("note", "confidence", "confidence.matched_by_name", t("reason.matched_by_name", lang), lang)
    elif not me.unsure and ctx.legibility in ("partial", "missing", "unreadable"):
        key = "reason.less_sure.partial" if ctx.legibility == "partial" else "reason.less_sure.missing"
        me.add("note", "confidence", "confidence.less_sure", t(key, lang), lang)
    elif ctx.data_confidence != "high" and not me.unsure:
        me.add("note", "confidence", "confidence.less_sure", t("reason.less_sure.data", lang), lang)
    has_strict = any(a.level != "sensitive" for a in m.allergies)
    if has_strict and ctx.source != "photo" and not me.allergy_hard:
        me.add("note", "confidence", "confidence.check_label", t("reason.check_label", lang), lang, order=1)


# ---------------------------------------------------------------------- assemble


def _member_verdict(ctx: ProductCtx, m: Member, household: Household, kb: KB, debug: bool) -> tuple[Verdict, dict | None]:
    me = _MemberEval()
    is_pet = m.type == "pet"
    lang = household.language if is_pet else m.language
    if is_pet:
        _check_pet(ctx, m, me, kb, lang)
    unfound = _check_allergies(ctx, m, me, kb, lang)
    _check_unsure(ctx, me, kb, lang, unfound)
    if not is_pet:
        _check_diets(ctx, m, me, kb, lang)
        if ctx.category != "supplement":
            _check_signals(ctx, m, me, kb, lang)
        _check_meds(ctx, m, me, kb, lang)
        if ctx.category == "supplement":
            me.add("note", "scope", "supplement.scope", t("reason.supplement_scope", lang), lang)
    _confidence_notes(ctx, m, me, lang)

    if me.allergy_hard:
        state = "ALLERGY"
        label = me.allergy_labels[0] if me.allergy_labels and me.allergy_labels[0].startswith("Toxic") else (
            "Allergy" if "Allergy" in me.allergy_labels else (me.allergy_labels[0] if me.allergy_labels else "Allergy"))
    elif me.unsure:
        state, label = "UNSURE", "Unsure"
    elif me.diet_label:
        state, label = "DIET", me.diet_label
    elif me.graded == "SKIP":
        state, label = "SKIP", "Skip it"
    elif me.graded == "EASY":
        state, label = "EASY", "Easy does it"
    else:
        state, label = "RIPE", "Ripe"
    ordered = sorted(enumerate(me.reasons), key=lambda x: (_SEV[x[1].sev], _GROUP[x[1].group], x[1].order, x[0]))
    verdict = Verdict(member_id=m.id, state=state, label=label, reasons=[r.reason for _i, r in ordered],
                      score=me.score)
    dbg = None
    if debug:
        dbg = {"state": state, "score": me.score, "graded": me.graded, "lenient_clamp": me.lenient,
               "penalties": [{"signal": p.sid, "weight": p.weight, "amount": p.amount, "context": p.context,
                              "product_type": p.ptype, "penalty": round(p.penalty, 2), "dv": p.dv} for p in me.penalties],
               "language": lang}
    return verdict, dbg


PET_LABELS = {"dog": "Toxic for dogs", "cat": "Toxic for cats"}
PET_LABEL_OTHER = "Toxic for pets"


def chip_labels(kb: KB | None = None) -> dict[str, list[str]]:
    """Every value Verdict.label can take, per state (English; the app localizes them).
    kb/README.md section 3 and contracts/README.md section 6 list exactly these
    (tests/test_engine_unit.py::test_chip_labels_documented)."""
    kb = kb or get_kb()
    diet: list[str] = []
    for did, d in kb.diets.items():
        diet.append((d.get("not_label") or {}).get("en", f"Not {did}"))
    for did, d in kb.diets.items():
        diet.append((d.get("maybe_label") or {}).get("en", f"Maybe not {did}"))
    return {"RIPE": ["Ripe"], "EASY": ["Easy does it"], "SKIP": ["Skip it"],
            "ALLERGY": ["Allergy", "May contain", *PET_LABELS.values(), PET_LABEL_OTHER],
            "DIET": [*diet, "Check"], "UNSURE": ["Unsure"]}


def evaluate(np: NormalizedProduct, household: Household, member_ids: list[str] | None = None,
             *, debug: bool = False) -> Evaluation:
    """Pure function: verdict per selected member (family order) + headline. Under 20 ms for 5."""
    kb = get_kb()
    ctx = ProductCtx.build(np)
    members = household.select(member_ids)
    verdicts: list[Verdict] = []
    dbg: dict[str, Any] = {}
    for m in members:
        v, d = _member_verdict(ctx, m, household, kb, debug)
        verdicts.append(v)
        if debug:
            dbg[m.id] = d
    headline = build_headline(verdicts, members, household.language, category=ctx.category, source=ctx.source)
    debug_out = None
    if debug:
        debug_out = {"members": dbg, "kb_version": kb.version, "legibility": ctx.legibility,
                     "certifications": sorted(ctx.certs), "kb_ids": sorted(np.kb_ids())}
    return Evaluation(verdicts=verdicts, headline=headline, debug=debug_out)


def evaluate_many(nps: list[NormalizedProduct], household: Household,
                  member_ids: list[str] | None = None) -> list[Evaluation]:
    """Batch evaluate (picks: 50 candidates x 5 members under 100 ms). No debug."""
    return [evaluate(np, household, member_ids) for np in nps]
