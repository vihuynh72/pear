//  PearAPIClient.swift
//  Pear Swift kit — async/await client for the Pear API (Foundation only, Sendable).
//
//  What this file does
//  - One method per endpoint (plan 7.6 + addendum 6):
//      health, scanBarcode, scanPhoto, ask, askAudio, picks, prices, explain (SSE, in
//      PearEventStream.swift), parseProfile, kbSearch.
//  - .live hits /v1/... and /health; .mock hits /mock/v1/... and /mock/health (fixtures
//    served by the backend, api/routes/mock.py) so the UI can be built before the pipeline
//    works. `mocking(["error": "too_blurry"])` returns a copy that adds the mock knobs
//    (?fixture=, ?error=, ?delay_ms=, ?event_ms=) to every call — use it to test error UI.
//  - Headers on every call: X-Request-ID (16 hex chars of a UUID), Accept,
//    Authorization: Bearer <Firebase ID token> when `tokenProvider` returns one (no provider
//    or nil token = guest mode), X-Pear-Debug: 1 when `debug` is on.
//  - Every failure is a `PearAPIError` (.api / .decoding / .transport) that carries the
//    request id; `error.nextAction` tells the UI what to offer (take_photo / retake / retry /
//    sign_in).
//
//  How to debug
//  - DEBUG builds print one line per request:
//      [Pear] POST /v1/scan/barcode 200 412ms rid=3f9a0c1d2b4e5f60 timing=lookup;dur=380, rules;dur=6
//    Search the backend logs (or Cloud Logging) for the rid; the backend's `request` log line
//    has the same id, status and timings_ms.
//  - `debug: true` makes the backend fill `debug` in ScanResult/AskResponse (and show
//    `score` in verdicts). Print `result.debug?.description`.
//  - Pass an `observer` to collect `PearRequestRecord`s for an in-app debug overlay.
//  - `send(_:as:)` returns the full `PearResponse` (status, request id, Server-Timing) for
//    any endpoint when the convenience methods hide too much.

import Foundation

// MARK: - Errors

nonisolated enum PearAPIError: Error, LocalizedError, CustomStringConvertible {
    /// The backend answered with an error body ({"error": {...}}) or a non-2xx status.
    case api(code: String, message: String, next: String?, requestId: String?, httpStatus: Int)
    /// 2xx, but the body did not match the contract. `bodyPreview` = first 800 chars.
    case decoding(underlying: any Error, bodyPreview: String, requestId: String?)
    /// No HTTP response: offline, timeout, TLS, cancelled (URLError.cancelled).
    case transport(URLError)

    /// What the UI should offer next. nil = nothing to offer (e.g. the call was cancelled).
    var nextAction: PearNextAction? {
        switch self {
        case let .api(code, _, next, _, status):
            if let next, !next.isEmpty { return PearNextAction(rawValue: next) }
            switch PearErrorCode(rawValue: code) {
            case .unauthorized: return .signIn
            case .rateLimited, .internalError, .unavailable: return .retry
            default: return status == 401 ? .signIn : (status >= 500 || status == 429 ? .retry : nil)
            }
        case .decoding:
            return .retry
        case .transport(let e):
            return e.code == .cancelled ? nil : .retry
        }
    }

    /// Contract error code for `.api`, nil otherwise.
    var code: PearErrorCode? {
        if case let .api(code, _, _, _, _) = self { return PearErrorCode(rawValue: code) }
        return nil
    }

    var requestId: String? {
        switch self {
        case let .api(_, _, _, rid, _): rid
        case let .decoding(_, _, rid): rid
        case .transport: nil
        }
    }

    var httpStatus: Int? {
        if case let .api(_, _, _, _, status) = self { return status }
        return nil
    }

    var isCancelled: Bool {
        if case .transport(let e) = self { return e.code == .cancelled }
        return false
    }

    /// Short English sentence for the UI. `.api` uses the backend's message.
    var userMessage: String {
        switch self {
        case let .api(_, message, _, _, _):
            return message
        case .decoding:
            return "Pear sent an answer this version of the app can't read. Try again?"
        case .transport(let e):
            switch e.code {
            case .timedOut: return "Pear is taking too long to answer. Try again?"
            case .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed:
                return "You're offline. Check your connection and try again."
            case .cancelled: return "Cancelled."
            default: return "Can't reach Pear right now. Try again?"
            }
        }
    }

    var errorDescription: String? { userMessage }

    /// Developer detail (includes the request id); never show this to users.
    var description: String {
        switch self {
        case let .api(code, message, next, rid, status):
            return "PearAPIError.api(\(code), http \(status), next=\(next ?? "nil"), rid=\(rid ?? "nil")): \(message)"
        case let .decoding(underlying, preview, rid):
            return "PearAPIError.decoding(rid=\(rid ?? "nil")): \(underlying)\nbody: \(preview)"
        case .transport(let e):
            return "PearAPIError.transport(\(e.code.rawValue)): \(e.localizedDescription)"
        }
    }
}

