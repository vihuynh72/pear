//
//  FloatingSticker.swift
//  Pear
//
//  A white rounded square with a soft shadow and a product symbol, tilted a little,
//  like the snack, bottle and cup stickers around the logo on the welcome screen.
//  Decorative, so hidden from VoiceOver.
//

import SwiftUI

struct FloatingSticker: View {
    let systemImage: String
    var rotation: Angle = .degrees(6)
    var size: CGFloat = 60

    var body: some View {
        Image(systemName: systemImage)
            .font(.system(size: PearMetrics.iconSize, weight: .regular))
            .foregroundStyle(.cocoa)
            .frame(width: size, height: size)
            .background {
                RoundedRectangle(cornerRadius: size * 0.34, style: .continuous)
                    .fill(.white)
                    .shadow(color: Color.cocoa.opacity(0.10), radius: 14, x: 0, y: 8)
            }
            .rotationEffect(rotation)
            .accessibilityHidden(true)
    }
}

#Preview {
    HStack(spacing: 32) {
        FloatingSticker(systemImage: "birthday.cake", rotation: .degrees(8))
        FloatingSticker(systemImage: "waterbottle", rotation: .degrees(-6))
        FloatingSticker(systemImage: "takeoutbag.and.cup.and.straw", rotation: .degrees(5))
    }
    .padding(40)
    .pearBackground()
}
