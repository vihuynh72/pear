#!/usr/bin/env bash
# Log-based metrics + a Cloud Monitoring dashboard for Pear latency (addendum 4, 7.5).
#
#     make metrics                                   # or: bash infra/monitoring/setup_metrics.sh
#     bash infra/monitoring/setup_metrics.sh --dry-run    # print configs + commands only
#     bash infra/monitoring/setup_metrics.sh --replace    # also replace an existing dashboard
#
# Source of truth: the one `request` summary line api/main.py logs per request through
# core.logging.log_event (JSON on Cloud Run, so fields land in jsonPayload):
#
#     {"severity":"INFO","message":"request","event":"request","method":"POST",
#      "path":"/v1/scan/barcode","status":200,"timings_ms":{"lookup":420,"rules":6,"total":610},
#      "uid":"guest","request_id":"..."}
#
# Metrics created (idempotent: existing ones are updated in place):
#   pear_request_latency       DISTRIBUTION of timings_ms.total, label path
#   pear_request_count         COUNTER, labels path + status
#   pear_stage_<name>          DISTRIBUTION of timings_ms.<name>, label path, for
#                              lookup, cache_get, label_reader, question_reader, rules, picks_wait
# Then creates the "Pear API" dashboard from infra/monitoring/dashboard.json.
#
# Log-based metrics only count entries written AFTER the metric exists. Charts fill in
# within ~2 minutes of traffic; run `make smoke URL=...` to generate some.
# Debug: gcloud logging read 'jsonPayload.event="request"' --project pear-510410 --limit 5
#        gcloud logging metrics list --project pear-510410
set -euo pipefail

PROJECT="${PROJECT:-pear-510410}"
SERVICE="${SERVICE:-pear-api}"
DRY_RUN=0
REPLACE=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_FILE="$HERE/dashboard.json"
DASHBOARD_NAME="Pear API"
STAGES=(lookup cache_get label_reader question_reader rules picks_wait)

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --replace) REPLACE=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" == "0" ]] && ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed. brew install --cask google-cloud-sdk (see infra/README.md)" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BASE_FILTER="resource.type=\\\"cloud_run_revision\\\" AND resource.labels.service_name=\\\"${SERVICE}\\\" AND jsonPayload.event=\\\"request\\\""

# Exponential buckets: 5 ms * 1.25^i, 40 buckets -> top finite bound ~37 s.
BUCKETS='"bucketOptions": {"exponentialBuckets": {"numFiniteBuckets": 40, "growthFactor": 1.25, "scale": 5}}'

write_distribution() {  # name field description
  local name="$1" field="$2" desc="$3"
  cat >"$TMP/$name.json" <<EOF
{
  "description": "$desc",
  "filter": "$BASE_FILTER AND jsonPayload.timings_ms.$field >= 0",
  "metricDescriptor": {
    "metricKind": "DELTA",
    "valueType": "DISTRIBUTION",
    "unit": "ms",
    "displayName": "$name",
    "labels": [{"key": "path", "valueType": "STRING", "description": "request path"}]
  },
  "valueExtractor": "EXTRACT(jsonPayload.timings_ms.$field)",
  "labelExtractors": {"path": "EXTRACT(jsonPayload.path)"},
  $BUCKETS
}
EOF
}

write_counter() {
  cat >"$TMP/pear_request_count.json" <<EOF
{
  "description": "Pear API requests by path and HTTP status (from the request log line)",
  "filter": "$BASE_FILTER",
  "metricDescriptor": {
    "metricKind": "DELTA",
    "valueType": "INT64",
    "unit": "1",
    "displayName": "pear_request_count",
    "labels": [
      {"key": "path", "valueType": "STRING", "description": "request path"},
      {"key": "status", "valueType": "STRING", "description": "HTTP status code"}
    ]
  },
  "labelExtractors": {"path": "EXTRACT(jsonPayload.path)", "status": "EXTRACT(jsonPayload.status)"}
}
EOF
}

upsert_metric() {  # name
  local name="$1" file="$TMP/$1.json"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "--- $file"
    cat "$file"
    echo "+ gcloud logging metrics create $name --project $PROJECT --config-from-file=$file"
    return
  fi
  if gcloud logging metrics describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    echo "    update $name"
    gcloud logging metrics update "$name" --project "$PROJECT" --config-from-file="$file" >/dev/null
  else
    echo "    create $name"
    gcloud logging metrics create "$name" --project "$PROJECT" --config-from-file="$file" >/dev/null
  fi
}

echo "==> 1/2 Log-based metrics ($PROJECT, service $SERVICE)"
write_distribution pear_request_latency total "Pear API server time per request (timings_ms.total), by path"
upsert_metric pear_request_latency
write_counter
upsert_metric pear_request_count
for s in "${STAGES[@]}"; do
  write_distribution "pear_stage_$s" "$s" "Pear API stage time timings_ms.$s, by path"
  upsert_metric "pear_stage_$s"
done

echo
echo "==> 2/2 Dashboard \"$DASHBOARD_NAME\" from $DASHBOARD_FILE"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ gcloud monitoring dashboards create --project $PROJECT --config-from-file=$DASHBOARD_FILE"
  exit 0
fi
EXISTING="$(gcloud monitoring dashboards list --project "$PROJECT" \
  --filter="displayName=\"$DASHBOARD_NAME\"" --format='value(name)' 2>/dev/null || true)"
if [[ -n "$EXISTING" && "$REPLACE" == "0" ]]; then
  echo "    exists ($EXISTING); pass --replace to recreate it from dashboard.json"
else
  for d in $EXISTING; do
    echo "    delete $d"
    gcloud monitoring dashboards delete "$d" --project "$PROJECT" --quiet >/dev/null
  done
  echo "    create"
  gcloud monitoring dashboards create --project "$PROJECT" --config-from-file="$DASHBOARD_FILE" >/dev/null
fi

echo
echo "Open: https://console.cloud.google.com/monitoring/dashboards?project=$PROJECT"
echo "Charts fill in ~2 min after traffic (make smoke URL=...)."
