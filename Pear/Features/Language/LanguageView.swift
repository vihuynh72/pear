//
//  LanguageView.swift
//  Pear
//
//  "Which language feels like home?" (a-5). Type a language's name in any language or
//  tap a card. Continue hands back the language code ("en", "vi" or "es").
//

import SwiftUI

struct LanguageView: View {
    var onBack: (() -> Void)?
    var onContinue: (String) -> Void

    @State private var selection: String
    @State private var query: String

    init(
        initialSelection: String = PearLanguage.deviceDefault,
        initialQuery: String = "",
        onBack: (() -> Void)? = nil,
        onContinue: @escaping (String) -> Void
    ) {
        self.onBack = onBack
        self.onContinue = onContinue
        self.selection = initialSelection
        self.query = initialQuery
    }

    private var matches: [PearLanguage] {
        LanguageSearch.filter(PearLanguage.all, query: query)
    }

    var body: some View {
        ScreenScaffold {
            VStack(alignment: .leading, spacing: 0) {
                if let onBack {
                    BackButton(action: onBack)
                        .padding(.bottom, PearMetrics.Spacing.md)
                }

                Text("Which language\nfeels like home?")
                    .font(PearFont.largeTitle)
                    .foregroundStyle(.cocoa)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)

                Text("Pear reads labels and answers out loud in the language you pick. You can change it any time.")
                    .font(PearFont.body)
                    .foregroundStyle(.latte)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, PearMetrics.Spacing.xs)

                SearchField(placeholder: "Type a language, any language", text: $query) {
                    if let first = matches.first { selection = first.code }
                }
                .padding(.top, PearMetrics.Spacing.lg)

                languageCards
                    .padding(.top, PearMetrics.Spacing.md)

                comingSoon
                    .padding(.top, PearMetrics.Spacing.lg)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.top, PearMetrics.Spacing.xxs)
        } footer: {
            PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {
                onContinue(selection)
            }
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.bottom, PearMetrics.Spacing.xs)
        }
    }

    @ViewBuilder
    private var languageCards: some View {
        let shown = matches
        VStack(spacing: PearMetrics.Spacing.sm) {
            if shown.isEmpty {
                noMatchNote
                    .font(PearFont.subheadline)
                    .foregroundStyle(.latte)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 4)
            }
            ForEach(shown.isEmpty ? PearLanguage.all : shown) { language in
                OptionCard(
                    title: Text(verbatim: language.endonym),
                    subtitle: Text(language.subtitle),
                    isSelected: selection == language.code
                ) {
                    selection = language.code
                }
            }
        }
        .animation(.snappy(duration: 0.2), value: shown.map(\.code))
    }

    @ViewBuilder
    private var noMatchNote: some View {
        if let soon = LanguageSearch.filter(PearLanguage.comingSoon, query: query).first {
            Label("\(soon.endonym) is on the way. Pick one of these for now.", systemImage: "globe")
        } else {
            Label("Pear doesn’t speak that one yet. Pick one of these for now.", systemImage: "globe")
        }
    }

    private var comingSoon: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
            Text("More languages are on the way")
                .font(PearFont.subheadline)
                .foregroundStyle(.latte)

            FlowLayout(spacing: PearMetrics.Spacing.xs, lineSpacing: PearMetrics.Spacing.xs) {
                ForEach(PearLanguage.comingSoon) { language in
                    SoonPill(name: language.endonym)
                }
            }
        }
    }
}

/// A disabled pill for a language that is coming later.
private struct SoonPill: View {
    let name: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(verbatim: name)
                .font(PearFont.body)
                .foregroundStyle(Color.cocoa.opacity(0.8))
            Text("Soon")
                .font(PearFont.caption)
                .foregroundStyle(.latte)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .frame(minHeight: 38)
        .background { Capsule().fill(.mist) }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("\(name), coming soon"))
    }
}

#Preview {
    LanguageView(initialSelection: "en", onBack: {}, onContinue: { _ in })
}

#Preview("Search in any language") {
    LanguageView(initialSelection: "vi", initialQuery: "vietnamita", onBack: {}, onContinue: { _ in })
}

#Preview("Coming soon match") {
    LanguageView(initialSelection: "en", initialQuery: "korean", onBack: {}, onContinue: { _ in })
}
