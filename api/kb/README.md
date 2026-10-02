# Pear knowledge base + rules contract (L4)

This file is the shared spec for `api/kb/*.json`, `api/rules/*`, and `api/tests/cases/*.json`.
Master plan sections 8.1–8.7 and addendum 10 are the source of truth; this file pins down names.

## 1. Profile ids (what the app sends in `household.members[]`)

| Field | Canonical ids (aliases accepted by the engine) |
|---|---|
| `allergies[].id` | peanut, tree_nut (tree_nuts), milk (dairy), egg (eggs), wheat, soy (soya), fish, shellfish (crustacean, crustaceans), sesame, gluten (celiac), lactose, mustard, sulfite (sulfites, sulphites), celery, lupin, corn (maize), kiwi |
| `allergies[].level` | strict ("Strict" in the app), sensitive ("Ingredients only" in the app) |
| `conditions[]` | high_blood_pressure (hypertension), diabetes, prediabetes, high_cholesterol, kidney_disease (ckd), kidney_potassium_limit, heart_disease, pregnancy |
| `pregnant` | bool; true behaves exactly like condition `pregnancy` |
| `skin[]` | eczema, psoriasis, sensitive_skin, acne_prone, rosacea, fragrance_sensitivity |
| `diets[]` | vegetarian, vegan, pescatarian, halal, kosher, no_pork, no_beef, no_alcohol, jain, buddhist_vegetarian, gluten_free |
| `less_of[]` | sugar, sodium, saturated_fat, caffeine, artificial_dyes, palm_oil |
| `avoid_dyes` | bool or null (null = on for baby/kid, off otherwise; `less_of: artificial_dyes` also turns it on) |
| `type` / `species` | person / pet; species dog, cat |
| `medications[]` | free text (P2 pharmacist notes only, never changes state) |
| `age_group` | baby, kid, teen, adult, senior ("65+" accepted) |

## 2. KB files (all JSON, UTF-8, NFC). Every entry has `id` and `source` (an id in `sources.json`).

Lexicon entries (anything that can be *matched in text*) share one shape, wherever they live:

```json
{
  "id": "sesame",
  "label": {"en": "sesame", "vi": "mè", "es": "sésamo"},
  "terms": {"en": ["sesame", "sesame seed", "tahini", "benne", "gingelly"],
            "vi": ["mè", "vừng", "dầu mè"],
            "es": ["sésamo", "ajonjolí"],
            "inci": ["sesamum indicum"]},
  "e_numbers": [],
  "off_tags": ["en:sesame-seeds", "en:sesame"],
  "implies": [],
  "possible_terms": {"en": []},
  "source": "fda_major_allergens"
}
```

- `terms`: matched case-insensitively on word boundaries, longest match first, plurals handled by the
  matcher (`s`/`es`), Vietnamese matched with diacritics (NFC). `inci` = cosmetic INCI names.
- `implies`: other kb ids this one always means (`whey` → `milk`; `wheat` → `gluten`; `lard` → `pork`).
- `possible_terms`: ambiguous strings that *might* be this (`lecithin` → soy). Produces `strength: possible`.
- Neutral entries (`"neutral": true`) exist only to block false matches by longest-match:
  `coconut milk`, `cocoa butter`, `shea butter`, `peanut-free`, `buckwheat`, `nutmeg`, `water chestnut`,
  `eggplant`, `cream of tartar`, `gluten-free`, `milk-free`, … They produce no hit.

