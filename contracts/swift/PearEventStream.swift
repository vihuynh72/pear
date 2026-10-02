//  PearEventStream.swift
//  Pear Swift kit — Server-Sent Events for POST /v1/explain.
//
//  What this file does
//  - `PearSSEParser`: a pure line-by-line SSE parser (event:, data:, blank-line dispatch,
//    ":" comments). Pure so Tests/DecodeFixtures.swift can test it without a server.
//  - `PearAPIClient.explain(productKey:household:memberIds:)`: streams `ExplainEvent`s
//    ({"member_id", "language", "text"}) read with URLSession.bytes(for:). The stream
//    finishes on `event: done` (or `data: [DONE]`, or the server closing the stream);
//    `event: error` with an error body throws `PearAPIError.api`.
//    A JSON (non-SSE) response — e.g. a mock that returns `[ExplainEvent]` — is accepted too.
//  - Cancelling the consuming Task (or dropping the stream) cancels the HTTP request.
//
//  How to debug
//  - DEBUG builds log one line when the stream ends: `[Pear] POST /v1/explain 200 2311ms rid=...`.
//  - Reproduce with curl -N: the raw stream should look like
//        data: {"member_id": "m_mia", "language": "en", "text": "..."}
//
//        event: done
//        data: {}
//  - A `.decoding` error's bodyPreview is the exact `data:` payload that failed.

import Foundation

// MARK: - Parser

nonisolated struct PearSSEParser: Sendable {
    enum Output: Sendable, Equatable {
        /// A dispatched event. `name` is nil for the default "message" event.
        case event(name: String?, data: String)
        /// `event: done` or `data: [DONE]`: the stream is complete.
        case done
    }

    private var eventName: String?
    private var dataLines: [String] = []
    /// True after `.done` was returned; later lines are ignored.
    private(set) var isDone = false

    init() {}

    /// Feed one line (without the trailing newline). Returns 0..n outputs.
    mutating func feed(_ rawLine: String) -> [Output] {
        guard !isDone else { return [] }
        let line = rawLine.hasSuffix("\r") ? String(rawLine.dropLast()) : rawLine
        if line.isEmpty { return flush() }
        if line.hasPrefix(":") { return [] }  // comment / keep-alive

        let field: Substring
        var value: Substring
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            value = line[line.index(after: colon)...]
            if value.hasPrefix(" ") { value = value.dropFirst() }
        } else {
            field = Substring(line)
            value = ""
        }

        switch field {
        case "event":
            var out = flush()  // a new event name starts a new event
            let name = value.trimmingCharacters(in: .whitespaces)
            if name == "done" { out.append(.done); isDone = true; return out }
            eventName = name.isEmpty ? nil : name
            return out
        case "data":
            if value.trimmingCharacters(in: .whitespaces) == "[DONE]" {
                var out = flush()
                out.append(.done)
                isDone = true
                return out
            }
            // Servers that forget the blank line between events: if the pending data is
            // already a complete JSON value, dispatch it before starting the next one.
            var out: [Output] = []
            if !dataLines.isEmpty, Self.isCompleteJSON(dataLines.joined(separator: "\n")) {
                out = flush(keepName: true)
            }
            dataLines.append(String(value))
            return out
        default:
            return []  // id:, retry:, unknown fields
        }
    }

    /// Dispatch whatever is pending (call at end of stream too).
    mutating func flush(keepName: Bool = false) -> [Output] {
        defer {
            dataLines = []
            if !keepName { eventName = nil }
        }
        guard !isDone, !dataLines.isEmpty else { return [] }
        let name = (eventName == "message") ? nil : eventName
        return [.event(name: name, data: dataLines.joined(separator: "\n"))]
    }

    private static func isCompleteJSON(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        guard t.hasPrefix("{") || t.hasPrefix("[") else { return false }
        return (try? JSONSerialization.jsonObject(with: Data(t.utf8), options: [.fragmentsAllowed])) != nil
    }
}

// MARK: - Explain stream

