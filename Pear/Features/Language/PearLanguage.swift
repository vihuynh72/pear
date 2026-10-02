//
//  PearLanguage.swift
//  Pear
//
//  The languages Pear speaks today, plus a search that understands a language's name
//  typed in any language: "vietnamese", "tiếng việt", "tieng viet", "vietnamita",
//  "spanish", "español", "inglés", "tiếng anh", "英语" and so on.
//

import Foundation
import SwiftUI

struct PearLanguage: Identifiable, Hashable {
    /// BCP 47 language code used by the backend: "en", "vi" or "es".
    let code: String
    /// The language's own name, shown as the card title.
    let endonym: String
    /// The second line on the card.
    let subtitle: LocalizedStringKey
    /// Extra spellings people type that the system locale names may miss.
    let extraNames: [String]

    var id: String { code }

    static func == (lhs: PearLanguage, rhs: PearLanguage) -> Bool { lhs.code == rhs.code }
    func hash(into hasher: inout Hasher) { hasher.combine(code) }

    static let english = PearLanguage(
        code: "en",
        endonym: "English",
        subtitle: "Labels, voice and answers",
        extraNames: ["english", "ingles", "anh", "tieng anh", "en"]
    )
    static let vietnamese = PearLanguage(
        code: "vi",
        endonym: "Tiếng Việt",
        subtitle: "Vietnamese",
        extraNames: ["vietnamese", "vietnamita", "tieng viet", "viet", "vi"]
    )
    static let spanish = PearLanguage(
        code: "es",
        endonym: "Español",
        subtitle: "Spanish",
        extraNames: ["spanish", "espanol", "castellano", "tay ban nha", "es"]
    )

    /// Live languages, in the order they appear.
    static let all: [PearLanguage] = [.english, .vietnamese, .spanish]

    /// Coming soon, shown as disabled pills. Searchable so Pear can say "on the way".
    static let comingSoon: [PearLanguage] = [
        PearLanguage(code: "zh", endonym: "中文", subtitle: "Chinese", extraNames: ["chinese", "mandarin", "chino", "tieng trung"]),
        PearLanguage(code: "fil", endonym: "Tagalog", subtitle: "Tagalog", extraNames: ["tagalog", "filipino", "tl"]),
        PearLanguage(code: "ko", endonym: "한국어", subtitle: "Korean", extraNames: ["korean", "coreano", "tieng han"])
    ]

    /// The device language when Pear speaks it, otherwise English.
    static var deviceDefault: String {
        let code = Locale.current.language.languageCode?.identifier ?? "en"
        return all.contains { $0.code == code } ? code : "en"
    }

    static func named(_ code: String) -> PearLanguage? {
        all.first { $0.code == code }
    }
}

// MARK: - Search

enum LanguageSearch {
    /// Languages whose name, in any language, matches the query. Empty query returns all.
    static func filter(_ languages: [PearLanguage], query: String) -> [PearLanguage] {
        let needle = normalize(query)
        guard !needle.isEmpty else { return languages }
        return languages.filter { language in
            names(for: language).contains { name in matches(name: name, needle: needle) }
        }
    }

    /// Lowercased, accents removed, so "Tiếng Việt" and "tieng viet" are the same.
    nonisolated static func normalize(_ text: String) -> String {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
            .replacingOccurrences(of: "đ", with: "d")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A name matches when it, or any word in it, starts with the query.
    private static func matches(name: String, needle: String) -> Bool {
        if name.hasPrefix(needle) { return true }
        let words = name.split { !$0.isLetter && !$0.isNumber }
        return words.contains { $0.hasPrefix(needle) }
    }

    private static var cache: [String: Set<String>] = [:]

    /// Every name for the language: its own name, extra spellings and the name of the
    /// language in every locale the system knows.
    private static func names(for language: PearLanguage) -> Set<String> {
        if let cached = cache[language.code] { return cached }
        var names = Set(language.extraNames.map(normalize))
        names.insert(normalize(language.endonym))
        for locale in displayLocales {
            if let name = locale.localizedString(forLanguageCode: language.code) {
                names.insert(normalize(name))
            }
        }
        cache[language.code] = names
        return names
    }

    /// One locale per language the system knows about.
    private static let displayLocales: [Locale] = {
        var seen = Set<String>()
        var locales: [Locale] = []
        for identifier in Locale.availableIdentifiers {
            let code = Locale(identifier: identifier).language.languageCode?.identifier ?? identifier
            if seen.insert(code).inserted {
                locales.append(Locale(identifier: code))
            }
        }
        return locales
    }()
}
