//
//  HouseholdStore.swift
//  Pear
//
//  The family Pear checks every label for. Holds the handoff kit's `Household` (sent with
//  every request) plus UI-only looks per member (sticker color, avatar symbol, a relationship
//  word like "Grandma"), which never leave the phone.
//
//  The household is saved exactly like `PearHouseholdStore` (Application Support/household.json,
//  snake_case through PearAPI.encoder). The looks are saved next to it in household_ui.json.
//  When nothing is saved yet, the demo family (PearDemo.household) is loaded.
//
//  Usage:
//      @Environment(PearEnvironment.self) private var pear
//      pear.household.members                       // Linh, Bà, Mia, Leo
//      pear.household.household                     // the model sent to the API
//      pear.household.appearance(for: member.id).tint
//      pear.household.save(member, appearance: look)    // add or update
//      pear.household.remove(id: member.id)
//      pear.household.spotsLeft                     // "N spots left"
//      pear.household.onChange = { flow.household = $0 }   // PearEnvironment does this
//

import Foundation

/// How a member looks in the app. UI only, never sent to the backend.
nonisolated struct MemberAppearance: Codable, Hashable, Sendable {
    /// StickerAvatar.Tint raw value: blush, honey, avocado, peach, mist.
    var tintName: String
    /// SF Symbol override; nil = picked from the age group or pet species.
    var symbol: String? = nil
    /// Shown instead of the age group in the family list, e.g. "Grandma". Never a pronoun.
    var relationship: String? = nil

    enum CodingKeys: String, CodingKey {
        case tintName = "tint"
        case symbol, relationship
    }
}

@Observable
final class HouseholdStore {
    private(set) var household: Household
    private(set) var appearances: [String: MemberAppearance]
    /// The person using the phone ("You" in the family list).
    private(set) var ownerMemberID: String?

    /// Called after every change with the new household. PearEnvironment sets
    /// `flow.household = household` here so the next scan uses it.
    @ObservationIgnored var onChange: ((Household) -> Void)?

    @ObservationIgnored private let persists: Bool

    /// `persists: false` keeps everything in memory (previews, tests).
    init(persists: Bool = true) {
        self.persists = persists
        if persists {
            let loaded = PearHouseholdStore.load()          // falls back to the demo family
            household = loaded
            let ui = HouseholdUIStorage.load()
            if let ui {
                appearances = ui.appearances
                ownerMemberID = ui.ownerMemberID ?? loaded.members.first?.id
            } else if loaded == PearDemo.household {
                appearances = Self.demoAppearances
                ownerMemberID = Self.demoOwnerID
            } else {
                appearances = [:]
                ownerMemberID = loaded.members.first?.id
            }
        } else {
            household = PearDemo.household
            appearances = Self.demoAppearances
            ownerMemberID = Self.demoOwnerID
        }
    }

    /// In-memory store with the demo family, for previews.
    static func preview() -> HouseholdStore { HouseholdStore(persists: false) }

    // MARK: Reading

    var members: [Member] { household.members }
    var memberLimit: Int { household.memberLimit }
    var spotsLeft: Int { max(0, household.memberLimit - household.members.count) }
    var canAddMember: Bool { spotsLeft > 0 }
    var ownerLanguage: String { household.ownerLanguage }

    func member(id: String) -> Member? { household.members.first { $0.id == id } }

    func isOwner(_ memberID: String) -> Bool { memberID == ownerMemberID }

    /// The first language of the person using the phone (Ask Pear's default pill).
    var askerLanguage: String {
        ownerMemberID.flatMap { member(id: $0) }?.primaryLanguage ?? household.ownerLanguage
    }

    func appearance(for memberID: String) -> MemberAppearance {
        if let look = appearances[memberID] { return look }
        let index = household.members.firstIndex { $0.id == memberID } ?? household.members.count
        return MemberAppearance(tintName: Self.tintCycle[index % Self.tintCycle.count].rawValue)
    }

