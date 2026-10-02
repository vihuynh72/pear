# Pear API: integration guide for the iOS app

The source of truth for every shape is `api/models/contracts.py` (Pydantic). Everything in this
folder validates against it (`api/tests/test_contracts.py`), and `openapi.json` is generated from it.

| File | What it is |
|---|---|
| `household.example.json` | The demo family (Linh, Bà, Mia, Leo). Send it as `household` on every call |
| `scan_result.example.json` | Plan 7.6 ScanResult example, made valid |
| `fixtures/*.json` | One valid response per endpoint + scenario, named `<endpoint>.<scenario>.json`. Served by `/mock` |
| `fixtures/index.json` | Which fixture the mock serves for which input (barcode, ask keywords, defaults, errors) |
| `fixtures/explain.sse.txt` | The `/v1/explain` event stream the mock replays |
| `demo_products/*.json` | RawProduct inputs for the demo set (fake but well-formed keys, demo only) |
| `openapi.json` | `cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py` |

## 1. Base URLs

| Where | Base URL | How |
|---|---|---|
| Laptop (same Wi-Fi as the phone) | `http://<Mac LAN IP>:8080` | `make dev` (= `cd api && uvicorn main:app --host 0.0.0.0 --port 8080 --reload`). LAN IP: `ipconfig getifaddr en0` |
| Cloud Run | `https://pear-api-….run.app` | `make url` (= `gcloud run services describe pear-api --region us-central1 --format 'value(status.url)'`) |
| Mock (either host) | same host + `/mock` prefix | `/mock/v1/...` and `/mock/health`. No credentials, no network, no Gemini |

Local http needs an ATS exception for the LAN IP in the debug build (`NSAllowsLocalNetworking`).
Switching real/mock is only a path prefix: `"\(base)/mock/v1/scan/barcode"` vs `"\(base)/v1/scan/barcode"`.

## 2. Headers

| Header | When | Notes |
|---|---|---|
| `Authorization: Bearer <Firebase ID token>` | Signed in | Guest mode sends nothing (auth is "optional" by default). A bad/expired token gets 401 `unauthorized`, next `sign_in` |
| `Content-Type: application/json` | JSON bodies | Multipart for photo and the ask audio fallback |
| `X-Request-ID: <your id>` | Optional | Echoed back in the `X-Request-ID` response header and in `request_id`. Otherwise the server makes one. Log it in the app: it is the key to the server logs |
| `X-Pear-Debug: 1` (or `?debug=1`) | Debugging only | Fills `debug` and the hidden `score` on verdicts. Never ship it on |

Every response has `X-Request-ID` and `Server-Timing` (stage timings, visible in Charles/Proxyman).

## 3. The mock (`/mock`)

Every `/v1` endpoint has a `/mock/v1` twin (a test enforces it). The mock returns fixture files, so
the UI can be built and every state can be reached before the pipeline works.

| Mock endpoint | Serves |
|---|---|
| `GET /mock/health` | `health.default.json` |
| `POST /mock/v1/scan/barcode` | by `code` from `index.json` → `scan_barcode_by_code`; unknown code → **404 `not_found`** (exactly like the real API) |
| `POST /mock/v1/scan/photo` | `scan_photo.lotion.json` (multipart fields accepted and ignored) |
| `POST /mock/v1/ask` | JSON or multipart. Keyword match on `text` (see below); audio-only multipart → `ask.check_vi.json` |
| `POST /mock/v1/picks` | `picks.default.json` |
| `POST /mock/v1/prices` | `prices.default.json`, keyed to the `product_keys` you send (unknown keys → `null`) |
| `POST /mock/v1/explain` | `explain.sse.txt` as a real SSE stream, ~150 ms between events (`member_ids` filters) |
| `POST /mock/v1/profile/parse` | `profile_parse.default.json` |
| `GET /mock/v1/kb/search?q=&lang=` | `kb_search.default.json`, filtered by `q`, `label` in `lang` |
| `GET /mock/fixtures` | the index and the list of fixture files |

Knobs, on any mock endpoint:

