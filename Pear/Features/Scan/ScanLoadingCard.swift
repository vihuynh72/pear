//
//  ScanLoadingCard.swift
//  Pear
//
//  The calm card while Pear works (design page c-15): pear, title, a progress bar and three
//  steps that tick along ("Finding the product", "Reading the ingredients", "Checking for 4
//  people"). The steps move on a gentle timer and the last one waits for the real answer, so
//  the card never claims to be done before the server is.
//

import SwiftUI

struct ScanLoadingCard: View {
    enum Kind { case barcode, photo, question }

    let kind: Kind
    let peopleCount: Int

    @State private var step = 0

    private var title: String {
        switch kind {
        case .barcode: "Checking the product"
        case .photo: "Reading the label"
        case .question: "Thinking about it"
        }
    }

    private var steps: [String] {
        let people = peopleCount == 1 ? "1 person" : "\(peopleCount) people"
        switch kind {
        case .barcode: return ["Finding the product", "Reading the ingredients", "Checking for \(people)"]
        case .photo: return ["Reading the photo", "Finding the ingredients", "Checking for \(people)"]
        case .question: return ["Understanding the question", "Finding the product", "Checking for \(people)"]
        }
    }

    private var footnote: String {
        switch kind {
        case .barcode: "Usually about a second"
        case .photo: "Usually about 3 seconds"
        case .question: "Usually about a second"
        }
    }

    var body: some View {
        VStack(spacing: PearMetrics.Spacing.lg) {
            PearLogo(isAccessibilityElement: false)
                .frame(height: 88)
            Text(verbatim: title)
                .font(PearFont.title)
                .foregroundStyle(.cocoa)

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.avocado100)
                    Capsule().fill(.avocado500)
                        .frame(width: geo.size.width * CGFloat(step + 1) / CGFloat(steps.count + 1))
                }
            }
            .frame(height: 10)
            .animation(.easeInOut(duration: 0.5), value: step)

            VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, text in
                    HStack(spacing: PearMetrics.Spacing.sm) {
                        stepDot(index)
                        Text(verbatim: text)
                            .font(PearFont.body)
                            .foregroundStyle(index <= step ? .cocoa : .latte)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Text(verbatim: footnote)
                .font(PearFont.caption)
                .foregroundStyle(.latte)
        }
        .padding(PearMetrics.Spacing.xl)
        .background(RoundedRectangle(cornerRadius: 36, style: .continuous).fill(Color.cream))
        .padding(.horizontal, PearMetrics.screenPadding)
        .accessibilityElement(children: .combine)
        .task {
            // Tick through the first steps; the last one stays "in progress" until the answer lands.
            for next in 1..<steps.count {
                try? await Task.sleep(for: .milliseconds(kind == .photo ? 900 : 450))
                if Task.isCancelled { return }
                step = next
            }
        }
    }

    @ViewBuilder
    private func stepDot(_ index: Int) -> some View {
        if index < step {
            Circle().fill(.avocado700).frame(width: 18, height: 18)
        } else if index == step {
            Circle().fill(.avocado500).frame(width: 18, height: 18)
                .padding(4)
                .background(Circle().fill(.avocado100))
        } else {
            Circle().strokeBorder(.mist, lineWidth: 2).frame(width: 18, height: 18)
        }
    }
}

#Preview {
    ZStack {
        Color.black.opacity(0.8).ignoresSafeArea()
        ScanLoadingCard(kind: .photo, peopleCount: 4)
    }
}
