// PearScanFlow.swift
// The whole scan flow as one observable object for SwiftUI (iOS 17 Observation):
//   scan (barcode or photo) -> verdict on screen -> picks (about 0.8 s later) -> prices (about 5 to 6 s after the scan)
// plus Ask Pear (typed or dictated text) with the product on screen as context.
// Picks and prices never block or replace the verdict. Foundation + Observation only.

import Foundation
import Observation

@MainActor
@Observable
final class PearScanFlow {
    enum Phase: Equatable {
        case idle
        case loading(String)                         // calm progress copy, e.g. "Checking..." / "Reading the label..."
        case result
        case failed(message: String, next: String)   // next: "take_photo" | "retake" | "retry"
    }

    /// One spoken line for Listen: the person's line in that person's language.
    struct SpokenLine: Hashable, Sendable {
        let text: String
        let language: String
    }

    var household: Household
    private(set) var phase: Phase = .idle
    private(set) var result: ScanResult?
    private(set) var picks: [PickItem] = []
    private(set) var picksLoading = false
    private(set) var prices: [String: Price] = [:]   // product key -> price (scanned product and picks)
    private(set) var pricesLoading = false
    private(set) var answer: AskResponse?
    private(set) var asking = false
    private(set) var askError: String?

    @ObservationIgnored let api: PearAPI
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var followUp: Task<Void, Never>?
    @ObservationIgnored private var lastRequest: (@MainActor () async -> Void)?

    init(household: Household = PearDemo.household, api: PearAPI = .shared) {
        self.household = household
        self.api = api
    }

    /// Key of the product on screen. Sent as last_product_key with every ask.
    var lastProductKey: String? { result?.product.key }

    /// GET /health in the background. Call on launch, on scenePhase .active and when the camera opens.
    func warm() {
        let api = self.api
        Task { await api.warm() }
    }

    // MARK: - Scan

    func scanBarcode(_ code: String) async {
        let api = self.api, household = self.household
        lastRequest = { [weak self] in await self?.scanBarcode(code) }
        await run(loading: "Checking...") { try await api.scanBarcode(code, household: household) }
    }

    /// jpeg: ingredient panel, about 1600 px long edge (PearImage.jpegForUpload). frontJPEG is optional.
    func scanPhoto(jpeg: Data, frontJPEG: Data? = nil) async {
        let api = self.api, household = self.household
        lastRequest = { [weak self] in await self?.scanPhoto(jpeg: jpeg, frontJPEG: frontJPEG) }
        await run(loading: "Reading the label...") {
            try await api.scanPhoto(jpeg: jpeg, frontJPEG: frontJPEG, household: household)
        }
    }

    /// Repeats the last scan (for next == "retry").
    func retry() async {
        await lastRequest?()
    }

    /// Back to the camera.
    func reset() {
        generation += 1
        followUp?.cancel()
        phase = .idle
        result = nil
        picks = []
        prices = [:]
        picksLoading = false
        pricesLoading = false
        answer = nil
        askError = nil
    }

    private func run(loading: String, _ call: @escaping @Sendable () async throws -> ScanResult) async {
        generation += 1
        let gen = generation
        followUp?.cancel()
        picks = []
        prices = [:]
        answer = nil
        askError = nil
        phase = .loading(loading)
        do {
            let r = try await call()
            guard gen == generation else { return }
            show(r)
        } catch is CancellationError {
            return
        } catch let error as PearAPIError {
            guard gen == generation else { return }
            phase = .failed(message: error.userMessage, next: error.nextAction)
        } catch {
            guard gen == generation else { return }
            phase = .failed(message: "Something went wrong. Try again.", next: "retry")
        }
    }

    /// Puts a result on screen, then loads picks and prices behind it.
    func show(_ r: ScanResult) {
        result = r
        phase = .result
        followUp?.cancel()
        let gen = generation
        followUp = Task { [weak self] in
            await self?.loadPicksAndPrices(for: r.product, generation: gen)
        }
    }