| Query | Effect |
|---|---|
| `?fixture=scan_photo.front_only` | Serve that file (with or without `.json`) |
| `?error=too_blurry` | That error fixture with the real HTTP status (table in section 7) |
| `?delay_ms=2500` | Wait first (max 30000). Test spinners, the "never a blank screen" rule, timeouts |
| `?event_ms=0` | Explain only: gap between SSE events |

Ask keyword routing (`index.json` → `ask_keywords`, first match wins): "instead", "alternative",
"something else", "thay", "khác", "otro/otra", "en vez" → `ask.alternatives`; "mì", "noodle", "ramen",
"được không" → `ask.check_vi`; anything else → `ask.scan_needed`. The mock echoes your `text` as `transcript`.

Demo barcodes (fake, well-formed EAN-13; UPC-A without the leading 0 also works):

| Code | Fixture | What it shows |
|---|---|---|
| `0000000000017` | `scan_barcode.sesame_crackers` | ALLERGY for Mia (contains sesame, may contain peanuts). "Not for Mia: contains sesame, may contain peanuts. Fine for Linh, Bà and Leo." |
| `0000000000024` | `scan_barcode.noodles_vi` | Vietnamese label: EASY for Bà (sodium 38% DV, reason in Vietnamese), DIET for Leo (shrimp, reason in Spanish) |
| `0000000000062` | `scan_barcode.ripe` | Ripe for everyone (bounce + haptic) |
| `0000000000031` | `scan_barcode.gummy_candy` | SKIP for Bà (sugar), DIET for Leo (gelatin), dye note for Mia |
| `0000000000055` | `scan_barcode.body_wash` | Rinse-off cosmetic: EASY for Mia (methylisothiazolinone counts half) |
| anything else | `error.not_found` (404) | "Snap the ingredients" flow |

Photo scenarios: `?fixture=scan_photo.lotion` (Mia SKIP, Linh EASY), `scan_photo.front_only`
(`less_sure`, `ask_for_ingredients`, Mia "May contain"), `scan_photo.partial_label` (Mia UNSURE).
`scan_photo.honey_vegan_bar` shows DIET "Check" for Leo (label says vegan but lists honey). Every scan
and ask fixture is generated by the real rules engine (`scripts/gen_fixtures.py`), so the texts are the
real templates.

Editing fixtures: change a file in `contracts/fixtures/`, and the next request serves it (files are
cached and reloaded when they change; no restart). Add a real barcode for a product you bought by
adding `"<code>": "<fixture>.json"` to `scan_barcode_by_code`.

## 4. Endpoints

All bodies are UTF-8 JSON unless noted. Inputs are lenient (unknown keys ignored). Outputs always
contain every key (null when empty), so Codable never trips on a missing key.

### GET /health
Call on launch and when the camera opens (warms Cloud Run and the TLS connection).
```json
{"ok": true, "service": "pear-api", "version": "0.3.0+dev", "env": "local", "region": "us-central1",
 "store": "memory", "gemini": "ok", "auth": "optional", "ask_engine": "adk", "kb_version": "mock0001",
 "uptime_s": 42, "features": {"picks": true, "vector": true, "prices": true, "explain": true,
 "enrich": true, "scan_log": true, "fdc": true}}
```
`checks` appears only with `?deep=1` or when something is broken. `ok: false` = a required router or
the knowledge base failed to load; show a gentle banner, barcodes may still work.

### POST /v1/scan/barcode → ScanResult
```json
{"code": "0000000000017", "household": { ...household.example.json... }, "member_ids": null}
```
`member_ids` (additive) is the "Who's this for?" toggle; null or omitted = everyone.

