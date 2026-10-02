//
//  ShopperSwitcher.swift
//  Pear
//
//  The small "Linh · EN" chip at the top of Home. It answers "Who's shopping today?" and sets
//  the one language every result comes back in (headline, chips, reasons, answers, Listen).
//  Picking a person switches to their first language; picking a language overrides it.
//

import SwiftUI

struct ShopperSwitcher: View {
    @Environment(PearEnvironment.self) private var pear

    private var people: [Member] { pear.household.members.filter { $0.type == "person" } }

    private var language: PearLanguage {
        PearLanguage.all.first { $0.code == pear.displayLanguage } ?? .english
    }

    var body: some View {
        Menu {
            Section("Who's shopping today?") {
                ForEach(people) { member in
                    Button {
                        pear.setShopper(member.id)
                    } label: {
                        let title = "\(member.name) · \(languageName(member.primaryLanguage))"
                        if member.id == pear.shopper?.id {
                            Label(title, systemImage: "checkmark")
                        } else {
                            Text(verbatim: title)
                        }
                    }
                }
            }
            Section("Show results in") {
                ForEach(PearLanguage.all) { option in
                    Button {
                        pear.setDisplayLanguage(option.code)
                    } label: {
                        if option.code == pear.displayLanguage {
                            Label(option.endonym, systemImage: "checkmark")
                        } else {
                            Text(verbatim: option.endonym)
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: PearMetrics.Spacing.xs) {
                Image(systemName: "globe")
                    .foregroundStyle(.avocado700)
                Text(verbatim: "\(pear.shopper?.name ?? "You") · \(language.code.uppercased())")
                    .foregroundStyle(.cocoa)
                Image(systemName: "chevron.down")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.latte)
            }
            .font(PearFont.headline)
            .padding(.horizontal, PearMetrics.Spacing.md)
            .frame(minHeight: PearMetrics.minTapTarget)
            .background(Capsule().fill(.white))
            .overlay(Capsule().strokeBorder(.mist, lineWidth: PearMetrics.borderWidth))
            .contentShape(Capsule())
        }
        .accessibilityLabel("Who's shopping today: \(pear.shopper?.name ?? "you"). Results in \(language.endonym).")
        .accessibilityHint("Changes who is shopping and the language of every result.")
    }

    private func languageName(_ code: String) -> String {
        PearLanguage.all.first { $0.code == code }?.endonym ?? code.uppercased()
    }
}

#Preview {
    ShopperSwitcher()
        .padding()
        .pearBackground()
        .environment(PearEnvironment.preview())
}
