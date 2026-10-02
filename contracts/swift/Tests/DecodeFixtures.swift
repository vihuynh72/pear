//  DecodeFixtures.swift
//  Pear Swift kit — macOS command-line contract check (built and run by ../check.sh).
//
//  What it does
//  1. Self-tests for code that needs no server: SSE parser, multipart builder, error
//     mapping / next actions, request JSON keys, open enums, Server-Timing, URL building.
//  2. contracts/household.example.json -> Household -> JSON must equal the file exactly
//     (same keys, nulls included), and `Household.demo` must encode to the same JSON.
//  3. Every contracts/fixtures/*.json is decoded with the type chosen by its file-name
//     prefix (scan_barcode/scan_photo -> ScanResult, ask -> AskResponse, ...), re-encoded,
//     and diffed against the original:
//       FAIL   decode error or a changed value (the Swift model is wrong, or the fixture is)
//       DRIFT  a key in the fixture that the Swift model drops (field missing in PearModels)
//       WARN   an enum value this kit does not know (decodes as .unknown)
//     *.sse.txt fixtures are run through PearSSEParser and must yield ExplainEvents + done.
//
//  Usage: decode-fixtures <fixtures_dir> <household.example.json> [--strict]
//    --strict also fails on DRIFT. Exit code 0 = all good.
//
//  How to debug: the FAIL line names the file and the JSON path (e.g. verdicts[2].reasons[0].severity).

import Foundation

@main
struct DecodeFixtures {
    static func main() {
        let args = CommandLine.arguments
        guard args.count >= 3 else {
            print("usage: decode-fixtures <fixtures_dir> <household.example.json> [--strict]")
            exit(2)
        }
        let report = Report(strict: args.contains("--strict"))
        SelfTests.run(report)
        checkHousehold(path: args[2], report: report)
        checkFixtures(dir: args[1], report: report)
        exit(report.finish() ? 0 : 1)
    }
}

// MARK: - Report

final class Report {
    let strict: Bool
    var failures = 0
    var drift = 0
    var warnings = 0
    var selfTests = 0
    var decoded = 0
    var skipped = 0

    init(strict: Bool) { self.strict = strict }

    func fail(_ msg: String) { failures += 1; print("  FAIL  \(msg)") }
    func driftKey(_ msg: String) { drift += 1; print("  DRIFT \(msg)") }
    func warn(_ msg: String) { warnings += 1; print("  WARN  \(msg)") }
    func ok(_ msg: String) { print("  ok    \(msg)") }

    func expect(_ cond: Bool, _ what: String) {
        selfTests += 1
        if !cond { fail("self-test: \(what)") }
    }

    func finish() -> Bool {
        let fixturesLine = decoded == 0 ? "0 fixtures" : "\(decoded) fixtures decoded"
        print("""

        SUMMARY: \(fixturesLine), \(skipped) skipped, \(selfTests) self-tests, \
        \(failures) failed, \(drift) drift, \(warnings) warnings\(strict ? " (strict)" : "")
        """)
        return failures == 0 && (!strict || drift == 0)
    }
}

// MARK: - Fixture type table

enum FixtureKind: String {
    case scanResult = "ScanResult", ask = "AskResponse", picks = "PicksResponse", prices = "PricesResponse"
    case profile = "ProfileParseResponse", kb = "KbSearchResponse", health = "HealthResponse"
    case error = "ErrorResponse", explain = "ExplainEvent", household = "Household"

    /// Longest prefixes first.
    static let prefixes: [(String, FixtureKind)] = [
        ("scan_barcode", .scanResult), ("scan_photo", .scanResult), ("profile_parse", .profile),
        ("kb_search", .kb), ("household", .household), ("explain", .explain), ("health", .health),
        ("prices", .prices), ("picks", .picks), ("error", .error), ("ask", .ask),
    ]

    static func forFile(_ name: String) -> FixtureKind? {
        prefixes.first { name.hasPrefix($0.0) }?.1
    }

