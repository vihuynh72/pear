"""All server-side user-facing strings (en, vi, es) plus the short phrases headlines and spoken
answers are built from. No Gemini here: barcode results need zero translation calls (plan 10.4).

    t("reason.allergen.contains", "vi", allergen="mè", detail=" (sesame seeds)")  -> "Có mè (sesame seeds)"
    t("some.new.key", "es", default="English text {x}", x=1)                        -> falls back to English default

Rules for every string: short and warm ("Easy does it", "Not for Mia: contains sesame."), names
the ingredient, never the word "safe" (nor "an toàn" / "seguro"), no medical advice beyond
"ask your doctor / pharmacist". tests/test_engine_unit.py scans every template for banned words.

Debug: python -c "from rules.templates import t; print(t('headline.all_ripe', 'vi'))"
"""

from __future__ import annotations

import re
from typing import Any

from models.contracts import Household, Member, Verdict, base_language
from models.domain import Evaluation
from rules.text import distinct_spans

LANGS = ("en", "vi", "es")
BANNED = ("safe", "an toàn", "seguro", "segura")

# key -> (en, vi, es)
_T: dict[str, tuple[str, str, str]] = {
    # ---------------------------------------------------------------- reasons: allergens
    "reason.allergen.contains": ("Contains {allergen}{detail}", "Chứa {allergen}{detail}", "Contiene {allergen}{detail}"),
    "reason.allergen.possible": ("Possible {allergen}{detail}", "Có thể từ {allergen}{detail}", "Posible {allergen}{detail}"),
    "reason.allergen.may_contain": ("May contain {allergen}{detail}", "Có thể chứa {allergen}{detail}", "Puede contener {allergen}{detail}"),
    "reason.allergen.trace_note": ("May contain {allergen} (label warning)", "Có thể chứa {allergen} (cảnh báo trên nhãn)",
                                   "Puede contener {allergen} (aviso de la etiqueta)"),
    "reason.allergen.unknown": ("Pear can't check for {allergen} yet. Check the label for it.",
                                "Pear chưa kiểm tra được {allergen}. Nhớ xem kỹ nhãn nhé.",
                                "Pear aún no puede revisar {allergen}. Revisa bien la etiqueta."),
    "reason.condition.unknown": ("Pear can't check for {condition} yet.", "Pear chưa kiểm tra được {condition}.",
                                 "Pear aún no puede revisar {condition}."),
    # ---------------------------------------------------------------- reasons: confidence
    "reason.unsure.partial": ("Couldn't read the whole label to check for {allergens}",
                              "Không đọc được hết nhãn để kiểm tra {allergens}",
                              "No se pudo leer toda la etiqueta para revisar {allergens}"),
    "reason.unsure.missing": ("No ingredient list to check for {allergens}",
                              "Không có danh sách thành phần để kiểm tra {allergens}",
                              "No hay lista de ingredientes para revisar {allergens}"),
    "reason.unsure.name": ("Matched by name, so Pear can't rule out {allergens}. Snap the ingredients.",
                           "Tìm theo tên nên chưa biết có {allergens} hay không. Chụp bảng thành phần nhé.",
                           "Lo encontramos por el nombre: no sabemos si tiene {allergens}. Toma una foto de los ingredientes."),
    "reason.less_sure.partial": ("Less sure: partial label", "Chưa chắc lắm: nhãn chưa đọc hết", "Con dudas: etiqueta incompleta"),
    "reason.less_sure.missing": ("Less sure: no ingredient list", "Chưa chắc lắm: không có danh sách thành phần",
                                 "Con dudas: sin lista de ingredientes"),
    "reason.less_sure.data": ("Less sure: limited product data", "Chưa chắc lắm: dữ liệu sản phẩm còn ít", "Con dudas: pocos datos del producto"),
    "reason.check_label": ("From a database. Check the label.", "Thông tin lấy từ cơ sở dữ liệu. Nhớ xem lại nhãn nhé.",
                           "Viene de una base de datos. Revisa la etiqueta."),
    "reason.matched_by_name": ("Matched by name. Snap the ingredients to confirm.",
                               "Tìm theo tên sản phẩm. Chụp bảng thành phần để chắc hơn.",
                               "Lo encontramos por el nombre. Toma una foto de los ingredientes para confirmar."),
    "reason.supplement_scope": ("Pear doesn't check supplement doses or drug interactions.",
                                "Pear không kiểm tra liều dùng hay tương tác thuốc của thực phẩm bổ sung.",
                                "Pear no revisa dosis de suplementos ni interacciones con medicamentos."),
    # ---------------------------------------------------------------- reasons: diets / certs
    "reason.diet.not": ("{label}: {items}", "{label}: {items}", "{label}: {items}"),
    "reason.diet.maybe": ("{label}: {items}", "{label}: {items}", "{label}: {items}"),
    "reason.diet.check": ("Check: label says {diet} but lists {items}", "Kiểm tra lại: nhãn ghi {diet} nhưng có {items}",
                          "Revisa: la etiqueta dice {diet} pero lleva {items}"),
    "reason.cert.settled": ("{items} assumed {assumed}, label says {diet}", "{items}: coi như {assumed} vì nhãn ghi {diet}",
                            "{items}: se asume {assumed}, la etiqueta dice {diet}"),
    "reason.cert.ok": ("Certified {diet}", "Có chứng nhận {diet}", "Certificado {diet}"),
    # ---------------------------------------------------------------- reasons: graded signals
    "reason.signal.sodium.some": ("Some sodium ({dv}% DV)", "Muối vừa phải ({dv}% nhu cầu mỗi ngày)", "Algo de sodio ({dv}% VD)"),
    "reason.signal.sodium.high": ("High sodium ({dv}% DV)", "Nhiều muối ({dv}% nhu cầu mỗi ngày)", "Mucho sodio ({dv}% VD)"),
    "reason.signal.sodium.very_high": ("Very high sodium ({dv}% DV)", "Rất nhiều muối ({dv}% nhu cầu mỗi ngày)", "Muchísimo sodio ({dv}% VD)"),
    "reason.signal.added_sugars.some": ("Some added sugar ({dv}% DV)", "Đường vừa phải ({dv}% nhu cầu mỗi ngày)", "Algo de azúcar añadido ({dv}% VD)"),
    "reason.signal.added_sugars.high": ("High added sugar ({dv}% DV)", "Nhiều đường ({dv}% nhu cầu mỗi ngày)", "Mucho azúcar añadido ({dv}% VD)"),
    "reason.signal.added_sugars.very_high": ("Very high added sugar ({dv}% DV)", "Rất nhiều đường ({dv}% nhu cầu mỗi ngày)",
                                             "Muchísimo azúcar añadido ({dv}% VD)"),
    "reason.signal.saturated_fat.some": ("Some saturated fat ({dv}% DV)", "Chất béo bão hòa vừa phải ({dv}% nhu cầu mỗi ngày)",
                                         "Algo de grasa saturada ({dv}% VD)"),
    "reason.signal.saturated_fat.high": ("High saturated fat ({dv}% DV)", "Nhiều chất béo bão hòa ({dv}% nhu cầu mỗi ngày)",
                                         "Mucha grasa saturada ({dv}% VD)"),
    "reason.signal.saturated_fat.very_high": ("Very high saturated fat ({dv}% DV)", "Rất nhiều chất béo bão hòa ({dv}% nhu cầu mỗi ngày)",
                                              "Muchísima grasa saturada ({dv}% VD)"),
    "reason.signal.fast_sugars": ("Sugar near the top of the list ({items})", "Đường nằm trong mấy thành phần đầu ({items})",
                                  "Azúcar entre los primeros ingredientes ({items})"),
    "reason.signal.phosphates": ("Phosphate additives ({items})", "Có phụ gia phốt phát ({items})", "Aditivos de fosfato ({items})"),
    "reason.signal.potassium_additives": ("Potassium additives ({items})", "Có phụ gia kali ({items})", "Aditivos de potasio ({items})"),
    "reason.signal.dyes": ("Synthetic dyes ({items})", "Có phẩm màu tổng hợp ({items})", "Colorantes artificiales ({items})"),
    "reason.signal.caffeine": ("Caffeine ({items})", "Có caffeine ({items})", "Contiene cafeína ({items})"),
    "reason.signal.palm_oil": ("Palm oil ({items})", "Có dầu cọ ({items})", "Aceite de palma ({items})"),
    "reason.signal.fragrance": ("Fragrance ({items})", "Có hương liệu ({items})", "Contiene fragancia ({items})"),
    "reason.signal.methylisothiazolinone": ("Skin allergen: {items}", "Chất dễ gây kích ứng da: {items}", "Alérgeno para la piel: {items}"),
    "reason.signal.formaldehyde_releasers": ("Formaldehyde releaser: {items}", "Chất giải phóng formaldehyde: {items}",
                                             "Liberador de formaldehído: {items}"),
    "reason.signal.acne_actives": ("Harsh on sensitive or eczema skin: {items}", "Dễ kích ứng da nhạy cảm hoặc da chàm: {items}",
                                   "Irrita la piel sensible o con eccema: {items}"),
    "reason.signal.sodium_lauryl_sulfate": ("Harsh cleanser: {items}", "Chất tẩy rửa mạnh: {items}", "Limpiador fuerte: {items}"),
    "reason.signal.retinoids": ("Retinoid ({items}). Ask your doctor about it during pregnancy.",
                                "Có retinoid ({items}). Nếu đang mang thai, hãy hỏi bác sĩ.",
                                "Retinoide ({items}). Si estás embarazada, consúltalo con tu médico."),
    "reason.signal.rinse_off": ("{text} (rinse-off, counts half)", "{text} (sản phẩm rửa trôi, tính một nửa)",
                                "{text} (se enjuaga, cuenta la mitad)"),
    # ---------------------------------------------------------------- reasons: pets
    "reason.pet": ("Toxic for {species}: {items}", "Độc với {species}: {items}", "Tóxico para {species}: {items}"),
    "species.dog": ("dogs", "chó", "perros"),
    "species.cat": ("cats", "mèo", "gatos"),
    "species.pet": ("pets", "thú cưng", "mascotas"),
    # ---------------------------------------------------------------- short phrases (headline / ask)
    "short.allergen.contains": ("contains {allergens}", "có {allergens}", "contiene {allergens}"),
    "short.allergen.possible": ("possible {allergens}", "có thể có {allergens}", "posible {allergens}"),
    "short.allergen.may_contain": ("may contain {allergens}", "có thể chứa {allergens}", "puede contener {allergens}"),
    "short.pet": ("toxic for {species}", "độc với {species}", "tóxico para {species}"),
    "short.signal.sodium": ("high sodium", "nhiều muối", "mucho sodio"),
    "short.signal.added_sugars": ("added sugar", "nhiều đường", "azúcar añadido"),
    "short.signal.fast_sugars": ("sugar", "nhiều đường", "azúcar"),
    "short.signal.saturated_fat": ("saturated fat", "nhiều chất béo bão hòa", "grasa saturada"),
    "short.signal.phosphates": ("phosphate additives", "phụ gia phốt phát", "aditivos de fosfato"),
    "short.signal.potassium_additives": ("potassium additives", "phụ gia kali", "aditivos de potasio"),
    "short.signal.dyes": ("synthetic dyes", "phẩm màu tổng hợp", "colorantes artificiales"),
    "short.signal.caffeine": ("caffeine", "caffeine", "cafeína"),
    "short.signal.palm_oil": ("palm oil", "dầu cọ", "aceite de palma"),
    "short.signal.fragrance": ("fragrance", "hương liệu", "fragancia"),
    "short.signal.methylisothiazolinone": ("skin allergen", "chất dễ gây kích ứng da", "alérgeno para la piel"),
    "short.signal.formaldehyde_releasers": ("skin allergen", "chất dễ gây kích ứng da", "alérgeno para la piel"),
    "short.signal.acne_actives": ("harsh acne active", "hoạt chất trị mụn mạnh", "activo antiacné fuerte"),
    "short.signal.sodium_lauryl_sulfate": ("harsh cleanser", "chất tẩy rửa mạnh", "limpiador fuerte"),
    "short.signal.retinoids": ("retinoid, ask your doctor", "có retinoid, hỏi bác sĩ", "retinoide, consulta a tu médico"),
    "dv.label": ("DV", "nhu cầu mỗi ngày", "VD"),
    "verb.food": ("have", "ăn", "comer"),
    "verb.other": ("use", "dùng", "usar"),
    # ---------------------------------------------------------------- headline (plan 8.6)
    "headline.all_ripe": ("Ripe for everyone. Go for it.", "Hợp với cả nhà. Cứ chọn nhé!", "Bien para todos. ¡Adelante!"),
    "headline.none": ("Add your family to see who this fits.", "Thêm người nhà để xem sản phẩm hợp với ai.",
                      "Agrega a tu familia para ver a quién le va bien."),
    "headline.allergy": ("Not for {names}: {reason}.", "Không dùng cho {names}: {reason}.", "No es para {names}: {reason}."),
    "headline.allergy_many": ("Not for {names}.", "Không dùng cho {names}.", "No es para {names}."),
    "headline.unsure": ("Couldn't confirm for {names}.", "Chưa xác nhận được cho {names}.", "No pudimos confirmar para {names}."),
    "headline.retake": ("Retake the ingredients?", "Chụp lại bảng thành phần nhé?", "¿Tomas otra foto de los ingredientes?"),
    "headline.snap": ("Snap the ingredients?", "Chụp bảng thành phần nhé?", "¿Le tomas una foto a los ingredientes?"),
    "headline.diet": ("{label} for {names}: {items}.", "{names}: {label_lower} ({items}).",
                      "Para {names}: {label_lower} ({items})."),
    "headline.diet_noitems": ("{label} for {names}.", "{names}: {label_lower}.", "Para {names}: {label_lower}."),
    "headline.diet_check": ("Check for {names}: label says {diet} but lists {items}.",
                            "Cần kiểm tra cho {names}: nhãn ghi {diet} nhưng có {items}.",
                            "Revisa para {names}: la etiqueta dice {diet} pero lleva {items}."),
    "headline.diet_many": ("Diet check for {names}.", "Cần xem có hợp chế độ ăn của {names} không.", "Revisa si va con la dieta de {names}."),
    "headline.skip": ("Skip it for {names}: {reason}.", "{names} nên tránh: {reason}.", "Mejor evitarlo para {names}: {reason}."),
    "headline.skip_many": ("Skip it for {names}.", "{names} nên tránh.", "Mejor evitarlo para {names}."),
    "headline.easy": ("Easy does it for {names}: {reason}.", "{names} nên {verb} ít thôi: {reason}.",
                      "Con moderación para {names}: {reason}."),
    "headline.easy_many": ("Easy does it for {names}.", "{names} nên {verb} ít thôi.", "Con moderación para {names}."),
    "headline.fine": ("Fine for {names}.", "Ổn với {names}.", "Bien para {names}."),
    # ---------------------------------------------------------------- spoken answers (/v1/ask)
    "ask.ripe": ("Looks fine for {name}. No problems found.", "{name} {verb} được nhé, không thấy vấn đề gì.",
                 "Le va bien a {name}. No encontramos problemas."),
    "ask.ripe_less_sure": ("Nothing flagged for {name}, but Pear couldn't read the full ingredients. Snap the label?",
                           "Chưa thấy vấn đề gì cho {name}, nhưng Pear chưa đọc được hết thành phần. Chụp nhãn nhé?",
                           "No vimos problemas para {name}, pero Pear no pudo leer todos los ingredientes. ¿Le tomas una foto a la etiqueta?"),
    "ask.ripe_note": ("Nothing flagged for {name}. Note: {note}", "Chưa thấy vấn đề gì cho {name}. Lưu ý: {note}",
                      "No vimos problemas para {name}. Ojo: {note}"),
    "ask.easy": ("Easy does it for {name}: {reason}.", "{name} nên {verb} ít thôi: {reason}.", "{name}, con moderación: {reason}."),
    "ask.skip": ("Better skip it for {name}: {reason}.", "{name} nên tránh: {reason}.", "Mejor que {name} lo evite: {reason}."),
    "ask.allergy": ("Not for {name}: {reason}.", "{name} không {verb} được: {reason}.", "No es para {name}: {reason}."),
    "ask.diet": ("For {name}: {reason}.", "{name}: {reason}.", "Para {name}: {reason}."),
    "ask.unsure": ("I couldn't confirm it for {name}. Snap the ingredients?",
                   "Pear chưa xác nhận được cho {name}. Chụp bảng thành phần nhé?",
                   "No pude confirmarlo para {name}. ¿Le tomas una foto a los ingredientes?"),
    "ask.scan_needed": ("I'm not sure which product you mean. Scan it and I'll check it for everyone.",
                        "Pear chưa rõ là sản phẩm nào. Quét mã vạch để Pear kiểm tra cho cả nhà nhé.",
                        "No sé bien qué producto es. Escanéalo y lo reviso para todos."),
    "ask.picks": ("Try {names}. They fit everyone.", "Thử {names} nhé, hợp với cả nhà.", "Prueba {names}. Le van bien a todos."),
    "ask.picks_some": ("Better picks for everyone: {names}.", "Lựa chọn hợp với cả nhà: {names}.", "Mejores opciones para todos: {names}."),
    # ---------------------------------------------------------------- errors (other lanes call t() with these keys)
    "error.front_only_no_match": ("Snap the ingredients.", "Chụp bảng thành phần nhé.", "Toma una foto de los ingredientes."),
    "error.too_blurry": ("Too blurry. Retake?", "Ảnh bị mờ. Chụp lại nhé?", "Está borrosa. ¿Tomas otra?"),
    "error.barcode_invalid": ("That barcode doesn't look right. Try scanning again?", "Mã vạch này có vẻ không đúng. Quét lại nhé?",
                              "Ese código no parece correcto. ¿Lo escaneas otra vez?"),
    "error.barcode_not_found": ("We couldn't find this barcode. Snap the ingredients?",
                                "Chưa tìm thấy mã vạch này. Chụp bảng thành phần nhé?",
                                "No encontramos este código. ¿Le tomas una foto a los ingredientes?"),
    "error.rate_limited": ("Pear is busy. Try again in a moment.", "Pear đang bận. Thử lại sau giây lát nhé.",
                           "Pear está ocupado. Inténtalo de nuevo en un momento."),
    "ask.picks_none": ("I couldn't find picks that fit everyone yet. Try scanning another one.",
                       "Pear chưa tìm được lựa chọn hợp với cả nhà. Thử quét sản phẩm khác nhé.",
                       "Todavía no encontré opciones para todos. Prueba a escanear otro."),
    "error.not_a_label": ("That doesn't look like a label. Snap the ingredients?",
                          "Ảnh này không giống nhãn sản phẩm. Chụp bảng thành phần nhé?",
                          "Eso no parece una etiqueta. ¿Le tomas una foto a los ingredientes?"),
    "error.product_missing": ("We couldn't find that product. Scan it again?", "Không tìm thấy sản phẩm này. Quét lại nhé?",
                              "No encontramos ese producto. ¿Lo escaneas otra vez?"),
    "error.explain_off": ("Explanations are off right now.", "Phần giải thích đang tạm tắt.",
                          "Las explicaciones están desactivadas por ahora."),
    # ---------------------------------------------------------------- explain (pipeline/explain.py fallback line)
    "explain.no_reasons": ("Nothing on this label stands out for {name}.", "Nhãn này không có gì cần lưu ý cho {name}.",
                           "No encontramos nada que señalar para {name}."),
}

