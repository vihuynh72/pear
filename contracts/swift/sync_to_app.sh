#!/usr/bin/env bash
# sync_to_app.sh — copy the Pear Swift kit into the iOS app (one way).
#
#   contracts/swift/sync_to_app.sh          # copy kit + kb_index.json into the app
#   contracts/swift/sync_to_app.sh --check  # exit 1 when the app copy is out of date
#
# What it does
#   - Copies every contracts/swift/*.swift (not Tests/) to Pear/Kit/, with a header line
#     "Synced from contracts/swift — edit there, then run sync_to_app.sh".
#     contracts/swift stays canonical: never edit Pear/Kit by hand.
#   - Removes Pear/Kit/*.swift files that no longer exist in contracts/swift (only files
#     that carry the sync header, so nothing hand-written is ever deleted).
#   - Copies contracts/kb_index.json to Pear/Resources/kb_index.json (chip search labels
#     and synonyms in en / vi / es) when it exists.
#   - Copies contracts/fixtures/*.json|*.txt to Pear/Resources/MockFixtures/ for the app's
#     offline mock (index.json becomes mock_index.json).
#   - Pear.xcodeproj uses a file-system synchronized group, so new files join the app
#     target automatically. Never edit project.pbxproj for this.
#
# Debugging
#   - The kit must compile with SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor (the app's
#     setting). contracts/swift/check.sh step 2 typechecks exactly that; run it first.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
KIT_DST="$ROOT/Pear/Kit"
RES_DST="$ROOT/Pear/Resources"
HEADER="// Synced from contracts/swift — edit there, then run sync_to_app.sh"
CHECK=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$KIT_DST" "$RES_DST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

stale=0
count=0
for src in "$HERE"/*.swift; do
  [ -e "$src" ] || continue
  name="$(basename "$src")"
  { printf '%s\n' "$HEADER"; cat "$src"; } > "$TMP/$name"
  count=$((count + 1))
  if ! cmp -s "$TMP/$name" "$KIT_DST/$name" 2>/dev/null; then
    stale=1
    if [ -z "$CHECK" ]; then cp "$TMP/$name" "$KIT_DST/$name"; echo "updated Pear/Kit/$name"; fi
  fi
done
[ "$count" -gt 0 ] || { echo "no kit sources in $HERE" >&2; exit 1; }

# Remove synced files whose source is gone.
for dst in "$KIT_DST"/*.swift; do
  [ -e "$dst" ] || continue
  name="$(basename "$dst")"
  if [ ! -e "$HERE/$name" ] && head -n 1 "$dst" | grep -qF "$HEADER"; then
    stale=1
    if [ -z "$CHECK" ]; then rm "$dst"; echo "removed Pear/Kit/$name"; fi
  fi
done

if [ -f "$ROOT/contracts/kb_index.json" ]; then
  if ! cmp -s "$ROOT/contracts/kb_index.json" "$RES_DST/kb_index.json" 2>/dev/null; then
    stale=1
    if [ -z "$CHECK" ]; then cp "$ROOT/contracts/kb_index.json" "$RES_DST/kb_index.json"; echo "updated Pear/Resources/kb_index.json"; fi
  fi
fi

# Fixtures for the app's offline mock (Pear/Services/OfflineMock.swift). index.json is
# renamed mock_index.json so the generic name can't collide with another bundle resource.
FIX_SRC="$ROOT/contracts/fixtures"
FIX_DST="$RES_DST/MockFixtures"
if [ -d "$FIX_SRC" ]; then
  mkdir -p "$FIX_DST"
  for src in "$FIX_SRC"/*.json "$FIX_SRC"/*.txt; do
    [ -e "$src" ] || continue
    name="$(basename "$src")"
    [ "$name" = "index.json" ] && name="mock_index.json"
    if ! cmp -s "$src" "$FIX_DST/$name" 2>/dev/null; then
      stale=1
      if [ -z "$CHECK" ]; then cp "$src" "$FIX_DST/$name"; echo "updated Pear/Resources/MockFixtures/$name"; fi
    fi
  done
  for dst in "$FIX_DST"/*; do
    [ -e "$dst" ] || continue
    name="$(basename "$dst")"
    src_name="$name"; [ "$name" = "mock_index.json" ] && src_name="index.json"
    if [ ! -e "$FIX_SRC/$src_name" ]; then
      stale=1
      if [ -z "$CHECK" ]; then rm "$dst"; echo "removed Pear/Resources/MockFixtures/$name"; fi
    fi
  done
fi

if [ -n "$CHECK" ]; then
  if [ "$stale" -eq 1 ]; then echo "Pear/Kit is out of date: run contracts/swift/sync_to_app.sh" >&2; exit 1; fi
  echo "Pear/Kit is up to date ($count files)"
else
  [ "$stale" -eq 0 ] && echo "Pear/Kit already up to date ($count files)" || echo "synced $count kit files"
fi