    /// Decode + re-encode with the Swift model. Returns (re-encoded JSON, decoded value).
    func roundTrip(_ data: Data) throws -> (Data, Any) {
        let dec = JSONDecoder()
        let enc = JSONEncoder()
        switch self {
        case .scanResult: let v = try dec.decode(ScanResult.self, from: data); return (try enc.encode(v), v)
        case .ask: let v = try dec.decode(AskResponse.self, from: data); return (try enc.encode(v), v)
        case .picks: let v = try dec.decode(PicksResponse.self, from: data); return (try enc.encode(v), v)
        case .prices: let v = try dec.decode(PricesResponse.self, from: data); return (try enc.encode(v), v)
        case .profile: let v = try dec.decode(ProfileParseResponse.self, from: data); return (try enc.encode(v), v)
        case .kb: let v = try dec.decode(KbSearchResponse.self, from: data); return (try enc.encode(v), v)
        case .health: let v = try dec.decode(HealthResponse.self, from: data); return (try enc.encode(v), v)
        case .error: let v = try dec.decode(ErrorResponse.self, from: data); return (try enc.encode(v), v)
        case .household: let v = try dec.decode(Household.self, from: data); return (try enc.encode(v), v)
        case .explain:
            if let list = try? dec.decode([ExplainEvent].self, from: data) { return (try enc.encode(list), list) }
            let v = try dec.decode(ExplainEvent.self, from: data); return (try enc.encode(v), v)
        }
    }
}

// MARK: - Household

func checkHousehold(path: String, report: Report) {
    print("\n== household: \(path)")
    guard let data = FileManager.default.contents(atPath: path) else {
        report.fail("cannot read \(path)")
        return
    }
    do {
        let household = try JSONDecoder().decode(Household.self, from: data)
        let original = try JSONSerialization.jsonObject(with: data)
        let reencoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(household))
        let diffs = exactDiff(original, reencoded, path: "")
        if diffs.isEmpty {
            report.ok("Household round-trips exactly (\(household.members.count) members, nulls included)")
        } else {
            diffs.forEach { report.fail("household round-trip: \($0)") }
        }
        let demo = try JSONSerialization.jsonObject(with: JSONEncoder().encode(Household.demo))
        let demoDiffs = exactDiff(original, demo, path: "")
        if demoDiffs.isEmpty {
            report.ok("Household.demo == household.example.json")
        } else {
            demoDiffs.forEach { report.fail("Household.demo vs example: \($0)") }
        }
    } catch {
        report.fail("household: \(describe(error))")
    }
}

// MARK: - Fixtures

func checkFixtures(dir: String, report: Report) {
    print("\n== fixtures: \(dir)")
    let fm = FileManager.default
    guard let names = try? fm.contentsOfDirectory(atPath: dir) else {
        print("  (folder missing) 0 fixtures")
        return
    }
    let files = names.filter { $0.hasSuffix(".json") || $0.hasSuffix(".sse.txt") }.sorted()
    if files.isEmpty { print("  (empty) 0 fixtures") }
    for name in files {
        let path = (dir as NSString).appendingPathComponent(name)
        guard let data = fm.contents(atPath: path), !data.isEmpty else {
            report.fail("\(name): empty or unreadable (still being written?)")
            continue
        }
        if name.hasSuffix(".sse.txt") {
            checkSSE(name: name, data: data, report: report)
            continue
        }
        if name.hasSuffix(".request.json") || name.hasPrefix("request") || name == "index.json" || name.hasPrefix("_") {
            report.skipped += 1
            print("  skip  \(name) (not a response fixture)")
            continue
        }
        guard let kind = FixtureKind.forFile(name) else {
            report.skipped += 1
            print("  skip  \(name) (no type for this file-name prefix)")
            continue
        }
        checkJSON(name: name, data: data, kind: kind, report: report)
    }
}

func checkJSON(name: String, data: Data, kind: FixtureKind, report: Report) {
    let original: Any
    do {
        original = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
    } catch {
        report.fail("\(name): invalid JSON (still being written?): \(error.localizedDescription)")
        return
    }
    do {
        let (encoded, value) = try kind.roundTrip(data)
        let reencoded = try JSONSerialization.jsonObject(with: encoded, options: [.fragmentsAllowed])
        var issues = Issues()
        lenientDiff(original, reencoded, path: "", into: &issues)
        unknownEnums(value, path: "", into: &issues.unknown)
        issues.changed.forEach { report.fail("\(name): value changed at \($0)") }
        issues.dropped.forEach { report.driftKey("\(name): key dropped by Swift model: \($0)") }
        issues.unknown.forEach { report.warn("\(name): unknown enum value \($0)") }
        report.decoded += 1
        if issues.changed.isEmpty && issues.dropped.isEmpty {
            report.ok("\(name) -> \(kind.rawValue)")
        }
    } catch {
        report.fail("\(name) as \(kind.rawValue): \(describe(error))")
    }
}