Response (`fixtures/scan_barcode.sesame_crackers.json`, trimmed):
```json
{
  "product": {
    "key": "off:0000000000017", "name": "Sesame Crackers", "brand": "Pear Demo Pantry",
    "category": "food", "product_type": null, "image_url": "https://placehold.co/600x600/png?text=Sesame+Crackers",
    "source": "open_food_facts", "photo_kind": null, "data_confidence": "high",
    "less_sure": false, "ask_for_ingredients": false,
    "ingredients": ["wheat flour", "sesame seeds", "salt"],
    "contains": ["wheat", "sesame", "gluten"], "may_contain": ["peanut"], "certifications": [],
    "label_language": "en", "code": "0000000000017", "quantity": "113 g",
    "nutrition": {"basis": "serving", "sodium_mg": 95, "sodium_dv": 4, "added_sugars_g": 0, "added_sugars_dv": 0,
                  "sugars_g": 0, "sat_fat_g": 0.5, "sat_fat_dv": 3, "caffeine_mg": null},
    "matched_by": "barcode"
  },
  "verdicts": [
    {"member_id": "m_linh", "state": "RIPE", "label": "Ripe", "reasons": [], "score": null},
    {"member_id": "m_ba", "state": "RIPE", "label": "Ripe", "reasons": [], "score": null},
    {"member_id": "m_mia", "state": "ALLERGY", "label": "Allergy", "score": null, "reasons": [
      {"code": "allergen.sesame", "text": "Contains sesame (sesame seeds)", "language": "en", "severity": "hard",
       "matched": ["sesame seeds", "sesame"], "source": "fda_major_allergens"},
      {"code": "allergen.peanut.may_contain", "text": "May contain peanuts", "language": "en", "severity": "hard",
       "matched": ["peanuts"], "source": "fda_major_allergens"}]},
    {"member_id": "m_leo", "state": "RIPE", "label": "Ripe", "reasons": [], "score": null}
  ],
  "headline": {"text": "Not for Mia: contains sesame, may contain peanuts. Fine for Linh, Bà and Leo.", "language": "en"},
  "timings_ms": {"cache": 41, "lookup": 486, "normalize": 3, "rules": 2, "total": 548},
  "request_id": "8f2c1d0a9b7e6f54",
  "debug": null
}
```
Rendering rules: verdicts are in family order. One chip per verdict (`label` + state symbol). Under
each non-RIPE chip show the first reason whose `severity` is not `note`; `reasons[].text` is already
in that member's language, so Listen speaks it with that member's voice. Highlight `matched` words in
`product.ingredients` on the Details screen. `headline` is in `household.owner_language`.
`product.contains` = allergens in the ingredients / "Contains:" statement; `product.may_contain` = ONLY
allergens the label warns about (may-contain / traces). Ambiguous words (lecithin → soy, oats → gluten)
are in neither list; they show as `allergen.<id>.possible` reasons for members with that allergy.
`reasons[].text` names the ingredient once ("Contains sesame (sesame seeds)"); `matched` may also
list shorter spans ("sesame") so every printed mention can be highlighted.
Badges: `less_sure` → "Less sure"; `ask_for_ingredients` → "Snap the ingredients to confirm";
source line from `source` ("From Open Food Facts. Check the label." / "Read from your photo.").
`image_url` may be null.

### POST /v1/scan/photo → ScanResult (multipart/form-data)
| Field | Type | Required |
|---|---|---|
| `image` | JPEG file (about 1600 px, quality 0.6, under 300 KB) | yes |
| `front_image` | JPEG file, the package front | no |
| `household` | household JSON as a string | yes |
| `member_ids` | JSON list as a string, e.g. `["m_mia"]` | no |

Response: a ScanResult as above with `source: "photo"`, key `photo:<12 hex>`, `photo_kind`
`ingredients | front_only | other`. Front only + matched by name: `less_sure: true`,
`ask_for_ingredients: true`, `matched_by: "name"`, `data_confidence: "medium"` (see
`fixtures/scan_photo.front_only.json`). Partial label: `less_sure: true`, strict allergies show UNSURE
(`fixtures/scan_photo.partial_label.json`). Too blurry: 422 `too_blurry`, next `retake`.

### POST /v1/ask → AskResponse
Primary (on-device dictation or typed text), `application/json`:
```json
{"text": "Bà ăn mì này được không?", "language": "vi", "last_product_key": "off:0000000000024",
 "household": { ... }, "asker_id": "m_linh"}
```
Fallback for a language the phone can't transcribe, `multipart/form-data`: `audio` (m4a file),
`household` (JSON string), `language`, `last_product_key`, `asker_id` (plain strings). A multipart
`text` field works too.

