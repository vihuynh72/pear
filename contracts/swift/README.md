# Pear Swift kit

Drop-in Swift networking for the Pear iOS app. It mirrors the backend contracts in
`api/models/contracts.py` (plan 7.6 + addendum 6) exactly. Foundation only, async/await,
no Combine, no packages. iOS 17+, Swift 6 friendly.

| File | What it is |
|---|---|
| `PearModels.swift` | Codable mirrors of every contract model (ScanResult, AskResponse, PicksResponse, PricesResponse, ProfileParseResponse, KbSearchResponse, HealthResponse, ErrorResponse, ExplainEvent, Household, Member, ...) |
| `PearAPIClient.swift` | `PearAPIClient`: one async method per endpoint, `PearAPIError`, request ids, debug logging |
| `PearEventStream.swift` | `client.explain(...)` as `AsyncThrowingStream<ExplainEvent, Error>` (SSE) + `PearSSEParser` |
| `PearMultipart.swift` | multipart/form-data builder for the photo scan and the audio ask fallback |
| `PearImageUpload.swift` | iOS only: shrink a photo to 1600 px long edge, JPEG 0.6, about 300 KB or less |
| `PearDemoHousehold.swift` | `Household.demo` (Linh, Bà, Mia, Leo), identical to `contracts/household.example.json` |
| `check.sh` | typechecks the kit for iOS and decodes every `contracts/fixtures/*.json` |
| `Tests/` | check.sh helpers (fixture decoder, live smoke, compile-only usage sample). **Do not add to the app.** |

Status when this kit was written: `check.sh` passes (iOS typecheck in Swift 6 and the way the Pear target
compiles, 23 fixtures decoded with 0 drift). With `PEAR_BASE_URL` set it also passes the full
mock-mode smoke against a local backend (every endpoint, multipart, SSE, errors, timeout,
cancel).

---

## 1. Add the files to the Xcode target

Add only the six `Pear*.swift` files at the top level of `contracts/swift/`. Skip `Tests/`,
`check.sh` and this README.

The Pear project uses Xcode folder-synchronized groups: anything inside `Pear/` joins the app
target automatically. Pick one of these:

**A. Reference, stays in sync (recommended).** In Xcode, right-click the `Pear` group, choose
**Add Files to "Pear"...**, select the six `contracts/swift/Pear*.swift` files, **turn OFF
"Copy items if needed"**, tick the **Pear** target and click Add. Xcode then points at the
files in `contracts/swift`, so kit updates (and `check.sh`) apply right away.

**B. Copy (simplest).** From the repo root:

```sh
mkdir -p Pear/Services/PearKit && cp contracts/swift/Pear*.swift Pear/Services/PearKit/
```

The synchronized group picks them up without touching the project file. Copy again after
every kit change, because the copies don't update themselves.

Requirements and notes:

- You need Xcode 26 or later (Swift 6.2+). The kit uses `nonisolated struct` and `@concurrent`.
- The Pear target sets `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor`. Every kit type is marked
  `nonisolated` so its Codable conformances work off the main actor. `check.sh` step 2
  compiles the kit the same way the target does (Swift 5 mode plus approachable concurrency,
  and Swift 6).
- Struct names follow the contract: `Household`, `Member`, `Allergy`, `Product`, `Verdict`,
  `Reason`, `Headline`, `ScanResult`, `Answer`, `AskResponse`, `PickItem`, `Price`,
  `Nutrition`, ... None of them clashes with a type in the app today. All enums carry a
  `Pear` prefix (`PearState`, `PearSeverity`, ...), so nothing shadows SwiftUI's `State`.

## 2. Configuration

```swift
import Foundation

enum PearEnv {
    #if DEBUG
    // Simulator: the Mac's localhost. Phone: the Mac's LAN IP (`ipconfig getifaddr en0`),
    // and run the backend with `--host 0.0.0.0`.
    static let baseURL = URL(string: "http://127.0.0.1:8080")!
    static let mode: PearAPIClient.Mode = .mock     // .live once the pipeline works
    static let debug = true
    #else
    static let baseURL = URL(string: "https://pear-api-XXXX-uc.a.run.app")!   // Cloud Run URL
    static let mode: PearAPIClient.Mode = .live
    static let debug = false
    #endif
}

let pear = PearAPIClient(
    baseURL: PearEnv.baseURL,
    mode: PearEnv.mode,
    tokenProvider: nil,   // guest mode. Signed in: { try? await Auth.auth().currentUser?.getIDToken() }
    debug: PearEnv.debug  // sends X-Pear-Debug: 1 -> `debug` blocks and verdict scores in responses
)
```

