//
//  PearApp.swift
//  Pear
//
//  Created by Vi Huynh on 10/1/26.
//

import SwiftUI

@main
struct PearApp: App {
    init() {
        // Registers any Baloo 2, Nunito or Patrick Hand files in the bundle (no Info.plist edit needed).
        FontRegistrar.registerBundledFonts()
    }

    var body: some Scene {
        WindowGroup {
            // RootView builds PearEnvironment (PearAPI, family, the one PearScanFlow, auth) and
            // AppRouter once, and injects both with .environment(...).
            RootView()
        }
    }
}