TEMPLATES: dict[str, dict[str, str]] = {k: dict(zip(LANGS, v, strict=True)) for k, v in _T.items()}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _lang(language: str | None) -> str:
    lang = base_language(language)
    return lang if lang in LANGS else "en"


def t(key: str, language: str, default: str | None = None, **kw: Any) -> str:
    """Localized template string, formatted with **kw.

    Lookup order: key in `language` -> key in English -> `default` -> the key itself.
    Never raises (a bad format arg returns the unformatted string). Callers outside the
    brain lane pass an English `default` so new keys work before translations exist.
    """
    entry = TEMPLATES.get(key)
    s: str | None = None
    if entry:
        s = entry.get(_lang(language)) or entry.get("en")
    if s is None:
        s = default if default is not None else key
    if not kw:
        return s
    try:
        return s.format_map(_Safe(kw))
    except Exception:  # noqa: BLE001 - never break a response over a template
        return s


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# ---------------------------------------------------------------------- short reasons

_DV_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
FOOD_SIGNALS = {"sodium", "added_sugars", "fast_sugars", "saturated_fat", "phosphates", "potassium_additives", "caffeine"}
COSMETIC_SIGNALS = {"fragrance", "methylisothiazolinone", "formaldehyde_releasers", "sodium_lauryl_sulfate", "retinoids"}


