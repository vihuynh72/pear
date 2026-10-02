"""Open Food Facts (food) and Open Beauty Facts (cosmetics): barcode lookup, name search,
category search, and the mapper from their JSON to models.domain.RawProduct.

    raw = await lookup_barcode(clients, "049000028911")     # races OFF + OBF, then USDA FDC
    hits = await search_by_name(clients, "top ramen soy sauce", brand="Nissin")
    page = await search_category(clients, "en:instant-noodles", page=1)   # scripts/ingest.py
    blob = await fetch_raw_json(clients, "3017624010701")   # GET /debug/off/{code}

Speed and politeness
- lookup_barcode races OFF and OBF concurrently under one `settings.t_off` cap (1.1 s).
  An OFF hit wins at once; an OBF hit waits OBF_GRACE_S for OFF, then wins (cosmetic).
  Past the cap, the race waits only while OFF is still in flight with no answer, up to
  `settings.t_off_max` (2.5 s): a cold TLS connection must not turn a real product into
  "not found" (seen locally 2026-10-02: first Top Ramen scan 1.1 s -> 404, retry 0.67 s -> hit).
  Both miss -> lookup_fdc.lookup_gtin within `settings.t_fdc` (only with USDA_API_KEY).
- Only the fields the mapper reads are requested (PRODUCT_FIELDS, addendum 5.3).
- A tiny in-process sliding-window limiter keeps us under OFF's per-IP limits
  (PEAR_OFF_READS_PER_MIN=80 product reads, PEAR_OFF_SEARCHES_PER_MIN=10 searches per host).
  Interactive calls never wait: a full window logs `off.throttled` and skips the call.
  Scripts pass max_wait so they sleep instead. An HTTP 429 is logged as `off.rate_limited`.
  If nothing could answer because of throttling, the caller gets PearError("rate_limited").

Mapping rules (addendum 5.3, kb/README)
- key off:<ean13> / obf:<ean13> (core.keys.product_key); category obf -> cosmetic.
- ingredients: top-level ingredient strings in printed order. From OFF's structured
  `ingredients` (sub-ingredients rendered inline "x (a, b)") when it was parsed from the text
  we chose; otherwise ingredients_text split on top-level commas (parentheses respected).
  "Contains: ..." / "May contain ..." statements move to contains_text / may_contain_text.
- nutrition per serving when OFF has it, else per 100 g (Nutrition.basis). OFF stores mass
  nutrients in grams: sodium_mg = g * 1000, sodium_dv = mg / 2300, added sugars dv = g / 50,
  saturated fat dv = g / 20, caffeine_mg = g * 1000.

Debugging
- ?debug=1 on /v1/scan/barcode shows `lookup` (per-source status, ms, codes tried, winner).
- GET /debug/off/{code} returns the raw OFF + OBF JSON this module would map.
- Logs: off.miss, off.error, off.timeout, off.throttled, off.rate_limited, off.race.
- stats() shows the limiter windows (GET /debug/... can print it).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from core.clients import Clients
from core.keys import barcode_variants, canonical_barcode, digits, product_key
from core.logging import log_event
from core.timing import debug_put
from models.contracts import Nutrition
from models.domain import RawProduct

SourceName = Literal["off", "obf"]

PRODUCT_FIELDS = ",".join([
    "code", "product_name", "product_name_en", "brands", "quantity", "image_front_url", "image_url",
    "lang", "ingredients_text", "ingredients_text_en", "ingredients", "allergens_tags", "traces_tags",
    "additives_tags", "ingredients_analysis_tags", "labels_tags", "categories_tags", "nutriments",
    "serving_size", "serving_quantity", "nutrition_data_per",
])
SEARCH_BASE = os.environ.get("PEAR_OFF_SEARCH_BASE", "https://search.openfoodfacts.org")
READS_PER_MIN = int(os.environ.get("PEAR_OFF_READS_PER_MIN", "80"))  # OFF allows ~100 product reads/min/IP
SEARCHES_PER_MIN = int(os.environ.get("PEAR_OFF_SEARCHES_PER_MIN", "10"))
OBF_GRACE_S = 0.3  # after an OBF hit, how long OFF may still win
US_COUNTRY_TAG = "en:united-states"

_SOURCE = {"off": "open_food_facts", "obf": "open_beauty_facts"}
log = logging.getLogger("pear.off")


# ----------------------------------------------------------------------------- rate limiting


class Throttled(Exception):
    """Our limiter said no, or the server answered 429."""


class _Limiter:
    """Sliding 60 s window. `acquire(max_wait=0)` never sleeps (interactive paths)."""

    def __init__(self, name: str, per_min: int) -> None:
        self.name, self.per_min = name, max(1, per_min)
        self.hits: deque[float] = deque()
        self.throttled = 0

    def _trim(self, now: float) -> None:
        while self.hits and now - self.hits[0] >= 60.0:
            self.hits.popleft()

    def _try(self) -> float:
        """0.0 when a slot was taken, else seconds until the next slot frees up."""
        now = time.monotonic()
        self._trim(now)
        if len(self.hits) < self.per_min:
            self.hits.append(now)
            return 0.0
        return max(0.01, 60.0 - (now - self.hits[0]))

    async def acquire(self, max_wait: float = 0.0) -> bool:
        while True:
            wait = self._try()
            if wait == 0.0:
                return True
            if wait > max_wait:
                self.throttled += 1
                log_event("off.throttled", level=logging.WARNING, limiter=self.name,
                          window=len(self.hits), per_min=self.per_min, retry_in_s=round(wait, 1))
                return False
            await asyncio.sleep(wait + 0.05)
            max_wait -= wait

    def snapshot(self) -> dict[str, Any]:
        self._trim(time.monotonic())
        return {"used": len(self.hits), "per_min": self.per_min, "throttled": self.throttled}


_LIMITERS: dict[str, _Limiter] = {}


def _limiter(base: str, kind: Literal["read", "search"]) -> _Limiter:
    host = httpx.URL(base).host or base
    name = f"{host}:{kind}"
    lim = _LIMITERS.get(name)
    if lim is None:
        lim = _LIMITERS[name] = _Limiter(name, READS_PER_MIN if kind == "read" else SEARCHES_PER_MIN)
    return lim


def stats() -> dict[str, Any]:
    return {name: lim.snapshot() for name, lim in _LIMITERS.items()}


def reset_limits() -> None:
    """Tests call this so limiter state never leaks between tests."""
    _LIMITERS.clear()


async def _get_json(clients: Clients, url: str, *, params: dict[str, Any], timeout_s: float,
                    kind: Literal["read", "search"], base: str,
                    max_wait: float = 0.0) -> tuple[int, Any]:
    if not await _limiter(base, kind).acquire(max_wait):
        raise Throttled(f"{base} {kind} limiter full")
    r = await clients.http.get(url, params=params, timeout=max(0.05, timeout_s))
    if r.status_code == 429:
        log_event("off.rate_limited", level=logging.WARNING, url=url, kind=kind)
        raise Throttled(f"{url} answered 429")
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data


# ----------------------------------------------------------------------------- small helpers


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0:  # NaN or negative
        return None
    return f


def _clean(s: Any, keep_lines: bool = False) -> str | None:
    if not isinstance(s, str):
        return None
    if not keep_lines:
        s = re.sub(r"\s+", " ", s.replace("_", " ")).strip()
        return s or None
    # OFF marks allergens as _milk_. Line breaks are kept (as "\n"): a "May contain ..." line
    # printed before the list must end at its line, not swallow the ingredients after it.
    s = s.replace("_", " ").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n[\s]*", "\n", s).strip()
    return s or None


def _str_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x]
    if isinstance(v, str) and v:
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _unique(xs: Any) -> list[str]:
    out: list[str] = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- ingredient text

_OPEN, _CLOSE = "([{", ")]}"
_PREFIX = re.compile(r"^\s*(?:ingredients?|ingrédients|ingredientes|thành phần|nguyên liệu)\s*[:：]\s*",
                     re.IGNORECASE)
_ALLERGEN_WORDS = ("milk|wheat|soy|soya|egg|eggs|peanut|peanuts|tree nut|tree nuts|fish|shellfish|"
                   "sesame|crustacean|almond|almonds|cashew|cashews|coconut|pecan|pecans|walnut|walnuts|"
                   "hazelnut|hazelnuts|gluten|mustard|celery|sulfite|sulfites|lupin")
_STATEMENT = re.compile(
    r"(?P<may>\bmay (?:also )?contain\b|\bmay be present\b|\btraces? of\b|\bcan contain\b"
    r"|\b(?:produced|processed|manufactured|made|packed|packaged|prepared)\s+(?:in|on)\s+"
    r"(?:a\s+)?(?:facility|plant|factory|equipment|shared|the same)"
    r"|\bcó thể (?:có )?chứa\b|\bpuede contener\b|\btrazas de\b)"
    r"|(?P<contains>\bcontains\s*:|\bcontains\s+(?=(?:" + _ALLERGEN_WORDS + r")\b)"
    r"|\ballergens?(?:\s+information)?\s*:|\bcontiene\s*:|\bchứa\s*:)",
    re.IGNORECASE,
)


_HEADER_AT = re.compile(r"\s*(?:ingredients?|ingrédients|ingredientes|thành phần|nguyên liệu)\s*[:：]", re.IGNORECASE)


def _depths(text: str) -> list[int]:
    out, depth = [], 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
            out.append(depth)
        elif ch in _CLOSE:
            out.append(depth)
            depth = max(0, depth - 1)
        else:
            out.append(depth)
    return out


def _sentence_end(text: str, start: int, depths: list[int]) -> int:
    for i in range(start, len(text)):
        if text[i] == "." and depths[i] == 0 and (i + 1 == len(text) or text[i + 1].isspace()):
            return i + 1
        if text[i] == "\n" and depths[i] == 0:
            return i
        if depths[i] == 0 and _HEADER_AT.match(text, i) and i > start:
            return i
    return len(text)


def extract_statements(text: str) -> tuple[str, list[str], list[str]]:
    """Pull top-level "Contains: ..." / "May contain ..." sentences out of an ingredient text.

    Returns (text without the statements, contains statements, may-contain statements).
    """
    contains: list[str] = []
    may: list[str] = []
    depths = _depths(text)
    cuts: list[tuple[int, int]] = []
    pos = 0
    while pos < len(text):
        m = _STATEMENT.search(text, pos)
        if not m:
            break
        if depths[m.start()] != 0:
            pos = m.end()
            continue
        end = _sentence_end(text, m.end(), depths)
        stmt = text[m.start():end].strip().rstrip(".").strip()
        (may if m.group("may") else contains).append(stmt)
        cuts.append((m.start(), end))
        pos = end
    if not cuts:
        return text, contains, may
    kept, last = [], 0
    for a, b in cuts:
        kept.append(text[last:a])
        last = b
    kept.append(text[last:])
    rest = re.sub(r"[\s,;.]+$", "", "".join(kept)).strip()
    return rest, contains, may


def split_ingredients(text: str | None) -> list[str]:
    """Top-level split on , ; and sentence periods, never inside (), [] or {}."""
    if not text:
        return []
    text = _PREFIX.sub("", text.strip())
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        is_sep = depth == 0 and (ch in ",;" or (ch == "." and (i + 1 == len(text) or text[i + 1].isspace())))
        if is_sep:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip().strip("*").strip()
        p = re.sub(r"^(?:and|y|và)\s+", "", p, flags=re.IGNORECASE)
        if p and not re.fullmatch(r"[\W_]+", p):
            out.append(p)
    return out


def _render_structured(ing: dict[str, Any]) -> str:
    text = _clean(ing.get("text")) or ""
    subs = [s for s in (_render_structured(x) for x in ing.get("ingredients") or [] if isinstance(x, dict)) if s]
    if subs and "(" not in text:
        text = f"{text} ({', '.join(subs)})" if text else ", ".join(subs)
    return text


def _pick_text(p: dict[str, Any]) -> tuple[str | None, bool]:
    """(ingredient text to use, whether OFF's structured list was parsed from it)."""
    lang = (p.get("lang") or "").lower()
    main = _clean(p.get("ingredients_text"), keep_lines=True)
    en = _clean(p.get("ingredients_text_en"), keep_lines=True)
    if main and (lang in ("en", "vi", "es") or not en):
        return main, True
    if en:
        return en, False
    return main, True


def map_ingredients(p: dict[str, Any]) -> tuple[list[str], str | None, str | None, str | None]:
    """(ingredients, ingredients_text, contains_text, may_contain_text) for an OFF/OBF product."""
    text, structured_ok = _pick_text(p)
    contains: list[str] = []
    may: list[str] = []
    body = text
    if text:
        body, contains, may = extract_statements(text)
    structured = p.get("ingredients") if isinstance(p.get("ingredients"), list) else []
    items: list[str] = []
    if structured and (structured_ok or not text):
        for ing in structured:
            if not isinstance(ing, dict):
                continue
            r = _render_structured(ing)
            if not r:
                continue
            if _STATEMENT.match(r):
                (may if _STATEMENT.match(r).group("may") else contains).append(r)
                continue
            items.append(r)
    if not items:
        items = split_ingredients(body)
    return (items, text, "; ".join(_unique(contains)) or None, "; ".join(_unique(may)) or None)


# ----------------------------------------------------------------------------- other fields

_SUPPLEMENT_TAGS = ("en:dietary-supplements", "en:food-supplements", "en:vitamins", "en:supplements")
_LEAVE_ON = ("lotion", "cream", "moisturizer", "moisturiser", "sunscreen", "sun screen", "serum", "balm",
             "deodorant", "ointment", "leave-in", "leave in", "body butter", "face oil", "body oil")
_RINSE_OFF = ("shampoo", "conditioner", "body wash", "wash", "soap", "cleanser", "face wash", "shower",
              "bath", "cleansing", "scrub")


def most_specific_category(tags: list[str]) -> str | None:
    """OFF lists categories parent-first; the last English tag is the most specific."""
    junk = ("undefined", "null", "other", "unknown", "")
    real = [t for t in tags if t.split(":", 1)[-1].strip().lower() not in junk]
    pool = [t for t in real if t.startswith("en:")] or real
    return pool[-1] if pool else None


def cosmetic_product_type(name: str | None, categories: list[str]) -> Literal["leave_on", "rinse_off"] | None:
    for hay in ((name or "").lower(), " ".join(c.split(":", 1)[-1].replace("-", " ") for c in categories)):
        if not hay.strip():
            continue
        if any(re.search(rf"\b{re.escape(w)}", hay) for w in _LEAVE_ON):
            return "leave_on"  # ambiguous -> leave_on (full weight is the careful choice)
        if any(re.search(rf"\b{re.escape(w)}", hay) for w in _RINSE_OFF):
            return "rinse_off"
    return None


def map_nutrition(n: Any, serving_quantity: Any = None) -> Nutrition | None:
    """OFF nutriments -> Nutrition. *_100g / *_serving values are grams for mass nutrients."""
    if not isinstance(n, dict) or not n:
        return None
    serving_qty = _num(serving_quantity)
    keys = ("sodium", "salt", "added-sugars", "sugars", "saturated-fat", "caffeine")
    has_serving = any(_num(n.get(f"{k}_serving")) is not None for k in keys)
    basis: Literal["serving", "100g"] = "serving" if (has_serving or serving_qty) else "100g"

    def grams(k: str) -> float | None:
        v = _num(n.get(f"{k}_serving"))
        if v is not None:
            return v
        per100 = _num(n.get(f"{k}_100g"))
        if per100 is None:
            return None
        if basis == "serving" and serving_qty:
            return per100 * serving_qty / 100.0
        return per100  # no serving data for this nutrient: per 100 g is the careful value

    sodium_g = grams("sodium")
    if sodium_g is None:
        salt = grams("salt")
        sodium_g = salt / 2.5 if salt is not None else None
    added = grams("added-sugars")
    sugars = grams("sugars")
    sat = grams("saturated-fat")
    caffeine = grams("caffeine")
    if all(v is None for v in (sodium_g, added, sugars, sat, caffeine)):
        return None
    sodium_mg = round(sodium_g * 1000, 1) if sodium_g is not None else None
    return Nutrition(
        basis=basis,
        sodium_mg=sodium_mg,
        sodium_dv=round(sodium_mg / 2300 * 100, 1) if sodium_mg is not None else None,
        added_sugars_g=round(added, 2) if added is not None else None,
        added_sugars_dv=round(added / 50 * 100, 1) if added is not None else None,
        sugars_g=round(sugars, 2) if sugars is not None else None,
        sat_fat_g=round(sat, 2) if sat is not None else None,
        sat_fat_dv=round(sat / 20 * 100, 1) if sat is not None else None,
        caffeine_mg=round(caffeine * 1000, 1) if caffeine is not None else None,
    )


def map_off_product(p: dict[str, Any], source: SourceName = "off", code: str | None = None) -> RawProduct:
    """OFF/OBF v3 `product` object (or a search hit) -> RawProduct."""
    raw_code = digits(str(p.get("code") or code or ""))
    canon = canonical_barcode(raw_code)
    lang = (p.get("lang") or "").lower() or None
    name = _clean(p.get("product_name")) or _clean(p.get("product_name_en"))
    if lang and lang not in ("en", "vi", "es") and _clean(p.get("product_name_en")):
        name = _clean(p.get("product_name_en"))
    brands = _str_list(p.get("brands"))
    categories = _str_list(p.get("categories_tags"))
    ingredients, text, contains_text, may_text = map_ingredients(p)
    if source == "obf":
        category = "cosmetic"
    elif any(t in _SUPPLEMENT_TAGS for t in categories):
        category = "supplement"
    else:
        category = "food"
    return RawProduct(
        key=product_key(source, canon),
        source=_SOURCE[source],
        code=canon or None,
        name=name,
        brand=brands[0].strip() if brands else None,
        category=category,
        product_type=cosmetic_product_type(name, categories) if category == "cosmetic" else None,
        image_url=p.get("image_front_url") or p.get("image_url") or None,
        quantity=_clean(p.get("quantity")),
        label_language=lang,
        matched_by="barcode",
        ingredients=ingredients,
        ingredients_text=text,
        contains_text=contains_text,
        may_contain_text=may_text,
        allergens_tags=_str_list(p.get("allergens_tags")),
        traces_tags=_str_list(p.get("traces_tags")),
        additives_tags=_str_list(p.get("additives_tags")),
        analysis_tags=_str_list(p.get("ingredients_analysis_tags")),
        labels_tags=_str_list(p.get("labels_tags")),
        categories_tags=categories,
        category_main=most_specific_category(categories),
        nutrition=map_nutrition(p.get("nutriments"), p.get("serving_quantity")) if category != "cosmetic" else None,
        legibility="full" if ingredients else "missing",
        data_confidence="high" if ingredients else "low",
        fetched_at=_now_iso(),
    )


# ----------------------------------------------------------------------------- barcode lookup


def _base(clients: Clients, source: SourceName) -> str:
    s = clients.settings
    return (s.off_base if source == "off" else s.obf_base).rstrip("/")


async def fetch_product(clients: Clients, source: SourceName, code: str, *, timeout_s: float,
                        max_wait: float = 0.0) -> dict[str, Any] | None:
    """One v3 product read. None when the product isn't there. Raises Throttled / httpx errors."""
    base = _base(clients, source)
    status, data = await _get_json(clients, f"{base}/api/v3/product/{code}",
                                   params={"fields": PRODUCT_FIELDS}, timeout_s=timeout_s,
                                   kind="read", base=base, max_wait=max_wait)
    if status == 404 or not isinstance(data, dict):
        return None
    if status >= 400:
        raise httpx.HTTPError(f"{base} answered {status}")
    if data.get("status") == "failure" or not isinstance(data.get("product"), dict):
        return None
    return data["product"]


async def _lookup_source(clients: Clients, source: SourceName, codes: list[str], deadline: float,
                         diag: dict[str, Any]) -> RawProduct | None:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    d = diag.setdefault(source, {"tried": [], "status": "miss"})
    try:
        for c in codes:
            remaining = deadline - loop.time()
            if remaining <= 0.05:
                d["status"] = "timeout"
                break
            d["tried"].append(c)
            prod = await fetch_product(clients, source, c, timeout_s=remaining)
            if prod:
                d["status"] = "hit"
                return map_off_product(prod, source, code=c)
        return None
    except asyncio.CancelledError:  # the race ended first (other source won, or the cap)
        d["status"] = "cancelled"
        raise
    except Throttled:
        d["status"] = "throttled"
        raise
    except (TimeoutError, httpx.TimeoutException):
        d["status"] = "timeout"
        log_event("off.timeout", level=logging.WARNING, source=source, codes=codes)
        return None
    except (httpx.HTTPError, ValueError) as e:
        d["status"] = "error"
        d["error"] = repr(e)[:200]
        log_event("off.error", level=logging.WARNING, source=source, why=repr(e)[:300])
        return None
    finally:
        d["ms"] = int((loop.time() - t0) * 1000)


def _task_result(t: asyncio.Task) -> RawProduct | None:
    if t.cancelled() or t.exception() is not None:
        return None
    return t.result()


async def _race(off_t: asyncio.Task, obf_t: asyncio.Task, deadline: float,
                hard_deadline: float | None = None) -> RawProduct | None:
    """First good answer wins (OFF preferred). `deadline` is the soft cap (t_off); past it the race
    only keeps going while OFF is still in flight, until `hard_deadline` (t_off_max)."""
    loop = asyncio.get_running_loop()
    hard = max(deadline, hard_deadline if hard_deadline is not None else deadline)
    pending: set[asyncio.Task] = {off_t, obf_t}
    obf_hit: RawProduct | None = None
    try:
        while pending:
            now = loop.time()
            remaining = (deadline if now < deadline else hard) - now
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=remaining,
                                               return_when=asyncio.FIRST_COMPLETED)
            if not done:
                if off_t in pending and loop.time() < hard:
                    continue  # soft cap passed, OFF still loading (cold connection): wait for it
                break
            if off_t in done and _task_result(off_t):
                return _task_result(off_t)
            if obf_t in done and _task_result(obf_t):
                obf_hit = _task_result(obf_t)
            if obf_hit is not None:
                if off_t in pending:
                    grace = min(OBF_GRACE_S, max(0.0, deadline - loop.time()))
                    done, pending = await asyncio.wait({off_t}, timeout=grace)
                    if off_t in done and _task_result(off_t):
                        return _task_result(off_t)
                return obf_hit
        return obf_hit
    finally:
        for t in pending:
            t.cancel()


