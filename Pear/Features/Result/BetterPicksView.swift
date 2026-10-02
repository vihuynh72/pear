//
//  BetterPicksView.swift
//  Pear
//
//  Better picks (design page c-20): up to three food products that fit everyone being checked
//  (PearScanFlow.picks, loaded right after the verdict), each with a grounded price when Google
//  Search found one (PearScanFlow.prices). Google's grounding terms ask apps to show the Search
//  Suggestions that come with grounded prices, so they sit under the list. "Add to my list"
//  saves the picks on the phone (ShoppingList; the camera's History sheet shows them).
//

import SwiftUI

struct BetterPicksView: View {
    @Environment(PearEnvironment.self) private var pear
    let flow: PearScanFlow

    private var language: String { pear.displayLanguage }
    private var peopleCount: Int { flow.household.members.count }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PearMetrics.Spacing.lg) {
                SheetBackButton()
                    .frame(height: PearMetrics.backButtonSize)

                HStack(spacing: PearMetrics.Spacing.md) {
                    if let product = flow.result?.product { ProductTile(product: product) }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: ResultCopy.betterPicks(language))
                            .font(PearFont.title)
                            .foregroundStyle(.cocoa)
                            .accessibilityAddTraits(.isHeader)
                        Text(verbatim: ResultCopy.insteadOf(flow.result?.product.name ?? "", peopleCount, language))
                            .font(PearFont.body)
                            .foregroundStyle(.latte)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                content

                Text(verbatim: ResultCopy.pickedForFamily(language))
                    .font(PearFont.caption)
                    .foregroundStyle(.latte)

                if !flow.picks.isEmpty {
                    let added = flow.picks.allSatisfy { pear.list.contains($0.product.key) }
                    PrimaryButton(LocalizedStringKey(added ? ResultCopy.addedToList(language) : ResultCopy.addToList(language)),
                                  systemImage: added ? "checkmark" : "plus") {
                        pear.list.add(flow.picks)
                        Haptics.ripe()
                    }
                    .disabled(added)
                }
            }
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.vertical, PearMetrics.Spacing.md)
        }
        .pearBackground()
        .toolbar(.hidden, for: .navigationBar)
    }

    @ViewBuilder
    private var content: some View {
        if flow.result?.product.category != "food" {
            message(ResultCopy.picksFoodOnly(language))
        } else if flow.picksLoading {
            HStack(spacing: PearMetrics.Spacing.sm) {
                ProgressView().tint(.avocado700)
                Text(verbatim: ResultCopy.findingPicks(language)).font(PearFont.body).foregroundStyle(.latte)
            }
            .padding(.vertical, PearMetrics.Spacing.lg)
        } else if flow.picks.isEmpty {
            message(ResultCopy.noPicks(language))
        } else {
            VStack(spacing: PearMetrics.Spacing.sm) {
                ForEach(flow.picks) { pick in
                    pickCard(pick)
                }
            }
            if flow.pricesLoading {
                Text(verbatim: ResultCopy.checkingPrices(language)).font(PearFont.caption).foregroundStyle(.latte)
            }
            ForEach(searchSuggestions, id: \.self) { html in
                PearSearchSuggestions(html: html).frame(height: 56)
            }
        }
    }

    private var searchSuggestions: [String] {
        let html = flow.picks.compactMap { flow.prices[$0.product.key]?.searchEntryHtml }.filter { !$0.isEmpty }
        return Array(Set(html)).sorted().prefix(1).map { $0 }
    }

    private func pickCard(_ pick: PickItem) -> some View {
        HStack(spacing: PearMetrics.Spacing.md) {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.honey)
                .frame(width: 64, height: 64)
                .overlay {
                    if let url = pick.product.imageUrl.flatMap(URL.init(string:)) {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { pickIcon(pick) }
                            .frame(width: 64, height: 64)
                            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    } else {
                        pickIcon(pick)
                    }
                }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: [pick.product.brand, pick.product.name].filter { !$0.isEmpty }.joined(separator: " "))
                    .font(PearFont.headline)
                    .foregroundStyle(.cocoa)
                    .fixedSize(horizontal: false, vertical: true)
                if let price = flow.prices[pick.product.key] {
                    Text(verbatim: ResultCopy.price(price, language))
                        .font(PearFont.body)
                        .foregroundStyle(.latte)
                    Text(verbatim: ResultCopy.foundWithGoogle(language))
                        .font(PearFont.caption)
                        .foregroundStyle(.latte)
                } else {
                    Text(verbatim: ResultCopy.ripeForAll(peopleCount, language))
                        .font(PearFont.body)
                        .foregroundStyle(.latte)
                }
            }
            Spacer(minLength: 0)
            if pear.list.contains(pick.product.key) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.avocado700)
                    .accessibilityLabel(Text(verbatim: ResultCopy.addedToList(language)))
            }
        }
        .padding(PearMetrics.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(.avocado50))
        .accessibilityElement(children: .combine)
    }

    private func pickIcon(_ pick: PickItem) -> some View {
        Image(systemName: ProductTile.symbol(for: pick.product.category))
            .font(.system(size: 26, weight: .medium))
            .foregroundStyle(.cocoa)
    }

    private func message(_ text: String) -> some View {
        Text(verbatim: text)
            .font(PearFont.body)
            .foregroundStyle(.latte)
            .padding(.vertical, PearMetrics.Spacing.lg)
    }
}
