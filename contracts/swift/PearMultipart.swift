//  PearMultipart.swift
//  Pear Swift kit — tiny multipart/form-data body builder (Foundation only).
//
//  What this file does
//  - Builds the bodies for POST /v1/scan/photo (image, front_image, household, member_ids)
//    and the audio fallback of POST /v1/ask (audio, household, language,
//    last_product_key, asker_id). Field names are fixed by the backend routes.
//
//  How to debug
//  - `PearMultipart.debugSummary` lists the parts and byte sizes without dumping the image.
//  - A 400 `bad_request` from the photo route whose message names a field
//    ("Invalid request: image: Field required") means a part name is wrong here.
//  - Quick server-side check: `curl -F image=@label.jpg -F household="$(cat household.json)"`
//    against the same URL; if curl works and the app does not, compare part names.

import Foundation

nonisolated struct PearMultipart: Sendable {
    let boundary: String
    private var body = Data()
    private var parts: [(name: String, bytes: Int, file: Bool)] = []

    init(boundary: String = "pear-" + UUID().uuidString.lowercased()) {
        self.boundary = boundary
    }

    /// Value for the Content-Type header.
    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    /// Adds a plain text field (e.g. household JSON string, language).
    mutating func addField(_ name: String, _ value: String) {
        append("--\(boundary)\r\n")
        // No Content-Type for text parts (same as curl -F / browsers): Starlette reads them as str.
        append("Content-Disposition: form-data; name=\"\(Self.escape(name))\"\r\n\r\n")
        append(value)
        append("\r\n")
        parts.append((name, value.utf8.count, false))
    }

    /// Adds a file part (image/jpeg, audio/mp4, ...).
    mutating func addFile(_ name: String, filename: String, mimeType: String, data: Data) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(Self.escape(name))\"; filename=\"\(Self.escape(filename))\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        append("\r\n")
        parts.append((name, data.count, true))
    }

    /// The complete body including the closing boundary.
    func finalized() -> Data {
        var out = body
        out.append(Data("--\(boundary)--\r\n".utf8))
        return out
    }

    /// "image(file, 231044 B), household(1600 B)" — safe to log.
    var debugSummary: String {
        parts.map { "\($0.name)(\($0.file ? "file, " : "")\($0.bytes) B)" }.joined(separator: ", ")
    }

    private mutating func append(_ s: String) { body.append(Data(s.utf8)) }

    private static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
    }
}
