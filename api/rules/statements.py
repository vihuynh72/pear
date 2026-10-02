"""Contains / may-contain statement parsing (plan 8.7.1: contains and may-contain parsed separately).

Every text the matcher scans gets a list of strength regions. A may-contain trigger ("May
contain", "may contain traces of", "processed in a facility that also processes", "Có thể
chứa", "Puede contener", "trazas de", ...) switches the rest of the sentence to may_contain; a
contains trigger ("Contains:", "Chứa", "Contiene", "Allergens:") switches it back to contains.
A sentence end (. ; ! ? newline) resets to the text's default strength. This is why a
may-contain sentence INSIDE an ingredient list becomes may_contain and never contains.

Failure direction is deliberate: if a sentence ends early (e.g. "Vit. B12"), the remainder
falls back to the default (contains) -> a false alarm, never a missed allergen.

Debug:  from rules.statements import regions
        regions("sugar, milk. may contain peanuts", "contains")
"""

from __future__ import annotations

import bisect
import re

_W = r"(?<![^\W_])"  # left word boundary that works for đ, ă, ñ, ...
_WE = r"(?![^\W_])"

MAY_RE = re.compile(
    _W + r"(?:"
    # English
    r"may\s+(?:also\s+)?contain(?:s)?(?:\s+traces?\s+of|\s+trace\s+amounts?\s+of)?"
    r"|traces?\s+(?:amounts?\s+)?of"
    r"|(?:produced|processed|manufactured|made|packed|packaged|prepared|produced)\s+(?:in|on|at)\s+"
    r"(?:a\s+|the\s+)?(?:same\s+|shared\s+)?(?:facility|factory|plant|equipment|line|lines|premises|site|kitchen)"
    r"(?:\s+(?:that|which|where))?(?:\s+also)?(?:\s+(?:processes|handles|uses|manufactures|produces|packages|makes|process|handle))?"
    r"|shared\s+(?:equipment|facility|line|lines)(?:\s+with)?"
    r"|(?:facility|factory|equipment|plant)\s+(?:that\s+|which\s+)?also\s+(?:processes|handles|uses|manufactures|produces|packages)"
    r"|cross[\s\-]?conta(?:ct|mination)(?:\s+with)?"
    # Vietnamese
    r"|có\s+thể\s+(?:có\s+)?(?:chứa|lẫn|có)"
    r"|(?:được\s+)?(?:sản\s+xuất|chế\s+biến|đóng\s+gói)\s+(?:trên|trong|tại)\s+(?:cùng\s+)?(?:dây\s+chuyền|thiết\s+bị)"
    r"(?:\s+(?:có\s+)?(?:chế\s+biến|sản\s+xuất|sử\s+dụng|xử\s+lý))?"
    # "Sản xuất tại nhà máy Acecook" is an address, not a warning: a factory / site form needs a
    # cross-contact verb in the same clause ("... nhà máy có chế biến ...").
    r"|(?:được\s+)?(?:sản\s+xuất|chế\s+biến|đóng\s+gói)\s+(?:trên|trong|tại)\s+(?:cùng\s+)?(?:nhà\s+máy|cơ\s+sở|xưởng)"
    r"[^.,;:\n]{0,40}?(?:có|cũng)\s+(?:chế\s+biến|sử\s+dụng|xử\s+lý|sản\s+xuất)"
    r"|(?:dùng\s+chung|chung)\s+(?:dây\s+chuyền|thiết\s+bị)(?:\s+với)?"
    r"|thiết\s+bị\s+dùng\s+chung(?:\s+với)?"
    r"|dấu\s+vết(?:\s+của)?"
    r"|lượng\s+vết(?:\s+của)?"
    # Spanish
    r"|pueden?\s+contener(?:\s+trazas?\s+de)?"
    r"|trazas?\s+de"
    r"|(?:elaborad|fabricad|producid|envasad|procesad|hech)[oa]s?\s+en\s+(?:una\s+|la\s+misma\s+|un\s+|las\s+|los\s+)?"
    r"(?:l[íi]neas?|equipos?)"
    r"(?:\s+(?:que|donde\s+se)\s+(?:también\s+)?(?:procesan?|manipulan?|utilizan?|elaboran?|usan?))?"
    # "envasado en establecimiento autorizado" is not a warning: plants need a processing verb.
    r"|(?:(?:elaborad|fabricad|producid|envasad|procesad|hech)[oa]s?|se\s+elabora)\s+en\s+(?:una\s+|la\s+misma\s+|un\s+|el\s+mismo\s+)?"
    r"(?:planta|f[áa]brica|instalaci[óo]n|instalaciones|establecimiento)"
    r"[^.;:\n]{0,30}?(?:que|donde\s+se)\s+(?:también\s+)?(?:procesan?|manipulan?|utilizan?|elaboran?|usan?)"
    r")" + _WE
)
CONTAINS_RE = re.compile(
    _W + r"(?:contains?|allergens?|allergy\s+advice|chứa|thành\s+phần\s+dị\s+ứng|contiene|contienen|alérgenos?)" + _WE
)
END_RE = re.compile(r"[.;!?\n\r。]")
# An ingredient-list header starts a new region: "May contain milk Ingredients: sugar, peanuts".
HEADER_RE = re.compile(_W + r"(?:ingredients?|ingredientes|ingrédients|thành\s+phần|nguyên\s+liệu|composition)\s*:")