// MARK: - Request records / response envelope

/// One finished request, for logs and debug overlays.
nonisolated struct PearRequestRecord: Sendable, CustomStringConvertible {
    let method: String
    let path: String
    /// nil when there was no HTTP response (transport error).
    let status: Int?
    let durationMs: Int
    let requestId: String
    /// Raw Server-Timing header: "lookup;dur=420, rules;dur=6, total;dur=610"
    let serverTiming: String?
    /// Contract error code, "decoding", or "transport.<URLError code>"; nil on success.
    let errorCode: String?

    var description: String {
        var line = "[Pear] \(method) \(path) \(status.map(String.init) ?? "---") \(durationMs)ms rid=\(requestId)"
        if let errorCode { line += " error=\(errorCode)" }
        if let serverTiming, !serverTiming.isEmpty { line += " timing=\(serverTiming)" }
        return line
    }
}

/// A decoded response plus the HTTP metadata the convenience methods drop.
nonisolated struct PearResponse<Value: Sendable>: Sendable {
    let value: Value
    let requestId: String
    let httpStatus: Int
    let durationMs: Int
    /// Parsed Server-Timing header, e.g. ["lookup": 420, "total": 610].
    let serverTiming: [String: Int]
}

/// Everything needed to build one request. Kit internal; use the client methods.
nonisolated struct PearEndpoint: Sendable {
    var method: String
    /// Path without the mock prefix, e.g. "/v1/scan/barcode".
    var path: String
    var query: [URLQueryItem] = []
    var body: Data? = nil
    var contentType: String? = nil
    var accept: String = "application/json"
    var timeout: TimeInterval
    /// Prefix the path with /mock in .mock mode (every endpoint has a mock twin).
    var mockable: Bool = true
}

// MARK: - Client

