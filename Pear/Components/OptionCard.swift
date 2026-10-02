//
//  OptionCard.swift
//  Pear
//
//  A full-width single-choice card with a title and an optional subtitle.
//  Selected: avocado50 fill, avocado700 border and a check circle.
//

import SwiftUI

struct OptionCard: View {
    private let title: Text
    private let subtitle: Text?
    private let systemImage: String?
    private let isSelected: Bool
    private let action: () -> Void

    init(
        title: Text,
        subtitle: Text? = nil,
        systemImage: String? = nil,
        isSelected: Bool,
        action: @escaping () -> Void
    ) {
        self.title = title
        self.subtitle = subtitle
        self.systemImage = systemImage
        self.isSelected = isSelected
        self.action = action
    }

    init(
        _ title: LocalizedStringKey,
        subtitle: LocalizedStringKey? = nil,
        systemImage: String? = nil,
        isSelected: Bool,
        action: @escaping () -> Void
    ) {
        // Assigned directly instead of delegating to self.init so Xcode previews can compile it.
        self.title = Text(title)
        self.subtitle = subtitle.map { Text($0) }
        self.systemImage = systemImage
        self.isSelected = isSelected
        self.action = action
    }

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: PearMetrics.Spacing.md) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: PearMetrics.iconSize))
                        .foregroundStyle(.cocoa)
                        .accessibilityHidden(true)
                }
                VStack(alignment: .leading, spacing: 2) {
                    title
                        .font(PearFont.headline)
                        .foregroundStyle(.cocoa)
                    if let subtitle {
                        subtitle
                            .font(PearFont.subheadline)
                            .foregroundStyle(.latte)
                    }
                }
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: isSelected ? "checkmark.circle" : "circle")
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(isSelected ? Color.avocado700 : Color.clear)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .fill(isSelected ? Color.avocado50 : Color.white)
            }
            .overlay {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .strokeBorder(
                        isSelected ? Color.avocado700 : Color.mist,
                        lineWidth: isSelected ? PearMetrics.selectedBorderWidth : PearMetrics.borderWidth
                    )
            }
            .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous))
        }
        .buttonStyle(PressableStyle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
        .animation(.snappy(duration: 0.2), value: isSelected)
    }
}

#Preview {
    @Previewable @State var selection = "en"
    VStack(spacing: 12) {
        OptionCard(title: Text(verbatim: "English"), subtitle: Text("Labels, voice and answers"), isSelected: selection == "en") {
            selection = "en"
        }
        OptionCard(title: Text(verbatim: "Tiếng Việt"), subtitle: Text("Vietnamese"), isSelected: selection == "vi") {
            selection = "vi"
        }
        OptionCard(title: Text(verbatim: "Español"), subtitle: Text("Spanish"), isSelected: selection == "es") {
            selection = "es"
        }
    }
    .padding(24)
    .pearBackground()
}