class LookupThrottled(Exception):
    """Nothing answered and at least one source was throttled (route -> rate_limited)."""


async def lookup_barcode(clients: Clients, code: str) -> RawProduct | None:
    """Race OFF + OBF (settings.t_off cap), then FDC. None = not found anywhere.

    Raises LookupThrottled when nothing was found and a source was skipped for rate limits.
    """
    s = clients.settings
    variants = barcode_variants(code)
    if not variants:
        return None
    codes = _unique(canonical_barcode(v) for v in variants)  # OFF normalizes 12 -> 13 itself
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    deadline = t0 + s.t_off
    hard_deadline = t0 + max(s.t_off, getattr(s, "t_off_max", s.t_off))
    diag: dict[str, Any] = {"codes": codes, "cap_s": s.t_off, "hard_cap_s": round(hard_deadline - t0, 2)}
    off_t = asyncio.create_task(_lookup_source(clients, "off", codes, hard_deadline, diag))
    obf_t = asyncio.create_task(_lookup_source(clients, "obf", codes, hard_deadline, diag))
    raw = await _race(off_t, obf_t, deadline, hard_deadline)
    throttled = any(isinstance(t.exception(), Throttled) for t in (off_t, obf_t)
                    if t.done() and not t.cancelled())
    diag["race_ms"] = int((loop.time() - t0) * 1000)
    diag["winner"] = raw.key if raw else None
    if raw is None and s.feature_fdc and s.usda_api_key:
        from pipeline import lookup_fdc

        try:
            raw = await asyncio.wait_for(lookup_fdc.lookup_gtin(clients, code), timeout=s.t_fdc)
            diag["fdc"] = "hit" if raw else "miss"
        except Exception as e:  # noqa: BLE001 - FDC is a best-effort backup
            diag["fdc"] = f"error: {e!r}"[:200]
            log_event("fdc.error", level=logging.WARNING, code=code, why=repr(e)[:300])
    debug_put("lookup", diag)
    log_event("off.race", code=code, winner=diag["winner"] or (raw.key if raw else None),
              ms=diag["race_ms"], off=diag.get("off", {}).get("status"),
              obf=diag.get("obf", {}).get("status"), fdc=diag.get("fdc"))
    if raw is None:
        log_event("off.miss", code=code, codes=codes, throttled=throttled)
        if throttled:
            raise LookupThrottled(code)
    return raw


