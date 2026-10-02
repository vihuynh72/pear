//
//  SearchField.swift
//  Pear
//
//  A white rounded search field with a magnifying glass and a clear button.
//

import SwiftUI

struct SearchField: View {
    let placeholder: LocalizedStringKey
    @Binding var text: String
    var onSubmit: () -> Void = {}

    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(spacing: PearMetrics.Spacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 20, weight: .medium))
                .foregroundStyle(.latte)
                .accessibilityHidden(true)

            TextField(placeholder, text: $text, prompt: Text(placeholder).foregroundStyle(.latte))
                .font(PearFont.body)
                .foregroundStyle(.cocoa)
                .tint(.avocado700)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .submitLabel(.search)
                .focused($isFocused)
                .onSubmit(onSubmit)

            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(.latte)
                        .frame(minWidth: PearMetrics.minTapTarget, minHeight: PearMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(Text("Clear search"))
            }
        }
        .padding(.leading, PearMetrics.Spacing.md)
        .padding(.trailing, text.isEmpty ? PearMetrics.Spacing.md : 4)
        .frame(minHeight: 52)
        .background {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                .fill(.white)
        }
        .overlay {
            RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                .strokeBorder(isFocused ? Color.avocado500 : Color.mist, lineWidth: isFocused ? 1.5 : PearMetrics.borderWidth)
        }
        .contentShape(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous))
        .onTapGesture { isFocused = true }
    }
}

#Preview {
    @Previewable @State var empty = ""
    @Previewable @State var filled = "sesame"
    VStack(spacing: 16) {
        SearchField(placeholder: "Type a language, any language", text: $empty)
        SearchField(placeholder: "Search, like sesame or kiwi", text: $filled)
    }
    .padding(24)
    .pearBackground()
}
