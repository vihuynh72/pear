//
//  ResultSheetView.swift
//  Pear
//
//  The result sheet (design pages c-16, c-17, c-18): product, the headline split into a big
//  title and a short line, one chip per person, "What we found", the source, then Listen /
//  Details and one main action:
//    - Less sure (partial, front of pack, matched by name) -> "Snap the ingredients" (+ "Type it in")
//    - Ripe for everyone, or not food                      -> "Scan another"
//    - otherwise                                            -> "See better picks"
//  Details (c-19) and Better picks (c-20) push inside the sheet. Every word the server sends
//  (headline, chip labels, reasons) is shown as sent; the sheet's own words follow the
//  shopper's language (ResultCopy).
//

import SwiftUI

struct ResultSheetView: View {
    @Environment(PearEnvironment.self) private var pear
    let flow: PearScanFlow
    /// Close the sheet and get the camera ready for the ingredient panel.
    var onSnapIngredients: () -> Void
    /// Close the sheet and switch the camera to Type.
    var onTypeItIn: () -> Void
    /// Close the sheet, back to the camera.
    var onScanAnother: () -> Void

    @State private var path: [Route] = []

    enum Route: Hashable { case details, picks }

    private var language: String { pear.displayLanguage }

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if let result = flow.result {
                    content(result)
                } else {
                    ProgressView().tint(.avocado700)
                }
            }
            .navigationDestination(for: Route.self) { route in
                switch route {
                case .details: ProductDetailsView(flow: flow, onBetterPicks: { path.append(.picks) })
                case .picks: BetterPicksView(flow: flow)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
    }

    private func content(_ result: ScanResult) -> some View {
        let parts = HeadlineParts.split(result.headline.text)
        return ScrollView {
            VStack(alignment: .leading, spacing: PearMetrics.Spacing.lg) {
                ProductHeader(product: result.product, language: language)

                VStack(alignment: .leading, spacing: PearMetrics.Spacing.xs) {
                    Text(verbatim: parts.title)
                        .font(PearFont.largeTitle)
                        .foregroundStyle(.cocoa)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    if !parts.body.isEmpty {
                        Text(verbatim: parts.body)
                            .font(PearFont.body)
                            .foregroundStyle(.latte)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)],
                          alignment: .leading, spacing: PearMetrics.Spacing.sm) {
                    ForEach(result.verdicts) { verdict in
                        VerdictChip(name: flow.member(verdict.memberId)?.name ?? verdict.memberId, verdict: verdict)
                    }
                }

                VStack(alignment: .leading, spacing: PearMetrics.Spacing.xs) {
                    Text(verbatim: ResultCopy.whatWeFound(language))
                        .font(PearFont.subheadline)
                        .foregroundStyle(.latte)
                    FindingsCard(lines: FindingLines.summary(result, flow: flow, language: language))
                }

                Text(verbatim: ResultCopy.source(result.product, language))
                    .font(PearFont.caption)
                    .foregroundStyle(.latte)

                actions(result)
            }
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.top, PearMetrics.Spacing.lg)
            .padding(.bottom, PearMetrics.Spacing.xl)
        }
        .background(Color.cream)
        .onAppear { Haptics.play(for: result) }
    }

    @ViewBuilder
    private func actions(_ result: ScanResult) -> some View {
        let product = result.product
        let lessSure = product.lessSure || product.askForIngredients
        let everyoneRipe = !result.verdicts.isEmpty && result.verdicts.allSatisfy { $0.state == .ripe }
        VStack(spacing: PearMetrics.Spacing.sm) {
            HStack(spacing: PearMetrics.Spacing.sm) {
                OutlineButton(title: ResultCopy.listen(language), systemImage: "speaker.wave.2") {
                    PearVoice.shared.speak(flow.listenLines())
                }
                if lessSure {
                    OutlineButton(title: ResultCopy.typeItIn(language), systemImage: "pencil", action: onTypeItIn)
                } else {
                    OutlineButton(title: ResultCopy.details(language), systemImage: "list.bullet.rectangle") {
                        path.append(.details)
                    }
                }
            }
            if lessSure {
                PrimaryButton(LocalizedStringKey(ResultCopy.snapIngredients(language)), systemImage: "camera", action: onSnapIngredients)
            } else if everyoneRipe || product.category != "food" {
                PrimaryButton(LocalizedStringKey(ResultCopy.scanAnother(language)), systemImage: "barcode.viewfinder", action: onScanAnother)
            } else {
                PrimaryButton(LocalizedStringKey(ResultCopy.seeBetterPicks(language)), systemImage: "sparkles") {
                    path.append(.picks)
                }
            }
        }
    }
}

// MARK: - Pieces shared by the result, details and picks screens

struct ProductHeader: View {
    let product: Product
    let language: String
    @State private var quantity: String?

