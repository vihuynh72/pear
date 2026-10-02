//
//  HistorySheet.swift
//  Pear
//
//  The camera's History button: recent results (tap to reopen, no network needed) and the
//  shopping list from Better picks. Both live only on the phone.
//

import SwiftUI

struct HistorySheet: View {
    @Environment(PearEnvironment.self) private var pear
    @Environment(\.dismiss) private var dismiss
    /// Reopen a saved result.
    var onOpen: (ScanResult) -> Void

    var body: some View {
        NavigationStack {
            List {
                Section("Recent scans") {
                    if pear.history.entries.isEmpty {
                        Text("Nothing scanned yet.").foregroundStyle(.latte)
                    }
                    ForEach(pear.history.entries) { entry in
                        Button {
                            onOpen(entry.result)
                            dismiss()
                        } label: {
                            historyRow(entry)
                        }
                    }
                }
                Section("My list") {
                    if pear.list.items.isEmpty {
                        Text("Add better picks to your list and they show up here.").foregroundStyle(.latte)
                    }
                    ForEach(pear.list.items) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(verbatim: item.name).font(PearFont.headline).foregroundStyle(.cocoa)
                            if !item.brand.isEmpty {
                                Text(verbatim: item.brand).font(PearFont.caption).foregroundStyle(.latte)
                            }
                        }
                        .swipeActions {
                            Button("Remove", role: .destructive) { pear.list.remove(item.key) }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Color.cream)
            .navigationTitle("History")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func historyRow(_ entry: ScanHistory.Entry) -> some View {
        let result = entry.result
        let worst = result.verdicts.min { $0.state.rank < $1.state.rank }
        return HStack(spacing: PearMetrics.Spacing.sm) {
            ProductTile(product: result.product, size: 44)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: result.product.name.isEmpty ? "Product" : result.product.name)
                    .font(PearFont.headline)
                    .foregroundStyle(.cocoa)
                Text(entry.date, style: .relative)
                    .font(PearFont.caption)
                    .foregroundStyle(.latte)
            }
            Spacer(minLength: 0)
            if let worst {
                VerdictChip(name: pear.household.member(id: worst.memberId)?.name ?? "", verdict: worst)
                    .scaleEffect(0.85)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
