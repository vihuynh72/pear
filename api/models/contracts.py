"""Public API contracts (PEAR_MASTER_PLAN 7.6 + PEAR_AGENT_ADDENDUM 6).

These Pydantic models ARE the contract. contracts/swift/PearModels.swift mirrors them,
contracts/fixtures/*.json validate against them (tests/test_contracts.py), and
`make openapi` exports contracts/openapi.json from them.

Conventions
- Inputs are lenient: unknown fields are ignored, most fields have defaults, a few common
  aliases are accepted (see validators). The app should still send the canonical form.
- Outputs are strict: every field below is always present (null when empty) so Swift
  Codable decoding never trips on a missing key.
- Fields marked "additive" are not in 7.6 but were added for integration/debugging.
  They are optional, so a client that ignores them still matches the contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------------------
# Enumerations (kept as Literal so OpenAPI/Swift export is trivial)
# --------------------------------------------------------------------------------------

State = Literal["RIPE", "EASY", "SKIP", "ALLERGY", "DIET", "UNSURE"]
Severity = Literal["hard", "soft", "note"]
Source = Literal["open_food_facts", "open_beauty_facts", "usda_fdc", "photo"]
PhotoKind = Literal["ingredients", "front_only", "other"]
DataConfidence = Literal["high", "medium", "low"]
Category = Literal["food", "cosmetic", "supplement", "other"]
ProductType = Literal["leave_on", "rinse_off"]
AllergyLevel = Literal["strict", "sensitive"]
AgeGroup = Literal["baby", "kid", "teen", "adult", "senior"]
MemberType = Literal["person", "pet"]
Intent = Literal["check", "alternatives", "unclear"]
AskNext = Literal["scan_needed", "result"]
# Contract codes are the first five; bad_request/unauthorized/unavailable are additive.
ErrorCode = Literal[
    "not_found", "unreadable", "too_blurry", "rate_limited", "internal",
    "bad_request", "unauthorized", "unavailable",
]
ErrorNext = Literal["take_photo", "retake", "retry", "sign_in"]

SUPPORTED_LANGUAGES = ("en", "vi", "es")


def base_language(tag: str | None) -> str:
    """'vi-VN' -> 'vi', 'ES' -> 'es', None/'' -> 'en'."""
    if not tag:
        return "en"
    return tag.replace("_", "-").split("-")[0].strip().lower() or "en"


class _In(BaseModel):
    """Lenient input model: ignore unknown keys so app/backend can evolve independently."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Household (input on every request)
# --------------------------------------------------------------------------------------


class Allergy(_In):
    id: str
    level: AllergyLevel = "strict"

    @field_validator("level", mode="before")
    @classmethod
    def _level(cls, v: Any) -> Any:
        return (v or "strict").lower() if isinstance(v, str) or v is None else v


class Member(_In):
    id: str
    type: MemberType = "person"
    species: str | None = None  # "dog" | "cat" when type == "pet"
    name: str
    age_group: AgeGroup = "adult"
    languages: list[str] = Field(default_factory=lambda: ["en"])
    allergies: list[Allergy] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    pregnant: bool = False
    skin: list[str] = Field(default_factory=list)
    diets: list[str] = Field(default_factory=list)
    less_of: list[str] = Field(default_factory=list)
    # None = use the age-group default (on for baby and kid, off otherwise; plan section 3).
    avoid_dyes: bool | None = None
    medications: list[str] = Field(default_factory=list)

    @field_validator("age_group", mode="before")
    @classmethod
    def _age(cls, v: Any) -> Any:
        if v is None:
            return "adult"
        if isinstance(v, str):
            v = v.strip().lower()
            return {"65+": "senior", "elder": "senior", "elderly": "senior", "child": "kid",
                    "infant": "baby", "toddler": "baby"}.get(v, v)
        return v

    @field_validator("languages", mode="before")
    @classmethod
    def _langs(cls, v: Any) -> Any:
        if not v:
            return ["en"]
        return [base_language(x) for x in v]

    @field_validator("allergies", mode="before")
    @classmethod
    def _allergies(cls, v: Any) -> Any:
        # Accept ["peanut", ...] as shorthand for [{"id": "peanut", "level": "strict"}].
        if isinstance(v, list):
            return [{"id": x} if isinstance(x, str) else x for x in v]
        return v

    @property
    def language(self) -> str:
        """Primary language, falling back to English for template text."""
        lang = self.languages[0] if self.languages else "en"
        return lang if lang in SUPPORTED_LANGUAGES else "en"

    @property
    def dyes_on(self) -> bool:
        if self.avoid_dyes is not None:
            return self.avoid_dyes or "artificial_dyes" in self.less_of
        return self.age_group in ("baby", "kid") or "artificial_dyes" in self.less_of


