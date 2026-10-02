//
//  AuthService.swift
//  Pear
//
//  Who is using the app. Guest mode works today (the backend's auth is "optional").
//  Google sign-in is a placeholder until Firebase is added.
//
//  Usage:
//      let auth: any AuthService = GuestAuthService()
//      try await auth.signIn()                 // Google: throws .notConfigured for now
//      let token = await auth.idToken()        // nil in guest mode
//      // When the server requires sign-in: PearAPI(idTokenProvider: { await auth.idToken() }).
//

import Foundation

protocol AuthService: AnyObject, Sendable {
    /// Display name of the signed-in person, nil for guests.
    var displayName: String? { get }
    var isSignedIn: Bool { get }
    func signIn() async throws
    func signOut()
    /// A fresh Firebase ID token for `Authorization: Bearer`, or nil (guest).
    func idToken() async -> String?
}

enum AuthError: LocalizedError {
    case notConfigured

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            String(localized: "Google sign-in is coming soon. You can look around as a guest for now.")
        }
    }
}

/// No account: every request goes out without a token.
@Observable
final class GuestAuthService: AuthService {
    var displayName: String? { nil }
    var isSignedIn: Bool { false }
    func signIn() async throws {}
    func signOut() {}
    func idToken() async -> String? { nil }
}

/// Placeholder for "Continue with Google".
///
/// TODO(Vi): wire up Firebase Auth when there is time after the demo:
/// 1. Add the SPM packages FirebaseAuth (firebase-ios-sdk) and GoogleSignIn (GoogleSignIn-iOS).
/// 2. Download GoogleService-Info.plist from the Firebase console and drop it into Pear/.
/// 3. Add the REVERSED_CLIENT_ID from that plist as a URL scheme (target > Info > URL Types)
///    and call `GIDSignIn.sharedInstance.handle(url)` from `.onOpenURL`.
/// 4. Call `FirebaseApp.configure()` at launch (PearApp.init).
/// 5. signIn(): `GIDSignIn.sharedInstance.signIn(withPresenting:)`, then
///    `Auth.auth().signIn(with: GoogleAuthProvider.credential(withIDToken:accessToken:))`.
/// 6. idToken(): `try? await Auth.auth().currentUser?.getIDToken()`. That
///    is what PearAPI(idTokenProvider:) needs, so every request gets the Bearer header (build the
///    PearScanFlow in PearEnvironment with that API).
@Observable
final class GoogleAuthService: AuthService {
    private(set) var displayName: String?
    var isSignedIn: Bool { displayName != nil }

    func signIn() async throws {
        throw AuthError.notConfigured
    }

    func signOut() {
        displayName = nil
    }

    func idToken() async -> String? { nil }
}