- **Mock mode** (`.mock`) calls `/mock/v1/...` and `/mock/health`. The backend serves these
  from `contracts/fixtures`, needs no credentials, and always works. Use it to build screens
  while the pipeline is in progress. Mock knobs per call:
  `pear.mocking(["error": "too_blurry"])`, `["fixture": "scan_photo.front_only"]`,
  `["delay_ms": "1500"]`, `["event_ms": "50"]` (explain only). They are ignored in `.live`.
  Mock barcodes: `0000000000017` (sesame crackers, Mia ALLERGY), `0000000000024`
  (noodles, Vietnamese), `0000000000062` (all Ripe). Any other code returns `not_found`.
- **Guest mode** means no `tokenProvider` (or it returns nil): no Authorization header is
  sent. When signed in, the provider returns the Firebase ID token and every call sends
  `Authorization: Bearer <token>`. A rejected token comes back as `next == .signIn`.
- **Timeouts:** the defaults are in `PearAPIClient.Timeouts` (barcode 8 s, photo 15 s, ask 12 s,
  ...). Pass `timeouts:` to `init` to change them, or `timeout:` to a single call.

### Info.plist for local http (development only)

The Pear target uses a generated Info.plist. Add these in the target's **Info** tab, and
keep them out of release builds (use a Debug-only Info.plist, or remove them before
submitting):

- `NSAppTransportSecurity` (Dictionary) > `NSAllowsLocalNetworking` = YES. This allows plain
  `http://` to local addresses: the Mac's LAN IP and `<your-mac>.local`. If a device still
  refuses, add `NSExceptionDomains` > `<your-mac>.local` > `NSExceptionAllowsInsecureHTTPLoads` = YES
  and use the `.local` name as the base URL.
- `NSLocalNetworkUsageDescription` (String), for example "Pear talks to the developer's Mac
  during testing." Without it, iOS blocks LAN connections from a real device, and the phone
  shows a one-time Local Network prompt.

Cloud Run is https, so it needs none of this.

## 3. Every call

```swift
let household = Household.demo                 // or your HouseholdStore's value

// Warmup: on launch and when the camera opens. Ignore errors.
_ = try? await pear.health()

// Barcode
let scan = try await pear.scanBarcode(code: "0000000000017", household: household)
// "Who's this for?" toggle:
let forMia = try await pear.scanBarcode(code: code, household: household, memberIds: ["m_mia"])

// Photo (ingredients label, optional front of pack)
let jpeg = PearImageUpload.jpegData(fromImageData: photo.fileDataRepresentation()!)!  // or jpegData(from: uiImage)
let photoScan = try await pear.scanPhoto(image: jpeg, frontImage: nil, household: household)

// Ask (dictated or typed text; always send the product on screen)
let reply = try await pear.ask(text: "Bà ăn mì này được không?", language: "vi",
                               lastProductKey: scan.product.key, household: household, askerId: "m_linh")

// Ask fallback: recorded m4a when SFSpeechRecognizer lacks the locale
let reply2 = try await pear.askAudio(audio: m4aData, mimeType: "audio/mp4", language: "vi",
                                     lastProductKey: scan.product.key, household: household, askerId: "m_ba")

// Picks (prefetched by the backend during the scan), then prices for the top 3
let picks = try await pear.picks(productKey: scan.product.key, household: household)
let prices = try await pear.prices(productKeys: picks.items.prefix(3).map(\.product.key), country: "US")
prices.price(for: key)?.displayText    // "About $3.49 at Target", then show Price.attribution
                                       // ("Found with Google Search."). If `searchEntryHtml` is set,
                                       // it must be displayed (WKWebView) per Google's grounding terms.

// Explain (stream, one event per member)
var texts: [String: String] = [:]
for try await event in pear.explain(productKey: scan.product.key, household: household) {
    texts[event.memberId, default: ""] += event.text
}

// Onboarding helpers
let chips = try await pear.parseProfile(text: "Mia is allergic to peanuts and sesame", language: "en")
let suggestions = try await pear.kbSearch(q: "pea", lang: "vi", limit: 10)
```