class Household(_In):
    household_id: str = "hh_guest"
    owner_language: str = "en"
    member_limit: int = 5
    members: list[Member] = Field(default_factory=list)

    @field_validator("owner_language", mode="before")
    @classmethod
    def _owner_lang(cls, v: Any) -> Any:
        return base_language(v)

    @property
    def language(self) -> str:
        return self.owner_language if self.owner_language in SUPPORTED_LANGUAGES else "en"

    def select(self, member_ids: list[str] | None) -> list[Member]:
        """Members in family order, filtered by the "Who's this for?" toggle (None = all)."""
        if not member_ids:
            return list(self.members)
        wanted = set(member_ids)
        picked = [m for m in self.members if m.id in wanted]
        return picked or list(self.members)


# --------------------------------------------------------------------------------------
# Product + ScanResult (output)
# --------------------------------------------------------------------------------------


class Nutrition(_Out):
    """Per-serving values when the source has them; `basis` says which. Additive."""

    basis: Literal["serving", "100g"] = "serving"
    sodium_mg: float | None = None
    sodium_dv: float | None = None  # percent of Daily Value, 0-100+
    added_sugars_g: float | None = None
    added_sugars_dv: float | None = None
    sugars_g: float | None = None  # total sugars (fallback when added sugars missing)
    sat_fat_g: float | None = None
    sat_fat_dv: float | None = None
    caffeine_mg: float | None = None


class Product(_Out):
    key: str
    name: str | None = None
    brand: str | None = None
    category: Category = "food"
    product_type: ProductType | None = None
    image_url: str | None = None
    source: Source
    photo_kind: PhotoKind | None = None
    data_confidence: DataConfidence = "high"
    less_sure: bool = False
    ask_for_ingredients: bool = False
    ingredients: list[str] = Field(default_factory=list)
    contains: list[str] = Field(default_factory=list)  # kb allergen ids
    may_contain: list[str] = Field(default_factory=list)  # kb allergen ids
    certifications: list[str] = Field(default_factory=list)  # vegan, halal, kosher, ...
    label_language: str | None = None
    # --- additive ---
    code: str | None = None  # barcode as scanned/canonicalized
    quantity: str | None = None  # "85 g"
    nutrition: Nutrition | None = None
    matched_by: Literal["barcode", "name", "photo", "vector"] | None = None


class Reason(_Out):
    code: str  # e.g. allergen.sesame, diet.vegan.maybe, signal.sodium (see kb/README.md)
    text: str  # in the member's language
    language: str
    severity: Severity
    matched: list[str] = Field(default_factory=list)  # exact words as printed, for highlights
    source: str | None = None  # kb source id (kb/sources.json)


class Verdict(_Out):
    member_id: str
    state: State
    label: str  # English chip word: Ripe, Easy does it, Skip it, Allergy, May contain, ...
    reasons: list[Reason] = Field(default_factory=list)
    score: float | None = None  # hidden: always null unless ?debug=1


class Headline(_Out):
    text: str
    language: str


class ScanResult(_Out):
    product: Product
    verdicts: list[Verdict]
    headline: Headline
    timings_ms: dict[str, int] = Field(default_factory=dict)
    # --- additive ---
    request_id: str | None = None
    debug: dict[str, Any] | None = None  # only with ?debug=1 or X-Pear-Debug: 1


