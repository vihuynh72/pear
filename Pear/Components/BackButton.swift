//
//  BackButton.swift
//  Pear
//
//  A soft avocado50 circle with a chevron. VoiceOver reads "Back".
//

import SwiftUI

struct BackButton: View {
    let action: () -> Void

    var body: some View {
        Button {
            action()
        } label: {
            Image(systemName: "chevron.left")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(.cocoa)
                .frame(width: PearMetrics.backButtonSize, height: PearMetrics.backButtonSize)
                .background { Circle().fill(.avocado50) }
                .contentShape(Circle())
        }
        .buttonStyle(PressableStyle())
        .accessibilityLabel(Text("Back"))
    }
}

#Preview {
    BackButton {}
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .pearBackground()
}
