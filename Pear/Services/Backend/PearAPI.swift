// PearAPI.swift
// Async client for the Pear backend on Cloud Run. Foundation only, no third-party packages.
// Every endpoint, body and timeout here was checked against the live service on October 2, 2026.
//
// Usage:
//   let api = PearAPI.shared                       // live backend
//   let api = PearAPI(useMock: true)               // canned fixtures from /mock/v1/... (same JSON shapes)
//   let result = try await api.scanBarcode("048121253196", household: PearDemo.household)

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

nonisolated final class PearAPI: Sendable {
    static let defaultBaseURL = URL(string: "https://pear-api-73536482701.us-central1.run.app")!
    static let shared = PearAPI()

    let baseURL: URL
    /// true: every call goes to /mock/v1/... (fixtures, no Gemini, instant). Use it to build UI offline.
    let useMock: Bool
    /// Firebase ID token, attached as "Authorization: Bearer <token>". Only needed once the server
    /// runs with AUTH_REQUIRED=1 (it doesn't today). Return nil to send no header.
    let idTokenProvider: (@Sendable () async -> String?)?
    private let session: URLSession

    init(baseURL: URL = PearAPI.defaultBaseURL,
         useMock: Bool = false,
         idTokenProvider: (@Sendable () async -> String?)? = nil,
         session: URLSession? = nil) {
        self.baseURL = baseURL
        self.useMock = useMock
        self.idTokenProvider = idTokenProvider
        if let session {
            self.session = session
        } else {
            let cfg = URLSessionConfiguration.default
            cfg.timeoutIntervalForRequest = 20
            cfg.timeoutIntervalForResource = 30
            cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
            cfg.urlCache = nil
            self.session = URLSession(configuration: cfg)
        }
    }

    // MARK: - Endpoints

    /// Opens the TLS connection and wakes the server. Call on launch, on foreground and when the camera opens.
    func warm() async {
        _ = try? await health()
    }

    /// GET /health -> {"ok": true, "gemini": true, "firestore": true, "version": "0.1"}
    func health() async throws -> Health {
        try await get("health", timeout: 8)
    }

    /// POST /v1/scan/barcode {code, household}. 8 to 14 digits; UPC-A (12) and EAN-13 both work.
    /// Errors: 404 not_found (next "take_photo"), 422 unreadable (next "retake").
    func scanBarcode(_ code: String, household: Household) async throws -> ScanResult {
        struct Body: Encodable { let code: String; let household: Household }
        let digits = code.filter(\.isNumber)
        return try await post("v1/scan/barcode", json: Body(code: digits, household: household), timeout: 10)
    }

    /// POST /v1/scan/photo, multipart: image (ingredient panel JPEG), optional front (JPEG), household (JSON string).
    /// Send JPEGs downscaled to about 1600 px on the long edge (PearImage.jpegForUpload).
    /// Errors: 422 too_blurry or unreadable (next "retake"), 429 rate_limited and 504 (next "retry").
    func scanPhoto(jpeg: Data, frontJPEG: Data? = nil, household: Household) async throws -> ScanResult {
        var form = MultipartForm()
        form.addFile(name: "image", filename: "label.jpg", mimeType: "image/jpeg", data: jpeg)
        if let frontJPEG {
            form.addFile(name: "front", filename: "front.jpg", mimeType: "image/jpeg", data: frontJPEG)
        }
        form.addField(name: "household", value: try Self.jsonString(household))
        return try await post("v1/scan/photo", form: form, timeout: 20)
    }

    /// POST /v1/ask {text, language, last_product_key, household}. Typed or dictated text.
    /// Always pass the key of the product on screen so "this" resolves without a search.
    /// language: "en" | "vi" | "es", or nil to let the server detect it.
    func ask(text: String, language: String? = nil, lastProductKey: String?, household: Household) async throws -> AskResponse {
        struct Body: Encodable {
            let text: String
            let language: String?
            let lastProductKey: String?
            let household: Household
        }
        let body = Body(text: text, language: language, lastProductKey: lastProductKey, household: household)
        return try await post("v1/ask", json: body, timeout: 15)
    }

    /// POST /v1/ask, multipart fallback: audio (m4a, audio/mp4), optional language and last_product_key, household.
    /// Use it only when on-device dictation can't do the asker's language.
    func askAudio(m4a: Data, language: String? = nil, lastProductKey: String?, household: Household) async throws -> AskResponse {
        var form = MultipartForm()
        form.addFile(name: "audio", filename: "question.m4a", mimeType: "audio/mp4", data: m4a)
        if let language { form.addField(name: "language", value: language) }
        if let lastProductKey { form.addField(name: "last_product_key", value: lastProductKey) }
        form.addField(name: "household", value: try Self.jsonString(household))
        return try await post("v1/ask", form: form, timeout: 20)
    }

    /// POST /v1/picks {product_key, household}. Up to 3 food products that fit the whole family.
    /// Prefetched on the server when the scan happened, so this usually returns in under a second.
    func picks(productKey: String, household: Household) async throws -> PicksResponse {
        struct Body: Encodable { let productKey: String; let household: Household }
        return try await post("v1/picks", json: Body(productKey: productKey, household: household), timeout: 10)
    }

    /// POST /v1/prices {product_keys, country}. Best effort: waits up to 6 s on the server, null per key
    /// when nothing was found. Never throws a user-facing error worth showing; hide the price on failure.
    func prices(productKeys: [String], country: String = "US") async throws -> PricesResponse {
        struct Body: Encodable { let productKeys: [String]; let country: String }
        return try await post("v1/prices", json: Body(productKeys: productKeys, country: country), timeout: 12)
    }

    /// GET /v1/kb/search?q=&limit= -> bare JSON array of suggestions (onboarding chips).
    /// The app also ships kb_index.json for instant offline filtering; use this for anything typed that isn't in it.
    func kbSearch(_ query: String, limit: Int = 10) async throws -> [KBSuggestion] {
        try await get("v1/kb/search",
                      query: [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: String(limit))],
                      timeout: 8)
    }

    /// POST /v1/profile/parse {text, language}: free text ("Mia can't have sesame, eczema, vegan") -> chips to confirm.
    func parseProfile(text: String, language: String = "en") async throws -> ProfileParse {
        struct Body: Encodable { let text: String; let language: String }
        return try await post("v1/profile/parse", json: Body(text: text, language: language), timeout: 10)
    }

    // MARK: - Plumbing

    static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }

    static func jsonString<T: Encodable>(_ value: T) throws -> String {
        String(decoding: try encoder().encode(value), as: UTF8.self)
    }

    private func url(_ path: String, query: [URLQueryItem] = []) -> URL {
        var base = baseURL.absoluteString
        while base.hasSuffix("/") { base.removeLast() }
        var comps = URLComponents(string: base + "/" + (useMock ? "mock/" : "") + path)!
        if !query.isEmpty {
            comps.queryItems = query
            // "+" means a space to the server; encode it so a typed "+" survives.
            comps.percentEncodedQuery = comps.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        }
        return comps.url!
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], timeout: TimeInterval) async throws -> T {
        var req = URLRequest(url: url(path, query: query), timeoutInterval: timeout)
        req.httpMethod = "GET"
        return try await perform(req)
    }

    private func post<T: Decodable, B: Encodable>(_ path: String, json body: B, timeout: TimeInterval) async throws -> T {
        var req = URLRequest(url: url(path), timeoutInterval: timeout)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try Self.encoder().encode(body)
        return try await perform(req)
    }

    private func post<T: Decodable>(_ path: String, form: MultipartForm, timeout: TimeInterval) async throws -> T {
        var req = URLRequest(url: url(path), timeoutInterval: timeout)
        req.httpMethod = "POST"
        req.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = form.finalized()
        return try await perform(req)
    }

    private func perform<T: Decodable>(_ request: URLRequest) async throws -> T {
        var request = request
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let provider = idTokenProvider, let token = await provider(), !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await load(request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            if let body = try? Self.decoder().decode(APIErrorBody.self, from: data) {
                throw PearAPIError.server(status: status, detail: body.error)
            }
            throw PearAPIError.http(status: status, body: String(decoding: data.prefix(400), as: UTF8.self))
        }
        do {
            return try Self.decoder().decode(T.self, from: data)
        } catch {
            throw PearAPIError.decoding(description: String(describing: error),
                                        body: String(decoding: data.prefix(400), as: UTF8.self))
        }
    }

    private func load(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch let error as URLError where error.code == .networkConnectionLost {
            // A pooled connection went stale (common right after the app comes back from the background). Retry once.
            do {
                return try await session.data(for: request)
            } catch let retryError as URLError {
                throw PearAPIError.transport(retryError)
            }
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError {
            throw PearAPIError.transport(error)
        }
    }
}

