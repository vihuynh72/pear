//
//  SelectChip.swift
//  Pear
//
//  A tappable capsule for multi-select lists (allergies, diets, age groups).
//  Selected: avocado100 fill, avocado500 border and a check mark, so the state never
//  relies on color alone. Put several in a FlowLayout so they wrap.
//

import SwiftUI

struct SelectChip: View {
    private let title: Text
    private let systemImage: String?
    private let isSelected: Bool
    private let action: () -> Void

    init(_ title: LocalizedStringKey, systemImage: String? = nil, isSelected: Bool, action: @escaping () -> Void) {
        self.title = Text(title)
        self.systemImage = systemImage
        self.isSelected = isSelected
        self.action = action
    }

    /// For titles that come from data, such as a knowledge base entry.
    init(verbatim title: String, systemImage: String? = nil, isSelected: Bool, action: @escaping () -> Void) {
        self.title = Text(verbatim: title)
        self.systemImage = systemImage
        self.isSelected = isSelected
        self.action = action
    }

    var body: some View {
        Button {
            action()
        } label: {
            HStack(spacing: 6) {
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.avocado700)
                        .accessibilityHidden(true)
                } else if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: 17, weight: .regular))
                        .foregroundStyle(.cocoa)
                        .accessibilityHidden(true)
                }
                title
                    .font(PearFont.body)
                    .foregroundStyle(.cocoa)
                    .multilineTextAlignment(.leading)
            }
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.vertical, 10)
            .frame(minHeight: PearMetrics.minTapTarget)
            .background {
                Capsule().fill(isSelected ? Color.avocado100 : Color.white)
            }
            .overlay {
                Capsule().strokeBorder(
                    isSelected ? Color.avocado500 : Color.mist,
                    lineWidth: isSelected ? 1.5 : PearMetrics.borderWidth
                )
            }
            .contentShape(Capsule())
        }
        .buttonStyle(PressableStyle())
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .animation(.snappy(duration: 0.2), value: isSelected)
    }
}

#Preview {
    @Previewable @State var selected: Set<String> = ["Sesame", "Peanuts"]
    let allergies = ["Milk", "Eggs", "Peanuts", "Tree nuts", "Sesame", "Soy", "Wheat", "Fish",
                     "Shellfish", "Gluten", "Lactose", "Mustard", "Sulfites", "Corn", "Kiwi"]
    FlowLayout(spacing: 12, lineSpacing: 12) {
        ForEach(allergies, id: \.self) { name in
            SelectChip(verbatim: name, isSelected: selected.contains(name)) {
                if selected.contains(name) { selected.remove(name) } else { selected.insert(name) }
            }
        }
    }
    .padding(24)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .pearBackground()
}
