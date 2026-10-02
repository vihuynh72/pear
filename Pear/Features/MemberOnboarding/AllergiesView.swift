//
//  AllergiesView.swift
//  Pear
//
//  Step 2, "Any allergies for Mia?" (a-8, a-9). Chips filter as you type; every chosen
//  allergy gets a card with Strict (default) or Ingredients only.
//

import SwiftUI

struct AllergiesView: View {
    @Bindable var draft: MemberDraft
    var initialQuery = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(draft.name.isEmpty ? "Any allergies?" : "Any allergies for \(draft.name)?")
                .font(PearFont.largeTitle)
                .foregroundStyle(.cocoa)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)

            Text("Tap all that apply. Pear checks every label for these, even the small print.")
                .font(PearFont.body)
                .foregroundStyle(.latte)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, PearMetrics.Spacing.xs)

            ProfileChipPicker(
                group: "allergies",
                sectionTitle: "Common allergies",
                searchPlaceholder: "Search, like sesame or kiwi",
                draft: draft,
                initialQuery: initialQuery
            )
            .padding(.top, PearMetrics.Spacing.lg)

            VStack(spacing: PearMetrics.Spacing.sm) {
                ForEach(draft.member.allergies, id: \.id) { allergy in
                    AllergyLevelCard(
                        title: ProfileCatalog.shared.title(id: allergy.id, group: "allergies"),
                        level: Binding(
                            get: { draft.level(forAllergy: allergy.id) },
                            set: { draft.setLevel($0, forAllergy: allergy.id) }
                        )
                    )
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
            .padding(.top, PearMetrics.Spacing.lg)
            .animation(.snappy(duration: 0.25), value: draft.member.allergies.map(\.id))
        }
    }
}

/// "Sesame / How careful should Pear be?" with Strict / Ingredients only.
struct AllergyLevelCard: View {
    let title: String
    /// The server's allergy level: "strict" or "sensitive" (shown as "Ingredients only").
    @Binding var level: String

    var body: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.md) {
            HStack(spacing: PearMetrics.Spacing.md) {
                Image(systemName: "allergens")
                    .font(.system(size: PearMetrics.iconSize, weight: .medium))
                    .foregroundStyle(.berry)
                    .frame(width: 52, height: 52)
                    .background { Circle().fill(.blush) }
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: title)
                        .font(PearFont.headline)
                        .foregroundStyle(.cocoa)
                    Text("How careful should Pear be?")
                        .font(PearFont.subheadline)
                        .foregroundStyle(.latte)
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)

            PearSegmentedControl(["strict", "sensitive"], selection: $level) { option in
                option == "strict" ? "Strict" : "Ingredients only"
            }

            Text(level == "strict"
                 ? "Strict means even “may contain” counts."
                 : "Pear flags it when it’s in the ingredients. “May contain” shows as a note.")
                .font(PearFont.caption)
                .foregroundStyle(.latte)
                .fixedSize(horizontal: false, vertical: true)
                .contentTransition(.opacity)
        }
        .padding(PearMetrics.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                .fill(.white)
        }
        .overlay {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                .strokeBorder(.mist, lineWidth: PearMetrics.borderWidth)
        }
        .accessibilityElement(children: .contain)
    }
}
