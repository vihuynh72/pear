//  LiveSmoke.swift
//  Pear Swift kit — call every endpoint through PearAPIClient against a running backend.
//
//  Built and run by ../check.sh when PEAR_BASE_URL is set:
//      cd api && PEAR_STORE=memory .venv/bin/uvicorn main:app --port 8080 &
//      PEAR_BASE_URL=http://127.0.0.1:8080 contracts/swift/check.sh
//  If URLSession cannot reach the network from your shell (sandboxes), add
//  PEAR_SMOKE_TRANSPORT=curl: requests then go through Tests/CurlTransport.swift.
//
//  Mock mode (/mock/v1/*) must fully pass: it proves the real wire format — multipart field
//  names, SSE streaming, error bodies + HTTP statuses, X-Request-ID echo, Server-Timing.
//  Live mode (/v1/*) is informational only (it may need Gemini/network), with one hard rule:
//  a live failure must arrive as a decodable PearAPIError.api, never as .decoding.
//
//  How to debug: each line prints the request id; grep the uvicorn log for it.

import Foundation

final class Records: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [PearRequestRecord] = []
    func add(_ r: PearRequestRecord) { lock.lock(); items.append(r); lock.unlock() }
    var all: [PearRequestRecord] { lock.lock(); defer { lock.unlock() }; return items }
}

@main
@MainActor
struct LiveSmoke {
    static var failures = 0

    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 3, let base = URL(string: args[1]),
              let data = FileManager.default.contents(atPath: args[2]),
              let household = try? JSONDecoder().decode(Household.self, from: data) else {
            print("usage: live-smoke <base_url> <household.example.json>")
            exit(2)
        }
        let records = Records()
        let viaCurl = ProcessInfo.processInfo.environment["PEAR_SMOKE_TRANSPORT"] == "curl"
        let session = viaCurl ? CurlURLProtocol.session() : URLSession.shared
        print("transport: \(viaCurl ? "curl (CurlURLProtocol, test only)" : "URLSession")")
        let mock = PearAPIClient(baseURL: base, mode: .mock, debug: true, session: session, observer: { records.add($0) })
        let live = PearAPIClient(baseURL: base, mode: .live, debug: true, session: session, observer: { records.add($0) })

        print("== mock mode: \(base)/mock")
        await expectOK("health (mock)") { try await mock.health().service }
        await expectOK("scanBarcode sesame") {
            let r = try await mock.scanBarcode(code: "0000000000017", household: household)
            try require(r.product.key == "off:0000000000017" && r.verdicts.count == household.members.count,
                        "unexpected product/verdicts \(r.product.key) \(r.verdicts.count)")
            try require(r.requestId != nil, "request_id missing")
            try require(r.verdict(for: "m_mia")?.state == .allergy, "Mia should be ALLERGY")
            return "\(r.headline.text) rid=\(r.requestId ?? "-")"
        }
        await expectError("scanBarcode unknown -> not_found", code: .notFound, next: .takePhoto, status: 404) {
            _ = try await mock.scanBarcode(code: "1234567890128", household: household)
        }
        let jpeg = Data([0xFF, 0xD8, 0xFF, 0xE0] + Array(repeating: 0, count: 64) + [0xFF, 0xD9])
        await expectOK("scanPhoto (multipart)") {
            let r = try await mock.scanPhoto(image: jpeg, frontImage: jpeg, household: household, memberIds: ["m_linh"])
            return "\(r.product.key) source=\(r.product.source)"
        }
        await expectError("scanPhoto ?error=too_blurry", code: .tooBlurry, next: .retake, status: 422) {
            _ = try await mock.mocking(["error": "too_blurry"]).scanPhoto(image: jpeg, household: household)
        }
        var productKey = "off:0000000000017"
        await expectOK("ask (JSON, vi)") {
            let r = try await mock.ask(text: "Bà ăn mì này được không?", language: "vi",
                                       lastProductKey: productKey, household: household, askerId: "m_linh")
            try require(!r.answer.text.isEmpty, "empty answer")
            if let key = r.scanResult?.product.key { productKey = key }
            return "intent=\(r.intent) next=\(r.next) lang=\(r.answer.language): \(r.answer.text.prefix(60))"
        }
        await expectOK("askAudio (multipart)") {
            let r = try await mock.askAudio(audio: Data(repeating: 1, count: 256), language: "vi",
                                            lastProductKey: productKey, household: household, askerId: "m_ba")
            return "intent=\(r.intent) transcript=\(r.transcript.prefix(40))"
        }
        var pickKeys: [String] = []
        await expectOK("picks") {
            let r = try await mock.picks(productKey: productKey, household: household)
            pickKeys = r.items.prefix(3).map(\.product.key)
            return "\(r.items.count) items prefetched=\(r.prefetched)"
        }
        await expectOK("prices") {
            let keys = pickKeys.isEmpty ? ["off:0000000000000"] : pickKeys
            let r = try await mock.prices(productKeys: keys)
            try require(Set(r.prices.keys) == Set(keys), "keys \(r.prices.keys.sorted()) != \(keys)")
            return r.prices.map { "\($0.key)=\($0.value?.displayText ?? "nil")" }.sorted().joined(separator: "; ")
        }
        await expectOK("explain (SSE)") {
            var count = 0
            for try await event in mock.mocking(["event_ms": "20"]).explain(productKey: productKey, household: household) {
                count += 1
                _ = event.text
            }
            try require(count > 0, "no events")
            return "\(count) events"
        }
        await expectOK("explain (SSE, member_ids filter)") {
            var ids: [String] = []
            for try await event in mock.mocking(["event_ms": "0"]).explain(productKey: productKey, household: household,
                                                                          memberIds: ["m_mia"]) {
                ids.append(event.memberId)
            }
            try require(ids == ["m_mia"], "got \(ids)")
            return "ok"
        }
        await expectOK("parseProfile") {
            let r = try await mock.parseProfile(text: "Mia is allergic to peanuts and sesame", language: "en")
            return "\(r.chips.count) chips, unmatched=\(r.unmatched)"
        }
        await expectOK("kbSearch") {
            let r = try await mock.kbSearch(q: "pea", lang: "vi", limit: 5)
            return "\(r.suggestions.count) suggestions: \(r.suggestions.map(\.label).prefix(3))"
        }
        await expectError("picks ?error=unauthorized", code: .unauthorized, next: .signIn, status: 401) {
            _ = try await mock.mocking(["error": "unauthorized"]).picks(productKey: productKey, household: household)
        }
        await expectError("timeout -> transport retry", code: nil, next: .retry, status: nil) {
            _ = try await mock.mocking(["delay_ms": "3000"]).health(timeout: 0.5)
        }
        let cancelled = Task { try await mock.mocking(["delay_ms": "3000"]).health() }
        try? await Task.sleep(for: .milliseconds(150))
        cancelled.cancel()
        do {
            _ = try await cancelled.value
            fail("cancel", "expected an error")
        } catch let e as PearAPIError {
            if e.isCancelled && e.nextAction == nil { print("  ok    cancel -> isCancelled, no next action") }
            else { fail("cancel", "got \(e)") }
        } catch {
            fail("cancel", "wrong error type \(error)")
        }

