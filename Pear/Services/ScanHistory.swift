//
//  ScanHistory.swift
//  Pear
//
//  Two small lists that live only on the phone (plan 7.7: profiles and history stay local;
//  the server logs scans in Firestore without photos):
//  - ScanHistory: the last 30 results (the camera's History button reopens one instantly).
//  - ShoppingList: products added from Better picks ("Add to my list").
//  Both are JSON files in Application Support, written with PearAPI's encoder.
//

import Foundation

@Observable
final class ScanHistory {
    struct Entry: Codable, Identifiable, Hashable {
        var id: String { result.product.key }
        let date: Date
        let result: ScanResult
    }

    private(set) var entries: [Entry] = []
    @ObservationIgnored private let fileName: String?

    init(persists: Bool = true) {
        fileName = persists ? "scan_history.json" : nil
        entries = LocalJSON.load([Entry].self, from: fileName) ?? []
    }

    /// Newest first; scanning the same product again moves it to the top.
    func add(_ result: ScanResult) {
        entries.removeAll { $0.result.product.key == result.product.key }
        entries.insert(Entry(date: Date(), result: result), at: 0)
        if entries.count > 30 { entries.removeLast(entries.count - 30) }
        LocalJSON.save(entries, to: fileName)
    }

    func clear() {
        entries = []
        LocalJSON.save(entries, to: fileName)
    }
}

@Observable
final class ShoppingList {
    struct Item: Codable, Identifiable, Hashable {
        var id: String { key }
        let key: String
        let name: String
        let brand: String
        let added: Date
    }

    private(set) var items: [Item] = []
    @ObservationIgnored private let fileName: String?

    init(persists: Bool = true) {
        fileName = persists ? "shopping_list.json" : nil
        items = LocalJSON.load([Item].self, from: fileName) ?? []
    }

    func contains(_ key: String) -> Bool { items.contains { $0.key == key } }

    func add(_ picks: [PickItem]) {
        for pick in picks where !contains(pick.product.key) {
            items.append(Item(key: pick.product.key, name: pick.product.name, brand: pick.product.brand, added: Date()))
        }
        LocalJSON.save(items, to: fileName)
    }

    func remove(_ key: String) {
        items.removeAll { $0.key == key }
        LocalJSON.save(items, to: fileName)
    }
}

/// Tiny JSON file helper for Application Support. nil file name = memory only (previews).
enum LocalJSON {
    static func url(_ name: String) -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(name)
    }

    static func load<T: Decodable>(_ type: T.Type, from name: String?) -> T? {
        guard let name, let data = try? Data(contentsOf: url(name)) else { return nil }
        return try? PearAPI.decoder().decode(T.self, from: data)
    }

    static func save<T: Encodable>(_ value: T, to name: String?) {
        guard let name else { return }
        do {
            let target = url(name)
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try PearAPI.encoder().encode(value).write(to: target, options: .atomic)
        } catch {
            #if DEBUG
            print("[Pear] could not save \(name): \(error)")
            #endif
        }
    }
}