func checkSSE(name: String, data: Data, report: Report) {
    var parser = PearSSEParser()
    var events: [ExplainEvent] = []
    var done = false
    let text = String(decoding: data, as: UTF8.self)
    do {
        outer: for line in text.components(separatedBy: "\n") + [""] {
            for output in parser.feed(line) {
                switch output {
                case .done: done = true; break outer
                case let .event(eventName, payload):
                    if let ev = try PearAPIClient.interpret(name: eventName, data: payload, requestId: "fixture", status: 200) {
                        events.append(ev)
                    }
                }
            }
        }
        report.decoded += 1
        if events.isEmpty { report.fail("\(name): no ExplainEvent decoded") }
        else if !done { report.fail("\(name): stream has no `event: done`") }
        else { report.ok("\(name) -> \(events.count) ExplainEvent + done") }
    } catch {
        report.fail("\(name): \(describe(error))")
    }
}

// MARK: - JSON diffing

struct Issues {
    var changed: [String] = []
    var dropped: [String] = []
    var unknown: [String] = []
}

/// Fixture (a) vs Swift re-encode (b). Missing == null on either side; a key with a
/// non-null value in the fixture that Swift omits is "dropped" (drift).
func lenientDiff(_ a: Any, _ b: Any?, path: String, into issues: inout Issues) {
    let shown = path.isEmpty ? "$" : path
    if a is NSNull {
        if let b, !(b is NSNull) { issues.changed.append("\(shown) (fixture null, Swift \(short(b)))") }
        return
    }
    guard let b, !(b is NSNull) else {
        issues.dropped.append(shown)
        return
    }
    switch (a, b) {
    case let (ad as [String: Any], bd as [String: Any]):
        for (key, av) in ad {
            let sub = path.isEmpty ? key : "\(path).\(key)"
            if let bv = bd[key] {
                lenientDiff(av, bv, path: sub, into: &issues)
            } else if !(av is NSNull) {
                issues.dropped.append(sub)
            }
        }
    case let (aa as [Any], ba as [Any]):
        guard aa.count == ba.count else {
            issues.changed.append("\(shown) (array count \(aa.count) vs \(ba.count))")
            return
        }
        for (i, pair) in zip(aa, ba).enumerated() {
            lenientDiff(pair.0, pair.1, path: "\(path)[\(i)]", into: &issues)
        }
    default:
        if !scalarEqual(a, b) { issues.changed.append("\(shown) (fixture \(short(a)), Swift \(short(b)))") }
    }
}

/// Exact: same keys on both sides (nulls included) and equal values.
func exactDiff(_ a: Any, _ b: Any, path: String) -> [String] {
    let shown = path.isEmpty ? "$" : path
    switch (a, b) {
    case let (ad as [String: Any], bd as [String: Any]):
        var out: [String] = []
        for key in Set(ad.keys).union(bd.keys).sorted() {
            let sub = path.isEmpty ? key : "\(path).\(key)"
            switch (ad[key], bd[key]) {
            case let (av?, bv?): out += exactDiff(av, bv, path: sub)
            case (_?, nil): out.append("\(sub): missing in Swift JSON")
            case (nil, _?): out.append("\(sub): extra in Swift JSON")
            default: break
            }
        }
        return out
    case let (aa as [Any], ba as [Any]):
        guard aa.count == ba.count else { return ["\(shown): array count \(aa.count) vs \(ba.count)"] }
        return zip(aa, ba).enumerated().flatMap { exactDiff($0.element.0, $0.element.1, path: "\(path)[\($0.offset)]") }
    default:
        if a is NSNull && b is NSNull { return [] }
        return scalarEqual(a, b) ? [] : ["\(shown): \(short(a)) vs \(short(b))"]
    }
}

