//
//  FamilyAvatarStrip.swift
//  Pear
//
//  The family's stickers in a row with names under them. Tap to edit the family.
//  Reusable on Home and on the result sheet ("Who's this for?").
//

import SwiftUI

struct FamilyAvatarStrip: View {
    let store: HouseholdStore
    var size: CGFloat = 56
    /// Dims members that are switched off ("Who's this for?").
    var isIncluded: (String) -> Bool = { _ in true }
    var onTap: (Member) -> Void

    var body: some View {
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: PearMetrics.Spacing.md) {
                ForEach(store.members) { member in
                    Button {
                        onTap(member)
                    } label: {
                        VStack(spacing: PearMetrics.Spacing.xxs) {
                            MemberAvatar(member: member, appearance: store.appearance(for: member.id), size: size)
                            Text(verbatim: member.name)
                                .font(PearFont.subheadline)
                                .foregroundStyle(.cocoa)
                                .lineLimit(1)
                        }
                        .frame(minWidth: size)
                        .opacity(isIncluded(member.id) ? 1 : 0.4)
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityLabel(Text(verbatim: member.name))
                    .accessibilityAddTraits(isIncluded(member.id) ? .isSelected : [])
                }
            }
            .padding(.horizontal, PearMetrics.screenPadding)
        }
        .scrollIndicators(.hidden)
    }
}

#Preview {
    FamilyAvatarStrip(store: .preview()) { _ in }
        .padding(.vertical, 24)
        .pearBackground()
}