// MARK: - Errors

nonisolated enum PearAPIError: Error, LocalizedError {
    /// The server's own error JSON: {"error": {"code", "message", "next"}}. Show detail.message as is.
    case server(status: Int, detail: APIErrorDetail)
    /// Non-2xx without the error JSON (proxy page, 401 before sign-in works, etc.).
    case http(status: Int, body: String)
    /// 2xx but the JSON didn't match the models. A bug: log description and body.
    case decoding(description: String, body: String)
    /// No connection, DNS, TLS, timeout.
    case transport(URLError)

    /// Short line for the screen.
    var userMessage: String {
        switch self {
        case .server(_, let detail): return detail.message
        case .http(let status, _): return status == 401 ? "Sign in to keep going." : "Something went wrong. Try again."
        case .decoding: return "Something went wrong. Try again."
        case .transport(let e): return e.code == .timedOut ? "That took too long. Try again." : "No connection. Try again."
        }
    }

    /// What the screen should offer next: "take_photo" (barcode not in the database), "retake" (bad photo or code), "retry".
    var nextAction: String {
        if case .server(_, let detail) = self { return detail.next }
        return "retry"
    }

    /// The server's error code when there is one: not_found | unreadable | too_blurry | rate_limited | internal.
    var code: String? {
        if case .server(_, let detail) = self { return detail.code }
        return nil
    }

    var errorDescription: String? { userMessage }
}

// MARK: - Multipart

nonisolated struct MultipartForm: Sendable {
    let boundary = "pear-\(UUID().uuidString)"
    private var body = Data()

    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    mutating func addField(name: String, value: String) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
        append(value)
        append("\r\n")
    }

    mutating func addFile(name: String, filename: String, mimeType: String, data: Data) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        append("\r\n")
    }

    func finalized() -> Data {
        var out = body
        out.append(Data("--\(boundary)--\r\n".utf8))
        return out
    }

    private mutating func append(_ string: String) {
        body.append(Data(string.utf8))
    }
}