Response (`fixtures/ask.check_vi.json`, trimmed):
```json
{"transcript": "Bà ăn mì này được không?", "language": "vi", "intent": "check", "product_query": "mì",
 "member_ids": ["m_ba"], "next": "result",
 "answer": {"text": "Bà nên ăn ít thôi: nhiều muối (38% DV).", "language": "vi"},
 "scan_result": { ...ScanResult with only Bà's verdict... },
 "picks": null, "timings_ms": {"question_reader": 712, "resolve": 46, "rules": 1, "total": 781},
 "request_id": "…", "debug": null}
```
Speak `answer.text` with an `answer.language` voice. `next: "scan_needed"` → open the camera
(`fixtures/ask.scan_needed.json`). `intent: "alternatives"` → `picks` is filled (`fixtures/ask.alternatives.json`).
Always send `last_product_key` (the key of the last ScanResult) so "this" resolves without a search.

### POST /v1/picks → PicksResponse
```json
{"product_key": "off:0000000000017", "household": { ... }, "member_ids": null}
```
```json
{"items": [{"product": {"key": "off:0000000000062", "name": "Lightly Salted Brown Rice Cakes", "brand": "Pear Demo Pantry",
             "image_url": "https://…", "category": "food"},
            "verdicts": [{"member_id": "m_linh", "state": "RIPE", "label": "Ripe"}, "…one per member…"]}],
 "prefetched": true, "timings_ms": {"wait": 40, "total": 60}, "request_id": "…"}
```
Food only; every item fits everyone (only RIPE/EASY). `items` can be empty: hide the row.

### POST /v1/prices → PricesResponse (P2)
```json
{"product_keys": ["off:0000000000062", "off:0000000000093", "off:0000000000109"], "country": "US"}
```
```json
{"prices": {"off:0000000000062": {"amount": 3.49, "currency": "USD", "retailer": "Target", "size": "4.9 oz",
            "source_title": "target.com", "source_url": "https://www.target.com/",
            "checked_at": "2026-10-02T20:15:00Z", "source": "google_search", "search_entry_html": null},
            "off:0000000000109": null},
 "timings_ms": {"wait": 1830, "total": 1840}, "request_id": "…"}
```
Waits up to about 6 s. `null` = no price; show nothing. Copy: "About $3.49 at Target. Found with
Google Search." When `search_entry_html` is non-null it must be shown (small WKWebView).

### POST /v1/explain → text/event-stream (P1)
Body: `{"product_key": "...", "household": {...}, "member_ids": null}`. Stream:
```
data: {"member_id": "m_linh", "language": "en", "text": "No problems found for Linh."}

data: {"member_id": "m_ba", "language": "vi", "text": "Không thấy vấn đề gì cho Bà."}

event: done
data: {}

```
One `data:` line (an ExplainEvent) per member, in the order they finish, each followed by a blank
line; then `event: done` always last. Replace that member's reason line in place when it arrives;
keep the templated line if it never comes. Parse with `URLSession.bytes(for:)` + `lines`.

### POST /v1/profile/parse → ProfileParseResponse
```json
{"text": "Mia: peanuts and sesame, very strict. Eczema. No red dye. Also latex.", "language": "en"}
```
```json
{"chips": [{"group": "allergies", "id": "peanut", "level": "strict", "label": "peanut", "note": null},
           {"group": "skin", "id": "eczema", "level": null, "label": "eczema", "note": null},
           {"group": "less_of", "id": "artificial_dyes", "level": null, "label": "artificial dyes",
            "note": "Red 40 is an artificial dye"}],
 "unmatched": ["latex"], "timings_ms": {"gemini": 820, "total": 836}, "request_id": "…"}
```
Show chips for the user to confirm; never add them silently. `group` is one of allergies, conditions,
skin, diets, less_of, medications; `level` only for allergies.

### GET /v1/kb/search?q=ses&lang=vi → KbSearchResponse
```json
{"query": "ses", "suggestions": [{"group": "allergies", "id": "sesame", "label": "mè",
  "labels": {"en": "sesame", "vi": "mè", "es": "sésamo"}, "score": 1.0}]}
```
The app bundles the same list for instant offline search; call this for terms not in the bundle.

## 5. Allergy levels (decided 2026-10-02)

