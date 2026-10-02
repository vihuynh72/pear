//
//  RootView.swift
//  Pear
//
//  The app's one NavigationStack. Splash, then either first-launch onboarding
//  (Welcome -> Language -> Family -> member setup) or Home (ContentView).
//  Injects PearEnvironment and AppRouter for every screen below.
//

import SwiftUI

struct RootView: View {
    @State private var router: AppRouter
    @State private var pear: PearEnvironment
    @State private var showGoogleNotice = false
    @Environment(\.scenePhase) private var scenePhase

    init(router: AppRouter = AppRouter(), pear: PearEnvironment = PearEnvironment()) {
        _router = State(initialValue: router)
        _pear = State(initialValue: pear)
    }

    var body: some View {
        ZStack {
            switch router.phase {
            case .splash:
                SplashView {
                    withAnimation(.easeInOut(duration: 0.35)) { router.splashFinished() }
                }
                .transition(.opacity)
            case .onboarding, .home:
                NavigationStack(path: $router.path) {
                    rootScreen
                        .pearScreen()
                        .navigationDestination(for: AppRouter.Route.self) { route in
                            destination(for: route)
                                .pearScreen()
                        }
                }
                .id(router.phase)
                .transition(.opacity)
            }
        }
        .environment(router)
        .environment(pear)
        .tint(.avocado700)
        // /health on launch opens the TLS connection, so the first scan is fast.
        .task { await pear.checkHealth() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { pear.flow.warm() }
        }
        .alert(Text("Google sign-in is coming soon"), isPresented: $showGoogleNotice) {
            Button("Continue as guest") { router.push(.language) }
            Button("Not now", role: .cancel) {}
        } message: {
            Text("You can look around as a guest for now. Your family stays on this phone.")
        }
    }

    @ViewBuilder
    private var rootScreen: some View {
        if router.phase == .home {
            ContentView()
        } else {
            WelcomeView(
                onGoogle: signInWithGoogle,
                onDemo: {
                    pear.household.loadDemo()
                    router.push(.language)
                }
            )
        }
    }

    @ViewBuilder
    private func destination(for route: AppRouter.Route) -> some View {
        switch route {
        case .language:
            LanguageView(
                initialSelection: router.hasFinishedOnboarding ? pear.household.ownerLanguage : PearLanguage.deviceDefault,
                onBack: { router.back() },
                onContinue: { code in
                    pear.household.setOwnerLanguage(code)
                    router.push(.family)
                }
            )
        case .family:
            FamilyView()
        case .member(let id):
            MemberOnboardingView(memberID: id, store: pear.household)
        }
    }

    private func signInWithGoogle() {
        Task {
            do {
                try await pear.auth.signIn()
                router.push(.language)
            } catch {
                showGoogleNotice = true
            }
        }
    }
}

private extension View {
    /// Pear screens draw their own back button and background.
    func pearScreen() -> some View {
        toolbar(.hidden, for: .navigationBar)
            .navigationBarBackButtonHidden(true)
    }
}

#Preview("First launch") {
    RootView(router: .preview(phase: .onboarding), pear: .preview())
}

#Preview("Family step") {
    RootView(router: .preview(phase: .onboarding, path: [.language, .family]), pear: .preview())
}

#Preview("Home") {
    RootView(router: .preview(phase: .home), pear: .preview())
}