    /// A blank member ("m_" + short UUID) and a sticker color nobody uses yet, for "Add someone".
    func makeNewMember() -> (member: Member, appearance: MemberAppearance) {
        let id = "m_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8).lowercased()
        let used = Set(household.members.map { appearance(for: $0.id).tintName })
        let tint = Self.tintCycle.first { !used.contains($0.rawValue) } ?? .mist
        let member = Member(id: id, name: "", languages: [household.ownerLanguage])
        return (member, MemberAppearance(tintName: tint.rawValue))
    }

    // MARK: Changing

    /// Adds the member, or updates it when the id already exists. Returns false at the member limit.
    @discardableResult
    func save(_ member: Member, appearance: MemberAppearance) -> Bool {
        if let index = household.members.firstIndex(where: { $0.id == member.id }) {
            household.members[index] = member
        } else {
            guard canAddMember else { return false }
            household.members.append(member)
            if ownerMemberID == nil { ownerMemberID = member.id }
        }
        appearances[member.id] = appearance
        persist()
        return true
    }

    func remove(id: String) {
        household.members.removeAll { $0.id == id }
        appearances[id] = nil
        if ownerMemberID == id { ownerMemberID = household.members.first?.id }
        persist()
    }

    /// The language picked on the Language screen: the household's headline language and
    /// the first language of the person using the phone.
    func setOwnerLanguage(_ code: String) {
        household.ownerLanguage = code
        if let ownerMemberID, let index = household.members.firstIndex(where: { $0.id == ownerMemberID }) {
            var languages = household.members[index].languages.filter { $0 != code }
            languages.insert(code, at: 0)
            household.members[index].languages = languages
        }
        persist()
    }

    func setMemberLimit(_ limit: Int) {
        household.memberLimit = max(limit, household.members.count, 1)
        persist()
    }

    /// Starts over with Linh, Bà, Mia and Leo (PearDemo.household).
    func loadDemo() {
        household = PearDemo.household
        appearances = Self.demoAppearances
        ownerMemberID = Self.demoOwnerID
        persist()
    }

    private func persist() {
        if persists {
            PearHouseholdStore.save(household)
            HouseholdUIStorage.save(HouseholdUIState(ownerMemberID: ownerMemberID, appearances: appearances))
        }
        onChange?(household)
    }

    // MARK: Demo looks (a-6)

    static let demoOwnerID = "m_linh"
    static let demoAppearances: [String: MemberAppearance] = [
        "m_linh": MemberAppearance(tintName: StickerAvatar.Tint.blush.rawValue),
        // "Bà" is Vietnamese for grandma, so the relationship comes from the data, not a guess.
        "m_ba": MemberAppearance(tintName: StickerAvatar.Tint.honey.rawValue, relationship: "Grandma"),
        "m_mia": MemberAppearance(tintName: StickerAvatar.Tint.avocado.rawValue),
        "m_leo": MemberAppearance(tintName: StickerAvatar.Tint.peach.rawValue),
    ]

    private static let tintCycle: [StickerAvatar.Tint] = [.blush, .honey, .avocado, .peach, .mist]
}

extension MemberAppearance {
    var tint: StickerAvatar.Tint {
        get { StickerAvatar.Tint(rawValue: tintName) ?? .avocado }
        set { tintName = newValue.rawValue }
    }
}

// MARK: - UI-only storage

/// UI-only state saved next to the household.
nonisolated struct HouseholdUIState: Codable, Sendable {
    var ownerMemberID: String?
    var appearances: [String: MemberAppearance]

    enum CodingKeys: String, CodingKey {
        case ownerMemberID = "owner_member_id"
        case appearances
    }
}

/// household_ui.json, in the same folder as PearHouseholdStore's household.json.
nonisolated enum HouseholdUIStorage {
    static var fileURL: URL {
        PearHouseholdStore.fileURL.deletingLastPathComponent().appendingPathComponent("household_ui.json")
    }

    static func load() -> HouseholdUIState? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(HouseholdUIState.self, from: data)
    }

    static func save(_ state: HouseholdUIState) {
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try JSONEncoder().encode(state).write(to: fileURL, options: .atomic)
        } catch {
            #if DEBUG
            print("[Pear] household_ui.json save failed: \(error)")
            #endif
        }
    }
}
