//
//  ProfileChipPicker.swift
//  Pear
//
//  Search field + chips for one profile group. Search filters the chips instantly
//  (labels and synonyms in English, Vietnamese and Spanish). When nothing matches,
//  "Ask Pear about “…”" sends the words to profile/parse and offers what comes back.
//

import SwiftUI

struct ProfileChipPicker: View {
    /// KBIndex group: allergies, conditions, skin, diets or less_of.
    let group: String
    let sectionTitle: LocalizedStringKey
    let searchPlaceholder: LocalizedStringKey
    @Bindable var draft: MemberDraft
    var initialQuery = ""

    @State private var query = ""
    @State private var didApplyInitialQuery = false

    private var catalog: ProfileCatalog { .shared }

    /// Search results (KBIndex.search), or every chip for the step plus any selected id kb_index doesn't list.
    private var visibleOptions: [KBSuggestion] {
        if !query.trimmingCharacters(in: .whitespaces).isEmpty {
            return catalog.search(query, in: group)
        }
        let all = catalog.chips(in: group)
        let extras = selectedIDs
            .filter { id in !all.contains { $0.id == id } }
            .map { KBSuggestion(group: group, id: $0, label: ["en": catalog.title(id: $0, group: group)]) }
        return all + extras
    }

    private var selectedIDs: [String] {
        switch group {
        case "allergies": draft.member.allergies.map(\.id)
        case "conditions": draft.member.conditions + (draft.member.pregnant ? [ProfileCatalog.pregnantID] : [])
        case "skin": draft.member.skin
        case "diets": draft.member.diets
        case "less_of": draft.member.lessOf
        default: []
        }
    }

    var body: some View {
        let options = visibleOptions
        VStack(alignment: .leading, spacing: 0) {
            SearchField(placeholder: searchPlaceholder, text: $query) {
                if let first = options.first, !draft.isSelected(first.id, in: group) {
                    draft.toggle(first.id, in: group)
                }
            }

            if options.isEmpty {
                AskPearSuggestions(term: query.trimmingCharacters(in: .whitespaces), group: group, draft: draft)
                    .padding(.top, PearMetrics.Spacing.lg)
            } else {
                Text(sectionTitle)
                    .font(PearFont.subheadline)
                    .foregroundStyle(.latte)
                    .padding(.top, PearMetrics.Spacing.lg)
                    .padding(.leading, PearMetrics.Spacing.xxs)
                    .accessibilityAddTraits(.isHeader)

                FlowLayout(spacing: PearMetrics.Spacing.xs, lineSpacing: PearMetrics.Spacing.xs) {
                    ForEach(options) { option in
                        SelectChip(
                            verbatim: option.label(for: ProfileCatalog.displayLanguage),
                            isSelected: draft.isSelected(option.id, in: group)
                        ) {
                            draft.toggle(option.id, in: group)
                        }
                    }
                }
                .padding(.top, PearMetrics.Spacing.sm)
            }
        }
        .animation(.snappy(duration: 0.2), value: options.map(\.id))
        .onAppear {
            guard !didApplyInitialQuery else { return }
            didApplyInitialQuery = true
            query = initialQuery
        }
    }
}

/// "Ask Pear about “carmine”": free text -> profile chips to confirm.
private struct AskPearSuggestions: View {
    let term: String
    let group: String
    @Bindable var draft: MemberDraft

    @Environment(PearEnvironment.self) private var pear

    private enum Phase {
        case idle
        case asking
        case found(ProfileParse)
        case failed(String)
    }

    @State private var phase: Phase = .idle
    @State private var askedTerm = ""

    var body: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
            Text("No chip for “\(term)” yet.")
                .font(PearFont.subheadline)
                .foregroundStyle(.latte)

            switch currentPhase {
            case .idle:
                askButton
            case .asking:
                HStack(spacing: PearMetrics.Spacing.sm) {
                    ProgressView()
                        .tint(.avocado700)
                    Text("Asking Pear…")
                        .font(PearFont.body)
                        .foregroundStyle(.cocoa)
                }
                .frame(minHeight: PearMetrics.minTapTarget)
            case .found(let response):
                suggestions(response)
            case .failed(let message):
                Text(verbatim: message)
                    .font(PearFont.subheadline)
                    .foregroundStyle(.latte)
                askButton
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A new search term resets the answer.
    private var currentPhase: Phase {
        askedTerm == term ? phase : .idle
    }

    private var askButton: some View {
        Button {
            Task { await ask() }
        } label: {
            HStack(spacing: PearMetrics.Spacing.sm) {
                Image(systemName: "sparkles")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(.avocado700)
                    .accessibilityHidden(true)
                Text("Ask Pear about “\(term)”")
                    .font(PearFont.headline)
                    .foregroundStyle(.cocoa)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.avocado700)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .fill(.avocado50)
            }
            .overlay {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .strokeBorder(.avocado200, lineWidth: 1.5)
            }
            .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous))
        }
        .buttonStyle(PressableStyle())
    }

    @ViewBuilder
    private func suggestions(_ response: ProfileParse) -> some View {
        if response.chips.isEmpty {
            Text("Pear couldn’t match “\(term)”. Try another word?")
                .font(PearFont.body)
                .foregroundStyle(.cocoa)
        } else {
            Text("Pear thinks you mean:")
                .font(PearFont.headline)
                .foregroundStyle(.cocoa)
            FlowLayout(spacing: PearMetrics.Spacing.xs, lineSpacing: PearMetrics.Spacing.xs) {
                ForEach(response.chips, id: \.self) { chip in
                    SelectChip(
                        verbatim: title(for: chip),
                        systemImage: "plus",
                        isSelected: draft.isSelected(chip.id, in: chip.group)
                    ) {
                        if draft.isSelected(chip.id, in: chip.group) {
                            draft.set(chip.id, in: chip.group, selected: false)
                        } else {
                            draft.apply(chip)
                        }
                    }
                }
            }
            Text("Tap to add. You can change it later.")
                .font(PearFont.caption)
                .foregroundStyle(.latte)
        }
    }

    /// "Eczema", or "Eczema · Skin" when the chip belongs to another step.
    private func title(for chip: ProfileChip) -> String {
        let base = ProfileCatalog.shared.title(id: chip.id, group: chip.group)
        guard chip.group != group else { return base }
        return "\(base) · \(Self.groupName(chip.group))"
    }

    private static func groupName(_ group: String) -> String {
        switch group {
        case "allergies": String(localized: "Allergy")
        case "conditions": String(localized: "Health")
        case "skin": String(localized: "Skin")
        case "diets": String(localized: "Diet")
        case "less_of": String(localized: "Less of")
        case "medications": String(localized: "Medicine")
        default: group
        }
    }

    private func ask() async {
        let text = term
        askedTerm = text
        phase = .asking
        do {
            let response = try await pear.api.parseProfile(text: text, language: pear.displayLanguage)
            guard askedTerm == text else { return }
            phase = .found(response)
        } catch let error as PearAPIError {
            guard askedTerm == text else { return }
            phase = .failed(error.userMessage)
        } catch {
            guard askedTerm == text else { return }
            phase = .failed(String(localized: "Can’t reach Pear right now. Try again?"))
        }
    }
}
