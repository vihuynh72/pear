# Pear

**Scan once, know for the whole family.** Pear pairs your family with products that fit.

Built solo by Vi at SF Hacks x GDG AI Hackathon (October 2, 2026), track: GDG, Build with AI
for Social Good.

## What Pear is

Every family carries a list in its head. Mia can't have sesame. Bà watches her salt. Leo is
vegan. The label doesn't know your family. Pear does.

Pear tells the person who shops for the household, in seconds, whether a food or cosmetic
product fits each person at home. Scan the barcode, snap the label, or ask a question in your
own language. Pear finds the ingredients, checks them against each person's allergies, health
conditions, skin issues and diet, and answers per person:

> Not for Mia: contains sesame, may contain peanuts. Fine for Linh, Bà and Leo.

Promises, in order: never miss an allergen (when unsure, say so; a false alarm is fine, a miss
is not), fast, calm results, every person in their own language, Google at the center.

Demo family: Linh (fragrance-sensitive skin, English and Vietnamese), Bà (72, high blood pressure
and prediabetes, Vietnamese), Mia (8, strict peanut and sesame allergy, eczema, English), Leo (16,
vegan, Spanish).

## How it works

Code routes. Gemini reads. Rules decide.

- **Barcode:** Firestore cache, else Open Food Facts and Open Beauty Facts raced (USDA FoodData
  Central as backup). No AI call on this path, so it stays fast.
- **Label photo:** one Gemini call reads the label and maps each ingredient to the knowledge base.
  A front-only photo is matched by name and marked "Less sure", with "Snap the ingredients".
- **Ask Pear:** a typed or dictated question ("Bà ăn mì này được không?") goes through one Gemini
  call, then code picks the product and the rules answer in the asker's language.
- **Rules engine:** deterministic Python with a sourced knowledge base. Gemini never decides a
  verdict; the same product and family always get the same answer.

Architecture, request flows and debugging: [docs/BACKEND.md](docs/BACKEND.md).

## Features built

Backend (`api/`, 3213 offline tests passing, including a 1848-test no-missed-allergen gate):

- Per-person verdicts: Ripe, Easy does it, Skip it, Allergy / May contain, Not vegan / Maybe not
  vegan / Check, Unsure. One reason per person that names the ingredient, in that person's
  language (English, Vietnamese, Spanish), and a headline in the owner's language.
- Allergens: the US major nine plus gluten, lactose, mustard, sulfites, celery, lupin, corn and
  kiwi, with synonyms in three languages, cosmetic INCI names, E-numbers and Open Food Facts tags.
  "Contains" and "may contain" are parsed separately. Two levels: Strict (may-contain counts) and
  Ingredients only. A partial or missing ingredient list never shows Ripe for a strict allergy.
- Diets (vegan, vegetarian, pescatarian, halal, kosher, no pork, no beef, no alcohol, Jain,
  Buddhist vegetarian, gluten-free) weighed together with certification marks.
- Graded health and skin signals (sodium, added sugars, saturated fat, phosphates, potassium,
  dyes, fragrance, skin allergens, retinoids, caffeine) from FDA Daily Values, with rinse-off
  products counting half. Pharmacist-style medication notes, pet toxins, supplement scope notes.
