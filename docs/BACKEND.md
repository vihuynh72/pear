# Pear backend (`api/`)

FastAPI on Cloud Run. Barcode, label photo or question in; one verdict per family member out.
State on 2026-10-02 15:30 PT: 3213 offline tests pass (2 live tests deselected), the allergen
gate passes (1848), `gen_fixtures --check` and `export_openapi --check` are clean, kb_version
`ce088993a1`. This repo's API (version 0.3.0) is **not deployed yet** (section 10.1).

Read with: `docs/PEAR_MASTER_PLAN.md` 0-8, `docs/PEAR_AGENT_ADDENDUM.md` (wins on conflicts),
`api/kb/README.md` (ids, reason codes, engine rules), `contracts/README.md` (app integration),
`infra/README.md` (deploy), `api/agent/README.md` (ADK).

Contents: 1 Architecture · 2 Module map · 3 Config and flags · 4 Run locally · 5 Test · 6 Debug ·
7 Extend · 8 Cut order · 9 Known limitations and TODOs · 10 Deploy notes

---

## 1. Architecture

### 1.1 The rule: code routes, Gemini reads, rules decide

The endpoint is the router (no model picks a path). Every path makes **at most one generative
Gemini call before the verdict**, and the barcode path makes none. Gemini never decides a state:
it reads labels, understands questions, maps unknown words and polishes text. `rules/` decides.

| Endpoint | Gemini calls before the response | Extra calls (background or optional) |
|---|---|---|
| `POST /v1/scan/barcode` | **0** | background: product embedding; `normalize_unknown` (one batched call) only for a new product with unmatched strings |
| `POST /v1/scan/photo` | **1** `label_reader` (read + map to kb ids) | front-only photo: one embedding for the name match; background: product embedding |
| `POST /v1/ask` (JSON text) | **1** `question_reader` | an embedding if the question names a product; `alternatives` reuses picks |
| `POST /v1/ask` (multipart audio) | **1** `question_reader_audio` (transcribe + understand together) | same as above |
| `POST /v1/explain` | 1 per member, concurrent, streamed **after** the verdict | none |
| `POST /v1/profile/parse` | 0 or 1 (only for terms the local kb pass didn't match) | none |
| `POST /v1/picks` | 0 (reuses the prefetch; may embed the scanned product once) | `price_finder` starts for each pick |
| `POST /v1/prices` | 1 `price_finder` per uncached key (Grounding with Google Search, 6 s cap) | none |
| `GET /v1/kb/search` | 0 | none |
| `GET /health?deep=1` | 1 tiny call (8 tokens) | none |

Every Gemini call goes through `pipeline/gemini.py`: thinking `minimal`, JSON schema output
(except `price_finder`, because JSON mode turns grounding off), no temperature/top_p/top_k, a
timeout per call, location `global`. Each call is a timing stage named after the call and logs
`gemini.call` (model, ms, token usage).

### 1.2 Request lifecycle (`main.py`)

1. **Startup (lifespan):** `core/clients.py` builds one `httpx.AsyncClient(http2=True)`, the
   `google.genai.Client` (only if ADC works) and `firestore.AsyncClient` (per `PEAR_STORE`), loads
   the kb (`rules.kb.get_kb()`), and warms the ADK graph. Nothing raises: a missing credential just
   marks that backend unavailable (`/health` shows it).
2. **Routers** import one by one. A router that fails to import is skipped and listed in
   `/health` → `checks.router_errors`. Required routers (health, scan_barcode, scan_photo, ask,
   profile, kb, mock) failing make `ok: false`; optional ones (explain, picks, prices, debug) don't.
3. **Middleware** per request: request id (`X-Request-ID` header or 16 random hex), debug flag
   (`?debug=1` or `X-Pear-Debug: 1`), Cloud Trace id, a fresh timing context. On the way out it sets
   `X-Request-ID` and `Server-Timing` and logs one `request` line (skipped for `/health`).
4. **Route:** thin. Parse → `pipeline/*` → `rules/*` → a contract model from `models/contracts.py`.
5. **Errors:** `PearError(code, message, next=...)` → `{"error": {code, message, next, request_id}}`
   with the status from `core/errors.py` (`not_found` 404, `too_blurry`/`unreadable` 422,
   `rate_limited` 429, `unavailable` 503, `bad_request` 400, `unauthorized` 401, `internal` 500).
   Validation errors become `bad_request`. Anything unhandled becomes `internal` with
   "Something went wrong. Try again?". With debug on, `detail` is added as `debug`.
6. **Shutdown:** `core.background.drain(3 s)`, then clients close.

Auth (`core/auth.py`, `PEAR_AUTH`): `optional` (default) verifies a Firebase ID token if one is
sent (bad token → 401, next `sign_in`) and treats no token as `guest`. Guest mode in the app is
just "send no token". `required` rejects missing tokens; `off` ignores the header.

### 1.3 Per-endpoint flow

**Barcode** (`routes/scan_barcode.py` → `pipeline/products.identify_barcode`)
1. Fewer than 6 digits → 400 `bad_request`.
2. `cache_get`: `product_store.get_by_code` does one Firestore `get_all` over every key spelling
   (`off:`/`obf:`/`fdc:` × UPC-A/EAN-13/UPC-E variants from `core/keys.py`).
3. A cached record with **no ingredient list** older than `PEAR_MISSING_RECHECK_S` (600 s) is
   re-fetched at the source (someone may have added the ingredients on OFF).
4. Hit: use the stored normalized product, re-normalized if the kb version changed.
5. Miss: `lookup`: `lookup_off.lookup_barcode` races Open Food Facts and Open Beauty Facts under
   `PEAR_T_OFF` (1.1 s). An OFF hit wins at once; an OBF hit waits 0.3 s for OFF. Past 1.1 s it keeps
   waiting, up to `PEAR_T_OFF_MAX` (2.5 s), **only while OFF hasn't answered** (a cold TLS connection
   is not "not found"). Both miss → USDA FDC (needs `USDA_API_KEY`, `PEAR_T_FDC` 1.5 s). Only OFF
   throttling answered → 429 `rate_limited`. Nothing → 404 `not_found`, `next: take_photo`.
