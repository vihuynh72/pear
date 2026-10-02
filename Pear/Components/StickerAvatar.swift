//
//  StickerAvatar.swift
//  Pear
//
//  A colored circle with a line symbol for a family member. The name always sits next
//  to it, so the avatar is hidden from VoiceOver by default.
//

import SwiftUI

struct StickerAvatar: View {
    /// What the sticker shows. Maps from the backend `age_group` and `type` fields.
    enum Kind: String, CaseIterable {
        case baby, kid, teen, adult, senior, pet, add

        /// `ageGroup` is the server's age_group: baby, kid, teen, adult or "65+". `type` is person or pet.
        init(ageGroup: String?, type: String = "person") {
            if type == "pet" {
                self = .pet
            } else if ageGroup == "65+" || ageGroup == "senior" {
                self = .senior
            } else {
                self = Kind(rawValue: ageGroup ?? "") ?? .adult
            }
        }

        var systemImage: String {
            switch self {
            case .baby: "face.smiling"
            case .kid: "figure.arms.open"
            case .teen: "figure.wave"
            case .adult: "person"
            // SF Symbols has no figure with a cane; the walking figure is the closest match.
            case .senior: "figure.walk"
            case .pet: "pawprint"
            case .add: "person.badge.plus"
            }
        }
    }

    /// The five sticker colors from the "Tell us about" screen.
    enum Tint: String, CaseIterable, Identifiable {
        case blush, honey, avocado, peach, mist

        var id: String { rawValue }

        var color: Color {
            switch self {
            case .blush: .blush
            case .honey: .honey
            case .avocado: .avocado200
            case .peach: .peach
            case .mist: .mist
            }
        }

        var name: LocalizedStringKey {
            switch self {
            case .blush: "Pink"
            case .honey: "Yellow"
            case .avocado: "Green"
            case .peach: "Peach"
            case .mist: "Gray"
            }
        }
    }

    var kind: Kind
    var tint: Tint = .avocado
    var size: CGFloat = 48
    /// Overrides the kind's symbol, e.g. "dog" or "cat" for a pet.
    var systemImage: String? = nil

    var body: some View {
        Image(systemName: systemImage ?? kind.systemImage)
            .font(.system(size: size * 0.44, weight: kind == .add ? .medium : .regular))
            .foregroundStyle(kind == .add ? Color.avocado700 : Color.cocoa)
            .frame(width: size, height: size)
            .background {
                Circle().fill(kind == .add ? Color.white : tint.color)
            }
            .accessibilityHidden(true)
    }
}

#Preview {
    VStack(spacing: 24) {
        HStack(spacing: 16) {
            StickerAvatar(kind: .adult, tint: .blush)
            StickerAvatar(kind: .senior, tint: .honey)
            StickerAvatar(kind: .kid, tint: .avocado)
            StickerAvatar(kind: .teen, tint: .peach)
        }
        HStack(spacing: 16) {
            StickerAvatar(kind: .baby, tint: .peach)
            StickerAvatar(kind: .pet, tint: .mist)
            StickerAvatar(kind: .add)
        }
        StickerAvatar(kind: .kid, tint: .avocado, size: 84)
    }
    .padding(24)
    .pearBackground()
}
