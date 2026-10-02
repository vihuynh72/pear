//
//  PearBackground.swift
//  Pear
//
//  Cream with a soft avocado glow in the top-left corner and a blush glow in the
//  bottom-right corner, as on every screen of the mockups.
//

import SwiftUI

struct PearBackground: View {
    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let reach = max(size.width, size.height)
            ZStack {
                Color.cream

                RadialGradient(
                    colors: [Color.avocado100, Color.avocado100.opacity(0.75), Color.avocado100.opacity(0)],
                    center: UnitPoint(x: -0.08, y: -0.02),
                    startRadius: 0,
                    endRadius: reach * 0.44
                )

                RadialGradient(
                    colors: [Color.blush, Color.blush.opacity(0.75), Color.blush.opacity(0)],
                    center: UnitPoint(x: 0.92, y: 0.95),
                    startRadius: 0,
                    endRadius: reach * 0.36
                )
            }
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

extension View {
    /// Places the Pear cream background with its two soft glows behind the view, edge to edge.
    func pearBackground() -> some View {
        background { PearBackground() }
    }
}

#Preview {
    Text("Pear")
        .font(PearFont.largeTitle)
        .foregroundStyle(.cocoa)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .pearBackground()
}