func isBool(_ x: Any) -> Bool {
    guard let n = x as? NSNumber else { return false }
    return CFGetTypeID(n as CFTypeRef) == CFBooleanGetTypeID()
}

func scalarEqual(_ a: Any, _ b: Any) -> Bool {
    if let sa = a as? String, let sb = b as? String { return sa == sb }
    if let na = a as? NSNumber, let nb = b as? NSNumber {
        if isBool(na) != isBool(nb) { return false }
        let x = na.doubleValue, y = nb.doubleValue
        return x == y || abs(x - y) <= 1e-9 * max(abs(x), abs(y))
    }
    if a is NSNull && b is NSNull { return true }
    return false
}

func short(_ x: Any) -> String {
    let s = "\(x)".replacingOccurrences(of: "\n", with: " ")
    return s.count > 80 ? String(s.prefix(80)) + "…" : s
}

/// Walks the decoded value and lists any `.unknown(...)` open-enum value.
func unknownEnums(_ value: Any, path: String, into out: inout [String], depth: Int = 0) {
    guard depth < 40 else { return }
    if let e = value as? any PearOpenEnum {
        if !e.isKnown { out.append("\(path.isEmpty ? "$" : path) = \"\(e.rawValue)\" (\(type(of: e)))") }
        return
    }
    for child in Mirror(reflecting: value).children {
        let label = child.label.map { $0 == "some" ? "" : ".\($0)" } ?? ""
        unknownEnums(child.value, path: path + label, into: &out, depth: depth + 1)
    }
}

func describe(_ error: any Error) -> String {
    func at(_ ctx: DecodingError.Context) -> String {
        let p = ctx.codingPath.map { $0.intValue.map { "[\($0)]" } ?? ".\($0.stringValue)" }.joined()
        return p.isEmpty ? "$" : String(p.drop { $0 == "." })
    }
    guard let e = error as? DecodingError else { return "\(error)" }
    switch e {
    case let .keyNotFound(key, ctx): return "missing key '\(key.stringValue)' at \(at(ctx))"
    case let .typeMismatch(type, ctx): return "type mismatch (\(type)) at \(at(ctx)): \(ctx.debugDescription)"
    case let .valueNotFound(type, ctx): return "null/missing \(type) at \(at(ctx))"
    case let .dataCorrupted(ctx): return "data corrupted at \(at(ctx)): \(ctx.debugDescription)"
    @unknown default: return "\(e)"
    }
}

// MARK: - Self-tests (no server needed)

enum SelfTests {
    static func run(_ r: Report) {
        print("== self-tests")
        sse(r)
        multipart(r)
        errors(r)
        requests(r)
        enums(r)
        misc(r)
        if r.failures == 0 { r.ok("\(r.selfTests) self-test assertions") }
    }

    static func feedAll(_ text: String) -> [PearSSEParser.Output] {
        var p = PearSSEParser()
        var out: [PearSSEParser.Output] = []
        for line in text.components(separatedBy: "\n") { out += p.feed(line) }
        out += p.flush()
        return out
    }