async def lookup_source(clients: Clients, source: SourceName, code: str,
                        *, timeout_s: float | None = None) -> RawProduct | None:
    """Fetch one product from one source by code (load_product re-fetch). Never raises."""
    s = clients.settings
    loop = asyncio.get_running_loop()
    diag: dict[str, Any] = {}
    codes = _unique(canonical_barcode(v) for v in barcode_variants(code))
    try:
        return await _lookup_source(clients, source, codes, loop.time() + (timeout_s or s.t_off * 2), diag)
    except Throttled:
        return None


# ----------------------------------------------------------------------------- search


def _search_query(query: str, brand: str | None) -> str:
    q = (query or "").strip()
    if brand and brand.strip() and brand.strip().lower() not in q.lower():
        q = f"{brand.strip()} {q}".strip()
    return q


async def search_by_name(clients: Clients, query: str, *, brand: str | None = None,
                         limit: int = 5) -> list[RawProduct]:
    """OFF name search: search-a-licious first, the classic search.pl as fallback. [] on failure.

    Search hits carry only some fields; callers that need the full record re-fetch by code
    (products.match_by_name does).
    """
    s = clients.settings
    q = _search_query(query, brand)
    if not q:
        return []
    timeout_s = max(s.t_off, 1.5)
    try:
        status, data = await _get_json(
            clients, f"{SEARCH_BASE}/search",
            params={"q": q, "page_size": limit, "fields": PRODUCT_FIELDS, "langs": "en"},
            timeout_s=timeout_s, kind="search", base=SEARCH_BASE)
        if status == 200 and isinstance(data, dict) and isinstance(data.get("hits"), list):
            out = [map_off_product(h, "off") for h in data["hits"] if isinstance(h, dict) and h.get("code")]
            debug_put("off_search", {"engine": "search-a-licious", "q": q, "n": len(out)})
            return out[:limit]
        log_event("off.search_error", level=logging.WARNING, engine="search-a-licious", status=status)
    except Throttled:
        pass
    except (httpx.HTTPError, ValueError) as e:
        log_event("off.search_error", level=logging.WARNING, engine="search-a-licious", why=repr(e)[:300])
    base = s.off_base.rstrip("/")
    try:
        status, data = await _get_json(
            clients, f"{base}/cgi/search.pl",
            params={"search_terms": q, "search_simple": 1, "action": "process", "json": 1,
                    "page_size": limit, "fields": PRODUCT_FIELDS},
            timeout_s=timeout_s, kind="search", base=base)
    except Throttled:
        return []
    except (httpx.HTTPError, ValueError) as e:
        log_event("off.search_error", level=logging.WARNING, engine="search.pl", why=repr(e)[:300])
        return []
    products = data.get("products") if isinstance(data, dict) else None
    out = [map_off_product(p, "off") for p in (products or []) if isinstance(p, dict) and p.get("code")]
    debug_put("off_search", {"engine": "search.pl", "q": q, "n": len(out), "status": status})
    return out[:limit]


