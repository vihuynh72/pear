"""Photo path (addendum 2 + 10): ONE Gemini call reads the label and maps each ingredient to
kb ids, then the rules engine decides. Gemini never decides a state (plan 8.7.4).

    POST /v1/scan/photo (routes/scan_photo.py)
      -> read_label(clients, image, front)      label_reader, model_label, t_label (4 s), 1024 tok
      -> legibility unreadable                  PearError too_blurry  (HTTP 422, next=retake)
         photo_kind other, no ingredients       PearError unreadable  (HTTP 422, next=retake)
         front_only                             pipeline.products.match_by_name(brand, name)
                                                  found -> ScanResult (less sure, matched by name)
                                                  none  -> PearError not_found (next=take_photo)
         ingredients                            label_to_raw -> rules.normalize.normalize
                                                  -> pipeline.result.run_rules / build_scan_result
      -> background: picks prefetch (food), pipeline.products.after_scan (save text-only + log)

Image bytes are never stored or logged: only the model's text answer goes anywhere.

Debugging
- ?debug=1 shows `gemini.label_reader` (model, ms, raw JSON text), `label` (photo_kind,
  legibility, ingredient count, label language), and the usual hits/evaluation from
  build_scan_result. Log events: photo.read, photo.front_only, photo.after_scan_failed.
- Print the system prompt: see pipeline/prompts.py.
- Tests: tests/test_photo.py (fake Gemini, in-memory store).
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from core.clients import Clients
from core.errors import PearError
from core.keys import photo_key
from core.logging import log_event
from core.timing import debug_put, stage
from models.contracts import Household, Nutrition, ScanResult, base_language
from models.domain import NormalizedProduct, RawProduct
from pipeline import result as result_mod
from pipeline.gemini import generate_json, image_part
from pipeline.prompts import LABEL_FRONT_NOTE, LABEL_SCHEMA, LABEL_USER, label_system, say

# Daily Values used when a label shows an absolute amount but no %DV (FDA, per day).
DV_SODIUM_MG = 2300.0
DV_ADDED_SUGARS_G = 50.0
DV_SAT_FAT_G = 20.0
NO_GLOSS_LANGUAGES = {"en", "vi", "es"}  # label_reader fills "en" only for other languages


# ----------------------------------------------------------------------------- lazy deps
# Owned by other lanes; imported at call time so this module loads while they are being
# written, and so tests can swap them (monkeypatch.setattr(photo_gemini, "_products", ...)).


def _products():
    from pipeline import products

    return products


def _picks():
    from pipeline import picks

    return picks


def _store():
    from pipeline import product_store

    return product_store


def _normalize(raw: RawProduct) -> NormalizedProduct:
    from rules import normalize as normalize_mod

    return normalize_mod.normalize(raw)


# ----------------------------------------------------------------------------- read


def sniff_image_mime(data: bytes) -> str:
    """Best-effort MIME type from magic bytes (the app sends JPEG; HEIC/PNG/WebP also work)."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis"):
            return "image/heic"
        if brand in (b"mif1", b"msf1", b"heif"):
            return "image/heif"
    return "image/jpeg"


async def read_label(clients: Clients, image: bytes, front: bytes | None = None) -> dict:
    """label_reader: one Gemini call. Returns the cleaned LABEL_SCHEMA dict."""
    s = clients.settings
    contents: list[Any] = [image_part(image, sniff_image_mime(image))]
    if front:
        contents += [image_part(front, sniff_image_mime(front)), LABEL_FRONT_NOTE]
    contents.append(LABEL_USER)
    data = await generate_json(clients, call="label_reader", model=s.model_label, contents=contents,
                               schema=LABEL_SCHEMA, system=label_system(), timeout_s=s.t_label,
                               max_output_tokens=1024)
    label = clean_label(data)
    summary = {"photo_kind": label["photo_kind"], "legibility": label["legibility"],
               "ingredients": len(label["ingredients"]), "label_language": label["label_language"],
               "has_front": bool(front)}
    log_event("photo.read", **summary)
    debug_put("label", summary)
    return label


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    if isinstance(v, str):
        try:
            return float(v.strip().rstrip("%").replace(",", "."))
        except ValueError:
            return None
    return None