Reading a result:

```swift
scan.headline.text                       // in scan.headline.language
for v in scan.verdicts.sorted(by: { $0.state.priority > $1.state.priority }) {
    let name = household.member(id: v.memberId)?.name ?? v.memberId
    // v.state: .ripe .easy .skip .allergy .diet .unsure (.unknown(raw) for future values)
    // v.label: the English chip word from the backend ("Allergy", "May contain", "Easy does it", ...)
    // v.reasons: [Reason]; reason.text is already in the member's language; reason.matched = words to highlight
}
scan.product.lessSure / askForIngredients   // show "Snap the ingredients" when true
scan.product.imageURL                       // URL?
```

`PearState`, `PearSeverity`, `PearSource`, `PearIntent`, `PearAskNext`, `PearErrorCode`,
`PearNextAction` and the other enums all decode unknown future values as `.unknown(String)`,
so a backend addition never breaks decoding. A `switch` needs a `default:` or an
`.unknown` branch. `PearAllergyLevel`: `.strict` is the "Strict" toggle and `.sensitive`
is the "Ingredients only" toggle. `PearAgeGroup.knownCases` and the other `knownCases` lists
feed pickers.

## 4. Recommended screen flows

1. **Launch:** `Task { _ = try? await pear.health() }`. Do it again when the camera opens.
   It warms Cloud Run and the phone's TLS connection.
2. **Barcode:** `scanBarcode` shows the verdict sheet. Right after the ScanResult arrives,
   call `picks(productKey:)` (it is usually already prefetched), then
   `prices(productKeys:)` with the first 3 pick keys. Prices fill in place; never cover the
   verdict with a spinner. On `not_found`, `nextAction` is `.takePhoto`: open the label camera.
3. **Photo:** shrink with `PearImageUpload`, then `scanPhoto`. If the result has
   `askForIngredients`, or the error is `too_blurry`/`unreadable` (`.retake`), ask for a
   clearer ingredients photo.
4. **Ask:** hold to dictate with SFSpeechRecognizer, using the asker's first language as the
   locale. On release, call `ask(text:language:lastProductKey:household:askerId:)` with
   `lastProductKey` = the product on screen. Then:
   - `reply.next == .scanNeeded`: open the camera.
   - `reply.scanResult`: show it as the new result.
   - `reply.picks`: filled when the intent is `.alternatives`.
   - Always speak the answer:
     ```swift
     let u = AVSpeechUtterance(string: reply.answer.text)
     u.voice = AVSpeechSynthesisVoice(language: reply.answer.language)   // "vi", "es", "en"
     synthesizer.speak(u)                                                // keep a strong ref to the synthesizer
     ```
5. **Explain (P1):** stream into per-member text views. Cancelling the Task cancels the
   request.

`Tests/UsageSample.swift` holds a compile-checked `@MainActor @Observable` view model with
all of these flows.

## 5. Errors: switch on the next action

Every method throws `PearAPIError`:

- `.api(code:message:next:requestId:httpStatus:)`: the backend's `{"error": {...}}`, or a
  non-JSON non-2xx response (e.g. a Cloud Run 502), mapped from its HTTP status.
- `.decoding(underlying:bodyPreview:requestId:)`: 2xx, but the body didn't match. Treat it
  as a bug and send the request id to the backend owner.
- `.transport(URLError)`: offline, timeout, TLS, or cancelled.

```swift
do {
    result = try await pear.scanBarcode(code: code, household: household)
} catch let error as PearAPIError {
    if error.isCancelled { return }
    banner = error.userMessage                 // backend message, or a short client sentence
    switch error.nextAction {
    case .takePhoto?: openLabelCamera()        // not_found barcode
    case .retake?:    reopenCamera()           // too_blurry / unreadable photo
    case .retry?:     showRetryButton()        // rate_limited, internal, unavailable, timeout, offline
    case .signIn?:    presentSignIn()          // unauthorized / expired token
    case .unknown?, nil: break                 // bad_request (a bug) or a future action
    }
    print(error)                               // developer detail incl. rid (never show this)
}
```