| App toggle | `level` | Allergen in ingredients / contains | "May contain" | Partial or missing ingredient list |
|---|---|---|---|---|
| Strict | `strict` | ALLERGY (`allergen.<id>`, `.possible`) | ALLERGY, label "May contain" (`allergen.<id>.may_contain`) | UNSURE unless an allergen was found |
| Ingredients only | `sensitive` | ALLERGY (same codes) | a note (`allergen.<id>.trace_note`); graded normally | graded normally + "Less sure" note |

## 6. States, labels, symbols (plan 8.3)

Precedence ALLERGY > UNSURE > DIET > SKIP > EASY > RIPE. A chip shows the highest; Details list every reason.

| `state` | `label` values (English; the app localizes) | Symbol | Chip colors |
|---|---|---|---|
| RIPE | Ripe | Filled green pear with a check | #E3F1DA, leaf text |
| EASY | Easy does it | Half-filled honey pear | #FCEFD2 |
| SKIP | Skip it | Peach pear outline with a minus | #FBE1D6 |
| ALLERGY | Allergy, May contain, Toxic for dogs, Toxic for cats, Toxic for pets | Berry shield beside the pear | #FBE7EB, berry text |
| DIET | Not vegan, Not vegetarian, Not pescatarian, Not halal, Not kosher, Not pork-free, Not beef-free, Not alcohol-free, Not Jain, Not Buddhist vegetarian, Not gluten-free; Maybe not vegan, Maybe not vegetarian, Maybe not pescatarian, Maybe not halal, Maybe not kosher, Maybe not pork-free, Maybe not beef-free, Maybe not alcohol-free, Maybe not Jain, Maybe not Buddhist vegetarian, Maybe not gluten-free; Check | Latte pear with a leaf mark | #EFE4DA |
| UNSURE | Unsure | Gray dotted pear with a question mark | #ECE8E3 |

That is the complete list of `label` values (`rules.engine.chip_labels()`, kept in sync with
`api/kb/README.md` section 3 by a test). Localize on the exact string; there is no "Has pork" chip.

`severity`: `hard` (drives ALLERGY / DIET / UNSURE), `soft` (graded EASY/SKIP signals, "maybe" diets),
`note` (never changes the state). `score` is hidden (null) unless debug is on. Never show the word "safe".

Reason codes (`reasons[].code`: `allergen.<id>`, `diet.<diet>.maybe`, `signal.sodium`, `confidence.unsure`, …)
are listed in `api/kb/README.md` section 3. Switch on the prefix, not the full code.

## 7. Errors

Every error has this body and a matching HTTP status:
```json
{"error": {"code": "too_blurry", "message": "Too blurry. Retake?", "next": "retake", "request_id": "…"}}
```

| `code` | HTTP | `next` | What the app does |
|---|---|---|---|
| `not_found` | 404 | `take_photo` | Barcode unknown: jump to "Snap the ingredients" |
| `too_blurry` | 422 | `retake` | "Too blurry. Retake?" and reopen the shutter |
| `unreadable` | 422 | `retake` | Not a label: ask for the ingredient panel |
| `rate_limited` | 429 | `retry` | Wait 1 to 2 s and retry once, then show the message |
| `internal` | 500 | `retry` | Show the message, offer Try again |
| `unavailable` (additive) | 503 | `retry` | Gemini/backend down: photo/voice off for now, barcodes still work |
| `unauthorized` (additive) | 401 | `sign_in` | Token expired: refresh the Firebase token once, else sign in (or guest) |
| `bad_request` (additive) | 400 | null | App bug: log `message` + `request_id` |

`message` is user-presentable English. Switch on `code`, not on `message`. Network timeout on the
phone (no response): treat like `retry`.

## 8. Latency targets (addendum 4) and call sequence

| Path | Target (server + network) |
|---|---|
| Barcode, cache hit | under 0.5 s |
| Barcode, cache miss | under 1.5 s |
| Photo | under 3 s |
| Text question | under 2 s, then speech starts |
| Picks | ready 0.3 to 0.8 s after the verdict (prefetched) |
| Prices | best effort, up to 6 s; never block anything |

Suggested client timeouts: barcode 4 s, photo 8 s, ask 6 s, picks 4 s, prices 8 s, explain 10 s.

