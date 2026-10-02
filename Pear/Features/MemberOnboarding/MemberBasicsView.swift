//
//  MemberBasicsView.swift
//  Pear
//
//  Step 1, "Tell us about Mia" (a-7): sticker color, name, person or pet, age group
//  (or dog / cat for a pet).
//

import SwiftUI

struct MemberBasicsView: View {
    @Bindable var draft: MemberDraft

    @FocusState private var nameFocused: Bool

    /// The server's age_group values.
    private static let ageGroups: [(group: String, title: LocalizedStringKey)] = [
        ("baby", "Baby 0 to 2"),
        ("kid", "Kid 3 to 12"),
        ("teen", "Teen 13 to 17"),
        ("adult", "Adult"),
        ("65+", "65 and up"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(draft.name.isEmpty ? "Who’s joining?" : "Tell us about \(draft.name)")
                .font(PearFont.largeTitle)
                .foregroundStyle(.cocoa)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)

            Text(draft.name.isEmpty
                 ? "Pear uses this to pick the right checks. You can change it later."
                 : "Pear uses this to pick the right checks for \(draft.name). You can change it later.")
                .font(PearFont.body)
                .foregroundStyle(.latte)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, PearMetrics.Spacing.xs)

            stickerRow
                .padding(.top, PearMetrics.Spacing.lg)

            fieldLabel("Name")
                .padding(.top, PearMetrics.Spacing.lg)
            nameField
                .padding(.top, PearMetrics.Spacing.xs)

            fieldLabel(draft.name.isEmpty ? "Who is this?" : "Who is \(draft.name)?")
                .padding(.top, PearMetrics.Spacing.lg)
            PearSegmentedControl(
                ["person", "pet"],
                selection: Binding(get: { draft.member.type }, set: { draft.setType($0) })
            ) { type in
                type == "pet" ? "A pet" : "A person"
            }
            .padding(.top, PearMetrics.Spacing.xs)

            if draft.isPet {
                fieldLabel("Dog or cat?")
                    .padding(.top, PearMetrics.Spacing.lg)
                speciesChips
                    .padding(.top, PearMetrics.Spacing.xs)
            } else {
                fieldLabel("Age group")
                    .padding(.top, PearMetrics.Spacing.lg)
                ageChips
                    .padding(.top, PearMetrics.Spacing.xs)
            }
        }
        .animation(.snappy(duration: 0.25), value: draft.isPet)
    }

    private func fieldLabel(_ title: LocalizedStringKey) -> some View {
        Text(title)
            .font(PearFont.subheadline)
            .foregroundStyle(.latte)
            .padding(.leading, PearMetrics.Spacing.xxs)
    }

    // MARK: Sticker

    private var stickerRow: some View {
        HStack(alignment: .center, spacing: PearMetrics.Spacing.lg) {
            MemberAvatar(member: draft.member, appearance: draft.appearance, size: 84)
                .animation(.snappy(duration: 0.2), value: draft.appearance.tintName)

            VStack(alignment: .leading, spacing: PearMetrics.Spacing.xxs) {
                Text("Sticker color")
                    .font(PearFont.subheadline)
                    .foregroundStyle(.latte)
                HStack(spacing: 0) {
                    ForEach(StickerAvatar.Tint.allCases) { tint in
                        StickerSwatch(tint: tint, isSelected: draft.appearance.tint == tint) {
                            draft.appearance.tint = tint
                        }
                    }
                }
                // The 44 pt tap targets are wider than the 30 pt circles; line the first circle up with the label.
                .padding(.leading, -(PearMetrics.minTapTarget - 30) / 2)
            }
        }
    }

    // MARK: Name

    private var nameField: some View {
        HStack(spacing: PearMetrics.Spacing.sm) {
            TextField(
                "Name",
                text: $draft.member.name,
                prompt: Text(draft.isPet ? "Like Bánh Bao" : "Like Mia or Bà").foregroundStyle(.latte)
            )
            .font(PearFont.body)
            .foregroundStyle(.cocoa)
            .textContentType(.name)
            .textInputAutocapitalization(.words)
            .autocorrectionDisabled()
            .submitLabel(.done)
            .focused($nameFocused)

            Image(systemName: "pencil")
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(.avocado700)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, PearMetrics.Spacing.md)
        .frame(minHeight: 56)
        .background {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                .fill(.white)
        }
        .overlay {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                .strokeBorder(nameFocused ? Color.avocado500 : Color.mist,
                              lineWidth: nameFocused ? 1.5 : PearMetrics.borderWidth)
        }
        .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous))
        .onTapGesture { nameFocused = true }
    }

    // MARK: Chips

    private var ageChips: some View {
        FlowLayout(spacing: PearMetrics.Spacing.xs, lineSpacing: PearMetrics.Spacing.xs) {
            ForEach(Self.ageGroups, id: \.group) { item in
                SelectChip(item.title, isSelected: draft.member.ageGroup == item.group) {
                    draft.setAgeGroup(item.group)
                }
            }
        }
    }

    private var speciesChips: some View {
        FlowLayout(spacing: PearMetrics.Spacing.xs, lineSpacing: PearMetrics.Spacing.xs) {
            SelectChip("Dog", systemImage: "dog", isSelected: draft.member.species == "dog") {
                draft.member.species = "dog"
            }
            SelectChip("Cat", systemImage: "cat", isSelected: draft.member.species == "cat") {
                draft.member.species = "cat"
            }
        }
    }
}

/// One sticker color circle. Selected: an avocado700 ring around it.
private struct StickerSwatch: View {
    let tint: StickerAvatar.Tint
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button {
            action()
        } label: {
            Circle()
                .fill(tint.color)
                .overlay {
                    Circle()
                        .strokeBorder(isSelected ? Color.avocado700 : Color.clear, lineWidth: 2.5)
                }
                .frame(width: isSelected ? 35 : 30, height: isSelected ? 35 : 30)
                .frame(minWidth: PearMetrics.minTapTarget, minHeight: PearMetrics.minTapTarget)
                .contentShape(Circle())
        }
        .buttonStyle(PressableStyle())
        .accessibilityLabel(Text(tint.name))
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}