def clean_label(data: dict | None) -> dict:
    """Coerce whatever the model returned into the LABEL_SCHEMA shape (never raises)."""
    data = data if isinstance(data, dict) else {}
    ingredients: list[dict[str, Any]] = []
    for item in data.get("ingredients") or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not _s(item.get("text")):
            continue
        ids = [i.strip() for i in item.get("kb_ids") or [] if isinstance(i, str) and i.strip()]
        ingredients.append({"text": _s(item["text"]), "en": _s(item.get("en")) or None, "kb_ids": ids})
    legibility = data.get("legibility") if data.get("legibility") in ("full", "partial", "unreadable") else None
    kind = data.get("photo_kind") if data.get("photo_kind") in ("ingredients", "front_only", "other") else None
    if kind is None:
        kind = "ingredients" if ingredients else "other"
    if legibility is None:
        legibility = "full" if ingredients else "unreadable"
    elif legibility == "unreadable" and ingredients:
        legibility = "partial"  # it read something: treat as a cut-off list, never as full
    category = data.get("category") if data.get("category") in ("food", "cosmetic", "supplement", "other") else "food"
    ptype = data.get("product_type") if data.get("product_type") in ("leave_on", "rinse_off") else None
    nutrition = data.get("nutrition") if isinstance(data.get("nutrition"), dict) else {}
    certs = [_s(c) for c in data.get("certifications") or [] if _s(c)]
    return {
        "legibility": legibility,
        "photo_kind": kind,
        "product_name": _s(data.get("product_name")) or None,
        "brand": _s(data.get("brand")) or None,
        "category": category,
        "product_type": ptype,
        "label_language": base_language(_s(data.get("label_language"))) if _s(data.get("label_language")) else None,
        "contains_statement": _s(data.get("contains_statement")) or None,
        "may_contain_statement": _s(data.get("may_contain_statement")) or None,
        "ingredients": ingredients,
        "certifications": certs,
        "nutrition": {k: _num(v) for k, v in nutrition.items() if _num(v) is not None},
    }


# ----------------------------------------------------------------------------- map


def _dv(dv: float | None, amount: float | None, daily: float) -> float | None:
    if dv is not None:
        return round(dv, 1)
    if amount is None:
        return None
    return round(amount / daily * 100.0, 1)


def nutrition_from_label(n: dict[str, Any] | None) -> Nutrition | None:
    """Per-serving Nutrition; %DV computed from absolute values when the label omits it."""
    if not n:
        return None
    sodium, sugars, sat = n.get("sodium_mg"), n.get("added_sugars_g"), n.get("sat_fat_g")
    out = Nutrition(
        basis="serving",
        sodium_mg=sodium,
        sodium_dv=_dv(n.get("sodium_dv"), sodium, DV_SODIUM_MG),
        added_sugars_g=sugars,
        added_sugars_dv=_dv(n.get("added_sugars_dv"), sugars, DV_ADDED_SUGARS_G),
        sat_fat_g=sat,
        sat_fat_dv=_dv(n.get("sat_fat_dv"), sat, DV_SAT_FAT_G),
    )
    has_any = any(v is not None for k, v in out.model_dump().items() if k != "basis")
    return out if has_any else None


def _valid_kb_ids() -> tuple[set[str], Any] | None:
    """(allowed ids, kb) or None when the kb is unavailable (then model ids pass through)."""
    try:
        from rules.kb import get_kb

        kb = get_kb()
        return set(kb.ids()), kb
    except Exception:
        return None


def _filter_ids(ids: list[str], allowed: tuple[set[str], Any] | None) -> list[str]:
    if allowed is None:
        return ids
    valid, kb = allowed
    out: list[str] = []
    for i in ids:
        try:
            c = kb.canonical(i)
        except Exception:
            c = i
        if c in valid and c not in out:
            out.append(c)
    return out


def label_to_raw(data: dict) -> RawProduct:
    """LABEL_SCHEMA dict -> RawProduct (source photo, key photo:<12 hex>). No interpretation."""
    label = clean_label(data)  # idempotent: accepts the raw model dict or read_label's output
    texts = [i["text"] for i in label["ingredients"]]
    lang = label.get("label_language")
    gloss = lang not in NO_GLOSS_LANGUAGES if lang else True
    allowed = _valid_kb_ids()
    legibility = label["legibility"]
    return RawProduct(
        key=photo_key(label.get("brand"), label.get("product_name"), texts),
        source="photo",
        matched_by="photo",
        name=label.get("product_name"),
        brand=label.get("brand"),
        category=label.get("category") or "food",
        product_type=label.get("product_type"),
        label_language=lang,
        photo_kind=label.get("photo_kind"),
        ingredients=texts,
        ingredients_en=[(i.get("en") if gloss else None) for i in label["ingredients"]],
        # "; " (a statement boundary), not ", ": a may-contain item read into the list must not
        # turn the real ingredients after it into may_contain.
        ingredients_text="; ".join(texts) or None,
        contains_text=label.get("contains_statement"),
        may_contain_text=label.get("may_contain_statement"),
        certifications_text=list(label.get("certifications") or []),
        model_kb_ids=[_filter_ids(i.get("kb_ids") or [], allowed) for i in label["ingredients"]],
        nutrition=nutrition_from_label(label.get("nutrition")),
        legibility=legibility,
        data_confidence={"full": "high", "partial": "medium"}.get(legibility, "low"),
    )


