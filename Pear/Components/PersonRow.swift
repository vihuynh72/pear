//
//  PersonRow.swift
//  Pear
//
//  A family member card: sticker avatar, name, a short subtitle built from the member's
//  data, and a chevron. AddPersonRow is the "Add someone" card that ends the list.
//

import SwiftUI

struct PersonRow: View {
    let name: String
    let subtitle: String
    var kind: StickerAvatar.Kind = .adult
    var tint: StickerAvatar.Tint = .avocado
    /// Overrides the avatar symbol (pet species).
    var systemImage: String? = nil
    let action: () -> Void

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: PearMetrics.Spacing.md) {
                StickerAvatar(kind: kind, tint: tint, systemImage: systemImage)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: name)
                        .font(PearFont.headline)
                        .foregroundStyle(.cocoa)
                    Text(verbatim: subtitle)
                        .font(PearFont.subheadline)
                        .foregroundStyle(.latte)
                }
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "chevron.right")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.avocado700)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .fill(.white)
            }
            .overlay {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .strokeBorder(.mist, lineWidth: PearMetrics.borderWidth)
            }
            .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous))
        }
        .buttonStyle(PressableStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint(Text("Edit"))
    }
}

/// The "Add someone" card at the end of the family list.
struct AddPersonRow: View {
    var title: LocalizedStringKey = "Add someone"
    let subtitle: LocalizedStringKey
    let action: () -> Void

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: PearMetrics.Spacing.md) {
                StickerAvatar(kind: .add)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(PearFont.headline)
                        .foregroundStyle(.cocoa)
                    Text(subtitle)
                        .font(PearFont.subheadline)
                        .foregroundStyle(.latte)
                }
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.vertical, PearMetrics.Spacing.sm)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .fill(.avocado50)
            }
            .overlay {
                RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                    .strokeBorder(.avocado200, lineWidth: 1.5)
            }
            .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous))
        }
        .buttonStyle(PressableStyle())
        .accessibilityElement(children: .combine)
    }
}

#Preview("Demo family") {
    // Subtitles here mirror contracts/household.example.json; the family screen builds them from member data.
    ScrollView {
        VStack(spacing: 12) {
            PersonRow(name: "Linh", subtitle: "You, English and Tiếng Việt", kind: .adult, tint: .blush) {}
            PersonRow(name: "Bà", subtitle: "Grandma, Tiếng Việt", kind: .senior, tint: .honey) {}
            PersonRow(name: "Mia", subtitle: "Kid, peanut and sesame allergy", kind: .kid, tint: .avocado) {}
            PersonRow(name: "Leo", subtitle: "Teen, vegan, Español", kind: .teen, tint: .peach) {}
            AddPersonRow(subtitle: "A person or a pet, 1 spot left") {}
        }
        .padding(24)
    }
    .pearBackground()
}