| File | Contains |
|---|---|
| `allergens.json` | `{"version", "items": [lexicon entry + "aliases": [...]]}` — the 17 allergen ids above |
| `ingredients.json` | lexicon entries for diet-relevant ingredients: gelatin, carmine, honey, beeswax, shellac, lanolin, natural_flavors, mono_diglycerides, ribonucleotides (disodium inosinate / guanylate, E626-E635: diet maybe), enzymes, vitamin_d3, l_cysteine, pork, lard, beef, tallow, meat, poultry, alcohol, root_vegetables, onion, garlic, animal_rennet, … and neutral entries |
| `diets.json` | `{"diets": [{"id": "vegan", "label": {...}, "forbidden": [kb ids], "maybe": [kb ids], "cert_ids": ["vegan"], "source"}]}` |
| `additives.json` | lexicon entries for dyes (red_40, yellow_5, yellow_6, blue_1, blue_2, green_3, red_3, …, each with e_numbers), phosphates, potassium_additives, fast_sugars, caffeine, palm_oil, fragrance, methylisothiazolinone, methylchloroisothiazolinone, formaldehyde_releasers, sodium_lauryl_sulfate, retinoids |
| `conditions.json` | `{"signals": [{"id", "kb_ids" or "nutrient", "weight", "applies": {"conditions": [...], "skin": [...], "less_of": [...], "pregnancy": bool, "dyes": bool}, "source"}]}` — the table in plan 8.4 |
| `skin.json` | skin-specific signal rows (same shape as conditions.json `signals`), or merged into conditions.json — rules/kb.py loads both |
| `meds.json` | P2 pharmacist notes: `{"notes": [{"id", "med_terms": [...], "kb_ids": [...], "text": {en,vi,es}, "source"}]}` |
| `pets.json` | P2 pet toxins as lexicon entries + `species` list |
| `certifications.json` | `{"items": [{"id": "vegan", "terms": {...}, "off_tags": ["en:vegan"]}]}` (vegan, vegetarian, halal, kosher, gluten_free, organic) |
| `sources.json` | `{"sources": [{"id": "fda_major_allergens", "title", "url"}]}` — plan section 21 |

`rules/kb.py` computes `kb.version` = short sha of all kb file bytes. Stored products remember the
version they were normalized with; a mismatch triggers re-normalization from `raw`.

## 3. Reason codes (`verdicts[].reasons[].code`)

| Code | State it drives | Example text (en) |
|---|---|---|
| `allergen.<id>` | ALLERGY (both levels) | Contains sesame (sesame seeds) |
| `allergen.<id>.may_contain` | ALLERGY (strict), label "May contain" | May contain peanuts |
| `allergen.<id>.possible` | ALLERGY (both levels; never in `product.may_contain`, section 5) | Possible soy (lecithin) |
| `allergen.<id>.trace_note` | note ("Ingredients only" level + may contain) | May contain milk (label warning) |
| `confidence.unsure` | UNSURE (strict allergy, partial/missing list, no allergen found) | Couldn't read the whole label |
| `confidence.less_sure` | note | Less sure: partial label |
| `confidence.check_label` | note (strict allergy on database data) | From a database. Check the label. |
| `confidence.matched_by_name` | note | Matched by name. Snap the ingredients to confirm. |
| `diet.<diet>` | DIET "Not vegan" | Not vegan: gelatin |
| `diet.<diet>.maybe` | DIET "Maybe not vegan" | Maybe not vegan: natural flavors |
| `diet.<diet>.check` | DIET "Check" (cert conflicts with ingredients) | Check: label says vegan but lists honey |
| `cert.<diet>` | note ("Certified" / settled maybes) | Natural flavors assumed plant-based, label says vegan |
| `signal.<signal_id>` | graded (EASY/SKIP) or note | High sodium (38% DV) |
| `pet.<id>` | ALLERGY, label "Toxic for dogs"/"Toxic for cats"/"Toxic for pets" | Toxic for dogs: xylitol |
| `med.<id>` | note only | Grapefruit can interact with some statins. Ask your pharmacist. |
| `supplement.scope` | note | Pear doesn't check supplement doses or drug interactions. |

Chip labels (`verdicts[].label`). English only; the app localizes them. This is the COMPLETE list
(`rules.engine.chip_labels()`; tests/test_engine_unit.py fails if this table drifts from the engine):

| `state` | Every possible `label` |
|---|---|
| RIPE | `Ripe` |
| EASY | `Easy does it` |
| SKIP | `Skip it` |
| ALLERGY | `Allergy`, `May contain` (strict allergy, only may-contain hits), `Toxic for dogs`, `Toxic for cats`, `Toxic for pets` (pet with no/other species) |
| DIET | `Not vegan`, `Not vegetarian`, `Not pescatarian`, `Not halal`, `Not kosher`, `Not pork-free`, `Not beef-free`, `Not alcohol-free`, `Not Jain`, `Not Buddhist vegetarian`, `Not gluten-free` |
| DIET | `Maybe not vegan`, `Maybe not vegetarian`, `Maybe not pescatarian`, `Maybe not halal`, `Maybe not kosher`, `Maybe not pork-free`, `Maybe not beef-free`, `Maybe not alcohol-free`, `Maybe not Jain`, `Maybe not Buddhist vegetarian`, `Maybe not gluten-free` |
| DIET | `Check` (certified label conflicts with an ingredient) |
| UNSURE | `Unsure` |

