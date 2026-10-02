//  CurlTransport.swift
//  Pear Swift kit — TEST ONLY: a URLProtocol that performs URLSession requests with /usr/bin/curl.
//
//  Why: some sandboxes (CI, agent shells) block URLSession's networking daemons while plain
//  curl works. LiveSmoke uses this when PEAR_SMOKE_TRANSPORT=curl so the real PearAPIClient
//  code path (URL building, headers, multipart bytes, status/header handling, SSE bytes,
//  decoding) still runs against a real backend. Never ship this in the app.
//
//  How to debug: set PEAR_CURL_TRACE=1 to print each curl command line.

import Foundation

final class CurlURLProtocol: URLProtocol, @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var stopped = false

    /// URLSession whose requests all go through curl.
    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [CurlURLProtocol.self]
        return URLSession(configuration: config)
    }

    override class func canInit(with request: URLRequest) -> Bool {
        ["http", "https"].contains(request.url?.scheme?.lowercased() ?? "")
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let request = self.request
        DispatchQueue.global().async { self.run(request) }
    }

    override func stopLoading() {
        lock.lock()
        stopped = true
        let p = process
        lock.unlock()
        if let p, p.isRunning { p.terminate() }
    }

    private func run(_ request: URLRequest) {
        guard let url = request.url else { return fail(URLError(.badURL)) }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("pear-curl-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }

        var args = ["-s", "-i", "--http1.1", "--max-time", String(max(1, Int(request.timeoutInterval.rounded(.up)))),
                    "-X", request.httpMethod ?? "GET"]
        for (name, value) in request.allHTTPHeaderFields ?? [:] { args += ["-H", "\(name): \(value)"] }
        args += ["-H", "Expect:"]
        if let body = Self.body(of: request) {
            try? body.write(to: tmp)
            args += ["--data-binary", "@\(tmp.path)"]
        }
        args.append(url.absoluteString)
        if ProcessInfo.processInfo.environment["PEAR_CURL_TRACE"] == "1" {
            print("curl " + args.map { $0.contains(" ") ? "'\($0)'" : $0 }.joined(separator: " "))
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        lock.lock()
        if stopped { lock.unlock(); return }
        process = p
        lock.unlock()
        do { try p.run() } catch { return fail(URLError(.cannotConnectToHost)) }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()

        lock.lock()
        let wasStopped = stopped
        lock.unlock()
        if wasStopped { return }
        switch p.terminationStatus {
        case 0: break
        case 28: return fail(URLError(.timedOut))
        case 6: return fail(URLError(.cannotFindHost))
        case 7: return fail(URLError(.cannotConnectToHost))
        default: return fail(URLError(.networkConnectionLost))
        }
        guard let (status, headers, body) = Self.parse(data),
              let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)
        else { return fail(URLError(.badServerResponse)) }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    private func fail(_ error: URLError) {
        client?.urlProtocol(self, didFailWithError: error)
    }

    /// URLSession hands protocols an httpBodyStream instead of httpBody.
    private static func body(of request: URLRequest) -> Data? {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return data
    }

    /// "HTTP/1.1 200 OK\r\nA: b\r\n\r\nbody" -> (200, headers, body). Skips "100 Continue" blocks.
    private static func parse(_ data: Data) -> (Int, [String: String], Data)? {
        var rest = data
        let separator = Data("\r\n\r\n".utf8)
        while let range = rest.range(of: separator) {
            let head = String(decoding: rest[rest.startIndex..<range.lowerBound], as: UTF8.self)
            let body = rest[range.upperBound...]
            var lines = head.components(separatedBy: "\r\n")
            let statusLine = lines.removeFirst()
            let parts = statusLine.split(separator: " ")
            guard parts.count >= 2, let status = Int(parts[1]) else { return nil }
            if status == 100 { rest = Data(body); continue }
            var headers: [String: String] = [:]
            for line in lines {
                guard let colon = line.firstIndex(of: ":") else { continue }
                headers[String(line[..<colon])] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            }
            return (status, headers, Data(body))
        }
        return nil
    }
}