6. `normalize` → picks prefetch (food only) → `rules` → `ScanResult` (score hidden unless debug).
7. `after_scan` (background): save + embed (new or not-yet-embedded products), enrichment, scan log.

**Photo** (`routes/scan_photo.py` → `pipeline/photo_gemini.scan_photo`)
1. Multipart `image` (+ optional `front_image`), `household` JSON string, optional `member_ids`.
2. `label_reader`: one Gemini call (`PEAR_MODEL_LABEL`, `PEAR_T_LABEL` 4 s, 1024 output tokens)
   with the schema from addendum 10 and the kb id list (`kb/kb_ids.txt`) in the system prompt.
3. Unreadable with no ingredients → 422 `too_blurry` (`retake`). `photo_kind: other` → 422
   `unreadable`. `front_only` → `match_by_name` (Firestore vector, then OFF search): match →
   result marked `less_sure`, `ask_for_ingredients`, `matched_by: name`; none → 404 `not_found`.
4. Ingredients → `label_to_raw` (key `photo:<12 hex sha1(brand|name|ingredients)>`, model kb ids
   kept only if they exist in the kb) → normalize (merge rule: allergen ids = union of the kb
   match and the model's ids, so the model can add flags, never remove one) → rules.
5. The image is never stored or logged. Only the model's text answer is saved.

**Ask** (`routes/ask.py` → `pipeline/ask.answer`)
1. `Content-Type` picks the parser: JSON `AskRequest` (primary, on-device dictation or typed) or
   multipart (`audio` m4a fallback, or a `text` field).
2. `PEAR_ASK_ENGINE=adk` (default) runs `agent.graph.run_ask` (section 1.8); any non-PearError
   failure logs `ask.adk_fallback` and answers through `answer_direct` (same functions).
3. `understand`: one Gemini call → language, intent (`check | alternatives | unclear`),
   product_query, member_ids, refers_to_last_product. Memoized, so a fallback never calls twice.
4. Code routes on intent. `check`: resolve the product (`last_product_key` via `load_product` when
   the question refers to it or names nothing, else `match_by_name(query)`) → rules → templated
   answer (`rules.templates.ask_answer`). `alternatives`: picks → `picks_answer`. `unclear` or no
   product → `next: scan_needed`.
5. Answer language: the detected language if en/vi/es, else the asker's first language, else en.
6. No Gemini (no ADC) → 503 `unavailable`, `next: retry`.

**Explain** (`routes/explain.py` → `pipeline/explain`): load the product (404 before the stream
starts) → same rules → one Gemini line per member, concurrently → each line is checked (banned
words, over 25 words, wrong language, failure) and falls back to the templated reason → SSE
`data: {member_id, language, text}` lines, then `event: done`. `PEAR_FEATURE_EXPLAIN=0` → 503.

**Profile parse** (`pipeline/profile_parse`): split terms → local kb search pass → one Gemini call
for leftovers only → ids not in the kb are dropped → Gemini down: local chips + `unmatched`, never
an error. The user confirms every chip in the app.

**kb search** (`routes/kb.py`): diacritic-insensitive search over profile options ("dau phong" →
peanut), optional `group` filter. Same data as `contracts/kb_index.json`.

**Mock** (`routes/mock.py`): every `/v1` route has a `/mock/v1` twin served from
`contracts/fixtures` (a test enforces it). Knobs: `?fixture=`, `?error=`, `?delay_ms=`, `?event_ms=`.

### 1.4 The seam: RawProduct → normalize → evaluate

```
source adapter (OFF / OBF / FDC / label_reader)
  -> models.domain.RawProduct       as printed, no interpretation
  -> rules.normalize.normalize()    -> NormalizedProduct (public Product + every kb Hit)
  -> rules.engine.evaluate()        -> Evaluation (verdicts + headline), pure, no I/O
  -> pipeline.result.build_scan_result()   -> ScanResult (same on every path)
```

- An adapter only fills `RawProduct`. Nothing outside `rules/` parses ingredient text.
- `Hit(kb_id, matched, where, strength)` is the single source of truth for flags. `where` is
  ingredients, contains, may_contain, tags, model, enrich or name; `strength` is contains,
  may_contain or possible. `matched` is the exact printed span (the app highlights it).
- `evaluate()` is deterministic: same input, same output. `debug=True` adds the engine trace
  (penalties, hidden scores).
- Stored products remember `kb_version`. A different version re-normalizes from `raw` on read
  (enrichment hits carried over) and re-saves in the background, so kb edits apply to cached
  products without a migration.

### 1.5 Store and caching (`pipeline/product_store.py`)

| Collection | Contents |
|---|---|
| `products/{key}` | `StoredProduct`: raw, normalized, flat `category` / `category_main` / `kb_ids`, `embedding` as a Firestore `Vector(768)`, `kb_version`, `fetched_at` |
| `scans/{auto}` | uid, product_key, states per member, timings_ms, source, request_id, created_at. No photos, no household |
| `prices/{key}` | price dict, `checked_at`. Fresh for 24 h |

- Keys: `off:<ean13>`, `obf:<ean13>`, `fdc:<gtin>`, `photo:<12 hex>` (`core/keys.py`).
- Vector query: `where(category == food).find_nearest(embedding, COSINE, limit)` needs the
  composite index (`make indexes`, created in the console at 11:52).
- Every Firestore call is capped by `PEAR_T_FIRESTORE` (1 s). A failed read is a miss
  (`store.read_failed`); a failed write is logged (`store.write_failed`) and swallowed.
- `PEAR_STORE=auto` uses Firestore when ADC works, else memory. Memory mode = dicts + brute-force
  cosine, lost on restart, and it enforces Firestore's "no nested arrays" rule so tests catch it.
- In-process caches (one per process): kb (built once), embeddings LRU (512, de-duplicates
  in-flight calls), picks `INFLIGHT` (newest 200, results reused for 10 min), prices
  `PRICE_INFLIGHT` (500; "no price" retried after 10 min), mock fixtures (reloaded on mtime),
  `/debug/metrics` ring buffer (last 200 requests), OFF rate limiter windows.

### 1.6 Background tasks (`core/background.py`)

`spawn(name, coro)` keeps a strong reference, counts ok/failed per name and logs failures.
Names: `product_save`, `product_refresh`, `scan_log`, `picks_prefetch`, `picks_cold`,
`price_lookup`. `GET /debug/background` shows the counters.

Cloud Run must run with `--no-cpu-throttling` (deploy.sh does), or these stall until the next
request arrives. One instance and one uvicorn worker, because the prefetch and the `/v1/picks`
call must meet in the same process memory.

### 1.7 Picks prefetch (`pipeline/picks.py`, addendum 7)

```
identify (barcode / photo / ask)  -> start_prefetch(np, household)    returns at once, food only
  background compute():
    candidates  vector: stored embedding of the scanned product (else embed it now)
                        -> find_nearest 50, category == food          (PEAR_FEATURE_VECTOR)
                fallback: by_category_main(category_main, 60)
    normalize   stored normalized, re-normalized when kb_version changed
    rank        evaluate_many for every member; drop the scanned product, anything ALLERGY /
                UNSURE / DIET / SKIP for anyone, and products with no ingredient list;
                sort same category_main first, then all-RIPE before any EASY, then distance; top 3
    prices      prices.start() for each pick
POST /v1/picks -> get(): reuse the in-flight task (prefetched: true) or compute now (false);
                  wait up to PEAR_T_PICKS_WAIT (2.5 s), else items []. Never errors.
```

In-flight key: `<product_key>|<sha1(members)[:12]>`. Ranking budget 100 ms for 50 × 5
(`picks.rank_slow` logs over it).

### 1.8 Prices (`pipeline/prices.py`, addendum 9, P2)

`start(pick)` → `prices/{key}` fresh under 24 h → hit; else `find_price`: one
`PEAR_MODEL_PRICE` call with the Google Search tool and no JSON schema, parse
`PRICE|<retailer>|<amount>|<size>` lines, keep 0.25 ≤ amount ≤ 100, and **drop any answer without
grounding chunks** (a price with no search result behind it never reaches the screen) →
`put_price`. `POST /v1/prices` waits up to `PEAR_T_PRICE` (6 s) in total for lookups in flight,
starts the rest, returns `null` for anything not done. Max 10 keys per call. The app must show
`search_entry_html` when it is set (Google's grounding terms).

### 1.9 ADK ask agent (`agent/graph.py`, addendum 11)

One ADK 2.3.0 Workflow, `pear_ask`:

```
START -> read_question --route = intent--> check        -> check_product  (pipeline.ask.check)
                                           alternatives -> suggest        (pipeline.ask.alternatives)
                                           unclear      -> unclear        (pipeline.ask.scan_needed)
```

`read_question` is the only node that calls a model (`pipeline.ask.understand`). Routes are
chosen by code. Nodes are plain async functions wrapping the same `pipeline.ask` functions the
direct engine calls, so `PEAR_ASK_ENGINE=direct` gives identical answers. `clients` and the body
travel in a per-call ContextVar, never in ADK session state. Measured overhead: median 0.49 ms
over 20 runs (budget 50 ms). Details and the 2.3.0 API notes: `api/agent/README.md`.

---

## 2. Module map (who owns what)

| Path | Owns | Lane |
|---|---|---|
| `api/main.py` | app, lifespan, middleware, error handlers, router list | L3 |
| `api/core/config.py` | every env var (section 3) | L3 |
| `api/core/clients.py` | one httpx / genai / Firestore client per process; `get_clients` dependency | L3 |
| `api/core/auth.py` | Firebase ID token verification, `current_user` dependency | L3 |
| `api/core/errors.py` | `PearError`, code → HTTP status | L3 |
| `api/core/logging.py` | JSON (Cloud Run) / pretty (local) logs, `log_event` | L3 |
| `api/core/timing.py` | `stage()` timings + trace spans, debug bag, `annotate` | L3 |
| `api/core/tracing.py` | Cloud Trace exporter (OpenTelemetry) | L3 |
| `api/core/background.py` | `spawn`, `drain`, stats | L3 |
| `api/core/keys.py` | product keys, barcode variants (UPC-A / EAN-13 / UPC-E) | L3 |
| `api/core/deps.py` | multipart household parsing, upload size caps (image 8 MB, audio 10 MB) | L3 |
| `api/models/contracts.py` | **public API contract** (fixed, additive only; mirrored by `contracts/swift`) | contracts |
| `api/models/domain.py` | internal seam: RawProduct, Hit, NormalizedProduct, Evaluation, StoredProduct | L3 + L4 |
| `api/routes/*.py` | thin HTTP layer per endpoint; `debug.py` (/debug/*), `mock.py` (/mock/*) | L3 |
| `api/pipeline/products.py` | identify by barcode / key / name, after-scan background work | L3 |
| `api/pipeline/lookup_off.py`, `lookup_fdc.py` | OFF / OBF / FDC adapters, race, rate limiter, search | L3 |
| `api/pipeline/photo_gemini.py` | photo path (label_reader → RawProduct) | L3 |
| `api/pipeline/ask.py` | ask path node functions + direct engine | L3 |
| `api/pipeline/picks.py`, `prices.py`, `explain.py`, `profile_parse.py`, `normalize_unknown.py` | P1/P2 features | L3 |
| `api/pipeline/gemini.py` | the only place that calls Gemini (generate + embed) | L3 |
| `api/pipeline/prompts.py` | every prompt and JSON schema; `say()` (never-raising template wrapper) | L3 |
| `api/pipeline/embeddings.py` | embedding text, cache, product/query vectors | L3 |
| `api/pipeline/product_store.py` | Firestore / memory store | L3 |
| `api/pipeline/result.py` | `run_rules` + `build_scan_result` (every path ends here) | L3 |
| `api/agent/graph.py` | ADK Workflow for /v1/ask | L3 |
| `api/rules/kb.py` | kb loader, token trie lexicon, search, `prompt_ids_text` | L4 |
| `api/rules/text.py` | NFC/casefold with offset map, tokens, folding, E-numbers, `distinct_spans` | L4 |
| `api/rules/statements.py` | contains / may-contain regions | L4 |
| `api/rules/normalize.py` | RawProduct → NormalizedProduct (all hits) | L4 |
| `api/rules/engine.py` | states, precedence, allergy levels, diets, certs, confidence, `chip_labels()` | L4 |
| `api/rules/scoring.py` | graded signals (plan 8.4) | L4 |
| `api/rules/headline.py` | headline (plan 8.6) | L4 |
| `api/rules/templates.py` | every user-facing string in en / vi / es | L4 |
| `api/kb/*.json` | knowledge base (allergens, ingredients, additives, diets, conditions, skin, meds, pets, certifications, sources) | L4 |
| `api/tests/` | pytest; `cases/*.json` = rules cases (`allergen` tag = the gate) | L4 + all |
| `api/scripts/` | dev_info, smoke, warm, ingest, gen_fixtures, export_openapi, export_kb_index, export_kb_ids | L3 / L4 |
| `contracts/` | fixtures, demo products, openapi.json, kb_index.json, Swift kit (`contracts/swift`) | contracts |
| `infra/` | IAM, deploy, secrets, Firestore index, monitoring | L3 |
| `Dockerfile` (repo root) | Cloud Run image (one uvicorn worker) | L3 |

---

## 3. Configuration and feature flags

All read from the environment. Source of truth: `api/core/config.py`; `GET /debug/config` prints
the resolved values (secrets redacted). `make dev` loads `api/.env` (copy `api/.env.example`;
every value there is the default). Cloud Run gets env from `infra/deploy.sh`.

**Identity**

| Var | Default | What |
|---|---|---|
| `PEAR_VERSION` | `0.3.0` | `/health` version is `<this>+<git sha>` |
| `PEAR_GIT_SHA` | `dev` | set by deploy.sh / Dockerfile build arg |
| `K_SERVICE`, `K_REVISION` | unset → `pear-api`, `local` | set by Cloud Run; `K_SERVICE` also switches env to `cloudrun`, JSON logs and tracing on |

**Google Cloud and models**

| Var | Default | What |
|---|---|---|
| `PEAR_PROJECT` | `GOOGLE_CLOUD_PROJECT`, else `pear-510410` | project for Gemini, Firestore, Trace |
| `PEAR_REGION` | `us-central1` | informational (Cloud Run + Firestore nam5) |
| `PEAR_GEMINI_LOCATION` | `global` | Gemini 3.x is served only from `global`; don't change |
| `PEAR_MODEL_LABEL` | `gemini-3.5-flash-lite` | label_reader (photo) |
| `PEAR_MODEL_QUESTION` | `gemini-3.5-flash-lite` | question_reader (ask, text and audio) |
| `PEAR_MODEL_TEXT` | `gemini-3.5-flash-lite` | explain, profile parse, unknown terms, `/health?deep=1` |
| `PEAR_MODEL_PRICE` | `gemini-3.5-flash` | price_finder (Search grounding) |
| `PEAR_MODEL_EMBEDDING` | `gemini-embedding-2` | product and query vectors |
| `PEAR_MODEL_EMBEDDING_FALLBACK` | `gemini-embedding-001` | query vectors only; stored vectors never mix models |
| `PEAR_EMBEDDING_DIM` | `768` | must match the Firestore vector index |

**Timeouts (seconds)**

| Var | Default | Var | Default |
|---|---|---|---|
| `PEAR_T_OFF` | 1.1 (OFF/OBF race cap) | `PEAR_T_TEXT` | 3.0 (explain, profile, unknown terms) |
| `PEAR_T_OFF_MAX` | 2.5 (only while OFF hasn't answered) | `PEAR_T_EMBED` | 2.0 |
| `PEAR_T_FDC` | 1.5 | `PEAR_T_PRICE` | 6.0 |
| `PEAR_T_LABEL` | 4.0 | `PEAR_T_PICKS_WAIT` | 2.5 |
| `PEAR_T_QUESTION` | 2.5 | `PEAR_T_FIRESTORE` | 1.0 (per call) |
| `PEAR_T_AUDIO` | 4.0 | | |

**Behaviour switches**

| Var | Default | Values |
|---|---|---|
| `PEAR_STORE` | `auto` | `auto` (Firestore if ADC works, else memory), `firestore` (fail if it can't build), `memory` |
| `PEAR_AUTH` | `optional` | `off`, `optional`, `required` |
| `PEAR_ASK_ENGINE` | `adk` | `adk` (falls back to direct on any ADK failure), `direct` |
| `PEAR_TRACE` | on with `K_SERVICE`, off locally | Cloud Trace export |
| `PEAR_DEBUG_ROUTES` | `1` | serve `/debug/*`; set `0` for anything public |
| `PEAR_LOG_FORMAT` | `json` on Cloud Run, `pretty` locally | |
| `PEAR_LOG_LEVEL` | `INFO` | above INFO also empties `/debug/metrics` (it reads the `request` lines) |

**Feature flags** (all on; `0` cuts the feature, section 8)

| Var | Cuts |
|---|---|
| `PEAR_FEATURE_PICKS` | prefetch + `/v1/picks` (returns `items: []`) |
| `PEAR_FEATURE_VECTOR` | Firestore vector candidates and vector name match (category_main fallback, OFF search) |
| `PEAR_FEATURE_PRICES` | price_finder + `/v1/prices` (all `null`) |
| `PEAR_FEATURE_EXPLAIN` | `/v1/explain` (503 `unavailable`) |
| `PEAR_FEATURE_ENRICH` | background Gemini mapping of unmatched ingredient strings |
| `PEAR_FEATURE_SCAN_LOG` | `scans/{id}` writes |
| `PEAR_FEATURE_FDC` | USDA FDC fallback (also off when `USDA_API_KEY` is empty) |

**External APIs and module-level knobs**

| Var | Default | What |
|---|---|---|
| `USDA_API_KEY` | empty (FDC off) | Cloud Run: Secret Manager `usda-api-key` (`make secrets`) |
| `PEAR_CONTACT_EMAIL` | `pear-hackathon-app` | OFF User-Agent `Pear/0.3.0 (<this>)`; set it on deploy |
| `PEAR_OFF_BASE` / `PEAR_OBF_BASE` / `PEAR_FDC_BASE` | world.openfoodfacts.org / world.openbeautyfacts.org / api.nal.usda.gov/fdc/v1 | |
| `PEAR_FIXTURES_DIR` | auto: `<repo>/contracts/fixtures` (`/app/contracts/fixtures` in the image) | `/mock` fixtures |
| `PEAR_OFF_SEARCH_BASE` | `https://search.openfoodfacts.org` | name / category search (`lookup_off.py`) |
| `PEAR_OFF_READS_PER_MIN` | `80` | OFF product reads per minute per process |
| `PEAR_OFF_SEARCHES_PER_MIN` | `10` | OFF searches per minute |
| `PEAR_NAME_MATCH_MAX_DIST` | `0.35` | max cosine distance for a name match (`products.py`) |
| `PEAR_MISSING_RECHECK_S` | `600` | re-fetch a cached product with no ingredient list after this long |
| `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS` | `false` | keeps household text out of ADK spans |
| `PORT` | `8080` | uvicorn port (Cloud Run sets it) |

Deploy-time knobs (`infra/deploy.sh`, `make`): `PROJECT`, `REGION`, `SERVICE`, `SA`, `SECRET`,
`EXTRA_ENV="PEAR_FEATURE_PRICES=0,..."`, `PEAR_NO_INVOKER_IAM_CHECK=1`, `PEAR_CONTACT_EMAIL`.

---

## 4. Run locally

```bash
make dev-install                 # api/.venv with uv + Python 3.13 (brew install uv)
cp api/.env.example api/.env     # optional; every value is the default
make dev                         # prints the Mac's LAN IP, serves 0.0.0.0:8080 with reload
make dev-info                    # just the LAN IP, URLs and config summary
```

- iPhone on the same Wi-Fi: use the `http://<LAN IP>:8080` that `make dev` prints. The app's
  debug build needs an ATS exception (`NSAllowsLocalNetworking`); the macOS firewall must allow
  Python. Simulator: `http://127.0.0.1:8080`.
- Docs UI: `http://localhost:8080/docs`. Mock only: `/mock/v1/...` (no credentials, no network).

**Without credentials** (this machine today): store is memory, Gemini is unavailable. Barcodes
work (Open Food Facts is reachable), `/mock` works, `/v1/scan/photo` and `/v1/ask` return 503
`unavailable`, profile parse returns local chips, explain falls back to templated lines, picks
are empty. Each skip logs once at INFO, not per scan.

**With Gemini and Firestore** (ADC):

```bash
brew install --cask google-cloud-sdk
gcloud auth login
gcloud auth application-default login      # ADC: what the API uses locally
gcloud config set project pear-510410
make dev                                   # /health?deep=1 -> store "firestore", gemini "ok"
PEAR_STORE=memory make dev                 # real Gemini, throwaway local store
```

Your account needs `roles/aiplatform.user` and `roles/datastore.user` on pear-510410 (you own it).

Container check: `make docker-build && make docker-run` (memory store; mounts ADC if present),
then `curl localhost:8080/health`.

---

## 5. Test

| Command | What | Result 2026-10-02 15:30 |
|---|---|---|
| `make test` | offline suite: fake Gemini, memory store, mocked HTTP (`pytest -m "not live"`) | 3213 passed, 2 deselected |
| `make test-allergens` | the no-missed-allergen gate (`pytest -m allergen`) | 1848 passed |
| `make test-live` | 2 tests against real Open Food Facts / Gemini (network, ADC) | not run (no ADC) |
| `make lint` | ruff | old findings in core/background.py, core/timing.py, tests/conftest.py |
| `cd api && .venv/bin/python -m scripts.gen_fixtures --check` | fixtures match the engine | clean |
| `cd api && .venv/bin/python -m scripts.export_openapi --check` | openapi.json matches contracts.py | clean |
| `make swift-check` | Swift kit typechecks and decodes every fixture | passed (fix lane) |
| `make smoke URL=...` / `make smoke-mock URL=...` | contract + latency smoke against a running server | 68/68 local (integrate lane) |

Sandboxed agents: always prefix Python with `PYTHONDONTWRITEBYTECODE=1` (the sandbox hangs on
`.pyc` writes) and disable the pytest cache:

```bash
cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -m "not live" -p no:cacheprovider
cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -m allergen -p no:cacheprovider
cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_photo.py -k front_only
```

Test tooling (`tests/conftest.py`): `app_client` (TestClient with the lifespan), `fake_genai`
(`fake_genai.on("[pear:label_reader]", {...})` matches on the prompt tag; an unmatched call fails
the test), `household` / `household_dict` (demo family), memory store, `respx` for HTTP.
In this sandbox Python's `urllib` hangs on localhost; use `curl` against a running server.

What the rules cases check on every case (`tests/test_rules.py`): no banned words, a strict
allergy with a partial / missing / name-matched list is never RIPE, deterministic output,
reason and headline languages, chip label fits the state, scores fit the tier. Case format:
`api/tests/cases/README.md`.

---

## 6. Debug

### 6.1 Request id, debug mode, Server-Timing

- Every response has `X-Request-ID` (send your own or the server makes 16 hex). Errors carry it
  as `error.request_id`, `ScanResult` as `request_id`, and every log line as `request_id`.
- `?debug=1` or header `X-Pear-Debug: 1`: `ScanResult.debug` (cache, lookup race, kb hits with
  matched words, unmatched strings, legibility, engine trace, `gemini.<call>` raw text and usage)
  and each verdict's hidden `score`. Picks, prices and profile parse have no `debug` field, so
  the same bag comes back as JSON in the `X-Pear-Debug` **response header**.
- `Server-Timing: cache_get;dur=41, lookup;dur=486, normalize;dur=3, rules;dur=2, total;dur=548`
  (same numbers as `timings_ms`; visible in Proxyman / Charles / browser devtools).

```bash
H="$(cat contracts/household.example.json)"
curl -si 'localhost:8080/v1/scan/barcode?debug=1' -H 'content-type: application/json' \
  -H 'X-Request-ID: vi-test-1' -d '{"code":"0070662010013","household":'"$H"'}' | head -20
```

### 6.2 `/debug/*` (on while `PEAR_DEBUG_ROUTES=1`)

| Route | Use |
|---|---|
| `GET /debug/config` | resolved settings (secrets redacted), client status, router import errors, kb version |
| `GET /debug/routes` | every registered route |
| `GET /debug/kb/match?text=tahini&lang=en` | kb hits for free text (one word or a whole list) |
| `POST /debug/evaluate` | replay a product through normalize + engine with scores and trace (6.6) |
| `GET /debug/demo_products` | names usable as `{"demo": "<name>"}` |
| `GET /debug/product/{key}` | stored `products/{key}` record (`?full=1` keeps the embedding) + cached price |
| `GET /debug/off/{code}` | raw Open Food Facts + Open Beauty Facts replies for a barcode |
| `POST /debug/label_reader` | multipart `image` (+ `front_image`) → raw label_reader JSON, no rules |
| `GET /debug/background` | background task counters and last errors |
| `GET /debug/inflight` | picks and prices in-flight tasks, limiter windows, ADK stats |
| `GET /debug/metrics` | p50 / p95 per path and stage over the last 200 requests (`?reset=1`) |

```bash
curl -s 'localhost:8080/debug/kb/match?text=d%E1%BA%A7u%20ph%E1%BB%99ng&lang=vi' | jq '.hits'
curl -s localhost:8080/debug/off/0070662010013 | jq '.responses | keys'
curl -s localhost:8080/debug/product/off:0070662010013 | jq '.product.normalized.hits'
curl -s -F image=@label.jpg localhost:8080/debug/label_reader | jq '.label'
curl -s localhost:8080/debug/metrics | jq '.paths'
```

### 6.3 `/health`

`GET /health` is cheap (the app calls it on launch and when the camera opens). It reports
`ok`, version, env, store (`firestore | memory`), gemini (`ok | unavailable: <why>`), auth,
ask_engine, kb_version, uptime, features. `GET /health?deep=1` also does a Firestore read and a
tiny Gemini call (8 output tokens) with latencies, plus `checks.router_errors`, `store_error`, `kb_error`,
`tracing`. After a deploy it must show `store: firestore`, `gemini: ok` and your git sha.

### 6.4 Structured logs

One JSON object per line on Cloud Run, one readable line locally:
`15:02:11 INFO  [req=vi-test-] pear: request  method=POST path=/v1/scan/barcode status=200 timings_ms={...}`

| Field | Meaning |
|---|---|
| `severity`, `message`, `logger` | standard |
| `event` | the event name (same as message for `log_event` lines) |
| `request_id` | the request's id |
| `logging.googleapis.com/trace` | groups the line under the request's Cloud Trace |
| `stack_trace` | on exceptions |

The per-request summary is `event: request` with `method`, `path`, `status`, `timings_ms`
(per stage + `total`), `uid`, and `product_key` / `states` for scans. The log-based metrics and
dashboard read exactly these fields. Other useful events: `scan.barcode`, `product.cache_hit`,
`product.lookup_miss`, `product.saved`, `off.race`, `off.timeout`, `off.throttled`,
`off.rate_limited`, `gemini.call`, `gemini.error`, `gemini.bad_json`, `photo.read`,
`photo.front_only`, `ask.understood`, `ask.resolved`, `ask.adk_fallback`, `adk.run`,
`picks.computed`, `picks.timeout`, `picks.vector_failed`, `prices.found`, `prices.none`,
`explain.line`, `profile.parsed`, `kb.search_miss`, `store.read_failed`, `store.write_failed`,
`error`. The household JSON, photos, audio and the full question text are not logged; kb search
terms (`kb.search_miss`) and product names / queries (`product.name_match`) are.

### 6.5 Cloud Logging queries

Logs Explorer, project pear-510410 (or `gcloud logging read '<query>' --project pear-510410 --limit 50`):

```
# everything for one request
resource.type="cloud_run_revision" AND resource.labels.service_name="pear-api"
jsonPayload.request_id="vi-test-1"

# slow barcode scans
jsonPayload.event="request" AND jsonPayload.path="/v1/scan/barcode" AND jsonPayload.timings_ms.total>1500

# server errors, Gemini failures, OFF throttling
jsonPayload.event="request" AND jsonPayload.status>=500
jsonPayload.event=("gemini.error" OR "gemini.bad_json")
jsonPayload.event=("off.throttled" OR "off.rate_limited")

# picks and ADK
jsonPayload.event=~"^picks\."
jsonPayload.event="adk.run"
```

`make logs` (last 100 lines) and `make tail` (stream) need gcloud. Cloud Trace (Trace explorer,
service `pear-api`) shows one span per `stage()`, the request waterfall for the latency slide.

### 6.6 "Why did Pear say X?"

1. Get the request id from the app log (`X-Request-ID`) and find it in the logs (6.5): the
   `request` line has `product_key`, `states` and `timings_ms`.
2. Replay it through the engine with the same household:
   ```bash
   curl -s localhost:8080/debug/evaluate -H 'content-type: application/json' \
     -d '{"product_key":"off:0070662010013","household":'"$H"'}' | jq '.summary'
   # or {"demo":"sesame_crackers"}  or  {"product": {...RawProduct...}}; "member_ids": ["m_mia"]
   ```
   `summary` gives state, label, score and reason codes per member; `normalized.hits` shows every
   kb hit with `matched`, `where` and `strength`; `normalized.unmatched` lists strings the kb
   didn't know; `evaluation.debug` has penalties and the per-member trace.
3. Wrong or missing ingredient text? `GET /debug/off/{code}` (what OFF sent) or
   `POST /debug/label_reader` (what Gemini read). Data problem, not a rules problem.
4. A word wasn't recognized? `GET /debug/kb/match?text=<word>&lang=vi`. If it returns nothing,
   add the term (7.1). If it hits the wrong id, look for a neutral entry or a longer term.
5. Fix it, add a case to `api/tests/cases/*.json` that fails before and passes after, run
   `make test-allergens` and `make test`.

---

## 7. Extend

### 7.1 Add a kb term or allergen synonym (plus a test case)

1. Edit the entry in `api/kb/allergens.json` (or `ingredients.json`, `additives.json`, ...).
   Shape and rules: `api/kb/README.md` section 2. Put the term in `terms.en|vi|es|inci` (NFC,
   lower case is fine, the matcher folds case and plurals). Ambiguous words go in
   `possible_terms` (strength `possible`). OFF tags in `off_tags`. A word that must NOT match goes
   in a `"neutral": true` entry (longest match wins: `cocoa butter` beats `butter`).
2. Check it: `GET /debug/kb/match?text=<label text>&lang=<lang>`, or
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from rules.normalize import match_text; print(match_text('bơ đậu phộng','vi'))"`.
3. Add a case to the matching `api/tests/cases/*.json` file with `"tags": ["allergen", ...]`
   (format: `api/tests/cases/README.md`). Include a neutral-trap case if the new term could
   over-match.
4. `make kb-export` (writes `contracts/kb_index.json` and `api/kb/kb_ids.txt`; `test_exports_match_kb`
   fails if you forget), `make fixtures`, then `make test-allergens` and `make test`.
5. kb_version changes, so cached products re-normalize on their next read. The app's bundled
   search list needs the UI agent (section 9, item A1).

### 7.2 Add a reason template

1. Add the key to `_T` in `api/rules/templates.py` as `(en, vi, es)`. Name the ingredient
   (`{items}`), stay short and warm, no medical advice, never the banned words ("safe",
   "an toàn", "seguro"). `test_templates_complete_and_no_banned_words` and
   `test_every_lane_template_key_is_translated` enforce this.
2. Call it with `t("reason.x", lang, ...)` in `rules/`, or `say("key", lang, default="English")` from
   `pipeline/` (never raises).
3. New reason code? Add it to `api/kb/README.md` section 3. New chip label? Also
   `contracts/README.md` section 6 (`test_chip_labels_documented` compares both to
   `rules.engine.chip_labels()`). Then `make fixtures`.

### 7.3 Add an endpoint (plus mock fixture and Swift method)

1. Contract: request/response models in `api/models/contracts.py`, **additive only** (new
   optional fields or new models; never rename or remove).
2. Logic in `api/pipeline/<thing>.py`; thin route in `api/routes/<thing>.py` using
   `clients: Clients = Depends(get_clients)` and `user: User = Depends(current_user)`; raise
   `PearError`; time stages with `stage()`; add `("routes.<thing>", required)` to `ROUTERS` in `main.py`.
3. Mock twin in `api/routes/mock.py` plus a fixture `contracts/fixtures/<endpoint>.<scenario>.json`
   and a default in `contracts/fixtures/index.json` (`test_every_v1_route_has_a_mock_twin`).
4. Tests: `api/tests/test_<thing>.py` with `app_client` + `fake_genai`; `tests/test_contracts.py`
   validates fixtures. Add a check to `scripts/smoke.py` if it matters on stage.
5. `make openapi`, document it in `contracts/README.md` section 4.
6. Swift: a Codable mirror in `contracts/swift/PearModels.swift` and a method in
   `contracts/swift/PearAPIClient.swift`, then `make swift-check`. Only the UI agent / Vi syncs
   it into the app (section 9, item A1).

### 7.4 Change a prompt

1. Every prompt and schema lives in `api/pipeline/prompts.py`. Keep the tag on the first line
   (`[pear:label_reader]`, ...): tests and logs match on it. Keep schema field order (Gemini fills
   in order, so legibility and allergen statements come before the ingredient list).
2. Print the built label prompt (it embeds the kb ids):
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "from pipeline.prompts import label_system; print(label_system())"`.
3. `make test` (fake Gemini checks the plumbing), then try real photos with ADC:
   `POST /debug/label_reader` and `POST /v1/scan/photo?debug=1` (`debug.gemini.label_reader`).
4. A prompt never decides a state. If you want different verdicts, change `rules/` or the kb.

---

## 8. Feature cut order (addendum 16) mapped to flags

Cut from the top. Every cut is an env var: `EXTRA_ENV="PEAR_FEATURE_PRICES=0" make deploy`, or
`gcloud run services update pear-api --region us-central1 --update-env-vars PEAR_FEATURE_PRICES=0`.

| Order | Cut | Flag | What the app sees |
|---|---|---|---|
| 1 | Prices | `PEAR_FEATURE_PRICES=0` | `/v1/prices` returns all `null`; picks don't start lookups |
| 2 | ADK ask agent | `PEAR_ASK_ENGINE=direct` | same answers, no ADK in the path |
| 3 | Vector index | `PEAR_FEATURE_VECTOR=0` | picks use category_main; name match uses OFF search |
| 4 | Picks | `PEAR_FEATURE_PICKS=0` | `items: []`, the app hides the row |
| also | Explain | `PEAR_FEATURE_EXPLAIN=0` | 503; the app keeps the templated lines |
| also | Enrichment, scan log, FDC | `PEAR_FEATURE_ENRICH=0`, `PEAR_FEATURE_SCAN_LOG=0`, `PEAR_FEATURE_FDC=0` | nothing visible |
| never | Barcode, photo, ask, allergen tests | | |

---

## 9. Known limitations and TODOs

**A. Integration (needs Vi or the UI agent; Pear/ was off limits)**

1. **Don't run `contracts/swift/sync_to_app.sh` as it is.** The app today has its own client in
   `Pear/Services/Backend/` (PearAPI.swift, PearModels.swift) and no `Pear/Kit/`. The sync would add
   `Pear/Kit/PearModels.swift`, whose 17 type names (Household, Member, Product, ScanResult,
   Verdict, ...) clash with `Pear/Services/Backend/PearModels.swift`, so the app wouldn't compile.
   It would also overwrite `Pear/Resources/kb_index.json`, which uses a different format
   (`version` Int, `label`, `synonyms` per language) than `contracts/kb_index.json` (`version`
   string, `labels`, flat `synonyms`); `KBIndex.loadBundled` would return nil and the onboarding
   chips would vanish. Pick one client (the kit is the contract) before syncing.
2. **The live Cloud Run service is a different, older backend.** `https://pear-api-73536482701.us-central1.run.app`
   answers as "Pear API 0.1": `/health` is `{"ok","gemini","firestore","version":"0.1"}` and
   `/v1/kb/search` returns a bare list. The app's `Pear/Services/Backend` models were built
   against it. This repo's API (0.3.0) differs in ways that break those models: `HealthResponse`
   (`gemini` is a string, no `firestore`), `KbSearchResponse` (`{query, suggestions}`, `label`
   string + `labels` map), and `Product.name` / `brand` and `PickProduct.name` / `brand` can be
   `null` (non-optional `String` in the app, so one product with no brand fails the whole decode).
   **Decision (Vi, 2026-10-02):** the app uses the handoff kit against the live 0.1 service for
   the demo. `make deploy` therefore defaults to `SERVICE=pear-api-v3` so this repo can never
   replace the demo service by accident; converge the wire format first (CLAUDE.md "Two backends"),
   then point the app at `pear-api-v3`.
   The other direction is fine: the household the app sends today (`age_group: "65+"`,
   `tree_nuts`, `sulfites`, `molluscs`, condition `pregnant`, `avoid_dyes` bool) validates and
   resolves to canonical ids here (checked 2026-10-02).
3. `Verdict.label` is always English (`Allergy`, `May contain`, `Not vegan`, ...); the app must
   localize on the exact string (complete list: `api/kb/README.md` section 3). The 0.1 server sent
   labels in the member's language.
4. The "add other allergy / health problem" field should call `GET /v1/kb/search?q=` with no group
   filter (or `/v1/profile/parse`) and save a hit under its real group; only an unmatched term is
   saved raw (the backend then checks the literal word, gives UNSURE for a strict allergy and a
   "Pear can't check for X yet" note otherwise).
5. Error `message` is localized to the household language, while `contracts/README.md` says
   English. Harmless: the app switches on `code`.

**B. Not verified live (no credentials on the build machine)**

1. Gemini paths: photo (label_reader), ask (question_reader + audio), explain, profile parse,
   prices, embeddings. All pass offline with `fake_genai` only. Check each once on Cloud Run.
2. Picks ranking end to end: needs the Firestore catalog (`make ingest`) and embeddings. Locally
   items are empty. If ingest ran before the batch-embedding fix, re-run it (only 1 in 50
   products got a vector, and it mixed all 50 texts).
3. Firestore writes of photo products (nested-array fix) are only tested against the memory
   store's emulation of the rule.

**C. Engine behaviour to know before the demo** (false alarms are allowed; misses are not)

1. Allergen-free claims ("Free from: peanuts", "No peanuts", "Made without milk") still read as
   Contains: a false alarm, never a miss. Avoid allergy-friendly brands on stage or accept it.
2. A possible-only hit (lecithin → soy) shows the chip label "Allergy", not "May contain". The
   state is correct.
3. "Ripe for everyone. Go for it." can show for a product with a missing or partial list when
   nobody has a strict allergy (members get RIPE plus a "Less sure" note).
4. No ingredient list: members without a strict allergy (including vegan Leo) are RIPE with a
   "Less sure" note, so the headline says "Fine for ...". Per spec (plan 8.5).
5. The headline keeps two sentences, so Bà's EASY drops out when an allergy clause and a "Fine
   for" sentence are both present (plan 8.6).
6. More possible hits by design after the red-team fixes: nut-word compounds as possible peanut,
   one-syllable Vietnamese words on unaccented labels, the product name when there is no list.
7. Remaining false alarms: lac dye → milk, "lactose-free milk" → lactose (milk itself is correct),
   named non-wheat flours (potato flour) → possible wheat.
8. Remaining gaps: "mi lk powder" (OCR space inside a word) is missed by the matcher (the
   photo model's kb ids may still add milk); vegan bone char / castoreum / mink and emu oil;
   pork in chà bông and carnitas. "May contain one or more of the following" inside an ingredient
   list is treated as cross-contact (policy call).
9. No never-miss fallback that marks low-diacritic Vietnamese labels as partial; the folded
   matcher pass covers the reported cases instead.

**D. Data and lookups**

1. Maruchan Lime Chicken (0041789001017): OFF has sodium 1.3 mg per serving (wrong), so Bà is RIPE.
   Use Top Ramen (0070662010013, 70% DV, EASY) for the high-sodium beat, or fix it on OFF.
2. OFF cap: the race waits up to 2.5 s (`PEAR_T_OFF_MAX`) while OFF hasn't answered, past the
   addendum's 1.1 s. Set it to 1.1 to restore the strict cap (then a cold first scan can 404).
3. OBF cold race: OFF answers "not found" fast and OBF is still slower than 1.1 s → a cosmetic
   barcode can 404 on the first try. Retry works; photos are the main path for cosmetics.
4. FDC lookup doesn't query 13-digit `gtinUpc` variants.
5. `PEAR_OFF_READS_PER_MIN` default went from 15 to 80; OFF allows about 100 product reads per
   minute per IP. Lower it in deploy env if OFF complains.

**E. Ops**

1. One instance, one worker: picks prefetch, prices, embedding cache and `/debug/metrics` live in
   process memory. Scaling out needs a shared cache first.
2. `PEAR_DEBUG_ROUTES` defaults on. Fine for judging; set `0` before anything public.
3. Memory store (no ADC) loses everything on restart.
4. Lint findings already present in core/background.py (UP035, ASYNC109), core/timing.py
   (UP035), tests/conftest.py (UP037).
5. Many files under `api/` and `contracts/` are still untracked in git (`git status`); stage them
   before the commit.

**F. Copy left as is** (vi/es): `label_many` for "Contains" lines, "Có caffeine (caffeine)"
repeat, the warfarin note wording, English-only Gemini error messages, es soy label "soja" (tests
pin it), sesame es "ajonjolí".

---

## 10. Deploy notes

### 10.1 Status

Not deployed from this repo yet. Blocked on Vi: install gcloud and sign in (ADC), `make iam`
(service account + roles, run.builder), `make deploy`, then `make indexes`, `make ingest`,
`make metrics`, `make warm`. Read section 9 A2 before `make deploy`: it replaces the 0.1 service
the app talks to today. Full runbook and troubleshooting: `infra/README.md`.

### 10.2 After a deploy

```bash
export PEAR_URL=$(make -s url)
curl -s $PEAR_URL/health?deep=1 | jq '{ok, version, store, gemini, checks}'
make smoke URL=$PEAR_URL N=5          # contract + latency; quote only these numbers
make warm URL=$PEAR_URL               # demo products + picks + prices into the caches
curl -s $PEAR_URL/debug/metrics | jq '.paths'
```

Judging settings (deploy.sh): `--min-instances 1 --max-instances 1 --no-cpu-throttling
--cpu-boost --cpu 2 --memory 1Gi --timeout 60 --concurrency 40`, service account
`pear-api@pear-510410.iam.gserviceaccount.com`, region us-central1 (Firestore nam5).