# --------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------


class BarcodeRequest(_In):
    code: str
    household: Household
    member_ids: list[str] | None = None  # additive: "Who's this for?" toggle


class AskRequest(_In):
    """POST /v1/ask JSON body (addendum 6). Multipart audio form is the fallback."""

    text: str
    language: str | None = None  # dictation locale hint, e.g. "vi"
    last_product_key: str | None = None
    household: Household
    asker_id: str | None = None  # additive: which member is asking (defaults to the first)

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, v: Any) -> Any:
        return base_language(v) if v else None


class ProductKeyRequest(_In):
    """Body for /v1/explain and /v1/picks."""

    product_key: str
    household: Household
    member_ids: list[str] | None = None


class PricesRequest(_In):
    product_keys: list[str]
    country: str = "US"


class ProfileParseRequest(_In):
    text: str
    language: str | None = None


# --------------------------------------------------------------------------------------
# Other responses
# --------------------------------------------------------------------------------------


class Answer(_Out):
    text: str
    language: str


class PickVerdict(_Out):
    member_id: str
    state: State
    label: str


class PickProduct(_Out):
    key: str
    name: str | None = None
    brand: str | None = None
    image_url: str | None = None
    category: Category = "food"


class PickItem(_Out):
    product: PickProduct
    verdicts: list[PickVerdict]


class PicksResponse(_Out):
    items: list[PickItem]
    prefetched: bool
    timings_ms: dict[str, int] = Field(default_factory=dict)
    request_id: str | None = None  # additive


class AskResponse(_Out):
    transcript: str
    language: str
    intent: Intent
    product_query: str | None = None
    member_ids: list[str] = Field(default_factory=list)
    next: AskNext
    answer: Answer
    scan_result: ScanResult | None = None
    # --- additive ---
    picks: PicksResponse | None = None  # filled when intent == "alternatives"
    timings_ms: dict[str, int] = Field(default_factory=dict)
    request_id: str | None = None
    debug: dict[str, Any] | None = None


class Price(_Out):
    amount: float
    currency: str = "USD"
    retailer: str
    size: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    checked_at: str  # ISO 8601 UTC
    source: Literal["google_search"] = "google_search"
    search_entry_html: str | None = None  # additive: Google Search Suggestions (must be shown)


class PricesResponse(_Out):
    prices: dict[str, Price | None]
    timings_ms: dict[str, int] = Field(default_factory=dict)
    request_id: str | None = None


class ExplainEvent(_Out):
    """One SSE `data:` line from /v1/explain."""

    member_id: str
    language: str
    text: str


class ProfileChip(_Out):
    group: Literal["allergies", "conditions", "skin", "diets", "less_of", "medications"]
    id: str
    level: AllergyLevel | None = None  # only for allergies
    label: str | None = None  # additive: display label in the request language
    note: str | None = None  # additive: "carmine: insect-based red color, matters for vegans"


class ProfileParseResponse(_Out):
    chips: list[ProfileChip]
    unmatched: list[str]
    timings_ms: dict[str, int] = Field(default_factory=dict)
    request_id: str | None = None


class KbSuggestion(_Out):
    group: str
    id: str
    label: str  # in the requested language
    labels: dict[str, str] = Field(default_factory=dict)  # en / vi / es
    score: float = 0.0


class KbSearchResponse(_Out):
    query: str
    suggestions: list[KbSuggestion]


class ErrorBody(_Out):
    code: ErrorCode
    message: str
    next: ErrorNext | None = None
    request_id: str | None = None  # additive


class ErrorResponse(_Out):
    error: ErrorBody


class HealthResponse(_Out):
    ok: bool
    service: str
    version: str
    env: str
    region: str | None
    store: str  # firestore | memory
    gemini: str  # ok | unavailable: <why>
    auth: str  # off | optional | required
    ask_engine: str  # adk | direct
    kb_version: str | None
    uptime_s: int
    features: dict[str, bool]
    checks: dict[str, Any] | None = None  # only with ?deep=1
