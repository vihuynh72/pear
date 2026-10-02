# CLAUDE.md

Pear: scan a barcode, snap a label or ask a question, and get one verdict per family member.
SwiftUI app in `Pear/`, FastAPI backend in `api/` on Cloud Run, Gemini on Agent Platform.

## Read first, in this order

1. This file.
2. `docs/BACKEND.md`: architecture, config, run, test, debug, extend, known limitations.
3. `docs/PEAR_MASTER_PLAN.md` sections 0-8, then `docs/PEAR_AGENT_ADDENDUM.md` (the addendum
   wins where they disagree).
4. `api/kb/README.md`: profile ids, kb file shapes, reason codes, chip labels, engine rules
   (including the 2026-10-02 allergy-level decision).
5. `contracts/README.md`: what the app sends and receives, mock, errors, debugging a verdict.

Agents edit only their own lane's files. In the master plan, agents edit only section 16
(status board) and section 17 (decision log).

## Layout and seams

```
Pear/           iOS app (UI agent's lane). Pear.xcodeproj at the repo root
api/            backend: main.py, core/, routes/, pipeline/, rules/, kb/, agent/, models/, scripts/, tests/
contracts/      fixtures, openapi.json, kb_index.json, demo_products/, swift/ (the Swift kit)
infra/          IAM, deploy, secrets, Firestore index, monitoring (scripts only, nothing runs by itself)
demo/           products.csv: demo barcodes + expected verdict per member
docs/           plan, addendum, BACKEND.md
```

