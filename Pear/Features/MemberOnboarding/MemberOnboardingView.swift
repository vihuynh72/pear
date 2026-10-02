//
//  MemberOnboardingView.swift
//  Pear
//
//  Adding or editing one family member (plan 5.2), in six skippable steps:
//  Basics, Allergies, Health, Skin, Diet, Less of. Back goes one step back (and leaves
//  without saving from the first step). Done saves to HouseholdStore and returns to Family.
//  Pets only have the Basics step; Pear checks foods that are toxic for dogs and cats.
//

import SwiftUI

enum MemberStep: Int, CaseIterable, Hashable {
    case basics, allergies, health, skin, diet, lessOf

    /// The KBIndex group behind the step (KBIndex.groupOrder).
    var group: String {
        switch self {
        case .basics, .allergies: "allergies"
        case .health: "conditions"
        case .skin: "skin"
        case .diet: "diets"
        case .lessOf: "less_of"
        }
    }
}

struct MemberOnboardingView: View {
    @Environment(PearEnvironment.self) private var pear
    @Environment(AppRouter.self) private var router

    @State private var draft: MemberDraft
    @State private var step: MemberStep
    @State private var movingForward = true
    private let previewQuery: String

    /// - Parameters:
    ///   - memberID: the member to edit, or nil to add someone new.
    ///   - startAt / previewQuery: for previews only.
    init(memberID: String?, store: HouseholdStore, startAt: MemberStep = .basics, previewQuery: String = "") {
        _draft = State(initialValue: MemberDraft.make(memberID: memberID, store: store))
        _step = State(initialValue: startAt)
        self.previewQuery = previewQuery
    }

    private var steps: [MemberStep] { draft.isPet ? [.basics] : MemberStep.allCases }
    private var stepIndex: Int { steps.firstIndex(of: step) ?? 0 }
    private var isLastStep: Bool { stepIndex == steps.count - 1 }

    var body: some View {
        ScreenScaffold {
            content
        } footer: {
            footer
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            OnboardingHeader(
                stepIndex: stepIndex,
                stepCount: steps.count,
                onBack: { goBack() },
                onSkip: { goForward() }
            )
            .padding(.bottom, PearMetrics.Spacing.md)

            stepContent
                .id(step)
                .transition(stepTransition)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, PearMetrics.screenPadding)
        .padding(.top, PearMetrics.Spacing.xxs)
        .clipped()
    }

    private var stepTransition: AnyTransition {
        .push(from: movingForward ? .trailing : .leading)
    }

    @ViewBuilder
    private var footerButton: some View {
        if isLastStep {
            PrimaryButton(draft.isNew ? "Add to family" : "Done", systemImage: "checkmark") {
                goForward()
            }
        } else {
            PrimaryButton("Continue", systemImage: "arrow.right", iconPlacement: .trailing) {
                goForward()
            }
        }
    }

    private var footer: some View {
        footerButton
            .padding(.horizontal, PearMetrics.screenPadding)
            .padding(.bottom, PearMetrics.Spacing.xs)
    }

    @ViewBuilder
    private var stepContent: some View {
        switch step {
        case .basics:
            MemberBasicsView(draft: draft)
        case .allergies:
            AllergiesView(draft: draft, initialQuery: previewQuery)
        case .health, .skin, .diet, .lessOf:
            ChipStepView(step: step, draft: draft, initialQuery: previewQuery)
        }
    }

    private func goForward() {
        if isLastStep {
            save()
            return
        }
        movingForward = true
        withAnimation(.snappy(duration: 0.3)) { step = steps[stepIndex + 1] }
    }

    private func goBack() {
        guard stepIndex > 0 else {
            router.back()
            return
        }
        movingForward = false
        withAnimation(.snappy(duration: 0.3)) { step = steps[stepIndex - 1] }
    }

    private func save() {
        let member = draft.finalized(memberCount: pear.household.members.count)
        pear.household.save(member, appearance: draft.appearance)
        router.back()
    }
}

/// Back circle, step dots in the middle, Skip on the right.
struct OnboardingHeader: View {
    let stepIndex: Int
    let stepCount: Int
    let onBack: () -> Void
    let onSkip: () -> Void

    var body: some View {
        HStack {
            BackButton(action: onBack)
            Spacer(minLength: PearMetrics.Spacing.sm)
            Button {
                onSkip()
            } label: {
                HStack(spacing: 4) {
                    Text("Skip")
                        .font(PearFont.body)
                    Image(systemName: "chevron.forward")
                        .font(.system(size: 13, weight: .semibold))
                        .accessibilityHidden(true)
                }
                .foregroundStyle(.avocado700)
                .frame(minWidth: PearMetrics.minTapTarget, minHeight: PearMetrics.minTapTarget)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressableStyle())
            .accessibilityHint(Text("Goes to the next step"))
        }
        .overlay {
            if stepCount > 1 {
                StepDots(count: stepCount, current: stepIndex)
            }
        }
    }
}

#Preview("Basics: Mia (a-7)") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_mia", store: pear.household)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Allergies: Mia (a-8)") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_mia", store: pear.household, startAt: .allergies)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Allergies search (a-9)") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_mia", store: pear.household, startAt: .allergies, previewQuery: "cacahuate")
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Allergies: Ask Pear") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_mia", store: pear.household, startAt: .allergies, previewQuery: "latex")
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Health: Bà") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_ba", store: pear.household, startAt: .health)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Skin: Linh") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_linh", store: pear.household, startAt: .skin)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Diet: Leo") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_leo", store: pear.household, startAt: .diet)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Less of: Mia") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: "m_mia", store: pear.household, startAt: .lessOf)
        .environment(pear)
        .environment(AppRouter.preview())
}

#Preview("Add someone") {
    let pear = PearEnvironment.preview()
    MemberOnboardingView(memberID: nil, store: pear.household)
        .environment(pear)
        .environment(AppRouter.preview())
}
