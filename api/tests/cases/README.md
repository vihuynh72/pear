# Rules test cases (`tests/test_rules.py` runs every `*.json` here)

One JSON file per theme (allergens_en.json, allergens_vi_es.json, diets.json, scoring.json, ...).
Each file is `{"cases": [ ... ]}`. A case:

```json
{
  "name": "tahini triggers strict sesame",
  "tags": ["allergen", "p0"],
  "product": {
    "key": "test:tahini", "source": "open_food_facts", "category": "food",
    "ingredients": ["chickpeas", "tahini", "lemon juice"],
    "legibility": "full"
  },
  "members": ["m_mia", "m_leo"],
  "extra_members": [],
  "owner_language": "en",
  "expect": {
    "m_mia": {"state": "ALLERGY", "label": "Allergy",
              "codes_include": ["allergen.sesame"], "matched_include": ["tahini"]},
    "m_leo": {"state": "RIPE"}
  },
  "headline_contains": ["Mia"],
  "headline_not_contains": ["safe"]
}
```

- `product` is a `models.domain.RawProduct` (only `key` and `source` are required).
- `members`: ids from the demo family (`contracts/household.example.json`: m_linh, m_ba, m_mia, m_leo).
  Omit to use the whole family. `extra_members`: full Member objects for one-off profiles
  (pets, pregnancy, sensitive allergies, halal, kidney disease, ...).
- `expect.<member_id>` keys (all optional except `state`):
  `state`, `label`, `codes_include`, `codes_exclude`, `matched_include`, `score_min`, `score_max`.
- `headline_contains` / `headline_not_contains`: substrings checked against `headline.text`.
- Tag `allergen` cases form the **no-missed-allergen gate** (plan 8.7): `make test-allergens`.

## Extra keys (brain-tests lane, appended 2026-10-02)

`tests/test_rules.py` also reads these. Unknown keys fail the "case is well formed" test, so this is
the complete list (`CASE_KEYS` / `EXPECT_KEYS` in test_rules.py).

Per member, under `expect.<member_id>` (all optional; a member needs at least one of `state`,
`state_in`, `codes_include`, `notes_include`):

| Key | Meaning |
|---|---|
| `state_in` | list of allowed states, for when the spec allows more than one (`["EASY", "SKIP"]`) |
| `notes_include` | codes that must be present with `severity: "note"` (same matching as `codes_include`) |
| `reasons_language` | every reason of this member must have this `language` (`"vi"`); fails if there are no reasons |

Case level:

| Key | Meaning |
|---|---|
| `member_ids` | passed to `evaluate()` (the "Who's this for?" toggle); null/omitted = everyone |
| `expect_member_ids` | exact verdict member ids, in household order, after the `member_ids` filter |
| `headline_first_name` | the first member name that appears in `headline.text` (the most serious clause leads) |
| `headline_max_sentences` | max sentences in the headline (split on `.`, `!`, `?` followed by whitespace) |
| `product_overrides` | fields set on `np.product` after `normalize()`, the way `pipeline.products.match_by_name` marks `less_sure` / `ask_for_ingredients` / `matched_by: "name"` / `data_confidence` |
| `why` | free text for humans (the spec rule behind the case), ignored by the runner |

`headline_contains` / `headline_not_contains` are case-insensitive NFC substring checks.

Code matching rules:

- `codes_include` / `notes_include`: an exact code or a prefix. `"signal."` = any signal;
  `"allergen.peanut"` also accepts `"allergen.peanut.may_contain"` / `".possible"`. Use the full code when
  the strength matters (`"allergen.peanut.may_contain"`). A `*.trace_note` code is a note, not an allergen
  hit, so it only matches when the pattern names it in full (`"allergen.milk.trace_note"`).
- `codes_exclude`: exact, or a prefix ONLY when the pattern ends with `.` or `*`.
  `"allergen."` = no allergen code at all; `"allergen.milk"` = only that exact code is forbidden
  (`"allergen.milk.possible"` would still pass); `"allergen.milk*"` = any milk allergen code.
- `matched_include`: every word must be a case-insensitive NFC substring of some `reason.matched` entry
  of that member's verdict. `matched` keeps every distinct printed span (the app highlights them), even
  when `reason.text` shows only the longest one ("Contains sesame (sesame seeds)").

Checks that run on EVERY case regardless of keys: no banned words ("safe" / "an toàn" / "seguro"),
strict allergy + partial / missing / name-matched list is never RIPE, verdicts in household order,
deterministic output, reasons in the member's first language and headline in the owner's, chip label
fits the state (complete label list: `api/kb/README.md` section 3), a reason code explains the state,
score tier fits the state, reasons sorted hard -> soft -> note.