    var body: some View {
        HStack(spacing: PearMetrics.Spacing.md) {
            ProductTile(product: product)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: product.name.isEmpty ? "Product" : product.name)
                    .font(PearFont.title)
                    .foregroundStyle(.cocoa)
                    .fixedSize(horizontal: false, vertical: true)
                Text(verbatim: subtitle)
                    .font(PearFont.body)
                    .foregroundStyle(.latte)
            }
            Spacer(minLength: 0)
        }
        .task(id: product.key) {
            quantity = await ProductSearch.facts(for: product.key)?.quantity
        }
    }

    private var subtitle: String {
        if product.photoKind == "front_only" { return ResultCopy.frontOnly(language) }
        return [product.brand, quantity ?? ""].filter { !$0.isEmpty }.joined(separator: ", ")
    }
}

struct FindingLine: Identifiable, Hashable {
    let id = UUID()
    let text: String
    let color: Color
}

struct FindingsCard: View {
    let lines: [FindingLine]

    var body: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.md) {
            ForEach(lines) { line in
                HStack(alignment: .firstTextBaseline, spacing: PearMetrics.Spacing.sm) {
                    Circle().fill(line.color).frame(width: 10, height: 10)
                    Text(verbatim: line.text)
                        .font(PearFont.body)
                        .foregroundStyle(.cocoa)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(PearMetrics.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(.avocado50))
    }
}

enum FindingLines {
    /// Result sheet: one line per person with something to say, then everyone who is fine.
    static func summary(_ result: ScanResult, flow: PearScanFlow, language: String) -> [FindingLine] {
        var lines: [FindingLine] = []
        let product = result.product
        if product.lessSure || product.askForIngredients {
            let read = ([product.name] + product.ingredients.prefix(3)).filter { !$0.isEmpty }.joined(separator: ", ")
            if !read.isEmpty { lines.append(FindingLine(text: ResultCopy.weRead(read, language), color: VerdictState.unsure.dot)) }
            if product.ingredients.isEmpty || product.askForIngredients {
                lines.append(FindingLine(text: ResultCopy.missingIngredients(language), color: VerdictState.unsure.dot))
            }
        }
        var fine: [String] = []
        for verdict in result.verdicts {
            let name = flow.member(verdict.memberId)?.name ?? verdict.memberId
            if verdict.state == .ripe {
                fine.append(name)
            } else if let reason = verdict.reasons.first {
                lines.append(FindingLine(text: "\(name): \(reason.text)", color: verdict.state.dot))
            }
        }
        let everyoneRipe = fine.count == result.verdicts.count && !result.verdicts.isEmpty
        if everyoneRipe {
            lines.append(FindingLine(text: ResultCopy.noAllergens(language), color: VerdictState.ripe.dot))
            // Small notes (e.g. "Some sodium") still show for the people they're about.
            for verdict in result.verdicts.prefix(4) {
                if let note = verdict.reasons.first(where: { $0.severity == "note" }) {
                    let name = flow.member(verdict.memberId)?.name ?? verdict.memberId
                    lines.append(FindingLine(text: "\(name): \(note.text)", color: VerdictState.ripe.dot))
                }
            }
        } else if !fine.isEmpty {
            lines.append(FindingLine(text: ResultCopy.nothingToWorry(ResultCopy.and(fine, language), language),
                                     color: VerdictState.ripe.dot))
        }
        return lines
    }

    /// Details: every reason for everyone who has one.
    static func everyReason(_ result: ScanResult, flow: PearScanFlow, language: String) -> [FindingLine] {
        var lines: [FindingLine] = []
        var fine: [String] = []
        for verdict in result.verdicts {
            let name = flow.member(verdict.memberId)?.name ?? verdict.memberId
            let reasons = verdict.reasons.filter { verdict.state != .ripe || $0.severity == "note" }
            if reasons.isEmpty {
                fine.append(name)
                continue
            }
            for reason in reasons {
                let color = reason.severity == "note" ? VerdictState.ripe.dot : verdict.state.dot
                lines.append(FindingLine(text: "\(name): \(reason.text)", color: color))
            }
        }
        if !fine.isEmpty {
            lines.append(FindingLine(text: ResultCopy.nothingToWorry(ResultCopy.and(fine, language), language),
                                     color: VerdictState.ripe.dot))
        }
        return lines
    }
}

struct OutlineButton: View {
    let title: String
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button { action() } label: {
            Label(title, systemImage: systemImage)
                .font(PearFont.headline)
                .foregroundStyle(.avocado700)
                .frame(maxWidth: .infinity, minHeight: PearMetrics.buttonHeight)
                .background {
                    RoundedRectangle(cornerRadius: PearMetrics.Radius.button, style: .continuous)
                        .strokeBorder(.avocado200, lineWidth: 1.5)
                }
                .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.button, style: .continuous))
        }
        .buttonStyle(PressableStyle())
    }
}

/// Back chevron for screens pushed inside the result sheet.
struct SheetBackButton: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        BackButton { dismiss() }
    }
}

#Preview("Result: bagels (allergy for Mia)") {
    ResultSheetView(flow: .previewShowing("scan_barcode"), onSnapIngredients: {}, onTypeItIn: {}, onScanAnother: {})
        .environment(PearEnvironment.preview())
}

#Preview("Result: front of pack (less sure)") {
    ResultSheetView(flow: .previewShowing("scan_photo_en_label"), onSnapIngredients: {}, onTypeItIn: {}, onScanAnother: {})
        .environment(PearEnvironment.preview())
}