def _code_parts(code: str) -> list[str]:
    return code.split(".")


def species_word(species: str | None, language: str) -> str:
    s = (species or "").strip().lower()
    return t(f"species.{s}" if s in ("dog", "cat") else "species.pet", language)


def allergy_phrase(reasons: list, language: str) -> str:
    """'contains sesame, may contain peanuts' from a verdict's hard allergen reasons."""
    from rules.kb import get_kb
    from rules.text import join_names

    kb = get_kb()
    groups: dict[str, list[str]] = {"contains": [], "possible": [], "may_contain": []}
    for r in reasons:
        parts = _code_parts(r.code)
        if parts[0] != "allergen" or r.severity != "hard" or len(parts) < 2:
            continue
        kind = parts[2] if len(parts) > 2 else "contains"
        if kind not in groups:
            continue
        aid = parts[1]
        label = kb.label_many(aid, language) if kind == "may_contain" else kb.label(aid, language)
        if label not in groups[kind]:
            groups[kind].append(label)
    out = [t(f"short.allergen.{k}", language, allergens=join_names(v, language)) for k, v in groups.items() if v]
    return ", ".join(out)


def short_reason(verdict: Verdict, member: Member | None, language: str, *, detail: bool = False) -> str:
    """Short phrase for a verdict's state in `language` ("contains sesame", "high sodium (38% DV)")."""
    reasons = verdict.reasons
    if verdict.state == "ALLERGY":
        pets = [r for r in reasons if r.code.startswith("pet.") and r.severity == "hard"]
        if pets:
            items = ", ".join(distinct_spans(pets[0].matched))
            phrase = t("short.pet", language, species=species_word(member.species if member else None, language))
            return f"{phrase} ({items})" if items and detail else phrase
        return allergy_phrase(reasons, language)
    if verdict.state == "DIET":
        r = next((r for r in reasons if r.code.startswith("diet.")), None)
        return diet_phrase(r, language) if r else ""
    if verdict.state in ("SKIP", "EASY"):
        r = next((r for r in reasons if r.code.startswith("signal.") and r.severity != "note"), None)
        if r is None:
            return ""
        sid = r.code.split(".", 1)[1]
        phrase = t(f"short.signal.{sid}", language, default=sid.replace("_", " "))
        if detail:
            m = _DV_RE.search(r.text)
            if m and sid in ("sodium", "added_sugars", "saturated_fat"):
                phrase += f" ({m.group(1)}% {t('dv.label', language)})"
        return phrase
    return ""


