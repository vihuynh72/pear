//
//  AppRouter.swift
//  Pear
//
//  Where the app is: the splash, first-launch onboarding (Welcome -> Language -> Family ->
//  member setup), or Home. Remembers when onboarding is done, so the next launch goes
//  straight Home after the splash.
//
//  Usage:
//      @Environment(AppRouter.self) private var router
//      router.push(.family)
//      router.editMember(id: member.id)
//      router.back()
//      router.finishOnboarding()
//

import Foundation

@Observable
final class AppRouter {
    enum Phase: Hashable {
        case splash
        case onboarding
        case home
    }

    enum Route: Hashable {
        case language
        case family
        /// nil = add someone new.
        case member(id: String?)
    }

    private(set) var phase: Phase = .splash
    var path: [Route] = []
    private(set) var hasFinishedOnboarding: Bool

    @ObservationIgnored private let defaults: UserDefaults
    private static let finishedKey = "pear.hasFinishedOnboarding"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        hasFinishedOnboarding = defaults.bool(forKey: Self.finishedKey)
    }

    /// A router that starts at a given phase without touching UserDefaults history.
    static func preview(phase: Phase = .onboarding, path: [Route] = []) -> AppRouter {
        let router = AppRouter(defaults: UserDefaults(suiteName: "pear.preview") ?? .standard)
        router.phase = phase
        router.path = path
        return router
    }

    func splashFinished() {
        phase = hasFinishedOnboarding ? .home : .onboarding
        path = []
    }

    func push(_ route: Route) { path.append(route) }

    func addMember() { path.append(.member(id: nil)) }

    func editMember(id: String) { path.append(.member(id: id)) }

    func back() {
        if !path.isEmpty { path.removeLast() }
    }

    /// Family screen's Continue: Home on first launch, otherwise back to wherever Family was opened from.
    func familyDone() {
        if phase == .onboarding {
            finishOnboarding()
        } else {
            path.removeAll { $0 == .family }
        }
    }

    func finishOnboarding() {
        hasFinishedOnboarding = true
        defaults.set(true, forKey: Self.finishedKey)
        path = []
        phase = .home
    }

    /// DEBUG: forget onboarding and start again at Welcome.
    func resetOnboarding() {
        hasFinishedOnboarding = false
        defaults.removeObject(forKey: Self.finishedKey)
        path = []
        phase = .onboarding
    }
}
