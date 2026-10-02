//
//  Haptics.swift
//  Pear
//
//  Two feelings only: a light tap when a label is Ripe for everyone, a warning buzz when
//  someone has an allergy match.
//
//  Usage:
//      Haptics.ripe()
//      Haptics.allergy()
//      Haptics.play(for: scanResult)     // picks the right one (or none)
//

import UIKit

enum Haptics {
    static func ripe() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    static func allergy() {
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
    }

    /// Allergy for anyone -> warning; Ripe for everyone -> light tap; otherwise nothing.
    static func play(for result: ScanResult) {
        if result.verdicts.contains(where: { $0.state == .allergy }) {
            allergy()
        } else if !result.verdicts.isEmpty, result.verdicts.allSatisfy({ $0.state == .ripe }) {
            ripe()
        }
    }
}
