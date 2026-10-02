#!/usr/bin/env bash
# Pear IAM setup (PEAR_AGENT_ADDENDUM 18 item 7). Run ONCE before the first deploy, from the
# laptop (after `gcloud auth login`) or Cloud Shell:
#
#     make iam                      # or: bash infra/setup_iam.sh
#     PROJECT=other-proj bash infra/setup_iam.sh
#     bash infra/setup_iam.sh --dry-run      # print the gcloud commands only
#
# What it does (idempotent: re-running is safe, "already exists" is ignored):
#   1. creates the runtime service account pear-api@$PROJECT.iam.gserviceaccount.com
#   2. grants it aiplatform.user (Gemini), datastore.user (Firestore), cloudtrace.agent,
#      logging.logWriter, monitoring.metricWriter, secretmanager.secretAccessor
#   3. grants roles/run.builder to the Compute Engine default service account, Google's
#      documented fix for `--source` deploys when the org blocks automatic default-SA grants.
#
# Debug: `gcloud projects get-iam-policy $PROJECT --flatten=bindings[].members \
#          --filter="bindings.members:pear-api@" --format="table(bindings.role)"`
set -euo pipefail

PROJECT="${PROJECT:-pear-510410}"
SA_NAME="${SA_NAME:-pear-api}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
# Project number for the compute default SA. Looked up live; this is pear-510410's.
DEFAULT_PROJECT_NUMBER="73536482701"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROLES=(
  roles/aiplatform.user
  roles/datastore.user
  roles/cloudtrace.agent
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/secretmanager.secretAccessor
)

step() { echo; echo "==> $*"; }

run() {
  # Print, then run (unless --dry-run).
  echo "+ $*" >&2
  if [[ "$DRY_RUN" == "0" ]]; then
    "$@"
  fi
}

if [[ "$DRY_RUN" == "0" ]] && ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed. brew install --cask google-cloud-sdk (see infra/README.md)" >&2
  exit 1
fi

step "Project: $PROJECT"

step "1/3 Service account $SA"
if [[ "$DRY_RUN" == "0" ]] && gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  echo "    already exists, skipping create"
else
  # A race with a concurrent create still prints "already exists": tolerate exactly that.
  if ! out=$(run gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" \
        --display-name "Pear API runtime" 2>&1); then
    if grep -qi "already exists" <<<"$out"; then
      echo "    already exists"
    else
      echo "$out" >&2
      exit 1
    fi
  else
    echo "$out"
  fi
fi

step "2/3 Roles for $SA"
for ROLE in "${ROLES[@]}"; do
  echo "    $ROLE"
  run gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:$SA" --role "$ROLE" --condition None --quiet >/dev/null
done

step "3/3 roles/run.builder for the Compute Engine default service account"
PROJECT_NUMBER="$DEFAULT_PROJECT_NUMBER"
if [[ "$DRY_RUN" == "0" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null || true)"
  PROJECT_NUMBER="${PROJECT_NUMBER:-$DEFAULT_PROJECT_NUMBER}"
fi
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "    $COMPUTE_SA"
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:$COMPUTE_SA" --role roles/run.builder --condition None --quiet >/dev/null

step "Done. Roles now held by $SA:"
if [[ "$DRY_RUN" == "0" ]]; then
  gcloud projects get-iam-policy "$PROJECT" --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:$SA" --format="value(bindings.role)" || true
fi
echo
echo "Next: make deploy   (infra/deploy.sh)"
