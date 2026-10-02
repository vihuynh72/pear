//
//  GoogleButton.swift
//  Pear
//
//  "Continue with Google": white, mist border, with the multicolor G drawn in SwiftUI.
//  Sign-in itself is a placeholder until Firebase is added.
//

import SwiftUI

struct GoogleButton: View {
    var title: LocalizedStringKey = "Continue with Google"
    let action: () -> Void

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: PearMetrics.Spacing.sm) {
                GoogleGMark()
                    .frame(width: 22, height: 22)
                    .accessibilityHidden(true)
                Text(title)
                    .font(PearFont.headline)
                    .foregroundStyle(.cocoa)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, PearMetrics.Spacing.lg)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: PearMetrics.buttonHeight)
            .background {
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .fill(.white)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .strokeBorder(.mist, lineWidth: PearMetrics.borderWidth)
            }
            .contentShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        }
        .buttonStyle(PressableStyle())
    }
}

/// The four-color Google "G", drawn with arcs so no image asset is needed.
struct GoogleGMark: View {
    private static let blue = Color(hex: 0x4285F4)
    private static let green = Color(hex: 0x34A853)
    private static let yellow = Color(hex: 0xFBBC05)
    private static let red = Color(hex: 0xEA4335)

    var body: some View {
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            let lineWidth = side * 0.2
            let inset = lineWidth / 2
            ZStack {
                // Trim runs clockwise from 3 o'clock.
                arc(from: 0.0, to: 0.13, color: Self.blue, lineWidth: lineWidth, inset: inset)
                arc(from: 0.125, to: 0.40, color: Self.green, lineWidth: lineWidth, inset: inset)
                arc(from: 0.395, to: 0.60, color: Self.yellow, lineWidth: lineWidth, inset: inset)
                arc(from: 0.595, to: 0.875, color: Self.red, lineWidth: lineWidth, inset: inset)
                // The crossbar of the G.
                Rectangle()
                    .fill(Self.blue)
                    .frame(width: side / 2, height: lineWidth)
                    .offset(x: side / 4 - side * 0.02)
            }
            .frame(width: side, height: side)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .aspectRatio(1, contentMode: .fit)
    }

    private func arc(from: CGFloat, to: CGFloat, color: Color, lineWidth: CGFloat, inset: CGFloat) -> some View {
        Circle()
            .inset(by: inset)
            .trim(from: from, to: to)
            .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .butt))
    }
}

/// Gentle press feedback for custom-drawn buttons.
struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.8 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

#Preview {
    VStack(spacing: 24) {
        GoogleButton {}
        GoogleGMark().frame(width: 96, height: 96)
    }
    .padding(24)
    .pearBackground()
}
