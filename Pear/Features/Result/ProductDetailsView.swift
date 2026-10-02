//
//  ProductDetailsView.swift
//  Pear
//
//  Details (design page c-19): every reason per person, the ingredient list with the exact
//  words that triggered a flag highlighted (Reason.matched: berry for allergy / hard, honey for
//  the rest), and sugar / sodium / saturated fat from Open Food Facts when the product came from
//  a barcode. "Report a mistake" opens the product on Open Food Facts, where data errors are fixed.
//

import SwiftUI

struct ProductDetailsView: View {
    @Environment(PearEnvironment.self) private var pear
    let flow: PearScanFlow
    var onBetterPicks: () -> Void

    @State private var facts: ProductFacts?

    private var language: String { pear.displayLanguage }

    var body: some View {
        if let result = flow.result {
            ScrollView {
                VStack(alignment: .leading, spacing: PearMetrics.Spacing.lg) {
                    SheetBackButton()
                        .frame(height: PearMetrics.backButtonSize)
                    ProductHeader(product: result.product, language: language)

                    section(ResultCopy.forEachPerson(language)) {
                        FindingsCard(lines: FindingLines.everyReason(result, flow: flow, language: language))
                    }

                    if !result.product.ingredients.isEmpty {
                        section(ResultCopy.ingredients(language)) {
                            Text(highlightedIngredients(result))
                                .font(PearFont.body)
                                .foregroundStyle(.cocoa)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(PearMetrics.Spacing.lg)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(.avocado50))
                        }
                    }

                    if let facts, facts.sugarsG != nil || facts.sodiumMg != nil || facts.saturatedFatG != nil {
                        section(ResultCopy.perServing(facts.basis, language)) {
                            nutritionCard(facts)
                        }
                    }

                    HStack(alignment: .firstTextBaseline) {
                        Text(verbatim: ResultCopy.source(result.product, language))
                            .font(PearFont.caption)
                            .foregroundStyle(.latte)
                        Spacer()
                        if let url = productPage(result.product.key) {
                            Link(ResultCopy.reportMistake(language), destination: url)
                                .font(PearFont.caption)
                                .foregroundStyle(.avocado700)
                        }
                    }

                    if result.product.category == "food",
                       !result.verdicts.allSatisfy({ $0.state == .ripe }) {
                        PrimaryButton(LocalizedStringKey(ResultCopy.seeBetterPicks(language)), systemImage: "sparkles",
                                      action: onBetterPicks)
                    }
                }
                .padding(.horizontal, PearMetrics.screenPadding)
                .padding(.vertical, PearMetrics.Spacing.md)
            }
            .pearBackground()
            .toolbar(.hidden, for: .navigationBar)
            .task(id: result.product.key) {
                facts = await ProductSearch.facts(for: result.product.key)
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.xs) {
            Text(verbatim: title)
                .font(PearFont.subheadline)
                .foregroundStyle(.latte)
                .accessibilityAddTraits(.isHeader)
            content()
        }
    }

    /// The ingredient list with every matched word colored by how serious its reason is.
    private func highlightedIngredients(_ result: ScanResult) -> AttributedString {
        var text = AttributedString(result.product.ingredients.joined(separator: ", "))
        let plain = String(text.characters)
        var marks: [(word: String, color: Color)] = []
        for verdict in result.verdicts {
            for reason in verdict.reasons where !reason.matched.isEmpty {
                let color: Color = (verdict.state == .allergy || reason.severity == "hard") ? .berry : Color(hex: 0xA7791F)
                marks += reason.matched.map { ($0, color) }
            }
        }
        // Allergy color wins when the same word is marked twice: apply the soft ones first.
        for mark in marks.sorted(by: { $0.color == .berry ? false : $1.color == .berry }) {
            var searchStart = plain.startIndex
            while let range = plain.range(of: mark.word, options: [.caseInsensitive, .diacriticInsensitive],
                                          range: searchStart..<plain.endIndex) {
                if let attributed = Range(range, in: text) {
                    text[attributed].foregroundColor = mark.color
                    text[attributed].font = PearFont.headline
                }
                searchStart = range.upperBound
            }
        }
        return text
    }

    private func nutritionCard(_ facts: ProductFacts) -> some View {
        VStack(spacing: PearMetrics.Spacing.md) {
            if let g = facts.sugarsG {
                nutrientRow(ResultCopy.sugar(language), value: format(g, unit: "g"), high: g / 50 >= 0.2)
            }
            if let mg = facts.sodiumMg {
                nutrientRow(ResultCopy.sodium(language), value: format(mg, unit: "mg"), high: mg / 2300 >= 0.2)
            }
            if let g = facts.saturatedFatG {
                nutrientRow(ResultCopy.saturatedFat(language), value: format(g, unit: "g"), high: g / 20 >= 0.2)
            }
        }
        .padding(PearMetrics.Spacing.lg)
        .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(.avocado50))
    }

    /// Dot turns honey at 20% of the daily value or more (FDA: 20% DV is high).
    private func nutrientRow(_ name: String, value: String, high: Bool) -> some View {
        HStack {
            Circle().fill(high ? VerdictState.easy.dot : VerdictState.ripe.dot).frame(width: 10, height: 10)
            Text(verbatim: name).font(PearFont.body).foregroundStyle(.cocoa)
            Spacer()
            Text(verbatim: value).font(PearFont.body).foregroundStyle(.cocoa)
        }
        .accessibilityElement(children: .combine)
    }

    private func format(_ value: Double, unit: String) -> String {
        let rounded = value >= 10 ? String(Int(value.rounded())) : String(format: "%.1f", value).replacingOccurrences(of: ".0", with: "")
        return "\(rounded) \(unit)"
    }

    private func productPage(_ key: String) -> URL? {
        let parts = key.split(separator: ":", maxSplits: 1)
        guard parts.count == 2 else { return nil }
        switch parts[0] {
        case "off": return URL(string: "https://world.openfoodfacts.org/product/\(parts[1])")
        case "obf": return URL(string: "https://world.openbeautyfacts.org/product/\(parts[1])")
        default: return nil
        }
    }
}