    static func sse(_ r: Report) {
        let ev = #"{"member_id": "m_mia", "language": "en", "text": "Not for Mia."}"#
        var out = feedAll("data: \(ev)\n\nevent: done\ndata: {}\n\n")
        r.expect(out == [.event(name: nil, data: ev), .done], "SSE basic + event: done -> \(out)")

        out = feedAll(": keep-alive\r\ndata: \(ev)\r\n\r\ndata: [DONE]\r\n")
        r.expect(out == [.event(name: nil, data: ev), .done], "SSE CRLF, comment, [DONE] -> \(out)")

        out = feedAll("data: \(ev)\ndata: \(ev)\n")
        r.expect(out == [.event(name: nil, data: ev), .event(name: nil, data: ev)], "SSE without blank lines -> \(out)")

        out = feedAll("data: {\"a\":\ndata: 1}\n\n")
        r.expect(out == [.event(name: nil, data: "{\"a\":\n1}")], "SSE multi-line data -> \(out)")

        out = feedAll("event: start\ndata: {}\n\nevent: message\ndata: \(ev)\n\n")
        r.expect(out == [.event(name: "start", data: "{}"), .event(name: nil, data: ev)], "SSE named events -> \(out)")

        do {
            let decoded = try PearAPIClient.interpret(name: nil, data: ev, requestId: "t", status: 200)
            r.expect(decoded?.memberId == "m_mia" && decoded?.text == "Not for Mia.", "SSE interpret ExplainEvent")
            let ignored = try PearAPIClient.interpret(name: "ping", data: "{}", requestId: "t", status: 200)
            r.expect(ignored == nil, "SSE unknown event ignored")
        } catch {
            r.expect(false, "SSE interpret threw \(error)")
        }
        let errJSON = #"{"error": {"code": "unavailable", "message": "AI reader down", "next": "retry", "request_id": "abc"}}"#
        do {
            _ = try PearAPIClient.interpret(name: "error", data: errJSON, requestId: "t", status: 200)
            r.expect(false, "SSE event: error should throw")
        } catch let e as PearAPIError {
            r.expect(e.code == .unavailable && e.nextAction == .retry && e.requestId == "abc", "SSE error -> \(e)")
        } catch {
            r.expect(false, "SSE error wrong type \(error)")
        }
        do {
            let list = try PearAPIClient.decodeJSONEvents(Data("[\(ev)]".utf8), requestId: "t")
            r.expect(list.count == 1, "explain JSON fallback")
        } catch {
            r.expect(false, "explain JSON fallback threw \(error)")
        }
    }

