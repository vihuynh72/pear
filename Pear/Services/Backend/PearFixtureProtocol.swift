// PearFixtureProtocol.swift
// DEBUG only, CI screenshot branch only. When the app is launched with `-PearFixtures`, network calls are
// answered from the JSON files bundled in Resources/Fixtures (responses captured from the live demo backend)
// instead of going out. Used for simulator screenshots while the live demo backend is down.

#if DEBUG
import Foundation

nonisolated final class PearFixtureProtocol: URLProtocol {
    static var isEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-PearFixtures")
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    private func requestBody() -> String {
        if let data = request.httpBody { return String(decoding: data, as: UTF8.self) }
        guard let stream = request.httpBodyStream else { return "" }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return String(decoding: data, as: UTF8.self)
    }

    override func startLoading() {
        let path = request.url?.path ?? ""
        var name = "scan_barcode_not_found"
        var status = 404
        if path.hasSuffix("/health") {
            name = "health"; status = 200
        } else if path.hasSuffix("/scan/barcode"), requestBody().contains("048121253196") {
            name = "scan_barcode"; status = 200
        }
        var data = Data("{}".utf8)
        if let url = Bundle.main.url(forResource: name, withExtension: "json"),
           let loaded = try? Data(contentsOf: url) {
            data = loaded
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
#endif
