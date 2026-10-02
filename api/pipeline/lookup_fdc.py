"""USDA FoodData Central: backup barcode lookup for US branded foods.

    raw = await lookup_gtin(clients, "070662010013")   # -> RawProduct(key="fdc:0070662010013") | None

Only used by lookup_off.lookup_barcode when Open Food Facts and Open Beauty Facts both miss,
settings.feature_fdc is on and USDA_API_KEY is set (Secret Manager on Cloud Run).
One request: GET {fdc_base}/foods/search?query=<gtin>&dataType=Branded&api_key=...
The search result's foodNutrients are per 100 g for branded foods; with servingSize in g/ml
we scale to one serving (Nutrition.basis = "serving").

Debugging
- Logs: fdc.miss / fdc.error / fdc.hit with the gtin and status.
- Try it by hand: curl "https://api.nal.usda.gov/fdc/v1/foods/search?query=070662010013&dataType=Branded&api_key=DEMO_KEY"
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.clients import Clients
from core.keys import barcode_variants, canonical_barcode, digits, product_key
from core.logging import log_event
from models.contracts import Nutrition
from models.domain import RawProduct

# FDC nutrient numbers -> our field. Units: 307/262 mg, the rest g.
_SODIUM, _ADDED, _SUGARS, _SAT, _CAFFEINE = "307", "539", "269", "606", "262"
_SERVING_UNITS = {"g", "grm", "ml", "mlt"}
log = logging.getLogger("pear.fdc")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f < 0 else f


def _query_code(code: str) -> str:
    """FDC indexes gtinUpc mostly as 12-digit UPC-A; prefer that spelling."""
    vs = barcode_variants(code)
    for v in vs:
        if len(v) == 12:
            return v
    return vs[0] if vs else digits(code)


def _nutrients(food: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for n in food.get("foodNutrients") or []:
        if not isinstance(n, dict):
            continue
        num = str(n.get("nutrientNumber") or n.get("number") or "")
        val = _num(n.get("value", n.get("amount")))
        if num and val is not None:
            out[num] = val
    return out


def map_nutrition(food: dict[str, Any]) -> Nutrition | None:
    per100 = _nutrients(food)
    if not per100:
        return None
    size = _num(food.get("servingSize"))
    unit = str(food.get("servingSizeUnit") or "").lower()
    factor, basis = (size / 100.0, "serving") if size and unit in _SERVING_UNITS else (1.0, "100g")

    def v(num: str) -> float | None:
        x = per100.get(num)
        return round(x * factor, 2) if x is not None else None

    sodium, added, sugars, sat, caffeine = v(_SODIUM), v(_ADDED), v(_SUGARS), v(_SAT), v(_CAFFEINE)
    if all(x is None for x in (sodium, added, sugars, sat, caffeine)):
        return None
    return Nutrition(
        basis=basis,
        sodium_mg=sodium,
        sodium_dv=round(sodium / 2300 * 100, 1) if sodium is not None else None,
        added_sugars_g=added,
        added_sugars_dv=round(added / 50 * 100, 1) if added is not None else None,
        sugars_g=sugars,
        sat_fat_g=sat,
        sat_fat_dv=round(sat / 20 * 100, 1) if sat is not None else None,
        caffeine_mg=caffeine,
    )


def map_fdc_food(food: dict[str, Any], code: str | None = None) -> RawProduct:
    from pipeline.lookup_off import _now_iso, extract_statements, split_ingredients

    gtin = canonical_barcode(digits(str(food.get("gtinUpc") or code or "")))
    text = (food.get("ingredients") or "").strip() or None
    contains: list[str] = []
    may: list[str] = []
    body = text
    if text:
        body, contains, may = extract_statements(text)
    ingredients = split_ingredients(body)
    name = (food.get("description") or "").strip() or None
    brand = (food.get("brandName") or food.get("brandOwner") or "").strip() or None
    size = food.get("householdServingFullText") or (
        f"{food.get('packageWeight')}" if food.get("packageWeight") else None)
    return RawProduct(
        key=product_key("fdc", gtin),
        source="usda_fdc",
        code=gtin or None,
        name=name.title() if name and name.isupper() else name,
        brand=brand.title() if brand and brand.isupper() else brand,
        category="food",
        quantity=size,
        label_language="en",
        matched_by="barcode",
        ingredients=ingredients,
        ingredients_text=text,
        contains_text="; ".join(contains) or None,
        may_contain_text="; ".join(may) or None,
        nutrition=map_nutrition(food),
        legibility="full" if ingredients else "missing",
        data_confidence="high" if ingredients else "low",
        fetched_at=_now_iso(),
    )


async def lookup_gtin(clients: Clients, code: str) -> RawProduct | None:
    """One FDC branded search by GTIN. None on miss, missing key, or any error (logged)."""
    s = clients.settings
    if not s.usda_api_key:
        return None
    q = _query_code(code)
    wanted = {canonical_barcode(v) for v in barcode_variants(code)}
    try:
        r = await clients.http.get(
            f"{s.fdc_base.rstrip('/')}/foods/search",
            params={"query": q, "dataType": "Branded", "pageSize": 5, "api_key": s.usda_api_key},
            timeout=s.t_fdc,
        )
    except httpx.HTTPError as e:
        log_event("fdc.error", level=logging.WARNING, gtin=q, why=repr(e)[:300])
        return None
    if r.status_code != 200:
        log_event("fdc.error", level=logging.WARNING, gtin=q, status=r.status_code)
        return None
    try:
        foods = (r.json() or {}).get("foods") or []
    except ValueError:
        return None
    for food in foods:
        if isinstance(food, dict) and canonical_barcode(digits(str(food.get("gtinUpc") or ""))) in wanted:
            log_event("fdc.hit", gtin=q, fdc_id=food.get("fdcId"))
            return map_fdc_food(food, code)
    log_event("fdc.miss", gtin=q, results=len(foods))
    return None
