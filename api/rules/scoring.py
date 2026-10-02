"""Graded signals (plan 8.4): score = 100 - sum(weight x amount x context x product_type), floor 0.

- amount: %DV per serving -> <5: 0, 5-19: 0.3, 20-39: 1.0, >=40: 1.6; ingredient flags = 1.0
- context: 1.0 matching condition / skin / pregnancy / dyes_on, 0.5 less-of goal, else 0
- product type: rinse-off 0.5, everything else 1.0
- tiers: >=85 RIPE, 60-84 EASY, <60 SKIP
- leniency: SKIP needs one applied weight-45 penalty or two "real" issues (penalty >= 10),
  otherwise it is clamped to EASY (one medium issue never goes past "Easy does it").

Worked examples (tests/test_engine_unit.py reproduces them exactly):
  Bà, noodles 38% DV sodium -> 75 EASY; + 25% DV added sugars -> 50 SKIP
  Mia, leave-on lotion MI + fragrance -> 35 SKIP; rinse-off wash MI -> 77.5 EASY
  Linh, rinse-off shampoo with fragrance -> 90 RIPE (note)

Debug: evaluate(np, hh, debug=True).debug["members"][member_id]["penalties"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.contracts import Member, Nutrition
from models.domain import Hit

DV_BASE = {"sodium": ("sodium_dv", "sodium_mg", 2300.0), "added_sugars": ("added_sugars_dv", "added_sugars_g", 50.0),
           "sat_fat": ("sat_fat_dv", "sat_fat_g", 20.0)}


@dataclass
class Penalty:
    signal: dict[str, Any]
    weight: float
    amount: float
    context: float
    ptype: float
    penalty: float
    dv: float | None = None
    hits: list[Hit] = field(default_factory=list)
    mg: float | None = None

    @property
    def sid(self) -> str:
        return self.signal["id"]


def amount_factor(dv: float | None) -> float:
    if dv is None or dv < 5:
        return 0.0
    if dv < 20:
        return 0.3
    if dv < 40:
        return 1.0
    return 1.6


def nutrient_dv(nutrition: Nutrition | None, nutrient: str) -> float | None:
    if nutrition is None or nutrient not in DV_BASE:
        return None
    dv_field, amt_field, daily = DV_BASE[nutrient]
    dv = getattr(nutrition, dv_field, None)
    if dv is not None:
        return float(dv)
    amt = getattr(nutrition, amt_field, None)
    if amt is None and nutrient == "added_sugars":
        amt = nutrition.sugars_g  # fallback when added sugars are missing (domain.Nutrition)
    return None if amt is None else float(amt) / daily * 100.0


def member_context(applies: dict[str, Any], m: Member, conds: set[str], skin: set[str], less_of: set[str]) -> float:
    ctx = 0.0
    if conds & set(applies.get("conditions", [])):
        ctx = 1.0
    if skin & set(applies.get("skin", [])):
        ctx = 1.0
    if applies.get("pregnancy") and (m.pregnant or "pregnancy" in conds):
        ctx = 1.0
    if applies.get("dyes") and m.dyes_on:
        ctx = 1.0
    if ctx == 0.0 and less_of & set(applies.get("less_of", [])):
        ctx = 0.5
    return ctx


def compute_penalties(signals: list[dict[str, Any]], *, member: Member, conds: set[str], skin: set[str],
                      less_of: set[str], category: str, product_type: str | None, nutrition: Nutrition | None,
                      hits_by_id: dict[str, list[Hit]]) -> list[Penalty]:
    out: list[Penalty] = []
    ptype = 0.5 if product_type == "rinse_off" else 1.0
    for sig in signals:
        if category not in sig.get("categories", ["food"]):
            continue
        ctx = member_context(sig.get("applies", {}), member, conds, skin, less_of)
        if ctx == 0.0:
            continue
        amount = 0.0
        dv = None
        mg = None
        hits: list[Hit] = []
        nutrient = sig.get("nutrient")
        if nutrient in DV_BASE:
            dv = nutrient_dv(nutrition, nutrient)
            amount = amount_factor(dv)
        elif nutrient == "caffeine" and nutrition is not None and (nutrition.caffeine_mg or 0) > 0:
            mg = float(nutrition.caffeine_mg)
            amount = 1.0
        first_n = sig.get("first_n")
        for kid in sig.get("kb_ids", []):
            for h in hits_by_id.get(kid, []):
                if h.strength == "may_contain":
                    continue
                if first_n is not None and (h.position is None or h.position >= first_n):
                    continue
                hits.append(h)
        if hits:
            amount = max(amount, 1.0)
        if amount <= 0:
            continue
        weight = float(sig["weight"])
        out.append(Penalty(signal=sig, weight=weight, amount=amount, context=ctx, ptype=ptype,
                           penalty=weight * amount * ctx * ptype, dv=dv, hits=hits, mg=mg))
    out.sort(key=lambda p: -p.penalty)
    return out


def tier(score: float, penalties: list[Penalty]) -> tuple[str, bool]:
    """(state, lenient_clamped)."""
    if score >= 85:
        return "RIPE", False
    if score >= 60:
        return "EASY", False
    serious = any(p.weight >= 45 for p in penalties)
    real = sum(1 for p in penalties if p.penalty >= 10)
    if serious or real >= 2:
        return "SKIP", False
    return "EASY", True
