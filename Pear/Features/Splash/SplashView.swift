//
//  SplashView.swift
//  Pear
//
//  First thing on launch (a-3): the logo, the wordmark and the tagline.
//

import SwiftUI

struct SplashView: View {
    /// Called once after a short pause. Pass nil to keep the splash on screen (previews).
    var onFinished: (() -> Void)? = nil
    var duration: Duration = .seconds(1.6)

    @State private var appeared = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            PearLogo(isAccessibilityElement: false)
                .frame(width: 155, height: 225)
                .scaleEffect(appeared || reduceMotion ? 1 : 0.92)

            Text(verbatim: "Pear")
                .font(PearFont.display)
                .foregroundStyle(.cocoa)
                .padding(.top, PearMetrics.Spacing.sm)
                .accessibilityAddTraits(.isHeader)

            Text("Pear pairs your family with products that fit.")
                .font(PearFont.body)
                .foregroundStyle(.latte)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 270)
                .padding(.top, PearMetrics.Spacing.md)
        }
        .opacity(appeared ? 1 : 0)
        .padding(.horizontal, PearMetrics.screenPadding)
        .padding(.bottom, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .pearBackground()
        .accessibilityElement(children: .combine)
        .task {
            withAnimation(reduceMotion ? .easeOut(duration: 0.3) : .spring(duration: 0.6, bounce: 0.3)) {
                appeared = true
            }
            guard let onFinished else { return }
            try? await Task.sleep(for: duration)
            guard !Task.isCancelled else { return }
            onFinished()
        }
    }
}

#Preview {
    SplashView()
}
