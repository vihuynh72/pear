//
//  FlowLayout.swift
//  Pear
//
//  Lays children out left to right and wraps to a new line when the row is full,
//  like the chip groups in the onboarding mockups.
//

import SwiftUI

struct FlowLayout: Layout {
    var spacing: CGFloat = PearMetrics.Spacing.xs
    var lineSpacing: CGFloat = PearMetrics.Spacing.xs

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(maxWidth: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.last.map { $0.y + $0.height } ?? 0
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = arrange(maxWidth: bounds.width, subviews: subviews)
        for row in rows {
            for item in row.items {
                // Center each item vertically within its row.
                let y = bounds.minY + row.y + (row.height - item.size.height) / 2
                subviews[item.index].place(
                    at: CGPoint(x: bounds.minX + item.x, y: y),
                    proposal: ProposedViewSize(item.size)
                )
            }
        }
    }

    private struct Item {
        let index: Int
        let x: CGFloat
        let size: CGSize
    }

    private struct Row {
        var items: [Item] = []
        var y: CGFloat = 0
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(maxWidth: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var current = Row()

        for index in subviews.indices {
            // Let long labels wrap inside the available width instead of overflowing.
            var size = subviews[index].sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
            size.width = min(size.width, maxWidth)

            let proposedX = current.items.isEmpty ? 0 : current.width + spacing
            if !current.items.isEmpty && proposedX + size.width > maxWidth {
                let nextY = current.y + current.height + lineSpacing
                rows.append(current)
                current = Row(y: nextY)
            }

            let x = current.items.isEmpty ? 0 : current.width + spacing
            current.items.append(Item(index: index, x: x, size: size))
            current.width = x + size.width
            current.height = max(current.height, size.height)
        }

        if !current.items.isEmpty { rows.append(current) }
        return rows
    }
}

private struct FlowLayoutPreviewTag: View {
    let name: String

    var body: some View {
        Text(verbatim: name)
            .font(PearFont.body)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background { Capsule().fill(.white) }
            .overlay { Capsule().strokeBorder(.mist) }
    }
}

private let flowLayoutPreviewNames: [String] = [
    "Milk", "Eggs", "Peanuts", "Tree nuts", "Sesame", "Soy", "Wheat", "Fish", "Shellfish", "Gluten"
]

#Preview {
    FlowLayout(spacing: 8, lineSpacing: 8) {
        ForEach(flowLayoutPreviewNames, id: \.self) { name in
            FlowLayoutPreviewTag(name: name)
        }
    }
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .pearBackground()
}
