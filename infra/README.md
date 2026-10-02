# Pear infra: from zero to a judged Cloud Run service

Everything here is a script you run from the repo root on the Mac (or Cloud Shell). Nothing
deploys by itself. Project `pear-510410`, region `us-central1` (Firestore is in nam5, which
is us-central1), service `pear-api-v3` (this repo; `pear-api` is the live v0.1 demo service, so the default never replaces it).

| File | What it does |
|---|---|
| `Dockerfile`, `.dockerignore`, `.gcloudignore` (repo root) | Image for `gcloud run deploy --source .`: python:3.13-slim, /app/api + /app/contracts, non-root, ONE uvicorn worker |
| `infra/setup_iam.sh` | Service account `pear-api` + its roles, `run.builder` for the compute default SA (addendum 18.7) |
| `infra/deploy.sh` | The addendum 13 deploy command, then the smoke test |
| `infra/secrets.sh` | Create/update the `usda-api-key` secret from stdin |
| `infra/firestore/` | Vector index check/create (`create_indexes.sh`), the same index in Firebase CLI format (`firestore.indexes.json`) |
| `infra/monitoring/` | Log-based latency metrics + the "Pear API" dashboard (`setup_metrics.sh`, `dashboard.json`) |
| `api/scripts/smoke.py` | Contract + latency smoke test against any URL (`make smoke URL=...`) |
| `api/scripts/dev_info.py` | LAN IP + URLs so the phone can reach `make dev` |

`make` with no target lists everything. `make -n <target>` prints what a target would run.

## 1. Install and sign in (once, about 5 minutes)

```bash
brew install --cask google-cloud-sdk
gcloud auth login                         # your account, for gcloud commands
gcloud auth application-default login     # ADC, for running the API locally against real Gemini/Firestore
gcloud config set project pear-510410
gcloud components install beta            # only for `make tail`
```

Local dev environment (Python 3.13 + uv):

```bash
make dev-install          # api/.venv from api/requirements-dev.txt
cp api/.env.example api/.env   # optional; every value there is the default
make test                 # offline: fake Gemini, memory store, no network
make dev                  # prints the Mac's LAN IP, then serves on 0.0.0.0:8080 with reload
```

The iPhone on the same Wi-Fi uses the `http://<LAN IP>:8080` that `make dev` prints. If it can't
connect: macOS firewall must allow Python, and the app's debug build needs an ATS exception
for plain http.

## 2. IAM (once, before the first deploy)

```bash
make iam            # bash infra/setup_iam.sh (idempotent; --dry-run prints the commands)
```

It creates `pear-api@pear-510410.iam.gserviceaccount.com` with `aiplatform.user`,
`datastore.user`, `cloudtrace.agent`, `logging.logWriter`, `monitoring.metricWriter`,
`secretmanager.secretAccessor`, and grants `roles/run.builder` to
`73536482701-compute@developer.gserviceaccount.com` (the documented fix for source deploys when
the org blocks automatic grants to default service accounts).

Optional USDA FoodData Central key (barcode fallback when Open Food Facts misses):

```bash
make secrets        # paste the key; input is hidden. Or: pbpaste | bash infra/secrets.sh
```

## 3. Deploy

```bash
export PEAR_CONTACT_EMAIL=<your contact email>   # Open Food Facts wants a contact in the User-Agent
make deploy-dry     # look at the exact gcloud command first
make deploy         # build in Cloud Build + deploy (~3 to 5 min), prints the URL, runs the smoke test
export PEAR_URL=$(make -s url)
```

What `infra/deploy.sh` sends: `--source .` from the repo root, `--service-account pear-api@...`,
`--min-instances 1 --max-instances 1 --no-cpu-throttling --cpu-boost --cpu 2 --memory 1Gi`,
`--allow-unauthenticated` (the app calls it directly; Firebase ID tokens are verified in-app),
`--timeout 60 --concurrency 40`, env `PEAR_GIT_SHA`, `PEAR_PROJECT`, `PEAR_REGION`,
`PEAR_CONTACT_EMAIL` (if set), and `--set-secrets USDA_API_KEY=usda-api-key:latest` only if that
secret exists. Flags: `--fast-dev` (min-instances 0), `--dry-run`, `--no-smoke`.
`EXTRA_ENV="PEAR_ASK_ENGINE=direct,PEAR_FEATURE_PRICES=0" make deploy` flips features without a code change.

After every deploy: `curl $PEAR_URL/health?deep=1` must show `"store": "firestore"`, `"gemini": "ok"`,
and `version` ending in the git sha you just deployed.

## 4. Smoke test and warm-up

```bash
make smoke URL=$PEAR_URL            # /health?deep=1, every /mock endpoint, barcode x5, picks, kb search
make smoke URL=$PEAR_URL N=10       # more samples for p50/p95
cd api && .venv/bin/python -m scripts.smoke --url $PEAR_URL --barcode 0049000028911 --ask --prices -v
make smoke-mock URL=http://localhost:8080   # just /health + /mock (no network, no Gemini)
```

It validates every JSON body against `api/models/contracts.py` (unknown keys fail too), prints
client wall ms and server `timings_ms.total` p50/p95 per endpoint, and exits 1 on any contract
mismatch or unexpected status. Quote only these measured numbers in the pitch.

Before judging (addendum 4 and 9):

