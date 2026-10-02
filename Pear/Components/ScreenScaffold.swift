//
//  ScreenScaffold.swift
//  Pear
//
//  The standard screen: scrollable content on the Pear background, with the footer
//  (usually the one main button) pinned to the bottom. Long content scrolls under it
//  with a soft edge. At accessibility text sizes the footer joins the scroll instead,
//  so a tall button never covers half the screen.
//

import SwiftUI

struct ScreenScaffold<Content: View, Footer: View>: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private let content: Content
    private let footer: Footer

    init(@ViewBuilder content: () -> Content, @ViewBuilder footer: () -> Footer) {
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                scrollingTogether
            } else {
                pinnedFooter
            }
        }
        .pearBackground()
    }

    /// Default: the footer stays put at the bottom, content scrolls above it.
    private var pinnedFooter: some View {
        ScrollView {
            content
                .frame(maxWidth: .infinity, alignment: .top)
                .padding(.bottom, PearMetrics.Spacing.lg)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .scrollEdgeEffectStyle(.soft, for: .bottom)
        .safeAreaBar(edge: .bottom, spacing: 0) {
            footer
                .padding(.top, PearMetrics.Spacing.sm)
        }
    }

    /// Accessibility sizes: content and footer scroll as one column.
    private var scrollingTogether: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    content
                    Spacer(minLength: PearMetrics.Spacing.lg)
                    footer
                }
                .frame(maxWidth: .infinity, minHeight: proxy.size.height, alignment: .top)
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollDismissesKeyboard(.interactively)
        }
    }
}

#Preview {
    ScreenScaffold {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.md) {
            Text("Title")
                .font(PearFont.largeTitle)
                .foregroundStyle(.cocoa)
            ForEach(1..<12) { index in
                Text("Row \(index)")
                    .font(PearFont.body)
                    .foregroundStyle(.latte)
                    .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            }
        }
        .padding(.horizontal, PearMetrics.screenPadding)
    } footer: {
        PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {}
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.bottom, PearMetrics.Spacing.xs)
    }
}