STRONG = "contains"
MAY = "may_contain"


class Regions:
    """Strength lookup by folded-text position."""

    __slots__ = ("starts", "strengths")

    def __init__(self, starts: list[int], strengths: list[str]) -> None:
        self.starts = starts
        self.strengths = strengths

    def at(self, pos: int) -> str:
        i = bisect.bisect_right(self.starts, pos) - 1
        return self.strengths[i] if i >= 0 else self.strengths[0]

    def has_may(self) -> bool:
        return MAY in self.strengths


def regions(norm: str, default: str = STRONG) -> Regions:
    """Split folded text into strength regions (see module docstring)."""
    events: list[tuple[int, int, str]] = []
    may_spans: list[tuple[int, int]] = []
    for m in MAY_RE.finditer(norm):
        events.append((m.start(), m.end(), MAY))
        may_spans.append((m.start(), m.end()))
        opened = norm.rfind("(", 0, m.start())
        if opened != -1 and norm.find(")", opened, m.start()) == -1:
            close = norm.find(")", m.end())
            if close != -1:  # "milk (may contain nuts), sugar": the region ends at ")"
                events.append((close, close + 1, "end"))
    for m in CONTAINS_RE.finditer(norm):
        if any(a <= m.start() < b or a < m.end() <= b for a, b in may_spans):
            continue
        # "Made in a facility that processes products that contain peanuts": a 'contain' later in
        # the same may-contain sentence is not a Contains statement unless it is a heading ("Contains:").
        heading = norm[m.end():m.end() + 2].lstrip()[:1] == ":"
        if not heading and any(b <= m.start() and not END_RE.search(norm, b, m.start()) for _a, b in may_spans):
            continue
        events.append((m.start(), m.end(), STRONG))
    for m in END_RE.finditer(norm):
        events.append((m.start(), m.end(), "end"))
    for m in HEADER_RE.finditer(norm):
        events.append((m.start(), m.end(), "end"))
    events.sort()
    starts = [0]
    strengths = [default]
    for s, _e, kind in events:
        new = default if kind == "end" else kind
        if new != strengths[-1]:
            if starts[-1] == s:
                strengths[-1] = new
            else:
                starts.append(s)
                strengths.append(new)
    return Regions(starts, strengths)


def mentions_may_contain(norm: str) -> bool:
    return MAY_RE.search(norm) is not None
