#!/usr/bin/env bash
# Firestore indexes for Pear (addendum 8.4). Checks first, creates only what is missing.
#
#     make indexes                       # or: bash infra/firestore/create_indexes.sh
#     bash infra/firestore/create_indexes.sh --dry-run
#     bash infra/firestore/create_indexes.sh --ttl   # also enable the optional prices TTL
#
# Indexes (infra/firestore/firestore.indexes.json is the same set in Firebase CLI format):
#   1. products: category ASC + embedding (vector, 768 dims, flat), collection scope.
#      Needed by pipeline.product_store.find_nearest (where category == "food" + find_nearest).
#      Already created in the console on 2026-10-02 11:42 (addendum 18 item 5); this script
#      just verifies it. Building a new one takes a few minutes ("CREATING" -> "READY").
#   2. products.category_main: NO composite index needed. The cut-order fallback
#      (by_category_main: where category_main == X, limit 60) uses Firestore's automatic
#      single-field index. Adding an order_by to that query WOULD need a composite index;
#      Firestore's error message then contains a console link that creates it in one click.
#   3. prices TTL (optional, --ttl): Firestore TTL deletes docs whose `expire_at` timestamp is
#      in the past. Only useful if pipeline/prices.py writes expire_at (= checked_at + 24 h);
#      freshness is enforced in code either way, TTL only keeps the collection small.
#
# Debug: gcloud firestore indexes composite list --project pear-510410
#        A query that needs a missing index fails with FAILED_PRECONDITION + a create link.
set -euo pipefail

PROJECT="${PROJECT:-pear-510410}"
DATABASE="${DATABASE:-(default)}"
DRY_RUN=0
TTL=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --ttl) TTL=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

run() {
  echo "+ $*" >&2
  if [[ "$DRY_RUN" == "0" ]]; then
    "$@"
  fi
}

if [[ "$DRY_RUN" == "0" ]] && ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed. brew install --cask google-cloud-sdk (see infra/README.md)" >&2
  exit 1
fi

echo "==> 1/3 products: category + embedding vector index ($PROJECT, database $DATABASE)"
EXISTING=""
if [[ "$DRY_RUN" == "0" ]]; then
  EXISTING="$(gcloud firestore indexes composite list --project "$PROJECT" --database "$DATABASE" \
    --format=json 2>/dev/null || echo '[]')"
fi
# Match on the JSON text: collection group products + a vectorConfig on embedding.
if grep -q '"embedding"' <<<"$EXISTING" && grep -q 'vectorConfig' <<<"$EXISTING" \
   && grep -q 'collectionGroups/products/' <<<"$EXISTING"; then
  echo "    present:"
  gcloud firestore indexes composite list --project "$PROJECT" --database "$DATABASE" \
    --filter="name~collectionGroups/products/" --format="table(name.basename(),state)" || true
else
  [[ "$DRY_RUN" == "0" ]] && echo "    missing, creating (takes a few minutes to become READY)"
  run gcloud firestore indexes composite create \
    --project "$PROJECT" --database "$DATABASE" \
    --collection-group=products \
    --query-scope=COLLECTION \
    --field-config=order=ASCENDING,field-path=category \
    --field-config='field-path=embedding,vector-config={"dimension":"768","flat":"{}"}' \
    --async
fi

echo
echo "==> 2/3 products.category_main: automatic single-field index, nothing to create"

echo
if [[ "$TTL" == "1" ]]; then
  echo "==> 3/3 prices TTL on expire_at"
  run gcloud firestore fields ttls update expire_at \
    --collection-group=prices --enable-ttl --project "$PROJECT" --database "$DATABASE" --async
else
  echo "==> 3/3 prices TTL: skipped (pass --ttl; only useful if prices docs carry expire_at)"
fi

echo
echo "Check state: gcloud firestore indexes composite list --project $PROJECT"