The chip label is always the English `not_label.en` / `maybe_label.en` from diets.json. The vi/es
`not_label` values ("Có thịt heo", "Tiene cerdo", …) are only the prefix of the reason text
(`reasons[].text`, member language), never a chip label. There is no "Has pork" chip.

Reason text rules (enforced in rules/engine.py, 2026-10-02): every reason names the ingredient
("Skin allergen: Methylisothiazolinone", "Synthetic dyes (Red 40)"); the item list is dropped only
when the text already is that word ("Fragrance", not "Fragrance (Fragrance)"). Printed spans are
shown longest-first without overlaps (`rules.text.distinct_spans`): "Contains sesame (sesame seeds)",
"Not vegan: bột tôm", never "sesame seeds, sesame" / "bột tôm, tôm". `reasons[].matched` still lists
every distinct printed span, because the app highlights them in `product.ingredients`.

## 4. Engine behaviour (summary; plan 8.3–8.7 is authoritative)

1. Precedence ALLERGY > UNSURE > DIET > SKIP > EASY > RIPE. Reasons list every hit, sorted hard→soft→note.
2. Allergy levels (decided 2026-10-02, matches the app's "Strict / Ingredients only" toggle; the toggle only
   decides what "may contain" does):
   - `strict` ("Strict"): contains / possible / may_contain → ALLERGY. Partial or missing ingredient list and
     no allergen found → UNSURE (never RIPE).
   - `sensitive` ("Ingredients only"): contains / possible → ALLERGY (same codes as strict, an allergen in the
     ingredients is never softened); may_contain → note `allergen.<id>.trace_note`. Partial/missing list →
     graded normally with the "Less sure" note (plan 8.5.2).
3. Label merge rule (addendum 10): allergen ids = union(kb match, model kb_ids). The model can add allergen
   flags, never remove them. Non-allergen model ids count only for strings the kb didn't match.
4. Scoring 8.4: score = 100 − Σ weight × amount × context × product_type (floor 0). Context = max of
   matching condition (1.0) / less-of goal (0.5). Rinse-off ×0.5. Tiers ≥85 RIPE, 60–84 EASY, <60 SKIP.
   Leniency: SKIP needs one applied penalty with weight 45 or ≥2 "real" issues (penalty ≥ 10); else clamp to EASY.
5. Certifications 8.5: cert + no conflict → `cert.<diet>` note; cert + only maybes → settled, note; cert +
   definite conflict → `diet.<diet>.check`; no cert → `diet.<diet>` / `diet.<diet>.maybe`.
6. Headline 8.6 in `household.owner_language`; reasons in each member's first language (en/vi/es; else en).
   Never the word "safe" (or "an toàn", "seguro"). No medical advice.
7. Gemini never decides a state. `evaluate()` is a pure function: same input, same output.

### Additional codes (L4, appended 2026-10-02; existing codes unchanged)

