// PearDemo.swift
// The demo family (same ids the server fixtures use) and a tiny JSON store for the household.
// Foundation only.

import Foundation

nonisolated enum PearDemo {
    /// Linh (owner, English + Vietnamese), Bà (Vietnamese, salt and sugar), Mia (kid: peanut + sesame, eczema, no dyes),
    /// Leo (teen, Spanish, vegan). ownerLanguage drives the headline language.
    static let household = Household(
        householdId: "hh_demo",
        ownerLanguage: "en",
        memberLimit: 5,
        members: [
            Member(id: "m_linh", name: "Linh", ageGroup: "adult", languages: ["en", "vi"],
                   skin: ["fragrance_sensitivity"]),
            Member(id: "m_ba", name: "Bà", ageGroup: "65+", languages: ["vi"],
                   conditions: ["high_blood_pressure", "prediabetes"]),
            Member(id: "m_mia", name: "Mia", ageGroup: "kid", languages: ["en"],
                   allergies: [Allergy(id: "peanut", level: "strict"), Allergy(id: "sesame", level: "strict")],
                   skin: ["eczema"], avoidDyes: true),
            Member(id: "m_leo", name: "Leo", ageGroup: "teen", languages: ["es"],
                   diets: ["vegan"]),
        ]
    )

    /// Barcodes already cached on the server, so they answer in well under a second.
    static let barcodes: [(code: String, note: String)] = [
        ("048121253196", "Thomas' Everything Bagels: Allergy for Mia (sesame)"),
        ("099482474058", "Whole Foods Plain Bagels: fits everyone"),
        ("071314008754", "Aunt Millie's Whole Grain Bagels: fits everyone"),
    ]
}

/// Saves the household as JSON in Application Support (plan 7.1). Falls back to the demo family.
nonisolated enum PearHouseholdStore {
    static var fileURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("household.json")
    }

    static func load() -> Household {
        guard let data = try? Data(contentsOf: fileURL),
              let household = try? PearAPI.decoder().decode(Household.self, from: data),
              !household.members.isEmpty
        else { return PearDemo.household }
        return household
    }

    static func save(_ household: Household) {
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try PearAPI.encoder().encode(household).write(to: fileURL, options: .atomic)
        } catch {
            print("PearHouseholdStore save failed: \(error)")
        }
    }

    static func reset() {
        try? FileManager.default.removeItem(at: fileURL)
    }
}