- **Contract seam:** `api/models/contracts.py` is the public API. It is fixed: changes are
  additive only (new optional fields, new models), never a rename or removal. It is mirrored by
  `contracts/swift/PearModels.swift`, validated against `contracts/fixtures` and exported to
  `contracts/openapi.json` (`make openapi`). The app does NOT use this kit today (see "Two
  backends" below); don't run `contracts/swift/sync_to_app.sh` until the wire formats converge.
- **Data seam:** `models/domain.py`. Every source adapter (Open Food Facts, Open Beauty Facts,
  USDA FDC, label_reader) fills a `RawProduct`; `rules.normalize.normalize()` makes a
  `NormalizedProduct` with every kb `Hit`; `rules.engine.evaluate()` returns the verdicts and
  headline; `pipeline/result.build_scan_result()` makes the `ScanResult` on every path. Nothing
  outside `rules/` parses ingredient text.
- **Gemini seam:** every model call goes through `api/pipeline/gemini.py`; every prompt and
  schema lives in `api/pipeline/prompts.py`. At most one generative call before a verdict; none
  on the barcode path.
- **Clients:** one httpx / genai / Firestore client per process (`core/clients.py`), reached with
  `Depends(get_clients)`. Errors are `core.errors.PearError`. Time work with `core.timing.stage()`.

## Commands (repo root)

```bash
make                    # list targets
make dev-install        # api/.venv (uv, Python 3.13)
make dev                # API on 0.0.0.0:8080 with reload, prints the LAN URL for the phone
make test               # offline suite (fake Gemini, memory store)
make test-allergens     # the no-missed-allergen gate
make kb-export          # contracts/kb_index.json + api/kb/kb_ids.txt after editing api/kb/*.json
make fixtures           # regenerate contracts/fixtures through the real engine
make openapi            # contracts/openapi.json
make swift-check        # Swift kit typecheck + decode every fixture
make smoke URL=...      # contract + latency smoke against a running server
```

Cloud targets (`make iam`, `make deploy`, `make logs`, ...) need gcloud and are Vi's to run.

## Sandboxed agents

- Always prefix Python with `PYTHONDONTWRITEBYTECODE=1` (the sandbox hangs on `.pyc` writes) and
  use `api/.venv/bin/python` from `api/`. Disable the pytest cache:
  `cd api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -m "not live" -p no:cacheprovider`
- Put a timeout on long commands. Python's `urllib` hangs on localhost here; use `curl`.
- No Google credentials on the build machine: tests use the `fake_genai` fixture, the server runs
  with `PEAR_STORE=memory`. Open Food Facts is reachable.
- No deploys, no gcloud, no git writes unless Vi asks. Stage only your own files, never `git add -A`.

## Rules that are not negotiable

- **Never miss an allergen.** `make test-allergens` must pass. A miss stops feature work until
  it's fixed. False alarms are fine if labeled ("Possible", "May contain", "Maybe not vegan").
  A partial, missing or name-matched ingredient list never gives RIPE to a strict allergy.
  Gemini never decides a state; the model can add allergen flags, never remove one.
- Every fix to a miss gets a case in `api/tests/cases/*.json` tagged `allergen`
  (format: `api/tests/cases/README.md`).
- User-facing text: never the word "safe" (or "an toàn", "seguro"), no medical advice, every
  reason names the ingredient, strings in en / vi / es in `api/rules/templates.py`.
- Never commit secrets: `api/.env`, `GoogleService-Info.plist`, service account JSON, the USDA key
  (Secret Manager `usda-api-key`). `.gitignore` covers these; check before staging.

## Where the UI lives

- `Pear/` is the app, built with Xcode synchronized folders: a file added under `Pear/` joins the
  target automatically. Never hand-edit `Pear.xcodeproj/project.pbxproj`.
- Networking + scan flow: `Pear/Services/Backend/` = the handoff kit (PearAPI, PearModels,
  PearScanFlow, PearScanner, PearDictation, PearVoice, PearImage, PearResultSheet) from
  `~/Downloads/pear-xcode-handoff/XCODE_AGENT_PROMPT.md`. Screens bind to `PearEnvironment`
  (`Pear/Services/PearEnvironment.swift`), which owns the one `PearScanFlow`, `HouseholdStore`
  and the auth placeholder. Onboarding chips come from `Pear/Resources/kb_index.json` (handoff
  format) through `ProfileCatalog`. Design: `Pear/Theme` + `Pear/Components` (Vi's design PDF).
- A pre-switch backup of the app is at `/tmp/pear_app_backup_1454` (temporary; may be gone).

## Two backends (decided 2026-10-02 by Vi)

| | Live demo backend ("0.1") | This repo's `api/` ("0.3.0") |
|---|---|---|
| Where | Cloud Run service `pear-api`, `https://pear-api-73536482701.us-central1.run.app`; source is not in this repo | `api/`; deploys as `pear-api-v3` by default (`make deploy`), so it can't replace the demo |
| App | The app talks to it today, through the handoff kit | Not used by the app yet |
| Wire differences | `/health` `{ok, gemini: bool, firestore: bool, version}`; `/v1/kb/search` bare array with `label` map; `verdicts[].label` localized ("Chín"); photo field `front`; ids `tree_nuts`, `sulfites`, `molluscs`, condition `pregnant`, age `65+` | `HealthResponse` (`gemini` string, `store`); `KbSearchResponse {query, suggestions}`; English `label`; photo field `front_image`; canonical ids `tree_nut`, `sulfite`, age `senior` (but it ACCEPTS every 0.1 input form) |

Next big task: make `api/` speak the 0.1 wire format on output too (health shape, kb/search
shape, localized chip labels, accept `front`, nullable-safe `name`/`brand`), so the handoff kit
works against both servers; then deploy `pear-api-v3`, point `PearAPI.defaultBaseURL` at it,
run the handoff acceptance checks, and retire `contracts/swift` (or regenerate it from the
handoff models). Until then, backend-only agents don't touch `Pear/` or `Pear.xcodeproj`.

## Debugging in one line each

- Any request: `?debug=1` or `X-Pear-Debug: 1`; match logs on the `X-Request-ID` response header.
- "Why did Pear say X": `POST /debug/evaluate {"product_key": "off:..."}` (or `{"demo": "sesame_crackers"}`).
- Is a word in the kb: `GET /debug/kb/match?text=...&lang=vi`.
- Is the server healthy: `GET /health?deep=1`.
