#!/usr/bin/env bash
# Deploy the Pear API to Cloud Run from source (addendum 13), then smoke-test it.
#
#     make deploy                 # judging settings: 1 warm instance, CPU always on
#     make deploy-dry             # print the gcloud command, change nothing
#     bash infra/deploy.sh --fast-dev     # min-instances 0 (cheap while iterating)
#     bash infra/deploy.sh --no-smoke     # skip the post-deploy smoke test
#     PEAR_CONTACT_EMAIL=you@example.com make deploy   # Open Food Facts User-Agent contact
#
# Must run from a checkout (it cd's to the repo root, where the Dockerfile is). Needs
# infra/setup_iam.sh to have run once (service account + run.builder), else the build or
# the Gemini/Firestore calls fail; see the troubleshooting table in infra/README.md.
#
# Why these flags:
#   --min/max-instances 1    one warm instance for judging; prefetch + /v1/picks share memory
#   --no-cpu-throttling      background work (picks prefetch, cache writes, prices) keeps CPU
#                            after the response is sent; with throttling it stalls
#   --cpu-boost              faster cold start
#   --allow-unauthenticated  the app calls the service directly; Firebase ID tokens are
#                            verified in-app (core/auth.py)
#   --timeout 60             nothing legit takes longer (photo 4 s, prices 6 s caps)
#   --concurrency 40         one worker, async I/O; 40 in flight is plenty for a demo
#
# Optional env: PROJECT (pear-510410), REGION (us-central1), SERVICE (pear-api-v3),
#   PEAR_NO_INVOKER_IAM_CHECK=1 adds --no-invoker-iam-check (org policy blocks allUsers),
#   EXTRA_ENV="PEAR_ASK_ENGINE=direct,PEAR_FEATURE_PRICES=0" appended to --set-env-vars.
set -euo pipefail

PROJECT="${PROJECT:-pear-510410}"
REGION="${REGION:-us-central1}"
# Default is pear-api-v3 so a deploy of this repo can never replace the live v0.1 demo service
# (pear-api). Set SERVICE=pear-api on purpose once the app has moved to this backend.
SERVICE="${SERVICE:-pear-api-v3}"
SA="${SA:-pear-api@${PROJECT}.iam.gserviceaccount.com}"
SECRET="${SECRET:-usda-api-key}"
MIN_INSTANCES=1
DRY_RUN=0
SMOKE=1

for arg in "$@"; do
  case "$arg" in
    --fast-dev) MIN_INSTANCES=0 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-smoke) SMOKE=0 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HAVE_GCLOUD=0
if command -v gcloud >/dev/null 2>&1; then HAVE_GCLOUD=1; fi
if [[ "$DRY_RUN" == "0" && "$HAVE_GCLOUD" == "0" ]]; then
  echo "gcloud is not installed. brew install --cask google-cloud-sdk (see infra/README.md)" >&2
  exit 1
fi

# --- git sha (shown in /health version "0.3.0+<sha>") ---
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [[ "$GIT_SHA" != "nogit" ]] && ! git diff --quiet HEAD -- api contracts 2>/dev/null; then
  GIT_SHA="${GIT_SHA}-dirty"
fi

# --- env vars ---
ENV_VARS="PEAR_GIT_SHA=${GIT_SHA},PEAR_PROJECT=${PROJECT},PEAR_REGION=${REGION}"
if [[ -n "${PEAR_CONTACT_EMAIL:-}" ]]; then
  ENV_VARS="${ENV_VARS},PEAR_CONTACT_EMAIL=${PEAR_CONTACT_EMAIL}"
else
  echo "note: PEAR_CONTACT_EMAIL is not set; Open Food Facts asks for a contact in the User-Agent." >&2
fi
if [[ -n "${EXTRA_ENV:-}" ]]; then
  ENV_VARS="${ENV_VARS},${EXTRA_ENV}"
fi

# --- secrets: only mount USDA_API_KEY if the secret exists ---
SECRET_ARGS=()
if [[ "$HAVE_GCLOUD" == "1" ]] && gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  SECRET_ARGS=(--set-secrets "USDA_API_KEY=${SECRET}:latest")
else
  echo "note: secret '$SECRET' not found (or gcloud missing); deploying without USDA_API_KEY (FDC fallback off). Create it with: make secrets" >&2
fi

EXTRA_ARGS=()
if [[ "${PEAR_NO_INVOKER_IAM_CHECK:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--no-invoker-iam-check)
fi

CMD=(gcloud run deploy "$SERVICE" --source . --project "$PROJECT"
  --region "$REGION"
  --service-account "$SA"
  --min-instances "$MIN_INSTANCES" --max-instances 1
  --no-cpu-throttling --cpu-boost --cpu 2 --memory 1Gi
  --allow-unauthenticated
  --timeout 60
  --concurrency 40
  --set-env-vars "$ENV_VARS"
  ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"}
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
  --quiet)

echo "==> Deploying $SERVICE to $REGION in $PROJECT (git $GIT_SHA, min-instances $MIN_INSTANCES)"
printf '+'
printf ' %q' "${CMD[@]}"
printf '\n'

if [[ "$DRY_RUN" == "1" ]]; then
  echo "(dry run: nothing deployed)"
  exit 0
fi

T0=$(date +%s)
"${CMD[@]}"
echo "==> Deployed in $(( $(date +%s) - T0 )) s"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo
echo "URL: $URL"
echo "     $URL/health?deep=1"
echo "     $URL/docs"
echo "     export PEAR_URL=$URL"
echo

if [[ "$SMOKE" == "1" ]]; then
  PY="$REPO_ROOT/api/.venv/bin/python"
  if [[ ! -x "$PY" ]]; then
    echo "skip smoke: $PY not found (run make dev-install), then: make smoke URL=$URL" >&2
    exit 0
  fi
  echo "==> Smoke test against $URL"
  (cd "$REPO_ROOT/api" && PYTHONDONTWRITEBYTECODE=1 "$PY" -m scripts.smoke --url "$URL")
fi