def diet_phrase(r, language: str) -> str:
    """'not vegan (gelatin)' / 'maybe not vegan (natural flavors)' / 'check: label says vegan...'."""
    from rules.kb import get_kb

    kb = get_kb()
    parts = _code_parts(r.code)
    did = parts[1] if len(parts) > 1 else ""
    kind = parts[2] if len(parts) > 2 else "not"
    diet = kb.diets.get(did, {})
    items = ", ".join(distinct_spans(r.matched))
    if kind == "check":
        return t("reason.diet.check", language, diet=(diet.get("label") or {}).get(_lang(language), did), items=items).lower()
    lab = (diet.get("maybe_label" if kind == "maybe" else "not_label") or {}).get(_lang(language)) or did
    lab = lab[:1].lower() + lab[1:]
    return f"{lab} ({items})" if items else lab


def _verb(verdicts: list[Verdict], category: str | None, language: str) -> str:
    if category:
        return t("verb.food" if category == "food" else "verb.other", language)
    codes = {r.code.split(".", 1)[1] for v in verdicts for r in v.reasons if r.code.startswith("signal.")}
    if codes & COSMETIC_SIGNALS:
        return t("verb.other", language)
    if codes & FOOD_SIGNALS:
        return t("verb.food", language)
    return t("verb.other", language)


