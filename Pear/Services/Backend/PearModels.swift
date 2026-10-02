// PearModels.swift
// Codable models for the Pear API. They mirror the live JSON exactly (snake_case on the wire,
// camelCase in Swift through the PearAPI decoder/encoder key strategies). Foundation only.
// Types are marked nonisolated so they work whether or not the target uses "Default Actor Isolation = MainActor".
// Verified by decoding real responses from https://pear-api-73536482701.us-central1.run.app on October 2, 2026.

import Foundation

// MARK: - Household (sent with every scan, ask and picks call)

nonisolated struct Household: Codable, Hashable, Sendable {
    var householdId: String = "hh_demo"
    var ownerLanguage: String = "en"        // headline language
    var memberLimit: Int = 5
    var members: [Member]
}

nonisolated struct Member: Codable, Hashable, Identifiable, Sendable {
    var id: String
    var type: String = "person"             // "person" | "pet"
    var species: String? = nil
    var name: String
    var ageGroup: String = "adult"          // "baby" | "kid" | "teen" | "adult" | "65+"
    var languages: [String] = ["en"]        // first one is primary: "en", "vi", "es"
    var allergies: [Allergy] = []
    var conditions: [String] = []           // high_blood_pressure, diabetes, prediabetes, high_cholesterol, kidney_disease, heart_disease
    var pregnant: Bool = false
    var skin: [String] = []                 // eczema, psoriasis, sensitive_skin, acne_prone, rosacea, fragrance_sensitivity
    var diets: [String] = []                // vegetarian, vegan, pescatarian, halal, kosher, no_pork, no_beef, no_alcohol, jain, buddhist_vegetarian, gluten_free
    var lessOf: [String] = []               // sugar, sodium, saturated_fat, caffeine, artificial_dyes, palm_oil
    var avoidDyes: Bool = false
    var medications: [String] = []

    var primaryLanguage: String { languages.first ?? "en" }
}

nonisolated struct Allergy: Codable, Hashable, Sendable {
    var id: String                          // peanut, tree_nuts, milk, egg, wheat, soy, fish, shellfish, sesame, gluten, lactose, mustard, sulfites, celery, lupin, corn, kiwi, molluscs
    var level: String = "strict"            // "strict" | "sensitive"
}

// MARK: - Scan result (/v1/scan/barcode, /v1/scan/photo, and AskResponse.scanResult)

nonisolated struct ScanResult: Codable, Hashable, Sendable {
    var product: Product
    var verdicts: [Verdict]                 // one per household member, household order
    var headline: Headline                  // in household.ownerLanguage
    var timingsMs: [String: Int]            // e.g. lookup, read, match, rules, total
}

nonisolated struct Product: Codable, Hashable, Sendable {
    var key: String                         // "off:0048121253196", "obf:...", "photo:62e0a9bb530a"
    var name: String
    var brand: String
    var category: String                    // food | cosmetic | supplement | other
    var productType: String?                // leave_on | rinse_off | nil
    var imageUrl: String?
    var source: String                      // open_food_facts | open_beauty_facts | usda_fdc | photo
    var photoKind: String?                  // ingredients | front_only | other | nil
    var dataConfidence: String              // high | medium | low
    var lessSure: Bool                      // show a "Less sure" badge
    var askForIngredients: Bool             // show "Snap the ingredients to confirm"
    var ingredients: [String]
    var contains: [String]                  // allergen ids
    var mayContain: [String]                // allergen ids
    var certifications: [String]            // vegan, vegetarian, halal, kosher, gluten_free, organic
    var labelLanguage: String?

    /// Source line for the result sheet (plan 5.6).
    var sourceLine: String {
        source == "photo" ? "Read from your photo." : "From Open Food Facts. Check the label."
    }
}

nonisolated enum VerdictState: String, Codable, Hashable, Sendable, CaseIterable {
    case allergy = "ALLERGY"
    case unsure = "UNSURE"
    case diet = "DIET"
    case skip = "SKIP"
    case easy = "EASY"
    case ripe = "RIPE"

    /// 0 is most serious (plan 8.3 precedence).
    var rank: Int { VerdictState.allCases.firstIndex(of: self) ?? 99 }
}