async def search_category(clients: Clients, category_tag: str, *, page: int = 1, page_size: int = 100,
                          country_tag: str | None = US_COUNTRY_TAG, max_wait: float = 70.0,
                          timeout_s: float = 30.0) -> list[RawProduct]:
    """One page of OFF products in a category (scripts/ingest.py). Waits for the limiter.

    Raises httpx errors and Throttled (when max_wait is exceeded); ingest reports them.
    """
    base = clients.settings.off_base.rstrip("/")
    params: dict[str, Any] = {"categories_tags": category_tag, "page": page, "page_size": page_size,
                              "fields": PRODUCT_FIELDS, "sort_by": "unique_scans_n"}
    if country_tag:
        params["countries_tags"] = country_tag
    status, data = await _get_json(clients, f"{base}/api/v2/search", params=params, timeout_s=timeout_s,
                                   kind="search", base=base, max_wait=max_wait)
    if status != 200 or not isinstance(data, dict):
        raise httpx.HTTPError(f"OFF search answered {status}")
    return [map_off_product(p, "off") for p in data.get("products") or [] if isinstance(p, dict) and p.get("code")]


async def fetch_raw_json(clients: Clients, code: str) -> dict[str, Any]:
    """Raw OFF + OBF v3 responses for /debug/off/{code}. Never raises."""
    codes = _unique(canonical_barcode(v) for v in barcode_variants(code))
    out: dict[str, Any] = {"code": code, "variants": barcode_variants(code), "fields": PRODUCT_FIELDS}

    async def one(source: SourceName) -> dict[str, Any]:
        base = _base(clients, source)
        res: dict[str, Any] = {}
        for c in codes:
            t0 = time.perf_counter()
            try:
                status, data = await _get_json(clients, f"{base}/api/v3/product/{c}",
                                               params={"fields": PRODUCT_FIELDS},
                                               timeout_s=max(clients.settings.t_off, 3.0),
                                               kind="read", base=base)
                res[c] = {"status": status, "ms": int((time.perf_counter() - t0) * 1000), "json": data}
                if status == 200:
                    break
            except Throttled as e:
                res[c] = {"status": "throttled", "why": str(e)}
                break
            except (httpx.HTTPError, ValueError) as e:
                res[c] = {"status": "error", "why": repr(e)[:300]}
        return res

    out["off"], out["obf"] = await asyncio.gather(one("off"), one("obf"))
    out["limits"] = stats()
    return out
