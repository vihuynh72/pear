//
//  PearMetrics.swift
//  Pear
//
//  Spacing, corner radii and control sizes. Heights are minimums so text can grow
//  with Dynamic Type without clipping.
//

import SwiftUI

nonisolated enum PearMetrics {
    /// Leading and trailing margin for every screen.
    static let screenPadding: CGFloat = 24

    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
        static let lg: CGFloat = 24
        static let xl: CGFloat = 32
        static let xxl: CGFloat = 48
    }

    enum Radius {
        /// Chips use a capsule; this is for small pills that are not capsules.
        static let small: CGFloat = 12
        /// Search fields and text fields.
        static let field: CGFloat = 18
        /// Option cards, person rows and other cards.
        static let card: CGFloat = 20
        /// Primary and Google buttons.
        static let button: CGFloat = 24
        /// Floating product stickers.
        static let sticker: CGFloat = 20
    }

    /// Minimum height of full-width buttons.
    static let buttonHeight: CGFloat = 56
    /// Minimum tappable size.
    static let minTapTarget: CGFloat = 44
    /// Back button circle.
    static let backButtonSize: CGFloat = 48
    /// Default icon point size next to words.
    static let iconSize: CGFloat = 24
    static let borderWidth: CGFloat = 1
    static let selectedBorderWidth: CGFloat = 2
}
