#!/usr/bin/env bash
# check.sh — verify the Pear Swift kit against the backend contracts. No Xcode project needed.
#
#   contracts/swift/check.sh            # typecheck for iOS + decode every fixture
#   contracts/swift/check.sh --strict   # also fail when a fixture has a key the Swift models drop
#   PEAR_BASE_URL=http://127.0.0.1:8080 contracts/swift/check.sh   # + live smoke vs a running backend
#   PEAR_SMOKE_TRANSPORT=curl ...       # smoke via curl when URLSession has no network (sandboxes)
#
# Steps
#   1. Typecheck all kit sources for the iOS 17 simulator in Swift 6 language mode.
#   2. Typecheck again the way the Pear app target compiles (Pear.xcodeproj: Swift 5 mode,
#      SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor, approachable concurrency) plus
#      Tests/UsageSample.swift, so drop-in use in the app is proven, and once more in Swift 6
#      with MainActor default isolation.
#   3. Build a macOS decoder (Tests/DecodeFixtures.swift + the non-UIKit kit files) and run it
#      over contracts/fixtures/*.json and contracts/household.example.json.
#      A folder that is empty or missing reports "0 fixtures" and still passes; a failed run
#      is retried once after 3 s in case a fixture was mid-write.
#   4. Optional: with PEAR_BASE_URL set, build Tests/LiveSmoke.swift and call every endpoint
#      in mock mode (+ /health live) against that server.
#
# Debugging
#   - Build products and the clang module cache live in $PEAR_SWIFT_BUILD_DIR
#     (default: $TMPDIR/pear-swift-kit). Delete it if the compiler complains about modules.
#   - Run the decoder by hand: "$BUILD/decode-fixtures" contracts/fixtures contracts/household.example.json
#   - Exit code: 0 = everything passed.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FIXTURES="${PEAR_FIXTURES_DIR:-$ROOT/contracts/fixtures}"
HOUSEHOLD="$ROOT/contracts/household.example.json"
BUILD="${PEAR_SWIFT_BUILD_DIR:-${TMPDIR:-/tmp}/pear-swift-kit}"
STRICT=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT="--strict" ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v xcrun >/dev/null || { echo "xcrun not found: install Xcode" >&2; exit 2; }
mkdir -p "$BUILD/module-cache"
export CLANG_MODULE_CACHE_PATH="$BUILD/module-cache"
MC=(-module-cache-path "$BUILD/module-cache")

# Kit sources = every .swift directly in contracts/swift. UIKit-only files are skipped on macOS.
KIT=()
MAC_KIT=()
for f in "$HERE"/*.swift; do
  [ -e "$f" ] || continue
  KIT+=("$f")
  if ! grep -q '^#if canImport(UIKit)' "$f"; then MAC_KIT+=("$f"); fi
done
[ "${#KIT[@]}" -gt 0 ] || { echo "no kit sources in $HERE" >&2; exit 1; }
SAMPLE=()
[ -f "$HERE/Tests/UsageSample.swift" ] && SAMPLE=("$HERE/Tests/UsageSample.swift")

IOS_SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
IOS=(-sdk "$IOS_SDK" -target arm64-apple-ios17.0-simulator)
APP_FEATURES=(-default-isolation MainActor
  -enable-upcoming-feature NonisolatedNonsendingByDefault
  -enable-upcoming-feature InferIsolatedConformances
  -enable-upcoming-feature MemberImportVisibility)
APP_FEATURES_SWIFT5=("${APP_FEATURES[@]}"
  -enable-upcoming-feature DisableOutwardActorInference
  -enable-upcoming-feature GlobalActorIsolatedTypesUsability
  -enable-upcoming-feature InferSendableFromCaptures)

step() { printf '\n==> %s\n' "$*"; }

step "1/4 typecheck ${#KIT[@]} kit files: iOS 17 simulator, Swift 6"
xcrun swiftc -typecheck -swift-version 6 -warnings-as-errors "${MC[@]}" "${IOS[@]}" "${KIT[@]}"
echo "ok"

step "2/4 typecheck like the Pear app target (MainActor default isolation; Swift 5 and Swift 6)"
xcrun swiftc -typecheck -swift-version 5 -warnings-as-errors "${APP_FEATURES_SWIFT5[@]}" "${MC[@]}" "${IOS[@]}" \
  "${KIT[@]}" ${SAMPLE[@]+"${SAMPLE[@]}"}
xcrun swiftc -typecheck -swift-version 6 -warnings-as-errors "${APP_FEATURES[@]}" "${MC[@]}" "${IOS[@]}" \
  "${KIT[@]}" ${SAMPLE[@]+"${SAMPLE[@]}"}
echo "ok${SAMPLE[@]+ (with Tests/UsageSample.swift)}"

step "3/4 decode fixtures with the Swift models (macOS build)"
xcrun swiftc -swift-version 6 -parse-as-library -Onone "${MC[@]}" -o "$BUILD/decode-fixtures" \
  "${MAC_KIT[@]}" "$HERE/Tests/DecodeFixtures.swift"
if ! "$BUILD/decode-fixtures" "$FIXTURES" "$HOUSEHOLD" $STRICT; then
  echo "decode failed; retrying once in 3 s (a fixture may have been mid-write)"
  sleep 3
  "$BUILD/decode-fixtures" "$FIXTURES" "$HOUSEHOLD" $STRICT
fi

if [ -n "${PEAR_BASE_URL:-}" ] && [ -f "$HERE/Tests/LiveSmoke.swift" ]; then
  step "4/4 live smoke against $PEAR_BASE_URL"
  xcrun swiftc -swift-version 6 -parse-as-library -Onone "${MC[@]}" -o "$BUILD/live-smoke" \
    "${MAC_KIT[@]}" "$HERE/Tests/LiveSmoke.swift" "$HERE/Tests/CurlTransport.swift"
  "$BUILD/live-smoke" "$PEAR_BASE_URL" "$HOUSEHOLD"
else
  step "4/4 live smoke skipped (set PEAR_BASE_URL=http://127.0.0.1:8080 with the backend running)"
fi

printf '\nALL CHECKS PASSED\n'
