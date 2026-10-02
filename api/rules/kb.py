"""Knowledge base loader (kb/*.json -> one compiled lexicon + rule tables + profile search).

What it builds once per process (get_kb() is cached):
- Lexicon: a token trie over every term of every lexicon entry (allergens, ingredients,
  additives, pets, meds). One trie, longest match first, so "peanut butter" beats "butter",
  "coconut milk" beats "milk" and neutral entries ("cocoa butter", "nutmeg", "gluten-free")
  consume their span without producing a hit. Terms of different entries that share the same
  text merge their targets ("whey" -> milk + lactose). `implies` adds targets to every term of
  the entry ("wheat" -> wheat + gluten). Plurals (s/es/ies) are handled at match time.
- e_map (E-numbers), tag_map (Open Food Facts tags), cert lexicon (labels -> cert ids).
- Rule tables: diets, signals (conditions.json + skin.json), meds notes, pet toxins.
- Profile options for /v1/kb/search and contracts/kb_index.json.

kb.version = short sha of all kb file bytes. Stored products remember the version they were
normalized with; a mismatch makes pipeline code re-normalize from raw.

Debug:
    from rules.kb import get_kb
    kb = get_kb(); kb.version; kb.lexicon.scan("Sữa bột, đậu phộng")   # raw lexicon matches
    kb.search("dau phong")                                                # profile search
    print(kb.prompt_ids_text())                                           # label_reader ids
A JSON error in kb/*.json raises at startup; /health shows it under checks.kb_error.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from models.contracts import KbSuggestion
from rules.statements import regions
from rules.text import E_NUMBER_RE, e_key, fold_chars, fold_search, gap_norm, nfc, original_span, prep, tokens

KB_DIR = Path(__file__).resolve().parents[1] / "kb"
LANGS = ("en", "vi", "es")
LEXICON_FILES = ("allergens.json", "ingredients.json", "additives.json", "pets.json", "meds.json")
PROFILE_GROUPS = ("allergies", "conditions", "skin", "diets", "less_of")
_END = "\x00"
_RANK = {"contains": 0, "possible": 1, "may_contain": 2}
# "Peanut free from ..." / "egg free-range": not an "X-free" claim, so the neutral must not fire.
_FREE_NEXT = {"from", "of", "range", "ranging", "standing", "run", "flowing"}
# Vietnamese-only letters: a text with any of these is Vietnamese, so folded one-syllable vi terms
# ("trung", "sua", "me") may fire as possible hits.
_VI_CHARS = re.compile(r"[đơưăâêôạ-ỹ]")
# In a well-accented Vietnamese label these unaccented syllables are real words ("me" = tamarind),
# so the folded pass leaves them alone there; in an unaccented label they fire as possible.
_VI_REAL_WORDS = {"me", "ca", "so", "bo", "mi", "can", "ngo", "cam", "lac"}
# Line-break hyphenation "Pea-\nnut", "PEA- NUT", "pea-nut": a second scan joins the halves.
_HYPHEN_JOIN = re.compile(r"(?<=[^\W\d_])-[ \t]*(?:\r?\n[ \t]*)?(?=[^\W\d_])")
# Bare INS codes (Codex style, VN / AU labels): "Preservative (220)", "chất nhũ hóa (322, 471)".
_BARE_INS = re.compile(r"(?<![\w.,%/])(\d{3,4})(?:\s?\(\s?(i{1,3}v?|vi{0,3}|[a-f])\s?\)|([a-f]))?(?![\w%])")


@dataclass
class TermInfo:
    targets: dict[str, str]  # kb_id -> strength (contains wins over possible)
    text: str
    vi_single: bool = False  # folded trie only: a one-syllable Vietnamese term ("trứng" -> "trung")
    any_firm: bool = False  # folded trie only: also reached by an es or multi-syllable term


@dataclass
class Match:
    """One lexicon match in a text. targets == {} means a neutral entry consumed the span."""

    kb_id: str | None
    strength: str
    matched: str  # exact original substring
    start: int  # folded-text position
    neutral: bool = False
    orig: int = 0  # position in the original (NFC) text
    end: int = 0  # folded-text end position


def _add_target(targets: dict[str, str], kb_id: str, strength: str) -> None:
    old = targets.get(kb_id)
    if old is None or _RANK[strength] < _RANK[old]:
        targets[kb_id] = strength


class Lexicon:
    def __init__(self) -> None:
        self.trie: dict = {}
        self.fold_trie: dict = {}  # vi/es terms with diacritics folded ("bot mi", "atun")
        self.max_depth = 1
        self.e_map: dict[str, dict[str, str]] = {}
        self.tag_map: dict[str, dict[str, str]] = {}
        self.n_terms = 0

    # ------------------------------------------------------------------ build
    def add_term(self, term: str, targets: dict[str, str], folded_lang: str | None = None) -> None:
        norm, _ = prep(term)
        toks = tokens(norm)
        if not toks:
            return
        self._insert(self.trie, norm, toks, term, targets)
        if folded_lang in ("vi", "es", "fr", "de"):
            fnorm = fold_chars(norm)
            info = self._insert(self.fold_trie, fnorm, tokens(fnorm), term, targets, count=False)
            if folded_lang in ("es", "fr", "de") or (targets and len(toks) > 1):
                info.any_firm = True
                info.vi_single = False
            elif targets and not info.any_firm:
                info.vi_single = True

    def _insert(self, trie: dict, norm: str, toks: list, term: str, targets: dict[str, str],
                count: bool = True) -> TermInfo:
        gaps = tuple(gap_norm(norm[toks[k].end:toks[k + 1].start]) for k in range(len(toks) - 1))
        node = trie
        for tk in toks:
            node = node.setdefault(tk.text, {})
        ends = node.setdefault(_END, {})
        info = ends.get(gaps)
        if info is None:
            info = ends[gaps] = TermInfo({}, term)
            if count:
                self.n_terms += 1
        for kid, st in targets.items():
            _add_target(info.targets, kid, st)
        self.max_depth = max(self.max_depth, len(toks))
        return info

    def add_e(self, code: str, targets: dict[str, str]) -> None:
        m = E_NUMBER_RE.fullmatch(prep(code)[0].strip())
        key = e_key(m.group(1), m.group(2))[0] if m else code.lower().replace(" ", "")
        d = self.e_map.setdefault(key, {})
        for kid, st in targets.items():
            _add_target(d, kid, st)

    def add_tag(self, tag: str, targets: dict[str, str]) -> None:
        d = self.tag_map.setdefault(tag.strip().lower(), {})
        for kid, st in targets.items():
            _add_target(d, kid, st)

    # ------------------------------------------------------------------ match
    @staticmethod
    def _child(node: dict, tok: str) -> dict | None:
        child = node.get(tok)
        if child is not None:
            return child
        if len(tok) > 3:
            if tok.endswith("ies"):
                child = node.get(tok[:-3] + "y")
                if child is not None:
                    return child
            if tok.endswith("s"):
                child = node.get(tok[:-1])
                if child is not None:
                    return child
                if tok.endswith("es"):
                    return node.get(tok[:-2])
        return None

    def _walk(self, trie: dict, norm: str, offs: list[int], src: str, reg, folded: bool = False,
              vi_text: str | None = "low") -> list[Match]:
        toks = tokens(norm)
        out: list[Match] = []
        n = len(toks)
        i = 0
        while i < n:
            node = trie
            best: tuple[int, TermInfo] | None = None
            for j in range(i, min(n, i + self.max_depth)):
                node = self._child(node, toks[j].text)
                if node is None:
                    break
                ends = node.get(_END)
                if ends:
                    raw_gaps = [norm[toks[k].end:toks[k + 1].start] for k in range(i, j)]
                    info = ends.get(tuple(gap_norm(g) for g in raw_gaps))
                    if info is None:
                        continue
                    if not info.targets:
                        # A neutral never spans a line break ("Contains: peanuts\nFree from ...") and
                        # "X free from / free-range" is not an "X-free" claim.
                        if any("\n" in g or "\r" in g for g in raw_gaps):
                            continue
                        if toks[j].text == "free" and j + 1 < n and toks[j + 1].text in _FREE_NEXT:
                            continue
                    if folded and info.vi_single and (
                            not vi_text or (vi_text == "high" and i == j and toks[i].text in _VI_REAL_WORDS)):
                        continue
                    best = (j, info)
            if best is None:
                i += 1
                continue
            j, info = best
            a, b = toks[i].start, toks[j].end
            if "(" in norm[a:b] and norm[a:b].count("(") > norm[a:b].count(")") and norm[b:b + 1] == ")":
                b += 1  # "Fragrance (Parfum)" -> keep the closing bracket in the highlight
            matched = original_span(src, offs, a, b)
            region = reg.at(a)
            if not info.targets:
                out.append(Match(None, region, matched, a, neutral=True, orig=offs[a], end=b))
            for kid, st in info.targets.items():
                if folded and info.vi_single:
                    st = "possible"  # unaccented one-syllable vi word: false alarm allowed, never a miss
                strength = "may_contain" if region == "may_contain" else st
                out.append(Match(kid, strength, matched, a, orig=offs[a], end=b))
            i = j + 1
        return out

    def scan(self, text: str, default: str = "contains", vi: str | None = None) -> list[Match]:
        """All matches in `text` (longest first, left to right), strengths resolved by statements.

        Passes (later passes only ADD hits, they never remove one):
          1. exact trie on the folded text
          2. diacritic-folded vi/es terms ("Bột mi", "trung", "ATUN") where pass 1 matched nothing
          3. hyphenation joined ("Pea-\nnut butter", "PEA- NUT OIL")
          4. E-numbers / INS (prefixed, and bare codes like "Preservative (220)")
        """
        if not text:
            return []
        norm, offs = prep(text)
        src = nfc(text)
        reg = regions(norm, default)
        out = self._walk(self.trie, norm, offs, src, reg)
        if self.fold_trie:
            fnorm = fold_chars(norm)
            covered = [(m.start, m.end) for m in out]
            vi_text = vi if vi is not None else vi_mode(text)
            fms = self._walk(self.fold_trie, fnorm, offs, src, reg, folded=True, vi_text=vi_text)
            if not vi_text and any(not m.neutral and m.kb_id and " " in m.matched.strip() for m in fms):
                vi_text = "low"  # unaccented Vietnamese with a multi-syllable hit ("sua bot")
                fms = self._walk(self.fold_trie, fnorm, offs, src, reg, folded=True, vi_text=vi_text)
            for m in fms:
                # skip where the exact pass already matched the same or a longer span
                if m.neutral or any(a < m.end and m.start < b and b - a >= m.end - m.start for a, b in covered):
                    continue
                out.append(m)
        if "-" in norm and _HYPHEN_JOIN.search(norm):
            keep = [True] * len(norm)
            for hm in _HYPHEN_JOIN.finditer(norm):
                for k in range(hm.start(), hm.end()):
                    keep[k] = False
            jnorm = "".join(c for c, k in zip(norm, keep) if k)
            joffs = [o for o, k in zip(offs, keep + [True]) if k]
            jreg = regions(jnorm, default)
            for m in self._walk(self.trie, jnorm, joffs, src, jreg):
                if not m.neutral and m.kb_id and "-" in m.matched:
                    out.append(m)
        if "e" in norm or "ins" in norm:
            for m in E_NUMBER_RE.finditer(norm):
                full, base = e_key(m.group(1), m.group(2))
                targets = self.e_map.get(full) or self.e_map.get(base)
                if not targets:
                    continue
                b = m.end()
                if norm[b - 1:b] == ")" and "(" not in norm[m.start():b]:
                    b -= 1  # "(E322i)" -> highlight "E322i"
                matched = original_span(src, offs, m.start(), b)
                region = reg.at(m.start())
                for kid, st in targets.items():
                    out.append(Match(kid, "may_contain" if region == "may_contain" else st, matched, m.start(),
                                     orig=offs[m.start()], end=b))
        if any(c.isdigit() for c in norm):
            out.extend(self._bare_ins(norm, offs, src, reg))
        return out

    def _bare_ins(self, norm: str, offs: list[int], src: str, reg) -> list[Match]:
        out: list[Match] = []
        for m in _BARE_INS.finditer(norm):
            before = norm[:m.start()].rstrip()
            after = norm[m.end():].lstrip()
            if not before or before[-1] not in "(,:":
                continue
            if after and after[0] not in "),;.":
                continue
            full, base = e_key(m.group(1), m.group(2) or m.group(3))
            targets = self.e_map.get(full) or self.e_map.get(base)
            if not targets:
                continue
            matched = original_span(src, offs, m.start(), m.end())
            region = reg.at(m.start())
            for kid, st in targets.items():
                out.append(Match(kid, "may_contain" if region == "may_contain" else st, matched, m.start(),
                                 orig=offs[m.start()], end=m.end()))
        return out


@dataclass
class KB:
    version: str = ""
    lexicon: Lexicon = field(default_factory=Lexicon)
    cert_lexicon: Lexicon = field(default_factory=Lexicon)
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)  # lexicon id -> entry
    allergens: dict[str, dict[str, Any]] = field(default_factory=dict)
    allergen_order: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)  # alias -> canonical (all groups)
    diets: dict[str, dict[str, Any]] = field(default_factory=dict)
    signals: list[dict[str, Any]] = field(default_factory=list)
    meds: list[dict[str, Any]] = field(default_factory=list)
    pet_toxins: list[dict[str, Any]] = field(default_factory=list)
    certs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cert_order: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    options: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # group -> options
    analysis_map: dict[str, dict[str, str]] = field(default_factory=dict)
    _search_index: list[tuple[str, str, dict[str, str], list[tuple[str, float, str]]]] = field(default_factory=list)
    _labels: dict[str, dict[str, str]] = field(default_factory=dict)

    # ------------------------------------------------------------------ public API
    def ids(self) -> list[str]:
        """All matchable (non-neutral) lexicon ids, in kb file order."""
        return [k for k, e in self.entries.items() if not e.get("neutral")]

    def prompt_ids_text(self) -> str:
        """One line per id, 'id: english label', for the label_reader system prompt (kb_ids.txt)."""
        return "\n".join(f"{i}: {self.label(i, 'en')}" for i in self.ids())

    def profile_ids(self) -> dict[str, list[str]]:
        return {g: [o["id"] for o in self.options.get(g, [])] for g in PROFILE_GROUPS}

    def label(self, kb_id: str, language: str = "en") -> str:
        labels = self._labels.get(kb_id) or self._labels.get(self.canonical(kb_id))
        if labels:
            return labels.get(language) or labels.get("en") or kb_id.replace("_", " ")
        return kb_id.replace("_", " ")

    def label_many(self, kb_id: str, language: str = "en") -> str:
        e = self.allergens.get(kb_id) or {}
        many = e.get("label_many") or {}
        return many.get(language) or self.label(kb_id, language)

    def is_allergen(self, kb_id: str) -> bool:
        return self.canonical(kb_id) in self.allergens

    def canonical(self, kb_id: str) -> str:
        k = (kb_id or "").strip().lower().replace("-", "_").replace(" ", "_")
        return self.aliases.get(k, k)

    def source_of(self, kb_id: str) -> str | None:
        e = self.entries.get(kb_id)
        return e.get("source") if e else None

    def search(self, q: str, language: str = "en", limit: int = 10,
               groups: list[str] | None = None) -> list[KbSuggestion]:
        """Profile option search, diacritic-insensitive prefix/substring scoring (< 2 ms)."""
        fq = fold_search(q)
        wanted = set(groups) if groups else None
        scored: list[tuple[float, int, str, KbSuggestion]] = []
        for order, (group, oid, labels, strings) in enumerate(self._search_index):
            if wanted is not None and group not in wanted:
                continue
            if not fq:
                sc = 1.0
            else:
                sc = 0.0
                for s, w, lang in strings:
                    if s == fq:
                        v = 100.0
                    elif s.startswith(fq):
                        v = 80.0 + 10.0 * len(fq) / len(s)
                    elif (" " + fq) in (" " + s):
                        v = 65.0 + 10.0 * len(fq) / len(s)
                    elif len(fq) >= 3 and fq in s:
                        v = 45.0 + 10.0 * len(fq) / len(s)
                    else:
                        continue
                    v *= w
                    if lang == language:
                        v += 3.0
                    if v > sc:
                        sc = v
                if sc <= 0:
                    continue
            label = labels.get(language) or labels.get("en") or oid
            scored.append((-sc, order, oid, KbSuggestion(group=group, id=oid, label=label,
                                                          labels=dict(labels), score=round(sc, 1))))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [s[3] for s in scored[: max(1, limit)]]

    def option_synonyms(self, group: str, oid: str) -> list[str]:
        for g, i, _labels, strings in self._search_index:
            if g == group and i == oid:
                return [s for s, _w, _l in strings]
        return []


# ---------------------------------------------------------------------- loading


def _read(name: str) -> Any:
    p = KB_DIR / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _version() -> str:
    h = hashlib.sha1()
    for p in sorted(KB_DIR.glob("*.json")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _entry_terms(e: dict, key: str) -> list[str]:
    out: list[str] = []
    for _lang, terms in (e.get(key) or {}).items():
        out.extend(terms)
    return out


def _entry_terms_lang(e: dict, key: str) -> list[tuple[str, str]]:
    return [(lang, t) for lang, terms in (e.get(key) or {}).items() for t in terms]


def _compile_entry(kb: KB, e: dict) -> None:
    kid = e["id"]
    implies = [kb.canonical(x) for x in e.get("implies", [])]
    neutral = bool(e.get("neutral"))

    def targets(strength: str) -> dict[str, str]:
        if neutral:
            return {}
        t = {kid: strength}
        for imp in implies:
            _add_target(t, imp, strength)
        return t

    for lang, term in _entry_terms_lang(e, "terms"):
        kb.lexicon.add_term(term, targets("contains"), lang)
    for lang, term in _entry_terms_lang(e, "possible_terms"):
        kb.lexicon.add_term(term, targets("possible"), lang)
    implied_possible = {x: "possible" for x in e.get("implies_possible", [])}
    if implied_possible and not neutral:
        for lang, term in _entry_terms_lang(e, "terms"):
            kb.lexicon.add_term(term, implied_possible, lang)
    for code in e.get("e_numbers", []):
        kb.lexicon.add_e(code, targets("contains"))
    for code in e.get("e_numbers_possible", []):
        kb.lexicon.add_e(code, targets("possible"))
    for tag in e.get("off_tags", []):
        kb.lexicon.add_tag(tag, targets("contains"))
    for tag in e.get("off_tags_possible", []):
        kb.lexicon.add_tag(tag, targets("possible"))


def _search_strings(opt: dict, extra: list[str] | None = None) -> list[tuple[str, float, str]]:
    seen: dict[str, tuple[float, str]] = {}

    def add(s: str, w: float, lang: str) -> None:
        f = fold_search(s)
        if f and (f not in seen or seen[f][0] < w):
            seen[f] = (w, lang)

    for lang, lab in (opt.get("label") or {}).items():
        add(lab, 1.0, lang)
    add(opt["id"].replace("_", " "), 0.95, "en")
    for a in opt.get("aliases", []):
        add(a, 0.95, "en")
    for lang, syns in (opt.get("synonyms") or {}).items():
        for s in syns:
            add(s, 0.92, lang)
    for lang, terms in (opt.get("terms") or {}).items():
        if lang == "inci":
            continue
        for s in terms:
            add(s, 0.85, lang)
    for s in extra or []:
        add(s, 0.85, "en")
    return [(s, w, lang) for s, (w, lang) in seen.items()]


def load_kb() -> KB:
    kb = KB(version=_version())
    kb.sources = {s["id"]: s for s in _read("sources.json").get("sources", [])}

    allergens = _read("allergens.json").get("items", [])
    for e in allergens:
        kb.allergens[e["id"]] = e
        kb.allergen_order.append(e["id"])
        for a in e.get("aliases", []):
            kb.aliases[a.lower().replace(" ", "_").replace("-", "_")] = e["id"]

    lexicon_entries: list[dict] = []
    for fname in LEXICON_FILES:
        for e in _read(fname).get("items", []):
            lexicon_entries.append(e)
            if e["id"] not in kb.entries:
                kb.entries[e["id"]] = e
            if e.get("label"):
                kb._labels.setdefault(e["id"], e["label"])
    for e in lexicon_entries:
        _compile_entry(kb, e)

    # Diets
    for d in _read("diets.json").get("diets", []):
        kb.diets[d["id"]] = d
        kb._labels.setdefault(d["id"], d.get("label", {}))
        for a in d.get("aliases", []):
            kb.aliases[a.lower().replace(" ", "_").replace("-", "_")] = d["id"]

    # Conditions / skin options + signals
    cond = _read("conditions.json")
    skin = _read("skin.json")
    kb.signals = list(cond.get("signals", [])) + list(skin.get("signals", []))
    options: dict[str, list[dict]] = {
        "allergies": allergens,
        "conditions": cond.get("options", {}).get("conditions", []),
        "skin": skin.get("options", []),
        "diets": list(kb.diets.values()),
        "less_of": cond.get("options", {}).get("less_of", []),
    }
    for g in ("conditions", "skin", "less_of"):
        for o in options[g]:
            kb._labels.setdefault(o["id"], o.get("label", {}))
            for a in o.get("aliases", []):
                kb.aliases[a.lower().replace(" ", "_").replace("-", "_")] = o["id"]
    kb.options = options
    for g in PROFILE_GROUPS:
        for o in options[g]:
            kb._search_index.append((g, o["id"], dict(o.get("label") or {}), _search_strings(o)))

    # Meds + pets
    kb.meds = _read("meds.json").get("notes", [])
    kb.pet_toxins = _read("pets.json").get("toxins", [])

    # Certifications
    for c in _read("certifications.json").get("items", []):
        kb.certs[c["id"]] = c
        kb.cert_order.append(c["id"])
        for term in _entry_terms(c, "terms"):
            kb.cert_lexicon.add_term(term, {c["id"]: "contains"})
        for tag in c.get("off_tags", []):
            kb.cert_lexicon.add_tag(tag, {c["id"]: "contains"})

    # OFF ingredients_analysis_tags
    kb.analysis_map = {k: dict(v) for k, v in _read("ingredients.json").get("analysis_tags", {}).items()}
    return kb


@lru_cache(maxsize=1)
def get_kb() -> KB:
    return load_kb()


def reload_kb() -> KB:
    """Drop the cache (tests, /debug). Returns the fresh KB."""
    get_kb.cache_clear()
    return get_kb()


def scan_text(text: str, default: str = "contains") -> list[Match]:
    return get_kb().lexicon.scan(text, default)


def vi_mode(*texts: str | None, label_language: str | None = None) -> str | None:
    """Vietnamese hint for the folded pass: None (not Vietnamese), "high" (well accented, so an
    unaccented "me" is tamarind) or "low" (diacritics mostly lost, OCR / OFF: every folded vi
    syllable may fire as a possible hit)."""
    joined = nfc(" ".join(t for t in texts if t)).casefold()
    words = [w for w in re.findall(r"[^\W\d_]+", joined)]
    has_vi = any(_VI_CHARS.search(w) for w in words)
    if not has_vi:
        return "low" if label_language == "vi" and words else None
    accented = sum(1 for w in words if any(ord(c) > 127 for c in w))
    return "high" if accented >= 0.5 * len(words) else "low"