# ----------------------------------------------------------------------------- scan


def _start_prefetch(clients: Clients, np: NormalizedProduct, household: Household) -> None:
    if np.product.category != "food":
        return
    try:
        _picks().start_prefetch(clients, np, household)
    except Exception as e:  # picks never break the scan
        log_event("photo.prefetch_failed", level=logging.WARNING, why=repr(e)[:300])


async def _after_scan(clients: Clients, **kw: Any) -> None:
    try:
        res = _products().after_scan(clients, **kw)
        if inspect.isawaitable(res):
            await res
    except Exception as e:  # saving/logging must never break the response
        log_event("photo.after_scan_failed", level=logging.WARNING, why=repr(e)[:300])


def _log_scan_bg(clients: Clients, *, uid: str, np: NormalizedProduct, result: ScanResult) -> None:
    """Front-only matches are not photo products, so only the scan log is written."""
    if not clients.settings.feature_scan_log:
        return
    try:
        from core.background import spawn

        spawn("scan_log", _store().log_scan(
            clients, uid=uid, product_key=np.product.key,
            states={v.member_id: v.state for v in result.verdicts},
            timings_ms=result.timings_ms, source="photo_front"))
    except Exception as e:
        log_event("photo.scan_log_failed", level=logging.WARNING, why=repr(e)[:300])


async def _front_only(clients: Clients, label: dict, household: Household,
                      member_ids: list[str] | None, uid: str) -> ScanResult:
    lang = household.language
    brand, name = label.get("brand"), label.get("product_name")
    np: NormalizedProduct | None = None
    if brand or name:
        with stage("match_by_name"):
            np = await _products().match_by_name(clients, brand=brand, name=name)
    # `name` is a reserved LogRecord attribute, so the field is product_name.
    log_event("photo.front_only", brand=brand, product_name=name, matched=np.product.key if np else None)
    debug_put("front_only", {"brand": brand, "name": name, "matched": np.product.key if np else None})
    if np is None:
        raise PearError("not_found", say("error.front_only_no_match", lang, default="Snap the ingredients."),
                        next="take_photo", detail={"brand": brand, "name": name})
    ev = result_mod.run_rules(np, household, member_ids)
    _start_prefetch(clients, np, household)
    out = result_mod.build_scan_result(np, household, member_ids, evaluation=ev)
    _log_scan_bg(clients, uid=uid, np=np, result=out)
    return out


async def scan_photo(clients: Clients, *, image: bytes, front: bytes | None, household: Household,
                     member_ids: list[str] | None, uid: str) -> ScanResult:
    """Photo -> ScanResult. Raises PearError too_blurry / unreadable / not_found (contract)."""
    lang = household.language
    label = await read_label(clients, image, front)
    has_ingredients = bool(label["ingredients"])
    if label["legibility"] == "unreadable" and not has_ingredients:
        raise PearError("too_blurry", say("error.too_blurry", lang, default="Too blurry. Retake?"),
                        next="retake", detail={"photo_kind": label["photo_kind"]})
    if not has_ingredients:
        if label["photo_kind"] == "front_only":
            return await _front_only(clients, label, household, member_ids, uid)
        if label["photo_kind"] == "other":
            raise PearError("unreadable", say("error.not_a_label", lang,
                                              default="That doesn't look like a label. Snap the ingredients?"),
                            next="retake", detail={"photo_kind": "other"})
        # An ingredient panel with nothing readable on it is a blurry photo.
        raise PearError("too_blurry", say("error.too_blurry", lang, default="Too blurry. Retake?"),
                        next="retake", detail={"photo_kind": label["photo_kind"], "ingredients": 0})

    raw = label_to_raw(label)
    with stage("normalize"):
        np = _normalize(raw)
    ev = result_mod.run_rules(np, household, member_ids)
    _start_prefetch(clients, np, household)
    out = result_mod.build_scan_result(np, household, member_ids, evaluation=ev)
    await _after_scan(clients, raw=raw, np=np, household=household, uid=uid, evaluation=ev, cached=False)
    return out
