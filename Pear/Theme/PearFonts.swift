//
//  PearFonts.swift
//  Pear
//
//  Type scale from the design board (b-4): Baloo 2 for headings, Nunito for body,
//  Patrick Hand for small hand-written tips. Every style scales with Dynamic Type.
//
//  Drop the TTF/OTF files anywhere under Pear/ (for example Pear/Resources/Fonts).
//  FontRegistrar registers every font it finds in the app bundle at launch, so no
//  Info.plist (UIAppFonts) edit is needed. Until the files are added, each style falls
//  back to the rounded system font for the matching text style.
//

import CoreText
import SwiftUI
import UIKit

/// The Pear type scale.
enum PearTextStyle: CaseIterable {
    /// 56 pt Baloo 2 SemiBold. The wordmark on the splash screen only.
    case display
    /// 34 pt Baloo 2 SemiBold.
    case largeTitle
    /// 24 pt Baloo 2 SemiBold.
    case title
    /// 17 pt Nunito Bold.
    case headline
    /// 17 pt Nunito Regular.
    case body
    /// 15 pt Nunito Regular. Card subtitles and section labels.
    case subheadline
    /// 13 pt Nunito Regular.
    case caption
    /// 22 pt Patrick Hand. Hand-written tips in avocado700.
    case note

    var size: CGFloat {
        switch self {
        case .display: 56
        case .largeTitle: 34
        case .title: 24
        case .headline, .body: 17
        case .subheadline: 15
        case .caption: 13
        case .note: 22
        }
    }

    /// The system text style this style scales with.
    var textStyle: Font.TextStyle {
        switch self {
        case .display, .largeTitle: .largeTitle
        case .title: .title
        case .headline: .headline
        case .body: .body
        case .subheadline: .subheadline
        case .caption: .caption
        case .note: .title2
        }
    }

    var weight: Font.Weight {
        switch self {
        case .display, .largeTitle, .title: .semibold
        case .headline: .bold
        case .body, .subheadline, .caption, .note: .regular
        }
    }

    /// PostScript names to try, best match first. Static and variable Google Fonts files both work.
    var fontNames: [String] {
        switch self {
        case .display, .largeTitle, .title:
            ["Baloo2-SemiBold", "Baloo2-Bold", "Baloo2-Medium", "Baloo2-Regular"]
        case .headline:
            ["Nunito-Bold", "Nunito-SemiBold", "Nunito-Regular"]
        case .body, .subheadline, .caption:
            ["Nunito-Regular", "Nunito-Medium"]
        case .note:
            ["PatrickHand-Regular"]
        }
    }

    /// The system text style used when the custom font is missing (closest size).
    var fallbackTextStyle: Font.TextStyle {
        switch self {
        case .title: .title2
        default: textStyle
        }
    }
}

/// Shorthand access to the Pear type scale: `.font(PearFont.body)`.
enum PearFont {
    static var display: Font { font(.display) }
    static var largeTitle: Font { font(.largeTitle) }
    static var title: Font { font(.title) }
    static var headline: Font { font(.headline) }
    static var body: Font { font(.body) }
    static var subheadline: Font { font(.subheadline) }
    static var caption: Font { font(.caption) }
    static var note: Font { font(.note) }

    static func font(_ style: PearTextStyle) -> Font {
        if let name = FontRegistrar.availableName(for: style) {
            return Font.custom(name, size: style.size, relativeTo: style.textStyle).weight(style.weight)
        }
        if style == .display {
            // The wordmark keeps its size; it is a logo, not body text.
            return .system(size: style.size, weight: style.weight, design: .rounded)
        }
        return .system(style.fallbackTextStyle, design: .rounded, weight: style.weight)
    }
}

extension Font {
    /// `Text("Hi").font(.pear(.title))`
    static func pear(_ style: PearTextStyle) -> Font { PearFont.font(style) }
}

// MARK: - Registration

/// Registers bundled font files with Core Text so `Font.custom` can find them.
enum FontRegistrar {
    private static var didRegister = false
    private static var resolvedNames: [PearTextStyle: String?] = [:]

    /// Registers every .ttf and .otf in the app bundle. Safe to call more than once.
    static func registerBundledFonts() {
        guard !didRegister else { return }
        didRegister = true
        resolvedNames.removeAll()

        for url in bundledFontURLs() {
            var error: Unmanaged<CFError>?
            // Fails harmlessly if the font is already registered.
            _ = CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error)
            error?.release()
        }
    }

    /// The first installed PostScript name for a style, or nil when the font files are missing.
    static func availableName(for style: PearTextStyle) -> String? {
        registerBundledFonts()
        if let cached = resolvedNames[style] { return cached }
        let name = style.fontNames.first { UIFont(name: $0, size: style.size) != nil }
        resolvedNames[style] = .some(name)
        return name
    }

    private static func bundledFontURLs() -> [URL] {
        guard let root = Bundle.main.resourceURL,
              let enumerator = FileManager.default.enumerator(
                  at: root,
                  includingPropertiesForKeys: nil,
                  options: [.skipsHiddenFiles, .skipsPackageDescendants]
              )
        else { return [] }

        var urls: [URL] = []
        for case let url as URL in enumerator where ["ttf", "otf"].contains(url.pathExtension.lowercased()) {
            urls.append(url)
        }
        return urls
    }
}

#Preview("Type scale") {
    VStack(alignment: .leading, spacing: 16) {
        Text("Pear").font(PearFont.display)
        Text("Large Title 34: Is this okay for my family?").font(PearFont.largeTitle)
        Text("Title 24: Not for Mia, contains sesame").font(PearFont.title)
        Text("Headline 17: Fine for Linh, Bà and Leo").font(PearFont.headline)
        Text("Body 17: Pear reads the label and checks it against everyone in your family.")
            .font(PearFont.body)
        Text("Subheadline 15: Labels, voice and answers").font(PearFont.subheadline)
            .foregroundStyle(.latte)
        Text("Caption 13: Strict allergy, updated today").font(PearFont.caption)
            .foregroundStyle(.latte)
        Text("Note 22: a little hand-written tip from Pear").font(PearFont.note)
            .foregroundStyle(.avocado700)
    }
    .foregroundStyle(.cocoa)
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .pearBackground()
}
