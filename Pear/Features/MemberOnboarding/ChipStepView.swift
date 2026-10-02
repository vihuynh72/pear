//
//  ChipStepView.swift
//  Pear
//
//  Steps 3 to 6 (Health, Skin, Diet, Less of): a title, a short why, and chips with an
//  instant search. One view, configured per step.
//

import SwiftUI

struct ChipStepView: View {
    let step: MemberStep
    @Bindable var draft: MemberDraft
    var initialQuery = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title)
                .font(PearFont.largeTitle)
                .foregroundStyle(.cocoa)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)

            Text(subtitle)
                .font(PearFont.body)
                .foregroundStyle(.latte)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, PearMetrics.Spacing.xs)

            ProfileChipPicker(
                group: step.group,
                sectionTitle: sectionTitle,
                searchPlaceholder: searchPlaceholder,
                draft: draft,
                initialQuery: initialQuery
            )
            .padding(.top, PearMetrics.Spacing.lg)

            if step == .lessOf, draft.hasKidDyeDefault {
                Label("Artificial dyes: on by default for kids", systemImage: "info.circle")
                    .font(PearFont.caption)
                    .foregroundStyle(.latte)
                    .padding(.top, PearMetrics.Spacing.md)
                    .padding(.leading, PearMetrics.Spacing.xxs)
            }
        }
    }

    private var title: LocalizedStringKey {
        let name = draft.name
        switch step {
        case .health: return name.isEmpty ? "Anything for health?" : "Anything for \(name)’s health?"
        case .skin: return name.isEmpty ? "Any skin needs?" : "Any skin needs for \(name)?"
        case .diet: return name.isEmpty ? "Any way of eating?" : "How does \(name) eat?"
        case .lessOf: return name.isEmpty ? "Anything to cut back on?" : "Cut back on anything for \(name)?"
        case .basics, .allergies: return ""
        }
    }

    private var subtitle: LocalizedStringKey {
        switch step {
        case .health: "Pear keeps an eye on salt, sugar and fat for these. Pear isn’t medical advice."
        case .skin: "For soap, lotion and shampoo. Pear flags ingredients that often bother skin like this."
        case .diet: "Pear spots hidden animal ingredients, alcohol and more."
        case .lessOf: "Not a no, just a gentle heads-up when a label has a lot."
        case .basics, .allergies: ""
        }
    }

    private var sectionTitle: LocalizedStringKey {
        switch step {
        case .health: "Health"
        case .skin: "Skin"
        case .diet: "Diet"
        case .lessOf: "Less of"
        case .basics, .allergies: ""
        }
    }

    private var searchPlaceholder: LocalizedStringKey {
        switch step {
        case .health: "Search, like diabetes"
        case .skin: "Search, like eczema"
        case .diet: "Search, like halal or vegan"
        case .lessOf: "Search, like sugar"
        case .basics, .allergies: "Search"
        }
    }
}
