//
//  PearColors.swift
//  Pear
//
//  Color tokens from the design board (b-3). Use these everywhere instead of raw colors.
//

import SwiftUI

nonisolated extension Color {
    /// Creates a color from a 24-bit RGB hex value, for example `Color(hex: 0x587530)`.
    init(hex: UInt32, opacity: Double = 1) {
        let red = Double((hex >> 16) & 0xFF) / 255
        let green = Double((hex >> 8) & 0xFF) / 255
        let blue = Double(hex & 0xFF) / 255
        // Assigning self (instead of self.init) keeps this file previewable.
        self = Color(.sRGB, red: red, green: green, blue: blue, opacity: opacity)
    }

    /// Creates a color from a hex string such as "#587530" or "587530". Returns nil for bad input.
    init?(hexString: String) {
        var cleaned = hexString.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleaned.hasPrefix("#") { cleaned.removeFirst() }
        guard cleaned.count == 6, let value = UInt32(cleaned, radix: 16) else { return nil }
        self = Color(hex: value)
    }
}

// MARK: - Tokens

nonisolated extension Color {
    // Greens
    static let avocado700 = Color(hex: 0x587530)
    static let avocado500 = Color(hex: 0x9EBC62)
    static let avocado200 = Color(hex: 0xD7E5B4)
    static let avocado100 = Color(hex: 0xE6EFD2)
    static let avocado50 = Color(hex: 0xF4F8EA)
    static let leaf = Color(hex: 0x6F8D3B)

    // Browns and text
    static let stem = Color(hex: 0x8B6B4A)
    /// Main text.
    static let cocoa = Color(hex: 0x3D352E)
    /// Secondary text.
    static let latte = Color(hex: 0x7C7268)

    // Surfaces
    /// App background.
    static let cream = Color(hex: 0xFBFAF4)
    /// Cards and sheets.
    static let card = Color.white
    /// Borders, disabled fills, dividers.
    static let mist = Color(hex: 0xE9E5DF)

    // Accents
    static let blush = Color(hex: 0xF8D7D5)
    static let honey = Color(hex: 0xF5D58A)
    static let peach = Color(hex: 0xF7BFA3)
    /// Allergy only. Never use for anything else.
    static let berry = Color(hex: 0xB03A55)
}

/// Lets tokens be used as `.foregroundStyle(.cocoa)` and `.fill(.avocado700)`.
nonisolated extension ShapeStyle where Self == Color {
    static var avocado700: Color { Color.avocado700 }
    static var avocado500: Color { Color.avocado500 }
    static var avocado200: Color { Color.avocado200 }
    static var avocado100: Color { Color.avocado100 }
    static var avocado50: Color { Color.avocado50 }
    static var leaf: Color { Color.leaf }
    static var stem: Color { Color.stem }
    static var cocoa: Color { Color.cocoa }
    static var latte: Color { Color.latte }
    static var cream: Color { Color.cream }
    static var card: Color { Color.card }
    static var mist: Color { Color.mist }
    static var blush: Color { Color.blush }
    static var honey: Color { Color.honey }
    static var peach: Color { Color.peach }
    static var berry: Color { Color.berry }
}

#Preview("Color tokens") {
    let tokens: [(String, Color)] = [
        ("avocado700", .avocado700), ("avocado500", .avocado500), ("avocado200", .avocado200),
        ("avocado100", .avocado100), ("avocado50", .avocado50), ("leaf", .leaf),
        ("stem", .stem), ("cocoa", .cocoa), ("latte", .latte), ("cream", .cream),
        ("blush", .blush), ("honey", .honey), ("peach", .peach), ("berry", .berry), ("mist", .mist)
    ]
    ScrollView {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 12)], spacing: 12) {
            ForEach(tokens, id: \.0) { token in
                VStack(alignment: .leading, spacing: 6) {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(token.1)
                        .frame(height: 72)
                        .overlay {
                            RoundedRectangle(cornerRadius: 18, style: .continuous)
                                .strokeBorder(.mist, lineWidth: 1)
                        }
                    Text(token.0)
                        .font(PearFont.caption)
                        .foregroundStyle(.cocoa)
                }
            }
        }
        .padding(24)
    }
    .background(.cream)
}
