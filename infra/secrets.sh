#!/usr/bin/env bash
# Create or update the `usda-api-key` Secret Manager secret from STDIN (never echoed, never
# passed on the command line, so it stays out of shell history and `ps`).
#
#     make secrets                                   # prompts (input hidden)
#     pbpaste | bash infra/secrets.sh                # from the clipboard
#     bash infra/secrets.sh < ~/usda.txt
#     SECRET=other-name PROJECT=pear-510410 bash infra/secrets.sh
#
# infra/deploy.sh mounts it as USDA_API_KEY (`--set-secrets USDA_API_KEY=usda-api-key:latest`)
# only when the secret exists, so FDC lookups stay optional. The runtime service account
# needs roles/secretmanager.secretAccessor (infra/setup_iam.sh grants it).
#
# Debug: `gcloud secrets versions list usda-api-key --project pear-510410` (shows versions,
# never values). On Cloud Run, GET /debug/config shows usda_api_key as "***set***".
set -euo pipefail

PROJECT="${PROJECT:-pear-510410}"
SECRET="${SECRET:-usda-api-key}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed. brew install --cask google-cloud-sdk (see infra/README.md)" >&2
  exit 1
fi

if [[ -t 0 ]]; then
  printf "Paste the USDA FoodData Central API key (input hidden), then Enter: " >&2
  IFS= read -rs KEY
  echo >&2
else
  IFS= read -r KEY || true
fi
# Trim surrounding whitespace / CR from a pasted key.
KEY="$(printf '%s' "$KEY" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

if [[ -z "$KEY" ]]; then
  echo "No key read from stdin; nothing changed." >&2
  exit 1
fi

if gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "==> Adding a new version to secret $SECRET ($PROJECT)" >&2
  printf '%s' "$KEY" | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=- >/dev/null
else
  echo "==> Creating secret $SECRET ($PROJECT)" >&2
  printf '%s' "$KEY" | gcloud secrets create "$SECRET" --project "$PROJECT" \
    --replication-policy=automatic --data-file=- >/dev/null
fi
unset KEY

echo "==> Done. Versions:" >&2
gcloud secrets versions list "$SECRET" --project "$PROJECT" --limit 3 \
  --format="table(name,state,createTime)" >&2 || true
echo "Next: make deploy (it adds --set-secrets USDA_API_KEY=$SECRET:latest automatically)" >&2