```bash
make warm URL=$PEAR_URL      # scripts/warm.py: demo products into the cache + prices for every demo pick
make ingest                  # scripts/ingest.py, once after Checkpoint 1: ~300 OFF products per demo category,
                             # normalized + embedded into Firestore for picks (~30 min, 10 searches/min)
make indexes                 # verifies the products vector index (category + embedding 768, flat) exists
```

## 5. Monitoring

```bash
make metrics       # bash infra/monitoring/setup_metrics.sh (--dry-run prints configs; --replace recreates the dashboard)
```

Creates log-based metrics from the one `request` log line per request (`jsonPayload.event="request"`,
`jsonPayload.path`, `jsonPayload.status`, `jsonPayload.timings_ms.*`):
`pear_request_latency` (distribution of `timings_ms.total`, label `path`), `pear_request_count`
(labels `path`, `status`), and `pear_stage_{lookup,cache_get,label_reader,question_reader,rules,picks_wait}`.
The "Pear API" dashboard shows p50/p95 per path, requests by status, stage p50/p95, Cloud Run's own
latency, instance count and a warnings log panel. Log-based metrics only count logs written after the
metric exists, so create them before the demo traffic, then run `make smoke`.

Logs: `make logs` (last 100 lines), `make tail` (stream). Every response has `X-Request-ID`; search it in
Logs Explorer with `jsonPayload.request_id="<id>"`. Cloud Trace shows one span per stage
(Trace explorer, filter service `pear-api`). Add `?debug=1` to any request to see the debug bag in the body.

## 6. Cloud Shell alternative (no local install)

Open https://shell.cloud.google.com with project pear-510410, then:

```bash
git clone <repo url> pear && cd pear      # or upload a zip of the repo (without Pear/ and api/.venv)
gcloud config set project pear-510410
bash infra/setup_iam.sh
PEAR_CONTACT_EMAIL=<your contact email> bash infra/deploy.sh --no-smoke
python3 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt
make smoke URL=$(make -s url)
```

Cloud Shell already has gcloud, docker and make, and is signed in as you.

## 7. Cost and scale

| | Judging (today) | After Friday |
|---|---|---|
| Instances | `--min-instances 1 --max-instances 1`: one warm instance, picks prefetch and `/v1/picks` meet in one process | `make deploy-fast` (min 0) to stop paying for idle; raise `--max-instances` only together with a shared picks cache (Firestore/Redis), since prefetch lives in process memory |
| CPU | `--no-cpu-throttling --cpu 2`: billed for the whole instance lifetime (roughly a few dollars a day at 2 vCPU / 1 GiB) | Keep it while background prefetch/prices matter; with throttling they stall until the next request |
| Workers | 1 uvicorn worker, `--concurrency 40` async | Same; scale with instances, not workers |
| Gemini | Flash-Lite for label/question reads, Flash + Search grounding for prices (6 s cap, 24 h cache) | `PEAR_FEATURE_PRICES=0` is the first cut |

Everything comes off the event credits billing account (addendum 18.2).

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 403 / PERMISSION_DENIED on Gemini (`aiplatform.endpoints.predict`) | runtime SA lacks `roles/aiplatform.user` | `make iam`, or the deploy used another SA: check `--service-account` |
| Firestore `403 Missing or insufficient permissions` / PermissionDenied | SA lacks `roles/datastore.user` | `make iam` |
| Source deploy fails in Cloud Build (`storage.objects.get` denied, "build failed" with no logs) | compute default SA has no `roles/run.builder` | `make iam` (step 3/3), then deploy again |
| Picks always empty or `prefetched: false` | CPU throttled after the response, or more than one worker/instance split the process memory | Keep `--no-cpu-throttling`, `--max-instances 1`, Dockerfile `--workers 1`; check `make indexes` and that `make ingest` ran |
| 404 `models/gemini-3.5-...` not found | Gemini 3.x is only served from the `global` location | Leave `PEAR_GEMINI_LOCATION=global` (default); don't set it to us-central1 |
| `/health` shows `"store": "memory"` on Cloud Run | ADC / service account problem: Firestore client failed, app fell back to memory | `curl $PEAR_URL/health?deep=1` and read `checks.store_error`; confirm the SA and `roles/datastore.user` |
| `/health` `ok: false` | a required router failed to import, or the kb didn't load | `checks.router_errors` / `checks.kb_error` name the module and exception; `make test` locally |
| Phone gets 403 from Cloud Run, or the deploy warns "Setting IAM policy failed" | org policy (domain restricted sharing) blocks `allUsers` invoker | `PEAR_NO_INVOKER_IAM_CHECK=1 make deploy` (adds `--no-invoker-iam-check`) or `gcloud run services update pear-api --region us-central1 --no-invoker-iam-check` |
| Vector query fails `FAILED_PRECONDITION ... index` | vector index missing or still building | `make indexes`; wait for READY. Picks fall back to category_main meanwhile |
| Barcode lookups `rate_limited` | Open Food Facts throttling (`off.throttled` in logs) | Warm the cache before judging (`make warm`); cached products never hit OFF |
| Smoke shows `version ...+dev` | deployed without `infra/deploy.sh` (no `PEAR_GIT_SHA`) | Deploy with `make deploy` |
| `make dev` phone can't connect | different Wi-Fi, firewall, or bound to 127.0.0.1 | Use the IP `make dev` prints, allow Python in the macOS firewall |
| Docker build is huge or slow | `.dockerignore` not applied (wrong directory) | Build from the repo root: `make docker-build` |