nonisolated struct Verdict: Codable, Hashable, Identifiable, Sendable {
    var memberId: String
    var state: VerdictState
    var label: String                       // chip word already in the member's language ("Allergy", "Chín", "Madura", "May contain"...)
    var reasons: [Reason]                   // first reason is the one-line explanation under the chip
    var id: String { memberId }
    // The server also sends "score": null; it is intentionally ignored.
}

nonisolated struct Reason: Codable, Hashable, Sendable {
    var code: String                        // allergen.sesame, diet.vegan.gelatin, health.sodium, label.check, ...
    var text: String                        // in the member's language, names the ingredient
    var language: String
    var severity: String                    // hard | soft | note
    var matched: [String]                   // exact label words to highlight on the details screen
    var source: String
}

nonisolated struct Headline: Codable, Hashable, Sendable {
    var text: String
    var language: String
}

// MARK: - Ask (/v1/ask)

nonisolated struct AskResponse: Codable, Hashable, Sendable {
    var transcript: String
    var language: String                    // speak the answer in this language
    var intent: String                      // check | alternatives | unclear
    var productQuery: String?
    var memberIds: [String]
    var next: String                        // "result" | "scan_needed"
    var answer: Answer
    var scanResult: ScanResult?             // present for intent "check"; nil when the product is unknown
}

nonisolated struct Answer: Codable, Hashable, Sendable {
    var text: String
    var language: String
}

// MARK: - Picks (/v1/picks) and prices (/v1/prices)

nonisolated struct PicksResponse: Codable, Hashable, Sendable {
    var items: [PickItem]                   // up to 3, every one fits the whole family
    var prefetched: Bool
    var timingsMs: [String: Int]
}

nonisolated struct PickItem: Codable, Hashable, Identifiable, Sendable {
    var product: PickProduct
    var verdicts: [PickVerdict]
    var id: String { product.key }
}

nonisolated struct PickProduct: Codable, Hashable, Sendable {
    var key: String
    var name: String
    var brand: String
    var imageUrl: String?
    var category: String
}

nonisolated struct PickVerdict: Codable, Hashable, Sendable {
    var memberId: String
    var state: VerdictState
    var label: String
}

nonisolated struct PricesResponse: Codable, Hashable, Sendable {
    var prices: [String: Price?]            // product key -> price, or null when none was found
    var timingsMs: [String: Int]
}

nonisolated struct Price: Codable, Hashable, Sendable {
    var amount: Double
    var currency: String                    // "USD"
    var retailer: String
    var size: String
    var sourceTitle: String
    var sourceUrl: String
    var searchEntryHtml: String?            // Google Search Suggestions chip (Google's grounding terms ask apps to show it)
    var checkedAt: String?
    var source: String                      // "google_search"

    /// e.g. "About $5.99 at The Fresh Grocer (15.5 oz)". Put `caption` under it in small latte text.
    var displayText: String {
        "About $" + String(format: "%.2f", amount) + " at \(retailer) (\(size))"
    }

    /// Attribution line required next to every price.
    var caption: String { "Found with Google Search." }
}

// MARK: - Onboarding (/v1/kb/search, /v1/profile/parse) and health

nonisolated struct KBSuggestion: Codable, Hashable, Identifiable, Sendable {
    var group: String                       // allergies | conditions | skin | diets | less_of
    var id: String
    var label: [String: String]             // "en", "vi", "es"

    func label(for language: String) -> String {
        label[String(language.prefix(2))] ?? label["en"] ?? id
    }
}

nonisolated struct ProfileParse: Codable, Hashable, Sendable {
    var chips: [ProfileChip]
    var unmatched: [String]
}

nonisolated struct ProfileChip: Codable, Hashable, Sendable {
    var group: String
    var id: String
    var level: String?                      // "strict" for allergies, nil otherwise; group "conditions" + id "pregnant" maps to Member.pregnant
}

nonisolated struct Health: Codable, Hashable, Sendable {
    var ok: Bool
    var gemini: Bool
    var firestore: Bool
    var version: String
}

// MARK: - Errors ({"error": {"code", "message", "next"}})

nonisolated struct APIErrorBody: Codable, Sendable {
    var error: APIErrorDetail
}

nonisolated struct APIErrorDetail: Codable, Hashable, Sendable {
    var code: String                        // not_found | unreadable | too_blurry | rate_limited | internal
    var message: String                     // short, user-facing ("Not in the database yet. Snap the ingredients.")
    var next: String                        // take_photo | retake | retry
}