| code | HTTP | next |
|---|---|---|
| not_found | 404 | take_photo |
| unreadable, too_blurry | 422 | retake |
| rate_limited | 429 | retry |
| internal | 500 | retry |
| unavailable | 503 | retry |
| unauthorized | 401 | sign_in |
| bad_request | 400 | none (a client bug: fix the request) |

## 6. Debugging

- **Request ids:** every call sends `X-Request-ID` (16 hex characters). The backend echoes it
  in the response header and in `request_id`, and logs one `request` line with the same id,
  status and `timings_ms`. `error.requestId`, `scanResult.requestId` and
  `askResponse.requestId` hold it. Search the server log for it.
- **DEBUG console:** one line per request:
  `[Pear] POST /v1/scan/barcode 200 412ms rid=3f9a0c1d2b4e5f60 timing=cache_get;dur=0, lookup;dur=380, rules;dur=6, total;dur=410`
  (`timing` is the Server-Timing header, per stage).
- **Debug flag:** `debug: true` sends `X-Pear-Debug: 1`. The backend then fills
  `scanResult.debug` / `askResponse.debug` (print `debug?.description` for pretty JSON) and
  `verdict.score`. Error bodies carry a `debug` block too; DEBUG builds print it under the
  request line (`[Pear]   error debug: {...}`).
- **Overlay:** pass `observer: { record in ... }` to `init` to get a `PearRequestRecord`
  (method, path, status, ms, rid, Server-Timing, error code) per request, for an in-app
  debug sheet.
- **Raw access:** `try await pear.send(endpoint, as: T.self)` returns `PearResponse<T>` with
  `requestId`, `httpStatus`, `durationMs` and the parsed `serverTiming`.
- **Timings:** `scanResult.timingsMs["lookup"]` and friends. The keys stay snake_case: the kit
  never uses `.convertFromSnakeCase`.
- **Mock knobs:** `pear.mocking(["error": "rate_limited"])` lets you test every error screen
  without breaking the server. The `X-Pear-Mock-Fixture` response header names the file
  that was served.
- **Decoding failures** print `bodyPreview`. Run `contracts/swift/check.sh` to see whether
  the fixtures still decode. A `DRIFT` line means the backend added a key the Swift models
  don't have yet.

## 7. Keeping the kit in sync

```sh
contracts/swift/check.sh            # after any change to contracts.py, fixtures or the kit
contracts/swift/check.sh --strict   # also fail on DRIFT (fixture keys the Swift models drop)

# Optional: smoke-test the real client against a running backend (mock + live paths)
cd api && PEAR_STORE=memory .venv/bin/uvicorn main:app --port 8080 &
PEAR_BASE_URL=http://127.0.0.1:8080 contracts/swift/check.sh
# In a sandbox where URLSession has no network, add PEAR_SMOKE_TRANSPORT=curl
```

Contract to Swift mapping: `State` → `PearState`, `Severity` → `PearSeverity`, `Source` →
`PearSource`, `PhotoKind` → `PearPhotoKind`, `DataConfidence` → `PearDataConfidence`,
`Category` → `PearCategory`, `ProductType` → `PearProductType`, `AllergyLevel` →
`PearAllergyLevel`, `AgeGroup` → `PearAgeGroup`, `MemberType` → `PearMemberType`, `Intent`
→ `PearIntent`, `AskNext` → `PearAskNext`, `ErrorCode` → `PearErrorCode` (`internal` is
`.internalError`), `ErrorNext` → `PearNextAction`, `matched_by` → `PearMatchedBy`,
`Nutrition.basis` → `PearNutritionBasis` (`"100g"` is `.per100g`), `ProfileChip.group` →
`PearProfileGroup`, `Price.source` → `PearPriceSource`, `dict[str, Any]` → `PearJSONValue`.
Every model keeps the contract name; properties are camelCase with explicit snake_case
`CodingKeys`.

Decoding follows Pydantic. A field without a default in `contracts.py` is required.
A field with a default decodes with `decodeIfPresent` and falls back to the same default.
Household/Member encode every contract key, including `"species": null` and
`"avoid_dyes": null`. Request bodies leave out nil optionals.