# ---------------------------------------------------------------------- answers


def ask_answer(ev: Evaluation, household: Household, member_ids: list[str] | None,
               language: str, product_name: str | None) -> str:
    """Spoken answer for /v1/ask intent=check, in the asker's language. Templated, no Gemini."""
    lang = _lang(language)
    members = {m.id: m for m in household.members}
    wanted = set(member_ids or [])
    selected = [v for v in ev.verdicts if v.member_id in wanted] or list(ev.verdicts)
    if not selected:
        return t("headline.none", lang)
    if len(selected) == 1:
        v = selected[0]
        m = members.get(v.member_id)
        name = m.name if m else v.member_id
        verb = _verb(selected, None, lang)
        key = {"RIPE": "ask.ripe", "EASY": "ask.easy", "SKIP": "ask.skip", "ALLERGY": "ask.allergy",
               "DIET": "ask.diet", "UNSURE": "ask.unsure"}[v.state]
        if v.state == "RIPE":
            codes = [r.code for r in v.reasons]
            if any(c.startswith("confidence.less_sure") for c in codes):
                key = "ask.ripe_less_sure"  # never "No problems found" on a partial / missing list
            else:
                note = next((r.text for r in v.reasons if r.code.startswith("med.") or r.code.endswith(".trace_note")), None)
                if note:
                    return cap(t("ask.ripe_note", lang, name=name, note=note.rstrip(".") + "."))
        return cap(t(key, lang, name=name, verb=verb, reason=short_reason(v, m, lang, detail=True)))
    from rules.headline import build_headline

    sel_members = [members[v.member_id] for v in selected if v.member_id in members]
    return build_headline(selected, sel_members, lang, category=None, source=None).text


def scan_needed_answer(language: str) -> str:
    return t("ask.scan_needed", language)


def picks_answer(names: list[str], language: str) -> str:
    from rules.text import join_names

    names = [n for n in names if n]
    if not names:
        return t("ask.picks_none", language)
    return t("ask.picks", language, names=join_names(names[:3], _lang(language)))
