//
//  MemberSummary.swift
//  Pear
//
//  Turns member data into the short words the family list shows:
//  "You, English and Tiếng Việt", "Grandma, Tiếng Việt", "Kid, peanut and sesame allergy",
//  "Teen, vegan, Español". Never uses pronouns.
//

import SwiftUI

enum MemberSummary {
    /// Role first ("You", a relationship word, or the age group), then up to two details.
    static func subtitle(
        for member: Member,
        appearance: MemberAppearance,
        isOwner: Bool,
        ownerLanguage: String,
        catalog: ProfileCatalog = .shared
    ) -> String {
        var details: [String] = []

        if !member.allergies.isEmpty {
            let names = member.allergies.map { catalog.phrase(id: $0.id, group: "allergies") }
            let list = names.formatted(.list(type: .and))
            details.append(String(localized: "\(list) allergy", comment: "Family list, e.g. “peanut and sesame allergy”"))
        }
        if let diet = member.diets.first {
            details.append(catalog.phrase(id: diet, group: "diets"))
        }
        // An allergy is the most important detail; it stands alone so the line stays short.
        let hasAllergy = !member.allergies.isEmpty
        if isOwner, !hasAllergy {
            details.append(member.languages.map { languageName($0) }.formatted(.list(type: .and)))
        } else if !hasAllergy, member.type != "pet", member.primaryLanguage != ownerLanguage || details.isEmpty {
            details.append(languageName(member.primaryLanguage))
        }

        return ([role(for: member, appearance: appearance, isOwner: isOwner)] + details.prefix(2))
            .joined(separator: ", ")
    }

    static func role(for member: Member, appearance: MemberAppearance, isOwner: Bool) -> String {
        if isOwner { return String(localized: "You") }
        if let relationship = appearance.relationship, !relationship.isEmpty {
            return String(localized: String.LocalizationValue(relationship))
        }
        if member.type == "pet" { return speciesName(member.species) }
        return ageGroupName(member.ageGroup)
    }

    /// The server's age_group ("baby", "kid", "teen", "adult", "65+") in the family list's words.
    static func ageGroupName(_ group: String) -> String {
        switch group {
        case "baby": String(localized: "Baby")
        case "kid": String(localized: "Kid")
        case "teen": String(localized: "Teen")
        case "adult": String(localized: "Adult")
        case "65+": String(localized: "65 and up")
        default: group.capitalized
        }
    }

    static func speciesName(_ species: String?) -> String {
        switch species {
        case "dog": String(localized: "Dog")
        case "cat": String(localized: "Cat")
        default: String(localized: "Pet")
        }
    }

    /// "Tiếng Việt" for "vi": the language's own name, like on the Language screen.
    static func languageName(_ code: String) -> String {
        if let language = PearLanguage.all.first(where: { $0.code == code }) { return language.endonym }
        return Locale(identifier: code).localizedString(forLanguageCode: code) ?? code
    }

    // MARK: Avatar

    static func avatarKind(for member: Member) -> StickerAvatar.Kind {
        StickerAvatar.Kind(ageGroup: member.ageGroup, type: member.type)
    }

    /// Saved symbol, or the pet's species, or nil for the age-group symbol.
    static func avatarSymbol(for member: Member, appearance: MemberAppearance) -> String? {
        if let symbol = appearance.symbol { return symbol }
        guard member.type == "pet" else { return nil }
        switch member.species {
        case "dog": return "dog"
        case "cat": return "cat"
        default: return nil
        }
    }
}

/// A member's sticker: their color and symbol.
struct MemberAvatar: View {
    let member: Member
    let appearance: MemberAppearance
    var size: CGFloat = 48

    var body: some View {
        StickerAvatar(
            kind: MemberSummary.avatarKind(for: member),
            tint: appearance.tint,
            size: size,
            systemImage: MemberSummary.avatarSymbol(for: member, appearance: appearance)
        )
    }
}

#Preview("Demo family subtitles") {
    let store = HouseholdStore.preview()
    VStack(alignment: .leading, spacing: 12) {
        ForEach(store.members) { member in
            HStack(spacing: 12) {
                MemberAvatar(member: member, appearance: store.appearance(for: member.id))
                Text(verbatim: MemberSummary.subtitle(
                    for: member,
                    appearance: store.appearance(for: member.id),
                    isOwner: store.isOwner(member.id),
                    ownerLanguage: store.ownerLanguage
                ))
                .font(PearFont.body)
                .foregroundStyle(.cocoa)
            }
        }
    }
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .pearBackground()
}
