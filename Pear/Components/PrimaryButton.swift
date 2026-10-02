//
//  PrimaryButton.swift
//  Pear
//
//  The one main action on a screen: full width, avocado700 fill, cream text, an icon and a word.
//

import SwiftUI

struct PrimaryButton: View {
    enum IconPlacement {
        case leading, trailing
    }

    private let title: LocalizedStringKey
    private let systemImage: String
    private let iconPlacement: IconPlacement
    private let action: () -> Void

    init(
        _ title: LocalizedStringKey,
        systemImage: String,
        iconPlacement: IconPlacement = .leading,
        action: @escaping () -> Void
    ) {
        self.title = title
        self.systemImage = systemImage
        self.iconPlacement = iconPlacement
        self.action = action
    }

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: PearMetrics.Spacing.xs) {
                if iconPlacement == .leading { icon }
                Text(title)
                if iconPlacement == .trailing { icon }
            }
        }
        .buttonStyle(PrimaryButtonStyle())
    }

    private var icon: some View {
        Image(systemName: systemImage)
            .font(.system(size: 20, weight: .semibold))
            .accessibilityHidden(true)
    }
}

/// Full-width filled style. Also usable directly: `.buttonStyle(PrimaryButtonStyle())`.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(PearFont.headline)
            .foregroundStyle(isEnabled ? Color.cream : Color.latte)
            .multilineTextAlignment(.center)
            .padding(.horizontal, PearMetrics.Spacing.lg)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: PearMetrics.buttonHeight)
            .background {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.button, style: .continuous)
                    .fill(isEnabled ? Color.avocado700 : Color.mist)
            }
            .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.button, style: .continuous))
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

#Preview {
    VStack(spacing: 16) {
        PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {}
        PrimaryButton("Scan a label", systemImage: "barcode.viewfinder") {}
        PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {}
            .disabled(true)
    }
    .padding(24)
    .pearBackground()
}