nonisolated extension PearAPIClient {
    /// POST /v1/explain — streams one `ExplainEvent` per member (per language chunk).
    ///
    ///     for try await event in client.explain(productKey: key, household: household) {
    ///         texts[event.memberId, default: ""] += event.text
    ///     }
    func explain(productKey: String, household: Household, memberIds: [String]? = nil,
                 timeout: TimeInterval? = nil) -> AsyncThrowingStream<ExplainEvent, any Error> {
        let body = ProductKeyRequest(productKey: productKey, household: household, memberIds: memberIds)
        let endpoint: PearEndpoint
        do {
            var ep = try jsonEndpoint("/v1/explain", body, timeout ?? timeouts.explain)
            ep.accept = "text/event-stream"
            endpoint = ep
        } catch {
            return AsyncThrowingStream { $0.finish(throwing: error) }
        }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    try await self.runEventStream(endpoint) { _ = continuation.yield($0) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Opens the SSE request and calls `yield` for each ExplainEvent until done.
    func runEventStream(_ endpoint: PearEndpoint, yield: @Sendable (ExplainEvent) -> Void) async throws {
        let rid = Self.newRequestId()
        let request = await makeRequest(endpoint, requestId: rid)
        let started = ContinuousClock.now
        var meta = (status: 0, requestId: rid, serverTiming: String?.none)
        do {
            let (bytes, response) = try await session.bytes(for: request)
            meta = Self.meta(response, fallbackRequestId: rid)
            let contentType = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Type") ?? ""

            guard (200..<300).contains(meta.status) else {
                let data = try await Self.collect(bytes, limit: 64 * 1024)
                throw Self.apiError(status: meta.status, data: data, requestId: meta.requestId)
            }
            if contentType.contains("application/json") {
                let data = try await Self.collect(bytes, limit: 2 * 1024 * 1024)
                for event in try Self.decodeJSONEvents(data, requestId: meta.requestId) { yield(event) }
            } else {
                try await Self.readSSE(bytes, requestId: meta.requestId, status: meta.status, yield: yield)
            }
            report(endpoint, status: meta.status, started: started, requestId: meta.requestId,
                   serverTiming: meta.serverTiming, errorCode: nil)
        } catch let error as PearAPIError {
            report(endpoint, status: meta.status == 0 ? nil : meta.status, started: started,
                   requestId: meta.requestId, serverTiming: meta.serverTiming,
                   errorCode: error.code?.rawValue ?? (error.isCancelled ? "transport.cancelled" : "decoding"))
            throw error
        } catch {
            let urlError = Self.urlError(from: error)
            report(endpoint, status: meta.status == 0 ? nil : meta.status, started: started,
                   requestId: meta.requestId, serverTiming: meta.serverTiming,
                   errorCode: "transport.\(urlError.code.rawValue)")
            throw PearAPIError.transport(urlError)
        }
    }

    /// Reads raw bytes and splits on "\n" ourselves (AsyncLineSequence drops the blank
    /// lines that delimit SSE events).
    static func readSSE(_ bytes: URLSession.AsyncBytes, requestId: String, status: Int,
                        yield: @Sendable (ExplainEvent) -> Void) async throws {
        var parser = PearSSEParser()
        var buffer: [UInt8] = []
        buffer.reserveCapacity(1024)

        func handle(_ outputs: [PearSSEParser.Output]) throws -> Bool {
            for output in outputs {
                switch output {
                case .done:
                    return true
                case let .event(name, data):
                    if let event = try Self.interpret(name: name, data: data, requestId: requestId, status: status) {
                        yield(event)
                    }
                }
            }
            return false
        }

        for try await byte in bytes {
            if byte == UInt8(ascii: "\n") {
                let line = String(decoding: buffer, as: UTF8.self)
                buffer.removeAll(keepingCapacity: true)
                if try handle(parser.feed(line)) { return }
            } else {
                buffer.append(byte)
            }
        }
        if !buffer.isEmpty, try handle(parser.feed(String(decoding: buffer, as: UTF8.self))) { return }
        _ = try handle(parser.flush())
    }

    /// One dispatched SSE event -> ExplainEvent, a thrown error, or nil (ignored event).
    static func interpret(name: String?, data: String, requestId: String, status: Int) throws -> ExplainEvent? {
        let payload = Data(data.utf8)
        switch name {
        case nil, "explain", "event", "delta", "chunk":
            if let event = try? JSONDecoder().decode(ExplainEvent.self, from: payload) { return event }
            if let err = try? JSONDecoder().decode(ErrorResponse.self, from: payload) {
                throw PearAPIError.api(code: err.error.code.rawValue, message: err.error.message,
                                       next: err.error.next?.rawValue,
                                       requestId: err.error.requestId ?? requestId, httpStatus: status)
            }
            do {
                return try JSONDecoder().decode(ExplainEvent.self, from: payload)
            } catch {
                throw PearAPIError.decoding(underlying: error, bodyPreview: preview(payload), requestId: requestId)
            }
        case "error":
            if let err = try? JSONDecoder().decode(ErrorResponse.self, from: payload) {
                throw PearAPIError.api(code: err.error.code.rawValue, message: err.error.message,
                                       next: err.error.next?.rawValue,
                                       requestId: err.error.requestId ?? requestId, httpStatus: status)
            }
            if let body = try? JSONDecoder().decode(ErrorBody.self, from: payload) {
                throw PearAPIError.api(code: body.code.rawValue, message: body.message, next: body.next?.rawValue,
                                       requestId: body.requestId ?? requestId, httpStatus: status)
            }
            throw PearAPIError.api(code: PearErrorCode.internalError.rawValue,
                                   message: "Pear couldn't finish the explanation. Try again?",
                                   next: PearNextAction.retry.rawValue, requestId: requestId, httpStatus: status)
        default:
            return nil  // additive event types (start, ping, ...) are ignored
        }
    }

    /// JSON fallback: `[ExplainEvent]`, `{"events": [...]}` or one `ExplainEvent`.
    static func decodeJSONEvents(_ data: Data, requestId: String) throws -> [ExplainEvent] {
        struct Wrapped: Decodable { let events: [ExplainEvent] }
        let decoder = JSONDecoder()
        if let list = try? decoder.decode([ExplainEvent].self, from: data) { return list }
        if let wrapped = try? decoder.decode(Wrapped.self, from: data) { return wrapped.events }
        do {
            return [try decoder.decode(ExplainEvent.self, from: data)]
        } catch {
            throw PearAPIError.decoding(underlying: error, bodyPreview: preview(data), requestId: requestId)
        }
    }

    static func collect(_ bytes: URLSession.AsyncBytes, limit: Int) async throws -> Data {
        var data = Data()
        for try await byte in bytes {
            data.append(byte)
            if data.count >= limit { break }
        }
        return data
    }
}
