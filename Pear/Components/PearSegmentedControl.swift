//
//  PearSegmentedControl.swift
//  Pear
//
//  A soft two- or three-way switch: mist track, white selected segment.
//  Used for "A person / A pet" and "Strict / Ingredients only".
//

import SwiftUI

struct PearSegmentedControl<Option: Hashable>: View {
    let options: [Option]
    @Binding var selection: Option
    let title: (Option) -> LocalizedStringKey

    @Namespace private var selectionNamespace

    init(
        _ options: [Option],
        selection: Binding<Option>,
        title: @escaping (Option) -> LocalizedStringKey
    ) {
        self.options = options
        self._selection = selection
        self.title = title
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(options, id: \.self) { option in
                segment(for: option)
            }
        }
        .padding(4)
        .background {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                .fill(.mist)
        }
    }

    private func segment(for option: Option) -> some View {
        let isSelected = option == selection
        return Button {
            withAnimation(.snappy(duration: 0.25)) { selection = option }
        } label: {
            Text(title(option))
                .font(PearFont.body)
                .foregroundStyle(isSelected ? Color.cocoa : Color.latte)
                .multilineTextAlignment(.center)
                .padding(.horizontal, PearMetrics.Spacing.sm)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, minHeight: 40)
                .background {
                    if isSelected {
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(.white)
                            .shadow(color: Color.cocoa.opacity(0.08), radius: 4, y: 2)
                            .matchedGeometryEffect(id: "selection", in: selectionNamespace)
                    }
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

#Preview {
    @Previewable @State var who = "person"
    @Previewable @State var level = "strict"
    VStack(spacing: 24) {
        PearSegmentedControl(["person", "pet"], selection: $who) { $0 == "person" ? "A person" : "A pet" }
        PearSegmentedControl(["strict", "sensitive"], selection: $level) { $0 == "strict" ? "Strict" : "Ingredients only" }
    }
    .padding(24)
    .pearBackground()
}
