//
//  FamilyView.swift
//  Pear
//
//  "Who are you shopping for?" (a-6). One row per family member (tap to edit), an
//  "Add someone" row while there are spots left, and Continue.
//

import SwiftUI

struct FamilyView: View {
    @Environment(PearEnvironment.self) private var pear
    @Environment(AppRouter.self) private var router

    var body: some View {
        let store = pear.household
        ScreenScaffold {
            VStack(alignment: .leading, spacing: 0) {
                BackButton { router.back() }
                    .padding(.bottom, PearMetrics.Spacing.md)

                Text("Who are you\nshopping for?")
                    .font(PearFont.largeTitle)
                    .foregroundStyle(.cocoa)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)

                Text("Add everyone whose food, drinks and soap you pick. Up to \(Self.spelledOut(store.memberLimit)) people, pets included.")
                    .font(PearFont.body)
                    .foregroundStyle(.latte)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, PearMetrics.Spacing.xs)

                VStack(spacing: PearMetrics.Spacing.sm) {
                    ForEach(store.members) { member in
                        row(for: member, in: store)
                    }
                    addRow(store)
                }
                .padding(.top, PearMetrics.Spacing.lg)
                .animation(.snappy(duration: 0.25), value: store.members.map(\.id))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.top, PearMetrics.Spacing.xxs)
        } footer: {
            PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {
                router.familyDone()
            }
            .disabled(store.members.isEmpty)
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.bottom, PearMetrics.Spacing.xs)
        }
    }

    private func row(for member: Member, in store: HouseholdStore) -> some View {
        let look = store.appearance(for: member.id)
        return PersonRow(
            name: member.name,
            subtitle: MemberSummary.subtitle(
                for: member,
                appearance: look,
                isOwner: store.isOwner(member.id),
                ownerLanguage: store.ownerLanguage
            ),
            kind: MemberSummary.avatarKind(for: member),
            tint: look.tint,
            systemImage: MemberSummary.avatarSymbol(for: member, appearance: look)
        ) {
            router.editMember(id: member.id)
        }
        .contextMenu {
            Button {
                router.editMember(id: member.id)
            } label: {
                Label("Edit \(member.name)", systemImage: "pencil")
            }
            if !store.isOwner(member.id) {
                Button(role: .destructive) {
                    store.remove(id: member.id)
                } label: {
                    Label("Remove \(member.name)", systemImage: "trash")
                }
            }
        }
    }

    @ViewBuilder
    private func addRow(_ store: HouseholdStore) -> some View {
        if store.canAddMember {
            AddPersonRow(subtitle: "A person or a pet, ^[\(store.spotsLeft) spot](inflect: true) left") {
                router.addMember()
            }
        } else {
            AddPersonRow(subtitle: "The family is full. Remove someone to add another.") {}
                .disabled(true)
                .opacity(0.6)
        }
    }

    private static func spelledOut(_ number: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .spellOut
        return formatter.string(from: NSNumber(value: number)) ?? String(number)
    }
}

#Preview("Demo family") {
    NavigationStack {
        FamilyView()
    }
    .environment(PearEnvironment.preview())
    .environment(AppRouter.preview())
}

#Preview("Family is full") {
    let pear = PearEnvironment.preview()
    let extra = pear.household.makeNewMember()
    var dog = extra.member
    dog.name = "Bánh Bao"
    dog.type = "pet"
    dog.species = "dog"
    pear.household.save(dog, appearance: extra.appearance)
    return NavigationStack {
        FamilyView()
    }
    .environment(pear)
    .environment(AppRouter.preview())
}
