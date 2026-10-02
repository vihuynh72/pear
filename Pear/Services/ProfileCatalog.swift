//
//  ProfileCatalog.swift
//  Pear
//
//  The chips on the onboarding steps (allergies, conditions, skin, diets, less_of), straight
//  from the bundled kb_index.json through the handoff kit's KBIndex. Ids are the server's ids
//  (tree_nuts, sulfites, molluscs, pregnant, artificial_dyes...). Search matches labels and
//  synonyms in English, Vietnamese and Spanish, so "dairy", "đậu phộng" and "cacahuate" work.
//
//  Usage:
//      let catalog = ProfileCatalog.shared
//      catalog.chips(in: "allergies")                  // every chip for the step, file order
//      catalog.search("cacahuate", in: "allergies")    // [Peanut]
//      catalog.title(id: "sesame", group: "allergies") // "Sesame"
//

import Foundation

final class ProfileCatalog {
    static let shared = ProfileCatalog()

    /// The chip that sets `Member.pregnant` instead of adding a condition.
    static let pregnantID = "pregnant"
    /// The less_of chip that also drives `Member.avoidDyes` (on by default for baby and kid).
    static let artificialDyesID = "artificial_dyes"
    /// Chip labels follow the app's copy, which is English today.
    static let displayLanguage = "en"

    /// "Common allergies" on the Allergies step, in the design's order (mockup a-8). The rest of
    /// kb_index (celery, lupin, molluscs) is found through search, and a selected one still shows.
    static let commonAllergyIDs = ["milk", "egg", "peanut", "tree_nuts", "sesame", "soy", "wheat", "fish",
                                   "shellfish", "gluten", "lactose", "mustard", "sulfites", "corn", "kiwi"]

    /// English chip words from the design where kb_index words differ ("Peanuts", not "Peanut";
    /// the Less of step already says "less", so its chips read "Sugar", not "Less sugar").
    static let chipTitles: [String: String] = [
        "allergies/peanut": "Peanuts", "allergies/egg": "Eggs",
        "less_of/sugar": "Sugar", "less_of/sodium": "Sodium", "less_of/saturated_fat": "Saturated fat",
        "less_of/caffeine": "Caffeine", "less_of/artificial_dyes": "Artificial dyes", "less_of/palm_oil": "Palm oil",
    ]

    let index: KBIndex?

    init(bundle: Bundle = .main) {
        index = KBIndex.loadBundled(bundle)
        #if DEBUG
        if index == nil { print("[Pear] kb_index.json missing from the bundle") }
        #endif
    }

    /// The chips for one onboarding step: the common allergies for "allergies", else kb_index.json order.
    func chips(in group: String) -> [KBSuggestion] {
        let all = (index?.chips(in: group) ?? []).map(withDesignTitle)
        guard group == "allergies" else { return all }
        return Self.commonAllergyIDs.compactMap { id in all.first { $0.id == id } }
    }

    func suggestion(id: String, in group: String) -> KBSuggestion? {
        index?.chips(in: group).first { $0.id == id }.map(withDesignTitle)
    }

    /// Instant search over labels and synonyms (any language), across every chip of the step.
    /// Empty query: the step's default chips.
    func search(_ query: String, in group: String) -> [KBSuggestion] {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return chips(in: group) }
        return (index?.search(query, in: group, limit: 20) ?? []).map(withDesignTitle)
    }

    /// "Peanuts", "High blood pressure", or the id made readable when kb_index doesn't know it.
    func title(id: String, group: String, language: String = ProfileCatalog.displayLanguage) -> String {
        if let suggestion = suggestion(id: id, in: group) { return suggestion.label(for: language) }
        let words = id.replacingOccurrences(of: "_", with: " ")
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    /// Lowercase words used inside sentences, from kb_index's own (singular) label: "peanut and sesame allergy".
    func phrase(id: String, group: String) -> String {
        if let base = index?.chips(in: group).first(where: { $0.id == id }) {
            return base.label(for: Self.displayLanguage).lowercased()
        }
        return title(id: id, group: group).lowercased()
    }

    private func withDesignTitle(_ suggestion: KBSuggestion) -> KBSuggestion {
        guard let title = Self.chipTitles["\(suggestion.group)/\(suggestion.id)"] else { return suggestion }
        var copy = suggestion
        copy.label[Self.displayLanguage] = title
        return copy
    }
}
