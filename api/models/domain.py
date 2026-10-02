"""Internal models: the seam between the data pipeline (L3) and the brain (L4).

    source adapter (OFF / OBF / FDC / label_reader)
        -> RawProduct            (as printed / as the source gave it, no interpretation)
        -> rules.normalize()     -> NormalizedProduct (public Product + kb hits)
        -> rules.evaluate()      -> Evaluation (verdicts + headline)

Every adapter only has to fill RawProduct. Every consumer of "what is in this product"
reads NormalizedProduct.hits. Nothing else should parse ingredient text.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from models.contracts import (
    Category,
    DataConfidence,
    Headline,
    Nutrition,
    PhotoKind,
    Product,
    ProductType,
    Source,
    Verdict,
)

Legibility = Literal["full", "partial", "unreadable", "missing"]
HitWhere = Literal["ingredients", "contains", "may_contain", "tags", "model", "enrich", "name"]
HitStrength = Literal["contains", "may_contain", "possible"]


class RawProduct(BaseModel):
    """What a source adapter returns. Fill what you have; leave the rest empty."""

    model_config = ConfigDict(extra="ignore")

    key: str  # off:<ean13> | obf:<ean13> | fdc:<gtin> | photo:<12 hex>
    source: Source
    code: str | None = None
    name: str | None = None
    brand: str | None = None
    category: Category = "food"
    product_type: ProductType | None = None
    image_url: str | None = None
    quantity: str | None = None
    label_language: str | None = None
    photo_kind: PhotoKind | None = None
    matched_by: Literal["barcode", "name", "photo", "vector"] | None = None

    # Ingredients exactly as printed, in printed order. One string per top-level ingredient;
    # sub-ingredients may stay inline ("chocolate (sugar, cocoa butter, milk)").
    ingredients: list[str] = Field(default_factory=list)
    # Optional English gloss per ingredient (label_reader fills it for non en/vi/es labels).
    ingredients_en: list[str | None] = Field(default_factory=list)
    # Full original ingredient text if the source has it (OFF ingredients_text).
    ingredients_text: str | None = None
    # "Contains: wheat, sesame." / "May contain peanuts." statements as printed.
    contains_text: str | None = None
    may_contain_text: str | None = None
    # Source taxonomy tags, untouched (OFF: en:sesame-seeds, en:e129, en:vegan, ...).
    allergens_tags: list[str] = Field(default_factory=list)
    traces_tags: list[str] = Field(default_factory=list)
    additives_tags: list[str] = Field(default_factory=list)
    analysis_tags: list[str] = Field(default_factory=list)  # OFF ingredients_analysis_tags
    labels_tags: list[str] = Field(default_factory=list)  # OFF labels_tags (certifications)
    categories_tags: list[str] = Field(default_factory=list)
    category_main: str | None = None  # most specific OFF category tag
    # Certifications seen on a photographed label, free text ("Certified Vegan", "Halal").
    certifications_text: list[str] = Field(default_factory=list)
    # label_reader's kb id guesses, parallel to `ingredients` (L4 merge rule, addendum 10).
    model_kb_ids: list[list[str]] = Field(default_factory=list)

    nutrition: Nutrition | None = None
    legibility: Legibility = "full"  # "missing" = the source has no ingredient list at all
    data_confidence: DataConfidence = "high"
    fetched_at: str | None = None  # ISO 8601


class Hit(BaseModel):
    """One knowledge-base match. Hits are the single source of truth for flags."""

    kb_id: str  # canonical kb id (peanut, sesame, gelatin, fragrance, red_40, ...)
    matched: str  # exact substring as printed (for highlights); tag name for tag hits
    where: HitWhere
    strength: HitStrength = "contains"
    ingredient_index: int | None = None  # index into RawProduct.ingredients, if any
    position: int | None = None  # 0-based position in the printed list (fast sugars rule)


class NormalizedProduct(BaseModel):
    product: Product  # public view, already contract-shaped
    hits: list[Hit] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)  # ingredient strings with no kb hit
    legibility: Legibility = "full"
    certifications: list[str] = Field(default_factory=list)  # normalized cert ids
    analysis_tags: list[str] = Field(default_factory=list)
    category_main: str | None = None
    kb_version: str = ""

    def kb_ids(self) -> set[str]:
        return {h.kb_id for h in self.hits}


class Evaluation(BaseModel):
    verdicts: list[Verdict]
    headline: Headline
    debug: dict[str, Any] | None = None


class StoredProduct(BaseModel):
    """Firestore products/{key} document (and the in-memory store's value)."""

    model_config = ConfigDict(extra="ignore")

    key: str
    source: Source
    category: Category = "food"
    category_main: str | None = None
    kb_ids: list[str] = Field(default_factory=list)  # flat, for picks + debugging
    kb_version: str = ""
    raw: RawProduct
    normalized: NormalizedProduct | None = None
    embedding: list[float] | None = None  # 768-d; stored as a Firestore Vector
    fetched_at: str | None = None