// MARK: - Onboarding helpers

nonisolated extension Member {
    /// Applies an onboarding chip (from kb_index.json, /v1/kb/search or /v1/profile/parse) to this person.
    /// "conditions/pregnant" sets `pregnant`; "less_of/artificial_dyes" also sets `avoidDyes` (the rule that
    /// flags dyes reads avoidDyes, and is always on for baby and kid).
    mutating func apply(group: String, id: String, level: String? = nil) {
        switch group {
        case "allergies":
            allergies.removeAll { $0.id == id }
            allergies.append(Allergy(id: id, level: level ?? "strict"))
        case "conditions":
            if id == "pregnant" { pregnant = true } else if !conditions.contains(id) { conditions.append(id) }
        case "skin":
            if !skin.contains(id) { skin.append(id) }
        case "diets":
            if !diets.contains(id) { diets.append(id) }
        case "less_of":
            if !lessOf.contains(id) { lessOf.append(id) }
            if id == "artificial_dyes" { avoidDyes = true }
        default:
            break
        }
    }

    mutating func remove(group: String, id: String) {
        switch group {
        case "allergies": allergies.removeAll { $0.id == id }
        case "conditions": if id == "pregnant" { pregnant = false } else { conditions.removeAll { $0 == id } }
        case "skin": skin.removeAll { $0 == id }
        case "diets": diets.removeAll { $0 == id }
        case "less_of":
            lessOf.removeAll { $0 == id }
            if id == "artificial_dyes" { avoidDyes = false }
        default: break
        }
    }

    func has(group: String, id: String) -> Bool {
        switch group {
        case "allergies": return allergies.contains { $0.id == id }
        case "conditions": return id == "pregnant" ? pregnant : conditions.contains(id)
        case "skin": return skin.contains(id)
        case "diets": return diets.contains(id)
        case "less_of": return lessOf.contains(id)
        default: return false
        }
    }
}

/// kb_index.json (bundled in the app): every onboarding chip with labels and synonyms in en, vi, es.
/// Filters instantly with no network. Groups: allergies, conditions, skin, diets, less_of.
nonisolated struct KBIndex: Codable, Sendable {
    var version: Int
    var languages: [String]
    var groups: [String: [KBEntry]]

    static let groupOrder = ["allergies", "conditions", "skin", "diets", "less_of"]

    static func loadBundled(_ bundle: Bundle = .main) -> KBIndex? {
        guard let url = bundle.url(forResource: "kb_index", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? PearAPI.decoder().decode(KBIndex.self, from: data)
    }

    /// Chips for one onboarding screen, in file order.
    func chips(in group: String) -> [KBSuggestion] {
        (groups[group] ?? []).map { KBSuggestion(group: group, id: $0.id, label: $0.label) }
    }

    /// Search bar on the chip screens. Matches labels and synonyms in any language, ignoring case and accents.
    /// Empty result: send the text to PearAPI.parseProfile and ask the user to confirm what comes back.
    func search(_ query: String, in group: String? = nil, limit: Int = 10) -> [KBSuggestion] {
        let q = Self.fold(query)
        guard !q.isEmpty else { return [] }
        var scored: [(score: Int, suggestion: KBSuggestion)] = []
        for name in Self.groupOrder where group == nil || group == name {
            for entry in groups[name] ?? [] {
                let labels = entry.label.values.map(Self.fold)
                let synonyms = entry.synonyms.values.flatMap { $0 }.map(Self.fold)
                let score: Int
                if labels.contains(where: { $0.hasPrefix(q) }) { score = 0 }
                else if synonyms.contains(where: { $0.hasPrefix(q) }) { score = 1 }
                else if (labels + synonyms).contains(where: { $0.contains(q) }) { score = 2 }
                else { continue }
                scored.append((score, KBSuggestion(group: name, id: entry.id, label: entry.label)))
            }
        }
        return Array(scored.sorted { $0.score < $1.score }.prefix(limit).map(\.suggestion))
    }

    static func fold(_ s: String) -> String {
        s.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
            .replacingOccurrences(of: "đ", with: "d")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

nonisolated struct KBEntry: Codable, Hashable, Sendable {
    var id: String
    var label: [String: String]
    var synonyms: [String: [String]]
    var memberField: String?               // "pregnant" for conditions/pregnant, nil otherwise
}
