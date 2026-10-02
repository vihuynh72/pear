//
//  WelcomeView.swift
//  Pear
//
//  Welcome and sign-in (a-4). One main action: Continue with Google.
//  The demo family link lets anyone look around without an account.
//

import SwiftUI

struct WelcomeView: View {
    var onGoogle: () -> Void
    var onDemo: () -> Void

    var body: some View {
        ScreenScaffold {
            VStack(spacing: 0) {
                WelcomeHero()
                    .padding(.top, 72)

                Text("Is this okay\nfor my family?")
                    .font(PearFont.largeTitle)
                    .foregroundStyle(.cocoa)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .padding(.top, 28)

                Text("Scan a label and Pear checks it for everyone you shop for, in your language.")
                    .font(PearFont.body)
                    .foregroundStyle(.latte)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 300)
                    .padding(.top, PearMetrics.Spacing.md)
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, PearMetrics.screenPadding)
        } footer: {
            actions
        }
    }

    private var actions: some View {
        VStack(spacing: PearMetrics.Spacing.xs) {
            GoogleButton(action: onGoogle)

            Button {
                onDemo()
            } label: {
                Label("Look around with a demo family", systemImage: "person.3")
                    .font(PearFont.body)
                    .foregroundStyle(.avocado700)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, minHeight: PearMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PressableStyle())
            .padding(.top, 4)

            Text("Pear isn’t medical advice. Always check the label.")
                .font(PearFont.caption)
                .foregroundStyle(.latte)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, PearMetrics.screenPadding)
        .padding(.bottom, PearMetrics.Spacing.xs)
    }
}

/// The logo with three product stickers floating around it.
private struct WelcomeHero: View {
    var body: some View {
        PearLogo()
            .frame(width: 124, height: 182)
            .frame(width: 320, height: 210)
            .overlay {
                FloatingSticker(systemImage: "birthday.cake", rotation: .degrees(7), size: 58)
                    .offset(x: -118, y: -38)
                FloatingSticker(systemImage: "waterbottle", rotation: .degrees(-7), size: 58)
                    .offset(x: 122, y: -60)
                FloatingSticker(systemImage: "takeoutbag.and.cup.and.straw", rotation: .degrees(5), size: 58)
                    .offset(x: 108, y: 74)
            }
    }
}

#Preview {
    WelcomeView(onGoogle: {}, onDemo: {})
}
