//  PearDemoHousehold.swift
//  Pear Swift kit — the demo family, identical to contracts/household.example.json.
//
//  What this file does
//  - `Household.demo`: Linh (adult, en+vi, fragrance sensitivity), Bà (senior, vi, high
//    blood pressure + prediabetes), Mia (kid, en, strict peanut + sesame, eczema, avoids
//    dyes) and Leo (teen, es, vegan). Use it for previews, mock mode and the stage demo.
//  - `Household.demoGuest`: an empty guest household (no members yet).
//
//  How to debug
//  - check.sh encodes `Household.demo` and compares it with
//    contracts/household.example.json; if that check fails, someone changed one side only.

import Foundation

nonisolated extension Household {
    static let demo = Household(
        householdId: "hh_demo",
        ownerLanguage: "en",
        memberLimit: 5,
        members: [
            Member(
                id: "m_linh", type: .person, species: nil, name: "Linh", ageGroup: .adult,
                languages: ["en", "vi"], allergies: [], conditions: [], pregnant: false,
                skin: ["fragrance_sensitivity"], diets: [], lessOf: [], avoidDyes: false, medications: []
            ),
            Member(
                id: "m_ba", type: .person, species: nil, name: "Bà", ageGroup: .senior,
                languages: ["vi"], allergies: [], conditions: ["high_blood_pressure", "prediabetes"],
                pregnant: false, skin: [], diets: [], lessOf: [], avoidDyes: false, medications: []
            ),
            Member(
                id: "m_mia", type: .person, species: nil, name: "Mia", ageGroup: .kid,
                languages: ["en"],
                allergies: [Allergy(id: "peanut", level: .strict), Allergy(id: "sesame", level: .strict)],
                conditions: [], pregnant: false, skin: ["eczema"], diets: [], lessOf: [],
                avoidDyes: true, medications: []
            ),
            Member(
                id: "m_leo", type: .person, species: nil, name: "Leo", ageGroup: .teen,
                languages: ["es"], allergies: [], conditions: [], pregnant: false,
                skin: [], diets: ["vegan"], lessOf: [], avoidDyes: false, medications: []
            ),
        ]
    )

    /// Guest mode before onboarding: no members (the backend evaluates nobody).
    static let demoGuest = Household(householdId: "hh_guest", ownerLanguage: "en", memberLimit: 5, members: [])
}