- Barcode, label photo (including front-only and blurry handling), and Ask Pear (JSON text, or an
  audio fallback for a language the phone can't transcribe).
- Better picks: prefetched in the background the moment a product is identified, found with
  Firestore vector search, re-checked by the rules engine for every family member.
- Prices on picks found with Gemini and Grounding with Google Search; a price with no search
  result behind it is never shown.
- Friendlier per-person explanations streamed from Gemini, checked for banned words, length and
  language, with the templated line as the fallback.
- Onboarding helpers: free-text profile parsing into chips to confirm, and diacritic-insensitive
  search ("dau phong" finds peanut).
- A `/mock` twin of every endpoint, `/debug` tools ("why did Pear say X"), structured logs, a
  span per stage, and latency metrics.

iOS app (`Pear/`, SwiftUI, in progress at code freeze): splash, sign-in screen, language
picker, family and member onboarding with chip search, home, one camera for barcodes and label
photos, Ask Pear with dictation, and the result sheet with spoken lines. Google sign-in in the app
is a placeholder today (guest mode); the backend already verifies Firebase ID tokens.

## Google products used

Counting only what is built in this repo:

| Product | What Pear uses it for |
|---|---|
| Gemini (3.5 Flash-Lite, 3.5 Flash) on Gemini Enterprise Agent Platform | Reads label photos and maps ingredients, understands questions, parses profiles, maps unknown terms, writes per-person explanations, finds prices |
| Gemini Embedding 2 | Product and query vectors for name matching and better picks |
| Grounding with Google Search | The price finder; only grounded answers reach the screen |
| Agent Development Kit (ADK 2.3) | The Ask Pear path is an ADK Workflow: one model node, code-chosen routes |
| Cloud Run | Hosts the Pear API (one warm instance, CPU always on for background work) |
| Cloud Build and Artifact Registry | Build the container on `gcloud run deploy --source .` |
| Firestore, including vector search | Product cache, scan log, price cache, `find_nearest` for picks |
| Firebase Authentication | Backend verifies Firebase ID tokens (firebase-admin); app sign-in is a placeholder |
| Cloud Logging, Cloud Monitoring, Cloud Trace | Structured logs per request, log-based latency metrics and a dashboard, one trace span per stage |
| Secret Manager | Holds the USDA FoodData Central key |

Also used while building: Agent Designer in Agent Platform Studio, to prototype the price prompt.

## Repo layout

```
Pear/               SwiftUI app (Xcode synchronized folders; Pear.xcodeproj at the repo root)
api/                FastAPI backend for Cloud Run
  main.py           app, middleware, error handling
  core/             config, clients, auth, logging, timing, tracing, background tasks
  routes/           one thin module per endpoint, plus /mock and /debug
  pipeline/         lookups, Gemini calls and prompts, picks, prices, ask, store
  rules/            normalizer, rules engine, scoring, headline, templates (en, vi, es)
  kb/               knowledge base JSON (allergens, diets, additives, conditions, ...)
  agent/            ADK Workflow for Ask Pear
  models/           contracts.py (the public API) and domain.py (internal seam)
  scripts/          smoke, warm, ingest, fixtures, exports, dev info
  tests/            pytest; cases/*.json are the rules test cases
contracts/          fixtures, openapi.json, kb_index.json, demo products, Swift kit (contracts/swift)
infra/              IAM, deploy, secrets, Firestore index, monitoring
demo/products.csv   demo barcodes and the expected verdict per person
docs/               master plan, agent addendum, backend guide
Dockerfile          Cloud Run image (repo root, so `--source .` works)
Makefile            `make` lists every target
```

## Quick start

Backend (Python 3.13, [uv](https://docs.astral.sh/uv/)):

```bash
make dev-install          # api/.venv
make test                 # offline: fake Gemini, in-memory store, no network
make test-allergens       # the no-missed-allergen gate
make dev                  # serves 0.0.0.0:8080 and prints the LAN URL for the phone
curl localhost:8080/health
```

Barcodes and the `/mock` API work without any Google credentials. For photos, questions and
Firestore, sign in once with `gcloud auth application-default login` (project pear-510410).
Deploying to Cloud Run: [infra/README.md](infra/README.md).

App: open `Pear.xcodeproj` in Xcode 27, pick the Pear scheme, and run on an iPhone or the
Simulator. The backend URL is `PearAPI.defaultBaseURL` in `Pear/Services/Backend/PearAPI.swift`;
the debug "Use mock" switch serves canned responses from the server's `/mock` endpoints.

## Docs

- [docs/BACKEND.md](docs/BACKEND.md): architecture, every setting, run, test, debug, extend,
  known limitations
- [contracts/README.md](contracts/README.md): API integration guide for the app
- [contracts/swift/README.md](contracts/swift/README.md): the Swift client kit
- [infra/README.md](infra/README.md): IAM, deploy, smoke test, monitoring
- [api/kb/README.md](api/kb/README.md): knowledge base ids, reason codes, engine rules
- [docs/PEAR_MASTER_PLAN.md](docs/PEAR_MASTER_PLAN.md) and
  [docs/PEAR_AGENT_ADDENDUM.md](docs/PEAR_AGENT_ADDENDUM.md): the plan

## Responsibility

**Pear isn't medical advice.** It reads labels and applies the rules you set for your family.
Always check the label, and ask a doctor, allergist or pharmacist about your health. Pear never
calls a product risk-free: when it can't confirm, it says so ("Unsure", "Less sure", "Snap the
ingredients"). Every flag carries a source (FDA, NIH, ASPCA and others, listed in
`api/kb/sources.json`). Photos are never stored; only the text read from them is cached.
Profiles live on the phone.

## AI tools used

As MLH asks: this project was built with **Claude Code** (Anthropic). Vi wrote the plan and
specs, then ran several Claude Code sessions in parallel lanes (backend, rules engine and
knowledge base, iOS UI, integration, red-team testing, docs). Claude Code wrote the code, tests
and docs; Vi reviewed, decided, tested and integrated. Inside the product, Google Gemini does the
reading, understanding and explaining described above.