    static func multipart(_ r: Report) {
        var form = PearMultipart(boundary: "BOUND")
        form.addFile("image", filename: "label.jpg", mimeType: "image/jpeg", data: Data([0xFF, 0xD8, 0xFF]))
        form.addField("household", #"{"household_id":"hh_demo"}"#)
        form.addField("member_ids", #"["m_mia"]"#)
        let body = form.finalized()
        let text = String(decoding: body, as: UTF8.self)
        r.expect(form.contentType == "multipart/form-data; boundary=BOUND", "multipart content type")
        r.expect(text.hasPrefix("--BOUND\r\nContent-Disposition: form-data; name=\"image\"; filename=\"label.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"),
                 "multipart file header")
        r.expect(text.contains("--BOUND\r\nContent-Disposition: form-data; name=\"household\"\r\n\r\n{\"household_id\":\"hh_demo\"}\r\n"),
                 "multipart text field")
        r.expect(text.hasSuffix("\r\n--BOUND--\r\n"), "multipart closing boundary")
        r.expect(body.range(of: Data([0xFF, 0xD8, 0xFF])) != nil, "multipart keeps binary bytes")
        r.expect(form.debugSummary == "image(file, 3 B), household(26 B), member_ids(9 B)", "multipart summary: \(form.debugSummary)")
    }

    static func errors(_ r: Report) {
        let notFound = Data(#"{"error": {"code": "not_found", "message": "We couldn't find this barcode.", "next": "take_photo", "request_id": "rid1"}}"#.utf8)
        var e = PearAPIClient.apiError(status: 404, data: notFound, requestId: "hdr")
        r.expect(e.code == .notFound && e.nextAction == .takePhoto && e.requestId == "rid1" && e.httpStatus == 404,
                 "404 not_found -> take_photo: \(e)")
        r.expect(e.userMessage == "We couldn't find this barcode.", "api message passthrough")

        let blurry = Data(#"{"error": {"code": "too_blurry", "message": "Blurry", "next": "retake", "request_id": null}}"#.utf8)
        e = PearAPIClient.apiError(status: 422, data: blurry, requestId: "hdr")
        r.expect(e.nextAction == .retake && e.requestId == "hdr", "too_blurry -> retake, header rid fallback: \(e)")

        e = PearAPIClient.apiError(status: 502, data: Data("<html>Bad Gateway</html>".utf8), requestId: "hdr")
        r.expect(e.code == .unavailable && e.nextAction == .retry, "502 HTML -> unavailable/retry: \(e)")

        e = PearAPIClient.apiError(status: 401, data: Data(), requestId: "hdr")
        r.expect(e.code == .unauthorized && e.nextAction == .signIn, "401 empty -> sign_in: \(e)")

        let noNext = Data(#"{"error": {"code": "bad_request", "message": "Invalid request: code", "next": null}}"#.utf8)
        e = PearAPIClient.apiError(status: 400, data: noNext, requestId: "hdr")
        r.expect(e.code == .badRequest && e.nextAction == nil, "bad_request -> no next action")

        let future = Data(#"{"error": {"code": "brand_new", "message": "x", "next": "open_settings"}}"#.utf8)
        e = PearAPIClient.apiError(status: 418, data: future, requestId: "hdr")
        r.expect(e.code == .unknown("brand_new") && e.nextAction == .unknown("open_settings"), "unknown code/next tolerated")

        r.expect(PearAPIError.transport(URLError(.cancelled)).nextAction == nil, "cancelled -> no next action")
        r.expect(PearAPIError.transport(URLError(.cancelled)).isCancelled, "isCancelled")
        r.expect(PearAPIError.transport(URLError(.timedOut)).nextAction == .retry, "timeout -> retry")
        r.expect(PearAPIClient.urlError(from: CancellationError()).code == .cancelled, "CancellationError -> URLError.cancelled")
        r.expect(!PearAPIError.transport(URLError(.notConnectedToInternet)).userMessage.lowercased().contains("safe"),
                 "no 'safe' in user copy")
    }

    static func keys(_ value: some Encodable) -> Set<String> {
        guard let data = try? JSONEncoder().encode(value),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [] }
        return Set(obj.keys)
    }

    static func requests(_ r: Report) {
        let hh = Household.demo
        r.expect(keys(BarcodeRequest(code: "0000000000017", household: hh)) == ["code", "household"], "BarcodeRequest keys (nil member_ids omitted)")
        r.expect(keys(BarcodeRequest(code: "1", household: hh, memberIds: ["m_mia"])) == ["code", "household", "member_ids"], "BarcodeRequest member_ids")
        r.expect(keys(AskRequest(text: "t", language: "vi", lastProductKey: "off:1", household: hh, askerId: "m_linh"))
                 == ["text", "language", "last_product_key", "household", "asker_id"], "AskRequest keys")
        r.expect(keys(ProductKeyRequest(productKey: "off:1", household: hh)) == ["product_key", "household"], "ProductKeyRequest keys")
        r.expect(keys(PricesRequest(productKeys: ["off:1"])) == ["product_keys", "country"], "PricesRequest keys")
        r.expect(keys(ProfileParseRequest(text: "x", language: "en")) == ["text", "language"], "ProfileParseRequest keys")

        let guestMember = Member(id: "m_1", name: "Sam")
        let memberKeys = keys(guestMember)
        r.expect(memberKeys == ["id", "type", "species", "name", "age_group", "languages", "allergies", "conditions",
                                "pregnant", "skin", "diets", "less_of", "avoid_dyes", "medications"],
                 "Member encodes every contract key: \(memberKeys.sorted())")
        if let data = try? JSONEncoder().encode(guestMember), let s = String(data: data, encoding: .utf8) {
            r.expect(s.contains(#""avoid_dyes":null"#) && s.contains(#""species":null"#), "Member nil -> JSON null")
        }
        r.expect(keys(Household()) == ["household_id", "owner_language", "member_limit", "members"], "Household keys")

        // Lenient household input, like the backend: shorthand allergies, missing fields.
        let lenient = Data(#"{"members": [{"id": "m_x", "name": "X", "allergies": ["peanut"], "avoid_dyes": null}]}"#.utf8)
        if let h = try? JSONDecoder().decode(Household.self, from: lenient) {
            r.expect(h.householdId == "hh_guest" && h.members[0].allergies == [Allergy(id: "peanut", level: .strict)]
                     && h.members[0].languages == ["en"] && h.members[0].avoidDyes == nil && h.members[0].dyesOn == false,
                     "lenient Household decode")
        } else {
            r.expect(false, "lenient Household decode threw")
        }
        r.expect(Household.demo.member(id: "m_mia")?.dyesOn == true, "Mia dyesOn")
    }

    static func enums(_ r: Report) {
        let json = Data(#"{"member_id": "m_x", "state": "SUPER_RIPE", "label": "?", "reasons": [{"code": "c", "text": "t", "language": "en", "severity": "info"}]}"#.utf8)
        do {
            let v = try JSONDecoder().decode(Verdict.self, from: json)
            r.expect(v.state == .unknown("SUPER_RIPE") && !v.state.isKnown, "unknown state tolerated")
            r.expect(v.reasons[0].severity == .unknown("info") && v.reasons[0].matched.isEmpty && v.score == nil,
                     "unknown severity + missing optional fields")
            let back = String(decoding: try JSONEncoder().encode(v.state), as: UTF8.self)
            r.expect(back == "\"SUPER_RIPE\"", "unknown enum re-encodes raw value")
        } catch {
            r.expect(false, "unknown enum decode threw \(describe(error))")
        }
        r.expect(PearState(rawValue: "ALLERGY") == .allergy && PearState.allergy.rawValue == "ALLERGY", "state raw values")
        r.expect(PearErrorCode(rawValue: "internal") == .internalError, "internal error code")
        r.expect(PearNutritionBasis(rawValue: "100g") == .per100g, "100g basis")
        r.expect(PearState.knownCases.count == 6 && PearErrorCode.knownCases.count == 8, "known case counts")
        r.expect(PearState.allergy.priority > PearState.skip.priority && PearState.easy.priority > PearState.ripe.priority, "state priority")
    }

    static func misc(_ r: Report) {
        let t = PearAPIClient.parseServerTiming("cache;dur=41, lookup;dur=486.6, total;dur=548")
        r.expect(t == ["cache": 41, "lookup": 487, "total": 548], "Server-Timing parse: \(t)")
        r.expect(PearAPIClient.parseServerTiming(nil).isEmpty && PearAPIClient.parseServerTiming("").isEmpty, "empty Server-Timing")

        let rid = PearAPIClient.newRequestId()
        r.expect(rid.count == 16 && rid.allSatisfy(\.isHexDigit), "request id = 16 hex: \(rid)")

        let live = PearAPIClient(baseURL: URL(string: "https://pear-api-xyz.a.run.app/")!)
        let mock = PearAPIClient(baseURL: URL(string: "http://192.168.1.20:8080/api")!, mode: .mock,
                                 mockParameters: ["error": "too_blurry"])
        let kb = PearEndpoint(method: "GET", path: "/v1/kb/search",
                              query: [URLQueryItem(name: "q", value: "c++ & milk"), URLQueryItem(name: "lang", value: "vi")],
                              timeout: 5)
        let u1 = live.url(for: kb).absoluteString
        r.expect(u1 == "https://pear-api-xyz.a.run.app/v1/kb/search?q=c%2B%2B%20%26%20milk&lang=vi", "live URL: \(u1)")
        let u2 = mock.url(for: kb).absoluteString
        r.expect(u2 == "http://192.168.1.20:8080/api/mock/v1/kb/search?q=c%2B%2B%20%26%20milk&lang=vi&error=too_blurry", "mock URL: \(u2)")
        let h = mock.url(for: PearEndpoint(method: "GET", path: "/health", timeout: 5)).absoluteString
        r.expect(h == "http://192.168.1.20:8080/api/mock/health?error=too_blurry", "mock health URL: \(h)")
        r.expect(live.mocking(["fixture": "x"]).url(for: kb).absoluteString == u1, "mock params ignored in live mode")

        // timings_ms keys must stay snake_case (no key strategy).
        let picks = Data(#"{"items": [], "prefetched": true, "timings_ms": {"label_reader": 5, "wait": 40}}"#.utf8)
        if let p = try? JSONDecoder().decode(PicksResponse.self, from: picks) {
            r.expect(p.timingsMs["label_reader"] == 5 && p.requestId == nil, "timings_ms keys kept")
        } else {
            r.expect(false, "PicksResponse decode threw")
        }
        let prices = Data(#"{"prices": {"off:1": {"amount": 3.99, "retailer": "Target", "checked_at": "2026-10-02T21:20:00Z"}, "off:2": null}}"#.utf8)
        if let p = try? JSONDecoder().decode(PricesResponse.self, from: prices) {
            r.expect(p.prices.count == 2 && p.price(for: "off:2") == nil && p.price(for: "off:1")?.currency == "USD"
                     && p.price(for: "off:1")?.source == .googleSearch, "prices with null values")
            r.expect(p.price(for: "off:1")?.checkedDate != nil, "checked_at parses as ISO 8601")
        } else {
            r.expect(false, "PricesResponse decode threw")
        }
    }
}
