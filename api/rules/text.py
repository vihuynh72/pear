"""Text plumbing shared by the kb loader and the matcher: NFC + casefold with an offset map,
tokens, gap normalization, diacritic folding for search, E-number spotting, and joins.

Why an offset map: the matcher works on a folded copy (casefold, curly quotes -> ', OFF
"_milk_" emphasis and "12%" percentages blanked) but Reason.matched must be the exact printed
substring, so every folded char remembers the index of the original (NFC) char it came from.

Debug:  from rules.text import prep, tokens
        norm, offs = prep("Sữa _BỘT_ 12%"); print(norm, tokens(norm))
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# Letter runs and digit runs are separate tokens ("Milk1" footnote -> milk + 1, "Red40" -> red + 40).
# Terms are tokenized the same way, so "vitamin b12" / "e129" still match (gap '' between runs).
TOKEN_RE = re.compile(r"[^\W\d_]+|\d+")
_GAP_STRIP = re.compile(r"[\s\-‐-―]+")
_PERCENT = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?\s?%")  # also "Hazelnuts13%"
_CHAR_MAP = {
    "_": " ", "*": " ", " ": " ", "’": "'", "‘": "'", "`": "'", "´": "'",
    "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-",
    "（": "(", "）": ")", "，": ",", "、": ",", "：": ":",
    # footnote marks glued to a word ("Milk¹", "Peanuts†") -> space
    "¹": " ", "²": " ", "³": " ", "⁴": " ", "⁵": " ", "⁶": " ", "⁷": " ", "⁸": " ", "⁹": " ", "⁰": " ",
    "†": " ", "‡": " ", "§": " ", "®": " ", "™": " ",
    # invisible joiners / soft hyphen inside a word ("mi\u200blk", "pea\u00adnut") -> dropped
    "\u00ad": "", "\u200b": "", "\u200c": "", "\u200d": "", "\u2060": "", "\ufeff": "",
    # OCR / legacy fonts write Vietnamese Đ as Icelandic eth
    "Ð": "đ", "ð": "đ",
}


def _fullwidth(c: str) -> str | None:
    o = ord(c)
    if 0xFF01 <= o <= 0xFF5E:  # full-width ASCII ("ＭＩＬＫ")
        return chr(o - 0xFEE0).casefold()
    return None
# E-number / INS spellings: E129, E 129, E-129, INS 129, E322(i), E472e, E150d
E_NUMBER_RE = re.compile(
    r"(?<![^\W_])(?:e|ins)\s?-?\s?(\d{3,4})(?:\s?\(?\s?([a-f]|i{1,3}v?|vi{0,3})\s?\)?)?(?![^\W_])"
)


class Token(NamedTuple):
    start: int
    end: int
    text: str


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def prep(text: str) -> tuple[str, list[int]]:
    """NFC+casefold copy of `text` and, per folded char, the index of its original char.

    offsets has one extra trailing entry (len(original)) so spans map back with offs[end].
    """
    src = nfc(text)
    out: list[str] = []
    offs: list[int] = []
    for i, c in enumerate(src):
        rep = _CHAR_MAP.get(c)
        if rep is None:
            rep = _fullwidth(c) if c >= "\uff01" else None
            if rep is None:
                rep = c.casefold()
        for ch in rep:
            out.append(ch)
            offs.append(i)
    offs.append(len(src))
    norm = "".join(out)
    if "%" in norm:  # blank percentages, same length so offsets stay valid
        norm = _PERCENT.sub(lambda m: " " * len(m.group()), norm)
    return norm, offs


def original_span(text_nfc: str, offs: list[int], a: int, b: int) -> str:
    """Folded span [a, b) -> exact original substring."""
    start = offs[a]
    end = offs[b - 1] + 1 if b > a else start
    return text_nfc[start:end]


def tokens(norm: str) -> list[Token]:
    return [Token(m.start(), m.end(), m.group()) for m in TOKEN_RE.finditer(norm)]


def gap_norm(gap: str) -> str:
    """Gap between two tokens with whitespace/hyphens removed ('' for 'peanut free'/'peanut-free')."""
    return _GAP_STRIP.sub("", gap)


def fold_search(s: str) -> str:
    """Diacritic-insensitive key for search: 'Đậu phộng' -> 'dau phong', 'maní' -> 'mani'."""
    s = unicodedata.normalize("NFD", (s or "").casefold().replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("_", " ").replace("-", " ")
    return " ".join(s.split())


def fold_chars(norm: str) -> str:
    """Same-length diacritic fold of an already-prepped text ('bột mì' -> 'bot mi', 'đ' -> 'd').

    One output char per input char so prep() offsets and statement regions stay valid."""
    out: list[str] = []
    for c in norm:
        if c < "\u00c0":
            out.append(c)
            continue
        if c == "đ":
            out.append("d")
            continue
        base = "".join(x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn")
        out.append(base if len(base) == 1 else c)
    return "".join(out)


def e_key(num: str, suffix: str | None) -> tuple[str, str]:
    """('322', 'i') -> ('e322i', 'e322'); letters a-f are part of the code (E472e, E150d)."""
    base = f"e{num}"
    if suffix and suffix in "abcdef" and len(suffix) == 1:
        return base + suffix, base
    return base, base


def distinct_spans(spans: list[str]) -> list[str]:
    """Printed spans for display: exact duplicates dropped (case-insensitive), and a span that is a
    whole-word part of a longer span is dropped too, so the longest printed span wins:
    ['sesame seeds', 'sesame'] -> ['sesame seeds'], ['bột tôm', 'tôm'] -> ['bột tôm'].
    Order is kept. Reason.matched keeps every span (the app highlights them); this is text only."""
    folded: list[str] = []
    uniq: list[str] = []
    for s in spans:
        f = " ".join(nfc(s).casefold().split())
        if f and f not in folded:
            folded.append(f)
            uniq.append(s)
    out: list[str] = []
    for i, f in enumerate(folded):
        pat = re.compile(rf"(?<![^\W_]){re.escape(f)}(?![^\W_])")
        if any(j != i and len(g) > len(f) and pat.search(g) for j, g in enumerate(folded)):
            continue
        out.append(uniq[i])
    return out


_AND = {"en": "and", "vi": "và", "es": "y"}


def join_names(names: list[str], language: str) -> str:
    """['Linh', 'Bà', 'Leo'] -> 'Linh, Bà and Leo' (vi: 'và', es: 'y', 'e' before an i- sound)."""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    conj = _AND.get(language, "and")
    last = names[-1]
    if language == "es":
        low = fold_search(last)
        if (low.startswith("i") or low.startswith("hi")) and not low.startswith(("hia", "hie", "hio", "hiu")):
            conj = "e"
    return f"{', '.join(names[:-1])} {conj} {last}"