| Code | State it drives | Example text (en) |
|---|---|---|
| `allergen.<id>.unknown` | note ("Ingredients only" allergy id the kb doesn't know; the literal word, singular or plural, was searched in the ingredients and not found) | Pear can't check for pomegranate yet. Check the label for it. |
| `confidence.unsure` (unknown strict allergy) | UNSURE (strict allergy id the kb doesn't know, literal word not found: never RIPE) | Pear can't check for pomegranate yet. Check the label for it. |
| `condition.<id>.unknown` | note (condition / skin id with no kb rule; a condition that is an allergen alias, e.g. "celiac disease", is checked as a strict allergy) | Pear can't check for gout yet. |
| `confidence.unsure` (matched by name) | UNSURE (strict allergy, product matched by name, allergen not found) | Matched by name, so Pear can't rule out peanut. Snap the ingredients. |
| `cert.<diet>` (no conflict) | note | Certified vegan |

### Matcher passes (red-team fixes, 2026-10-02)

`Lexicon.scan` runs extra passes that can only ADD hits, never remove one:
1. Diacritic-folded vi/es/fr/de terms ("Bột mi", "SUA BOT", "ATUN", "fr:celeri"). Multi-syllable / es matches
   keep the term strength; a one-syllable vi term ("trung", "sua", "me") is `possible`, and only fires when the
   product is Vietnamese. In a well-accented vi label the real unaccented words me/ca/so/bo/mi/can/ngo/cam/lac
   are left alone ("nước, đường, me" = tamarind).
2. Line-break hyphenation joined ("Pea-\nnut butter", "PEA- NUT OIL"); soft hyphens / zero-width chars dropped;
   full-width letters folded; footnote marks (¹ † ®) and glued digits split off ("Milk1", "Hazelnuts13%").
3. Bare INS codes after "(" "," or ":" ("Preservative (220)", "chất nhũ hóa (322, 471)").
A neutral term never spans a line break, and "X free from / of / range" is not an "X-free" claim.
An ingredient header ("Ingredients:", "Thành phần:", "Ingredientes:") ends a may-contain region, and a later
"contain" inside a cross-contact sentence does not flip it back to contains (only a "Contains:" heading does).
With no ingredient list, the product name is scanned for `possible` hits (where = "name").

DIET chip labels always start with "Not " / "Maybe not " or are "Check" (no_pork -> "Not pork-free",
no_beef -> "Not beef-free", no_alcohol -> "Not alcohol-free"); full list in section 3. Pets use the
owner's language.

## 5. Product.contains / Product.may_contain vs "possible" (decided 2026-10-02)

- `product.contains`: allergens printed in the ingredients, a "Contains:" statement, or OFF `allergens_tags`.
- `product.may_contain`: ONLY allergens from a may-contain / traces statement or OFF `traces_tags`, i.e. what
  the label itself warns about. An allergen that is also in `contains` is not repeated here.
- `possible` hits (ambiguous ingredient words) are in NEITHER list. They reach exactly the members who have
  that allergy, as `allergen.<id>.possible` -> ALLERGY ("Possible soy (lecithin)"), at both levels. So the
  product card never says "May contain celery" without a label warning, and nothing is missed for the
  person who has the allergy. Strict/sensitive behaviour is unchanged.

Which ambiguous words stay `possible` (a careful allergist would want the person to check them):

| Word(s) | Possible | Why it stays |
|---|---|---|
| lecithin, E322, vegetable oil (soy), vegetable protein, hoisin | soy | lecithin is usually soy unless the source is named (sunflower/rapeseed lecithin are neutral) |
| flour, cereal, noodles, soy sauce, `en:gluten` tag | wheat | unspecified flour in US/VN labels is wheat flour; soy sauce is brewed with wheat |
| oats, oat flour, colloidal oatmeal | gluten | oats are routinely cross-contaminated with wheat |
| starch, modified/food starch, tinh bột, almidón, dextrose, glucose syrup, maltodextrin | corn | corn is the default starch/sugar source and is not a labeled allergen in the US/VN, so it can hide |
| natural flavor(s)/flavour(s)/flavoring(s), spices, vegetable broth | celery | US labels may hide celery in flavors and spices (celery is not a US major allergen) |
| spices | mustard | same reason as celery |
| coconut, shea | tree nut | FDA lists coconut as a tree nut; most tree-nut allergies tolerate it, the person decides |
| nuts, mixed nuts, các loại hạt, frutos secos | peanut (tree nut = contains) | "nuts" on a label does not say which |
| caramel, margarine, creamer, toffee, kem | milk (+ lactose) | often dairy-based, not always declared as milk |
| seafood, omega-3, DHA, glucosamine, surimi | fish / shellfish | source not named |
| hummus, za'atar, furikake | sesame | sesame is a standard ingredient |
| wine, wine vinegar, dried apricots | sulfite | commonly sulfited |
| lactitol, E966 | lactose (E966 also milk) | made from lactose |

All `possible_terms` live in `allergens.json` (`possible_terms`, `e_numbers_possible`, `off_tags_possible`).

Noise removed (2026-10-02), because the label names a non-corn source: rice/pea/arrowroot/mung bean/sago
starch, tinh bột gạo / nếp / sắn / năng / đậu xanh, almidón de arroz / tapioca / yuca / mandioca / guisante
are neutral; tinh bột khoai mì / củ sắn and almidón de papa / patata are root vegetables (Jain), no corn.
Unnamed "starch" / "tinh bột" / "almidón" stays possible corn, "wheat starch" / "tinh bột mì" is wheat.