    private func loadPicksAndPrices(for product: Product, generation gen: Int) async {
        var keys = [product.key]
        if product.category == "food" {             // the server only has picks for food
            picksLoading = true
            let response = try? await api.picks(productKey: product.key, household: household)
            guard gen == generation, !Task.isCancelled else { return }
            picks = response?.items ?? []
            picksLoading = false
            keys += picks.map(\.product.key)
        }
        pricesLoading = true
        let response = try? await api.prices(productKeys: keys)
        guard gen == generation, !Task.isCancelled else { return }
        prices = response?.prices.compactMapValues { $0 } ?? [:]
        pricesLoading = false
    }

    // MARK: - Ask Pear

    /// language: "en" | "vi" | "es" from the language pill, or nil to let the server detect it.
    /// When the answer carries a scan result (intent "check" or "alternatives"), it replaces the result on screen.
    /// Speak `answer.answer.text` in `answer.answer.language` (PearVoice).
    @discardableResult
    func ask(_ text: String, language: String? = nil) async -> AskResponse? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        asking = true
        askError = nil
        defer { asking = false }
        do {
            let response = try await api.ask(text: trimmed, language: language,
                                             lastProductKey: lastProductKey, household: household)
            answer = response
            if response.next == "result", let r = response.scanResult, r != result {
                generation += 1
                picks = []
                prices = [:]
                show(r)
            }
            return response
        } catch is CancellationError {
            return nil
        } catch let error as PearAPIError {
            askError = error.userMessage
            return nil
        } catch {
            askError = "Something went wrong. Try again."
            return nil
        }
    }

    /// Fallback for a language the phone can't dictate: m4a from PearRecorder, understood by Gemini on the server.
    @discardableResult
    func askAudio(_ m4a: Data, language: String? = nil) async -> AskResponse? {
        asking = true
        askError = nil
        defer { asking = false }
        do {
            let response = try await api.askAudio(m4a: m4a, language: language,
                                                  lastProductKey: lastProductKey, household: household)
            answer = response
            if response.next == "result", let r = response.scanResult, r != result {
                generation += 1
                picks = []
                prices = [:]
                show(r)
            }
            return response
        } catch is CancellationError {
            return nil
        } catch let error as PearAPIError {
            askError = error.userMessage
            return nil
        } catch {
            askError = "Something went wrong. Try again."
            return nil
        }
    }

    // MARK: - Helpers for the result sheet

    func member(_ id: String) -> Member? {
        household.members.first { $0.id == id }
    }

    /// The one line under a chip (plan 5.6): first reason, shown when the state isn't RIPE.
    func reasonLine(for verdict: Verdict) -> String? {
        guard verdict.state != .ripe else { return nil }
        return verdict.reasons.first?.text
    }

    /// Most serious state on the sheet (for haptics: ALLERGY shakes, all RIPE bounces).
    var mostSerious: VerdictState? {
        result?.verdicts.map(\.state).min { $0.rank < $1.rank }
    }

    var everyoneRipe: Bool {
        guard let verdicts = result?.verdicts, !verdicts.isEmpty else { return false }
        return verdicts.allSatisfy { $0.state == .ripe }
    }

    /// Listen: "Mia: Allergy. Contains sesame (sesame seeds)" in English, "Bà: Chín." in Vietnamese, and so on.
    func listenLines() -> [SpokenLine] {
        guard let result else { return [] }
        return result.verdicts.compactMap { verdict in
            guard let person = member(verdict.memberId) else { return nil }
            var text = "\(person.name): \(verdict.label)."
            if let reason = reasonLine(for: verdict) {
                // Reasons like "Puede no ser vegano: tiene ..." already start with the chip word; don't say it twice.
                text = reason.lowercased().hasPrefix(verdict.label.lowercased())
                    ? "\(person.name): \(reason)." : text + " \(reason)."
            }
            return SpokenLine(text: text, language: person.primaryLanguage)
        }
    }
}