nonisolated final class PearAPIClient: Sendable {
    enum Mode: String, Sendable {
        /// Real pipeline: /v1/...
        case live
        /// Backend fixtures: /mock/v1/...
        case mock
    }

    /// Per-endpoint timeouts in seconds (URLRequest.timeoutInterval; for SSE it is the idle gap).
    struct Timeouts: Sendable {
        var health: TimeInterval = 5
        var barcode: TimeInterval = 8
        var photo: TimeInterval = 15
        var ask: TimeInterval = 12
        var askAudio: TimeInterval = 20
        var picks: TimeInterval = 8
        var prices: TimeInterval = 10
        var explain: TimeInterval = 30
        var profile: TimeInterval = 10
        var kb: TimeInterval = 5

        init() {}
    }

    /// Returns the current Firebase ID token, or nil for guest mode.
    typealias TokenProvider = @Sendable () async -> String?
    /// Called once per finished request (success or failure), on an arbitrary thread.
    typealias Observer = @Sendable (PearRequestRecord) -> Void

    let baseURL: URL
    let mode: Mode
    let debug: Bool
    let session: URLSession
    let timeouts: Timeouts
    let tokenProvider: TokenProvider?
    let observer: Observer?
    /// Extra query parameters sent only in .mock mode, e.g. ["fixture": "scan_photo.front_only"],
    /// ["error": "too_blurry"], ["delay_ms": "1500"].
    let mockParameters: [String: String]

    init(
        baseURL: URL,
        mode: Mode = .live,
        tokenProvider: TokenProvider? = nil,
        debug: Bool = false,
        session: URLSession = .shared,
        timeouts: Timeouts = Timeouts(),
        observer: Observer? = nil,
        mockParameters: [String: String] = [:]
    ) {
        self.baseURL = baseURL
        self.mode = mode
        self.tokenProvider = tokenProvider
        self.debug = debug
        self.session = session
        self.timeouts = timeouts
        self.observer = observer
        self.mockParameters = mockParameters
    }

    /// A copy with different mock knobs (only used in .mock mode):
    ///     try await client.mocking(["error": "too_blurry"]).scanPhoto(...)
    func mocking(_ parameters: [String: String]) -> PearAPIClient {
        PearAPIClient(baseURL: baseURL, mode: mode, tokenProvider: tokenProvider, debug: debug, session: session,
                      timeouts: timeouts, observer: observer, mockParameters: parameters)
    }

    // MARK: Endpoints

    /// GET /health (mock: /mock/health) — call on launch and when the camera opens
    /// (warms Cloud Run + TLS). Ignore failures there; it is only a warmup.
    func health(deep: Bool = false, timeout: TimeInterval? = nil) async throws -> HealthResponse {
        let ep = PearEndpoint(method: "GET", path: "/health",
                              query: deep ? [URLQueryItem(name: "deep", value: "1")] : [],
                              timeout: timeout ?? timeouts.health)
        return try await send(ep, as: HealthResponse.self).value
    }

    /// POST /v1/scan/barcode. `memberIds` = "Who's this for?" toggle (nil = everyone).
    func scanBarcode(code: String, household: Household, memberIds: [String]? = nil,
                     timeout: TimeInterval? = nil) async throws -> ScanResult {
        let body = BarcodeRequest(code: code, household: household, memberIds: memberIds)
        return try await sendIdentified(jsonEndpoint("/v1/scan/barcode", body, timeout ?? timeouts.barcode))
    }

    /// POST /v1/scan/photo (multipart). `image` = ingredients label JPEG (see PearImageUpload).
    func scanPhoto(image: Data, frontImage: Data? = nil, household: Household, memberIds: [String]? = nil,
                   timeout: TimeInterval? = nil) async throws -> ScanResult {
        var form = PearMultipart()
        form.addFile("image", filename: "label.jpg", mimeType: "image/jpeg", data: image)
        if let frontImage {
            form.addFile("front_image", filename: "front.jpg", mimeType: "image/jpeg", data: frontImage)
        }
        form.addField("household", try Self.jsonString(household))
        if let memberIds { form.addField("member_ids", try Self.jsonString(memberIds)) }
        return try await sendIdentified(formEndpoint("/v1/scan/photo", form, timeout ?? timeouts.photo))
    }

    /// POST /v1/ask with the JSON body (primary path: dictated or typed text).
    /// Always pass `lastProductKey` (the product on screen) so "this" resolves without a search.
    func ask(text: String, language: String? = nil, lastProductKey: String? = nil, household: Household,
             askerId: String? = nil, timeout: TimeInterval? = nil) async throws -> AskResponse {
        let body = AskRequest(text: text, language: language, lastProductKey: lastProductKey,
                              household: household, askerId: askerId)
        return try await sendIdentified(jsonEndpoint("/v1/ask", body, timeout ?? timeouts.ask))
    }

    /// POST /v1/ask multipart fallback (recorded m4a, for locales SFSpeechRecognizer lacks).
    func askAudio(audio: Data, mimeType: String = "audio/mp4", language: String? = nil,
                  lastProductKey: String? = nil, household: Household, askerId: String? = nil,
                  timeout: TimeInterval? = nil) async throws -> AskResponse {
        var form = PearMultipart()
        form.addFile("audio", filename: Self.audioFilename(mimeType), mimeType: mimeType, data: audio)
        form.addField("household", try Self.jsonString(household))
        if let language { form.addField("language", language) }
        if let lastProductKey { form.addField("last_product_key", lastProductKey) }
        if let askerId { form.addField("asker_id", askerId) }
        return try await sendIdentified(formEndpoint("/v1/ask", form, timeout ?? timeouts.askAudio))
    }

    /// POST /v1/picks — call as soon as a ScanResult arrives (the backend prefetched them).
    func picks(productKey: String, household: Household, memberIds: [String]? = nil,
               timeout: TimeInterval? = nil) async throws -> PicksResponse {
        let body = ProductKeyRequest(productKey: productKey, household: household, memberIds: memberIds)
        return try await sendIdentified(jsonEndpoint("/v1/picks", body, timeout ?? timeouts.picks))
    }

    /// POST /v1/prices — best effort; missing prices come back as nil values.
    func prices(productKeys: [String], country: String = "US",
                timeout: TimeInterval? = nil) async throws -> PricesResponse {
        let body = PricesRequest(productKeys: productKeys, country: country)
        return try await sendIdentified(jsonEndpoint("/v1/prices", body, timeout ?? timeouts.prices))
    }

    /// POST /v1/profile/parse — free text ("Mia is allergic to peanuts") -> profile chips.
    func parseProfile(text: String, language: String? = nil,
                      timeout: TimeInterval? = nil) async throws -> ProfileParseResponse {
        let body = ProfileParseRequest(text: text, language: language)
        return try await sendIdentified(jsonEndpoint("/v1/profile/parse", body, timeout ?? timeouts.profile))
    }

    /// GET /v1/kb/search?q=&lang=&limit= — autocomplete for allergies, conditions, diets, ...
    func kbSearch(q: String, lang: String = "en", limit: Int = 10,
                  timeout: TimeInterval? = nil) async throws -> KbSearchResponse {
        let ep = PearEndpoint(method: "GET", path: "/v1/kb/search",
                              query: [URLQueryItem(name: "q", value: q),
                                      URLQueryItem(name: "lang", value: lang),
                                      URLQueryItem(name: "limit", value: String(limit))],
                              timeout: timeout ?? timeouts.kb)
        return try await send(ep, as: KbSearchResponse.self).value
    }

    // MARK: Core

    /// Sends any endpoint and decodes `Value`. Always throws `PearAPIError`.
    @concurrent
    func send<Value: Decodable & Sendable>(_ endpoint: PearEndpoint, as type: Value.Type = Value.self)
        async throws -> PearResponse<Value> {
        let rid = Self.newRequestId()
        let request = await makeRequest(endpoint, requestId: rid)
        let started = ContinuousClock.now
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            let urlError = Self.urlError(from: error)
            report(endpoint, status: nil, started: started, requestId: rid, serverTiming: nil,
                   errorCode: "transport.\(urlError.code.rawValue)")
            throw PearAPIError.transport(urlError)
        }
        let meta = Self.meta(response, fallbackRequestId: rid)
        guard (200..<300).contains(meta.status) else {
            let error = Self.apiError(status: meta.status, data: data, requestId: meta.requestId)
            report(endpoint, status: meta.status, started: started, requestId: meta.requestId,
                   serverTiming: meta.serverTiming, errorCode: error.code?.rawValue ?? "http")
            #if DEBUG
            if debug, let body = try? JSONDecoder().decode(ErrorResponse.self, from: data), let detail = body.debug {
                print("[Pear]   error debug: \(detail)")
            }
            #endif
            throw error
        }
        do {
            let value = try JSONDecoder().decode(Value.self, from: data)
            let ms = report(endpoint, status: meta.status, started: started, requestId: meta.requestId,
                            serverTiming: meta.serverTiming, errorCode: nil)
            return PearResponse(value: value, requestId: meta.requestId, httpStatus: meta.status,
                                durationMs: ms, serverTiming: Self.parseServerTiming(meta.serverTiming))
        } catch {
            report(endpoint, status: meta.status, started: started, requestId: meta.requestId,
                   serverTiming: meta.serverTiming, errorCode: "decoding")
            throw PearAPIError.decoding(underlying: error, bodyPreview: Self.preview(data),
                                        requestId: meta.requestId)
        }
    }

    /// `send` + fill `request_id` from the X-Request-ID header when the body has none.
    func sendIdentified<Value: Decodable & PearRequestIdentified>(_ endpoint: PearEndpoint) async throws -> Value {
        let response = try await send(endpoint, as: Value.self)
        var value = response.value
        if value.requestId == nil { value.requestId = response.requestId }
        return value
    }

    // MARK: Request building (kit internal)

    func jsonEndpoint(_ path: String, _ body: some Encodable, _ timeout: TimeInterval) throws -> PearEndpoint {
        let data: Data
        do {
            data = try JSONEncoder().encode(body)
        } catch {
            throw PearAPIError.decoding(underlying: error, bodyPreview: "request body for \(path) could not be encoded",
                                        requestId: nil)
        }
        return PearEndpoint(method: "POST", path: path, body: data, contentType: "application/json", timeout: timeout)
    }

    func formEndpoint(_ path: String, _ form: PearMultipart, _ timeout: TimeInterval) -> PearEndpoint {
        PearEndpoint(method: "POST", path: path, body: form.finalized(), contentType: form.contentType, timeout: timeout)
    }

    func url(for endpoint: PearEndpoint) -> URL {
        let mocked = endpoint.mockable && mode == .mock
        let path = (mocked ? "/mock" : "") + endpoint.path
        guard var comps = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            return baseURL.appendingPathComponent(path)
        }
        var basePath = comps.percentEncodedPath
        while basePath.hasSuffix("/") { basePath.removeLast() }
        comps.percentEncodedPath = basePath + path
        var query = endpoint.query
        if mocked {
            query += mockParameters.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        if !query.isEmpty {
            comps.percentEncodedQueryItems = query.map {
                URLQueryItem(name: Self.queryEncode($0.name), value: $0.value.map(Self.queryEncode))
            }
        }
        return comps.url ?? baseURL.appendingPathComponent(path)
    }

    func makeRequest(_ endpoint: PearEndpoint, requestId: String) async -> URLRequest {
        var req = URLRequest(url: url(for: endpoint), cachePolicy: .reloadIgnoringLocalCacheData,
                             timeoutInterval: endpoint.timeout)
        req.httpMethod = endpoint.method
        req.setValue(requestId, forHTTPHeaderField: "X-Request-ID")
        req.setValue(endpoint.accept, forHTTPHeaderField: "Accept")
        if debug { req.setValue("1", forHTTPHeaderField: "X-Pear-Debug") }
        if let tokenProvider, let token = await tokenProvider(), !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body = endpoint.body {
            req.httpBody = body
            req.setValue(endpoint.contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    /// Logs (DEBUG builds) and notifies the observer. Returns the duration in ms.
    @discardableResult
    func report(_ endpoint: PearEndpoint, status: Int?, started: ContinuousClock.Instant, requestId: String,
                serverTiming: String?, errorCode: String?) -> Int {
        let elapsed = started.duration(to: .now).components
        let ms = Int(elapsed.seconds * 1000 + elapsed.attoseconds / 1_000_000_000_000_000)
        let record = PearRequestRecord(method: endpoint.method, path: url(for: endpoint).path, status: status,
                                       durationMs: ms, requestId: requestId, serverTiming: serverTiming,
                                       errorCode: errorCode)
        #if DEBUG
        print(record.description)
        #endif
        observer?(record)
        return ms
    }

    // MARK: Helpers (kit internal)

    static func newRequestId() -> String {
        String(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(16))
    }

    static func jsonString(_ value: some Encodable) throws -> String {
        do {
            return String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
        } catch {
            throw PearAPIError.decoding(underlying: error, bodyPreview: "form field could not be encoded", requestId: nil)
        }
    }

    static func meta(_ response: URLResponse, fallbackRequestId: String)
        -> (status: Int, requestId: String, serverTiming: String?) {
        let http = response as? HTTPURLResponse
        let rid = http?.value(forHTTPHeaderField: "X-Request-ID").flatMap { $0.isEmpty ? nil : $0 }
        return (http?.statusCode ?? 0, rid ?? fallbackRequestId, http?.value(forHTTPHeaderField: "Server-Timing"))
    }

    static func urlError(from error: any Error) -> URLError {
        if let e = error as? URLError { return e }
        if error is CancellationError { return URLError(.cancelled) }
        return URLError(.unknown, userInfo: [NSUnderlyingErrorKey: error as NSError])
    }

    /// Error body -> `.api`. Non-JSON bodies (Cloud Run 502 page, proxy errors) get a
    /// code derived from the HTTP status.
    static func apiError(status: Int, data: Data, requestId: String) -> PearAPIError {
        if let body = try? JSONDecoder().decode(ErrorResponse.self, from: data) {
            return .api(code: body.error.code.rawValue, message: body.error.message,
                        next: body.error.next?.rawValue, requestId: body.error.requestId ?? requestId,
                        httpStatus: status)
        }
        let code: PearErrorCode = switch status {
        case 401, 403: .unauthorized
        case 404: .notFound
        case 429: .rateLimited
        case 502, 503, 504: .unavailable
        case 500...: .internalError
        default: .badRequest
        }
        let next: PearNextAction? = switch code {
        case .unauthorized: .signIn
        case .rateLimited, .unavailable, .internalError: .retry
        default: nil
        }
        let message = code == .unauthorized ? "Please sign in again." : "Pear had a problem (HTTP \(status)). Try again?"
        return .api(code: code.rawValue, message: message, next: next?.rawValue, requestId: requestId, httpStatus: status)
    }

    static func preview(_ data: Data, limit: Int = 800) -> String {
        let text = String(decoding: data.prefix(limit), as: UTF8.self)
        return data.count > limit ? text + "…(\(data.count) bytes)" : text
    }

    /// "lookup;dur=420, rules;dur=6.2" -> ["lookup": 420, "rules": 6]
    static func parseServerTiming(_ header: String?) -> [String: Int] {
        guard let header, !header.isEmpty else { return [:] }
        var out: [String: Int] = [:]
        for metric in header.split(separator: ",") {
            let params = metric.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }
            guard let name = params.first, !name.isEmpty else { continue }
            let dur = params.dropFirst().first { $0.hasPrefix("dur=") }.flatMap { Double($0.dropFirst(4)) }
            out[name] = Int((dur ?? 0).rounded())
        }
        return out
    }

    static func audioFilename(_ mimeType: String) -> String {
        switch mimeType.lowercased() {
        case "audio/wav", "audio/x-wav", "audio/wave": "question.wav"
        case "audio/mpeg", "audio/mp3": "question.mp3"
        case "audio/aac": "question.aac"
        case "audio/webm": "question.webm"
        default: "question.m4a"
        }
    }

    /// Percent-encodes a query component; also encodes + & = so "c++" survives.
    static func queryEncode(_ s: String) -> String {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "+&=?#")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }
}
