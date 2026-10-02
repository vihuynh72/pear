//
//  PearEnvironment.swift
//  Pear
//
//  One object that owns the app's services. RootView creates it once and injects it with
//  `.environment(pear)`, so any screen can read it.
//
//  Usage:
//      @Environment(PearEnvironment.self) private var pear
//
//      pear.household.members      // the family (HouseholdStore)
//      pear.flow                   // PearScanFlow: scanBarcode / scanPhoto / ask / picks / prices
//      pear.api                    // PearAPI (live; the server's /mock only in #Previews)
//      pear.flow.warm()            // on launch, on foreground and when the camera opens
//      pear.displayLanguage        // the one language for every result ("en", "vi", "es")
//      pear.setShopper(id) / pear.setDisplayLanguage("vi")   // Home's "Who's shopping today?"
//      pear.health                 // .ok(Health) after launch
//
//      #Preview { MyScreen().environment(PearEnvironment.preview()) }
//

import Foundation

@Observable
final class PearEnvironment {
    enum HealthStatus {
        case unknown
        case checking
        case ok(Health)
        case failed(String)
    }

    let auth: any AuthService
    let household: HouseholdStore
    let flow: PearScanFlow
    /// true only for #Previews (the server's /mock fixtures). The app always talks to the live server.
    let useMock: Bool
    private(set) var health: HealthStatus = .unknown

    /// "Who's shopping today?" (Home's switcher). nil = the person using the phone.
    private(set) var shopperID: String?
    /// A language picked directly in the switcher. nil = the shopper's first language.
    private(set) var languageOverride: String?
    /// The camera's "For everyone ▾" pill: only these members are checked. nil = everyone.
    private(set) var forMemberIDs: Set<String>?

    /// Recent results (camera History) and "Add to my list" from Better picks. Phone only.
    let history: ScanHistory
    let list: ShoppingList

    var api: PearAPI { flow.api }

    private static let shopperKey = "pear.shopperID"
    private static let languageKey = "pear.displayLanguage"

    init(
        // Signed out = guest (no token). signIn() explains that Google is coming soon.
        auth: any AuthService = GoogleAuthService(),
        household: HouseholdStore = HouseholdStore(),
        useMock: Bool = false
    ) {
        // An older build had a "Use mock" switch on Home that was saved here. The switch is gone,
        // so clear the saved value: the app must never get stuck on mock data with no way back.
        UserDefaults.standard.removeObject(forKey: "pear.useMock")
        let shopperID = UserDefaults.standard.string(forKey: Self.shopperKey)
        let languageOverride = UserDefaults.standard.string(forKey: Self.languageKey)
        let language = Self.resolveLanguage(store: household, shopperID: shopperID, override: languageOverride)
        self.auth = auth
        self.household = household
        self.useMock = useMock
        self.shopperID = shopperID
        self.languageOverride = languageOverride
        self.history = ScanHistory(persists: !useMock)
        self.list = ShoppingList(persists: !useMock)
        self.flow = PearScanFlow(household: Self.oneLanguage(household.household, language),
                                 api: useMock ? PearAPI(useMock: true) : .shared)
        connectHousehold()
    }

    // MARK: Who's this for (the camera's "For everyone" pill)

    /// Toggling the last selected person off goes back to everyone.
    func toggleFor(_ memberID: String) {
        var ids = forMemberIDs ?? []
        if ids.contains(memberID) { ids.remove(memberID) } else { ids.insert(memberID) }
        forMemberIDs = ids.isEmpty || ids.count == household.members.count ? nil : ids
        syncFlowHousehold()
    }

    func checkEveryone() {
        forMemberIDs = nil
        syncFlowHousehold()
    }

    func isChecked(_ memberID: String) -> Bool { forMemberIDs?.contains(memberID) ?? true }

    /// "For everyone", "For Mia", "For Mia and Leo", "For 3 people".
    var forLabel: String {
        guard let ids = forMemberIDs else { return "For everyone" }
        let names = household.members.filter { ids.contains($0.id) }.map(\.name)
        switch names.count {
        case 0: return "For everyone"
        case 1: return "For \(names[0])"
        case 2: return "For \(names[0]) and \(names[1])"
        default: return "For \(names.count) people"
        }
    }

    /// How many people the next check covers (the loading card's "Checking for 4 people").
    var checkedCount: Int { flow.household.members.count }

    // MARK: Who's shopping today (one language for every result)

    /// The person shopping today: the switcher's choice, else the person using the phone.
    var shopper: Member? {
        Self.resolveShopper(store: household, shopperID: shopperID)
    }

    /// The one language for every headline, chip, reason, answer and Listen line ("en", "vi", "es").
    var displayLanguage: String {
        Self.resolveLanguage(store: household, shopperID: shopperID, override: languageOverride)
    }

    /// Picking a person also switches to their language.
    func setShopper(_ memberID: String) {
        shopperID = memberID
        languageOverride = nil
        UserDefaults.standard.set(memberID, forKey: Self.shopperKey)
        UserDefaults.standard.removeObject(forKey: Self.languageKey)
        syncFlowHousehold()
    }

    func setDisplayLanguage(_ code: String) {
        languageOverride = code
        UserDefaults.standard.set(code, forKey: Self.languageKey)
        syncFlowHousehold()
    }

    /// The server writes each member's lines in that member's first language and the headline in
    /// ownerLanguage. Sending a copy where everyone's first language is the shopper's gets every
    /// result in one language. The saved household (and its real languages) never changes.
    static func oneLanguage(_ household: Household, _ code: String) -> Household {
        var copy = household
        copy.ownerLanguage = code
        copy.members = household.members.map { member in
            var member = member
            member.languages = [code] + member.languages.filter { $0 != code }
            return member
        }
        return copy
    }

    private static func resolveShopper(store: HouseholdStore, shopperID: String?) -> Member? {
        let people = store.members.filter { $0.type == "person" }
        if let shopperID, let chosen = people.first(where: { $0.id == shopperID }) { return chosen }
        if let owner = store.ownerMemberID.flatMap({ store.member(id: $0) }) { return owner }
        return people.first
    }

    private static func resolveLanguage(store: HouseholdStore, shopperID: String?, override: String?) -> String {
        let code = override
            ?? resolveShopper(store: store, shopperID: shopperID)?.primaryLanguage
            ?? store.ownerLanguage
        return PearLanguage.all.contains { $0.code == code } ? code : "en"
    }

    private func syncFlowHousehold() {
        var sent = Self.oneLanguage(household.household, displayLanguage)
        if let ids = forMemberIDs {
            let chosen = sent.members.filter { ids.contains($0.id) }
            if !chosen.isEmpty { sent.members = chosen }
        }
        flow.household = sent
    }

    /// Demo family in memory, the server's mock endpoints. For #Previews.
    static func preview() -> PearEnvironment {
        PearEnvironment(auth: GuestAuthService(), household: .preview(), useMock: true)
    }

    /// GET /health. Called on launch: warms the server and the TLS connection, logs the result.
    func checkHealth() async {
        health = .checking
        do {
            let result = try await api.health()
            health = .ok(result)
            #if DEBUG
            print("[Pear] /health ok=\(result.ok) gemini=\(result.gemini) firestore=\(result.firestore) v\(result.version)\(useMock ? " (mock)" : "")")
            #endif
        } catch let error as PearAPIError {
            health = .failed(error.userMessage)
        } catch {
            health = .failed("No connection. Try again.")
        }
    }

    private func connectHousehold() {
        household.onChange = { [weak self] _ in
            self?.syncFlowHousehold()
        }
    }
}
