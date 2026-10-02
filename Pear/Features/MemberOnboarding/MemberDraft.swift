//
//  MemberDraft.swift
//  Pear
//
//  The member being added or edited, across the six onboarding steps. Nothing is saved
//  until Done; Back on the first step throws the draft away. Chips are applied with the
//  handoff kit's Member.apply / remove / has, so ids and fields match the server exactly.
//

import Foundation

@Observable
final class MemberDraft {
    var member: Member
    var appearance: MemberAppearance
    let isNew: Bool

    init(member: Member, appearance: MemberAppearance, isNew: Bool) {
        self.member = member
        self.appearance = appearance
        self.isNew = isNew
    }

    /// A draft for an existing member, or a blank one for "Add someone".
    static func make(memberID: String?, store: HouseholdStore) -> MemberDraft {
        if let memberID, let member = store.member(id: memberID) {
            return MemberDraft(member: member, appearance: store.appearance(for: memberID), isNew: false)
        }
        let blank = store.makeNewMember()
        return MemberDraft(member: blank.member, appearance: blank.appearance, isNew: true)
    }

    /// The typed name, trimmed. Empty while the person hasn't typed one yet.
    var name: String { member.name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var isPet: Bool { member.type == "pet" }

    /// Baby and kid get artificial dyes on by default (plan section 3).
    var hasKidDyeDefault: Bool { Self.isKidAge(member.ageGroup) }

    private static func isKidAge(_ group: String) -> Bool { group == "baby" || group == "kid" }

    // MARK: Basics

    /// "person" or "pet".
    func setType(_ type: String) {
        member.type = type
        if type == "pet" {
            if member.species == nil { member.species = "dog" }
        } else {
            member.species = nil
        }
    }

    /// The server's age_group: "baby", "kid", "teen", "adult" or "65+".
    /// Switching into baby / kid turns artificial dyes on (avoidDyes + the Less of chip);
    /// switching out turns that default off again.
    func setAgeGroup(_ group: String) {
        let wasKid = hasKidDyeDefault
        member.ageGroup = group
        guard wasKid != hasKidDyeDefault else { return }
        if hasKidDyeDefault {
            member.apply(group: "less_of", id: ProfileCatalog.artificialDyesID)
            member.avoidDyes = true
        } else {
            member.remove(group: "less_of", id: ProfileCatalog.artificialDyesID)
            member.avoidDyes = false
        }
    }

    // MARK: Chips

    func isSelected(_ id: String, in group: String) -> Bool {
        if group == "less_of", id == ProfileCatalog.artificialDyesID, member.avoidDyes { return true }
        return member.has(group: group, id: id)
    }

    func toggle(_ id: String, in group: String) {
        set(id, in: group, selected: !isSelected(id, in: group))
    }

    /// level: "strict" or "sensitive" (allergies only).
    func set(_ id: String, in group: String, selected: Bool, level: String = "strict") {
        if selected {
            member.apply(group: group, id: id, level: group == "allergies" ? level : nil)
        } else {
            member.remove(group: group, id: id)
        }
    }

    /// Adds a chip Pear suggested from free text (/v1/profile/parse).
    func apply(_ chip: ProfileChip) {
        member.apply(group: chip.group, id: chip.id, level: chip.group == "allergies" ? (chip.level ?? "strict") : nil)
    }

    // MARK: Allergy level

    func level(forAllergy id: String) -> String {
        member.allergies.first { $0.id == id }?.level ?? "strict"
    }

    func setLevel(_ level: String, forAllergy id: String) {
        guard let index = member.allergies.firstIndex(where: { $0.id == id }) else { return }
        member.allergies[index].level = level
    }

    // MARK: Saving

    /// The member as it will be saved. An empty name becomes "Dog", "Cat" or "Person 5".
    func finalized(memberCount: Int) -> Member {
        var member = member
        member.name = name
        if member.name.isEmpty {
            member.name = isPet
                ? MemberSummary.speciesName(member.species)
                : String(localized: "Person \(memberCount + (isNew ? 1 : 0))")
        }
        if member.languages.isEmpty { member.languages = ["en"] }
        // Baby and kid always avoid dyes on the server; keep the flag in step with the age.
        if hasKidDyeDefault, member.lessOf.contains(ProfileCatalog.artificialDyesID) { member.avoidDyes = true }
        return member
    }
}
