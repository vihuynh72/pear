//
//  StepDots.swift
//  Pear
//
//  Onboarding progress: done steps in avocado500, the current step as a wide avocado700
//  pill, upcoming steps in avocado200. VoiceOver reads "Step 2 of 6".
//

import SwiftUI

struct StepDots: View {
    /// Total number of steps.
    let count: Int
    /// Zero-based index of the current step.
    let current: Int

    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<max(count, 0), id: \.self) { index in
                Capsule()
                    .fill(color(for: index))
                    .frame(width: index == current ? 20 : 7, height: 7)
            }
        }
        .animation(.snappy, value: current)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Step \(min(current + 1, count)) of \(count)"))
    }

    private func color(for index: Int) -> Color {
        if index == current { return .avocado700 }
        return index < current ? .avocado500 : .avocado200
    }
}

#Preview {
    VStack(spacing: 24) {
        StepDots(count: 6, current: 0)
        StepDots(count: 6, current: 1)
        StepDots(count: 6, current: 5)
    }
    .padding(24)
    .pearBackground()
}
