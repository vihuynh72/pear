//
//  ContentView.swift
//  Pear
//
//  Created by Vi Huynh on 10/1/26.
//
//  Home, after onboarding. "Scan a label" opens the camera (ScanView); "Ask Pear" opens
//  the Ask sheet. Everything comes from the environment:
//      @Environment(PearEnvironment.self) private var pear   // pear.flow, pear.household, pear.api
//      @Environment(AppRouter.self) private var router       // router.push(.family)
//

import SwiftUI

struct ContentView: View {
    @Environment(PearEnvironment.self) private var pear
    @Environment(AppRouter.self) private var router

    /// The camera, opened in Barcode mode ("Scan a label") or Voice mode ("Ask Pear").
    @State private var scanMode: ScanView.Mode?

    var body: some View {
        ScrollView {
            VStack(spacing: PearMetrics.Spacing.lg) {
                HStack {
                    Spacer()
                    ShopperSwitcher()
                }
                .padding(.horizontal, PearMetrics.screenPadding)
                .padding(.top, PearMetrics.Spacing.xs)

                PearLogo(isAccessibilityElement: false)
                    .frame(height: 96)

                Text("What are we checking today?")
                    .font(PearFont.largeTitle)
                    .foregroundStyle(.cocoa)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .padding(.horizontal, PearMetrics.screenPadding)

                VStack(spacing: PearMetrics.Spacing.sm) {
                    PrimaryButton("Scan a label", systemImage: "barcode.viewfinder") {
                        pear.flow.warm()
                        scanMode = .barcode
                    }
                    Button {
                        pear.flow.warm()
                        scanMode = .voice
                    } label: {
                        Label("Ask Pear", systemImage: "mic")
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
                    Text("Scan a barcode or an ingredient label, or just ask.")
                        .font(PearFont.caption)
                        .foregroundStyle(.latte)
                }
                .padding(.horizontal, PearMetrics.screenPadding)

                VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
                    HStack {
                        Text("Your family")
                            .font(PearFont.title)
                            .foregroundStyle(.cocoa)
                            .accessibilityAddTraits(.isHeader)
                        Spacer()
                        Button {
                            router.push(.family)
                        } label: {
                            Label("Edit", systemImage: "pencil")
                                .font(PearFont.body)
                                .foregroundStyle(.avocado700)
                                .frame(minHeight: PearMetrics.minTapTarget)
                        }
                        .buttonStyle(PressableStyle())
                    }
                    .padding(.horizontal, PearMetrics.screenPadding)

                    FamilyAvatarStrip(store: pear.household) { member in
                        router.editMember(id: member.id)
                    }
                }
            }
            .padding(.bottom, PearMetrics.Spacing.xl)
        }
        .pearBackground()
        .fullScreenCover(item: $scanMode) { mode in
            ScanView(initialMode: mode)
                .environment(pear)
        }
    }
}

#Preview {
    NavigationStack {
        ContentView()
    }
    .environment(PearEnvironment.preview())
    .environment(AppRouter.preview(phase: .home))
}
