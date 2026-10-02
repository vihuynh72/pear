//
//  ProductSearch.swift
//  Pear
//
//  Open Food Facts, called straight from the phone for the two things the Pear API doesn't
//  return: search by name (the camera's Type mode) and nutrition / package size per barcode
//  (Details' "Per serving" and "340 g"). Verdicts never come from here: every product the user
//  picks goes through PearAPI.scanBarcode, so the server's rules decide (plan 8.7).
//
//  Open Food Facts asks for a named User-Agent and allows about 10 searches a minute per phone,
//  so Type mode searches on submit, not on every keystroke.
//

import Foundation

nonisolated struct FoundProduct: Identifiable, Hashable, Sendable {
    let code: String
    let name: String
    let brand: String
    let imageURL: URL?
    var id: String { code }
}

nonisolated struct ProductFacts: Hashable, Sendable {
    var quantity: String?
    var sugarsG: Double?
    var sodiumMg: Double?
    var saturatedFatG: Double?
    /// "serving" when Open Food Facts had per-serving values, else "100g".
    var basis: String = "serving"
}

nonisolated enum ProductSearch {
    private static let userAgent = "Pear/0.3 (iOS; SF Hacks x GDG hackathon)"

    private static let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 8
        cfg.httpAdditionalHeaders = ["User-Agent": userAgent]
        return URLSession(configuration: cfg)
    }()

    /// Name or brand search (search-a-licious). Products without a barcode or name are skipped.
    static func search(_ query: String, limit: Int = 6) async throws -> [FoundProduct] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return [] }
        var comps = URLComponents(string: "https://search.openfoodfacts.org/search")!
        comps.queryItems = [
            URLQueryItem(name: "q", value: q),
            URLQueryItem(name: "page_size", value: String(limit * 2)),
            URLQueryItem(name: "fields", value: "code,product_name,brands,image_front_small_url"),
        ]
        let (data, _) = try await session.data(from: comps.url!)
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let hits = root["hits"] as? [[String: Any]] else { return [] }
        var seen = Set<String>()
        var out: [FoundProduct] = []
        for hit in hits {
            guard let code = hit["code"] as? String, !code.isEmpty, !seen.contains(code),
                  let name = (hit["product_name"] as? String)?.trimmingCharacters(in: .whitespaces), !name.isEmpty
            else { continue }
            seen.insert(code)
            let brands = (hit["brands"] as? [String]) ?? ((hit["brands"] as? String).map { [$0] } ?? [])
            let image = (hit["image_front_small_url"] as? String).flatMap(URL.init(string:))
            out.append(FoundProduct(code: code, name: name, brand: brands.first ?? "", imageURL: image))
            if out.count == limit { break }
        }
        return out
    }

    /// Package size + sugar / sodium / saturated fat for a product key like "off:0048121253196".
    /// nil for photo products or when Open Food Facts has nothing.
    static func facts(for productKey: String) async -> ProductFacts? {
        let parts = productKey.split(separator: ":", maxSplits: 1)
        guard parts.count == 2, parts[0] == "off" || parts[0] == "obf" else { return nil }
        let host = parts[0] == "off" ? "world.openfoodfacts.org" : "world.openbeautyfacts.org"
        guard let url = URL(string: "https://\(host)/api/v2/product/\(parts[1])?fields=quantity,nutriments"),
              let (data, _) = try? await session.data(from: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let product = root["product"] as? [String: Any] else { return nil }
        let n = product["nutriments"] as? [String: Any] ?? [:]
        func value(_ key: String) -> (Double?, Bool) {
            if let v = number(n["\(key)_serving"]) { return (v, true) }
            return (number(n["\(key)_100g"]), false)
        }
        let sugars = value("sugars"), sodium = value("sodium"), fat = value("saturated-fat")
        var facts = ProductFacts()
        facts.quantity = (product["quantity"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        facts.sugarsG = sugars.0
        facts.sodiumMg = sodium.0.map { $0 * 1000 }          // Open Food Facts stores grams
        facts.saturatedFatG = fat.0
        facts.basis = (sugars.1 || sodium.1 || fat.1) ? "serving" : "100g"
        return facts
    }

    private static func number(_ any: Any?) -> Double? {
        if let d = any as? Double { return d }
        if let i = any as? Int { return Double(i) }
        if let s = any as? String { return Double(s) }
        return nil
    }
}