        print("\n== live mode (informational; must never be a decoding error)")
        await informational("health (live)") {
            let h = try await live.health()
            return "ok=\(h.ok) store=\(h.store) gemini=\(h.gemini) ask_engine=\(h.askEngine) kb=\(h.kbVersion ?? "-")"
        }
        await informational("scanBarcode (live, OFF)") {
            let r = try await live.scanBarcode(code: "0737628064502", household: household)
            return "\(r.product.name ?? r.product.key): \(r.verdicts.map { "\($0.memberId)=\($0.state)" })"
        }
        await informational("scanBarcode (live, bad code)") {
            _ = try await live.scanBarcode(code: "0000000000000", household: household)
            return "unexpected success"
        }
        await informational("ask (live)") {
            let r = try await live.ask(text: "Can Mia eat this?", language: "en", lastProductKey: "off:0737628064502",
                                       household: household)
            return "\(r.intent) \(r.next): \(r.answer.text.prefix(60))"
        }
        await informational("kbSearch (live)") {
            let r = try await live.kbSearch(q: "peanut")
            return "\(r.suggestions.count) suggestions"
        }
        await informational("prices (live)") {
            let r = try await live.prices(productKeys: ["off:0737628064502"])
            return "\(r.prices.count) keys"
        }

        let all = records.all
        let withTiming = all.filter { !($0.serverTiming ?? "").isEmpty }.count
        let echoed = all.filter { $0.requestId.count >= 8 }.count
        print("\n\(all.count) requests recorded, \(withTiming) with Server-Timing, \(echoed) with request ids")
        if withTiming == 0 { fail("Server-Timing", "no response carried a Server-Timing header") }
        print(failures == 0 ? "LIVE SMOKE PASSED" : "LIVE SMOKE FAILED: \(failures)")
        exit(failures == 0 ? 0 : 1)
    }

    struct Failed: Error { let why: String }

    static func require(_ cond: Bool, _ why: @autoclosure () -> String) throws {
        if !cond { throw Failed(why: why()) }
    }

    static func fail(_ name: String, _ why: String) {
        failures += 1
        print("  FAIL  \(name): \(why)")
    }

    static func expectOK(_ name: String, _ body: () async throws -> String) async {
        do {
            print("  ok    \(name): \(try await body())")
        } catch let e as Failed {
            fail(name, e.why)
        } catch {
            fail(name, "\(error)")
        }
    }

    static func expectError(_ name: String, code: PearErrorCode?, next: PearNextAction?, status: Int?,
                            _ body: () async throws -> Void) async {
        do {
            try await body()
            fail(name, "expected an error")
        } catch let e as PearAPIError {
            let good = e.code == code && e.nextAction == next && e.httpStatus == status
                && (code == nil || e.requestId != nil)
            if good { print("  ok    \(name): \(e.userMessage) rid=\(e.requestId ?? "-")") }
            else { fail(name, "got \(e) next=\(String(describing: e.nextAction))") }
        } catch {
            fail(name, "wrong error type \(error)")
        }
    }

    static func informational(_ name: String, _ body: () async throws -> String) async {
        do {
            print("  ok    \(name): \(try await body())")
        } catch let e as PearAPIError {
            if case .decoding = e { fail(name, "contract mismatch: \(e)") }
            else { print("  info  \(name): \(e.code?.rawValue ?? "transport") next=\(e.nextAction?.rawValue ?? "-") rid=\(e.requestId ?? "-"): \(e.userMessage)") }
        } catch {
            fail(name, "\(error)")
        }
    }
}