| Screen | Calls |
|---|---|
| Launch | `GET /health` (also when the camera opens) |
| Barcode | `POST /v1/scan/barcode` → show the verdict at once → `POST /v1/picks` with `product.key` → `POST /v1/prices` with the pick keys. Optional: `POST /v1/explain` to polish lines |
| Photo | `POST /v1/scan/photo` → same as barcode after the verdict. `404/422` → the matching retake/snap flow |
| Ask | dictation text → `POST /v1/ask` with `last_product_key` → speak `answer.text`; `next == "scan_needed"` → camera; `scan_result` → show the result sheet |
| Onboarding | instant search on the bundled list; unknown terms → `GET /v1/kb/search` or `POST /v1/profile/parse` → confirm chips |

Picks and prices load after the verdict and never cover it.

## 9. Additive fields (not in plan 7.6; all optional, safe to ignore)

- ScanResult: `request_id`, `debug`
- Product: `code`, `quantity`, `nutrition`, `matched_by` (`barcode | name | photo | vector`)
- BarcodeRequest / ProductKeyRequest: `member_ids`
- AskRequest: `asker_id`; AskResponse: `answer`, `picks`, `timings_ms`, `request_id`, `debug`
- PicksResponse / PricesResponse / ProfileParseResponse: `timings_ms`, `request_id`
- Price: `search_entry_html`; ProfileChip: `label`, `note`
- ErrorBody: `request_id`; error codes `bad_request`, `unauthorized`, `unavailable`; next `sign_in`
- Member `avoid_dyes: null` means "use the age default" (on for baby and kid)

## 10. How to debug a wrong verdict

1. Reproduce with debug on: add `?debug=1` (or `X-Pear-Debug: 1`). The response gets `debug`
   (kb hits with matched words, unmatched ingredients, legibility, the engine's trace, Gemini raw
   output and timings) and each verdict's hidden `score`.
2. Replay through the engine: `POST /debug/evaluate` with one of
   `{"product_key": "off:…"}` (the stored record), `{"demo": "sesame_crackers"}` (see `GET /debug/demo_products`),
   or `{"product": {…RawProduct…}}`, plus optional `household` (defaults to the demo family) and
   `member_ids`. It returns `normalized` (every hit), `evaluation` (scores + debug) and a one-line `summary` per member.
3. Look at the stored product: `GET /debug/product/{key}` (raw source data, normalized record, kb version).
   Barcode data looks wrong? `GET /debug/off/{code}` shows the raw Open Food Facts / Open Beauty Facts replies.
4. A photo read looks wrong? `POST /debug/label_reader` (multipart `image`, optional `front_image`)
   returns the raw label_reader JSON with no rules.
5. Find the request in the logs by its `request_id` (`X-Request-ID`): one `request` line with
   `timings_ms` per request, plus `error` lines. Cloud Run: Logs Explorer, `jsonPayload.request_id="…"`.
6. Single word check: `GET /debug/kb/match?text=tahini&lang=en`.
7. Other tools: `GET /debug/config` (settings, which routers failed to import), `/debug/routes`,
   `/debug/metrics` (p50/p95 per path over the last 200 requests, for the latency slide),
   `/debug/background`, `/debug/inflight` (picks/prices tasks).

`/debug/*` exists only when `PEAR_DEBUG_ROUTES` is on (default). Turn it off for anything public:
`PEAR_DEBUG_ROUTES=0`.

## 11. Keeping fixtures honest

`make fixtures` and `make openapi` from the repo root run the same scripts; `make smoke-mock URL=…`
checks a deployed `/health` + `/mock`. By hand:

```
cd api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/gen_fixtures.py --check   # do fixtures match the engine?
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/gen_fixtures.py           # regenerate scan/ask/picks fixtures
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py         # refresh openapi.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_contracts.py tests/test_mock.py tests/test_debug.py -q
```
`gen_fixtures.py` runs `demo_products/*.json` through the real normalizer, rules engine and
`pipeline/result.py` with the demo family, keeps realistic `timings_ms`, and leaves the
hand-written fixtures alone while the engine is still a stub. Errors, health, kb search, profile
parse, prices and the explain stream are hand-written.
