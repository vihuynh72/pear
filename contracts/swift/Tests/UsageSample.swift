//  UsageSample.swift
//  Pear Swift kit — compile-only sample of the recommended screen flows (NOT shipped).
//
//  check.sh typechecks this file the way the Pear app target compiles (MainActor default
//  isolation, Swift 5 + approachable concurrency, and Swift 6), which proves the kit drops
//  into the app without concurrency errors. Copy pieces into the app's view models.
//
//  Flows (README.md "Screen flows"):
//    launch / camera open  -> health() warmup, errors ignored
//    barcode               -> scanBarcode -> show verdicts -> picks -> prices (fill in place)
//    photo                 -> PearImageUpload.jpegData -> scanPhoto
//    ask                   -> dictated text -> ask(lastProductKey:) -> speak answer.text
//    explain               -> stream per-member text

#if canImport(SwiftUI) && canImport(UIKit)
import AVFoundation
import Foundation
import Observation
import SwiftUI
import UIKit

@MainActor
@Observable
final class PearUsageSampleModel {
    @ObservationIgnored let client: PearAPIClient
    var household = Household.demo
    var result: ScanResult?
    var picks: PicksResponse?
    var prices: PricesResponse?
    var answer: AskResponse?
    var explanations: [String: String] = [:]
    var errorText: String?
    var showCamera = false
    var showSignIn = false
    @ObservationIgnored private let speech = AVSpeechSynthesizer()

    init(baseURL: URL = URL(string: "http://192.168.1.20:8080")!, mock: Bool = true) {
        client = PearAPIClient(
            baseURL: baseURL,
            mode: mock ? .mock : .live,
            tokenProvider: nil,  // guest mode; Firebase: { try? await Auth.auth().currentUser?.getIDToken() }
            debug: true
        )
    }

    func warmup() async {
        _ = try? await client.health()
    }

    func scan(code: String) async {
        do {
            let scan = try await client.scanBarcode(code: code, household: household)
            result = scan
            errorText = nil
            await loadPicksAndPrices(for: scan.product.key)
        } catch {
            handle(error)
        }
    }

    func scanPhoto(_ image: UIImage, front: UIImage? = nil) async {
        guard let jpeg = PearImageUpload.jpegData(from: image) else { return }
        let frontJPEG = front.flatMap { PearImageUpload.jpegData(from: $0) }
        do {
            let scan = try await client.scanPhoto(image: jpeg, frontImage: frontJPEG, household: household)
            result = scan
            if scan.product.askForIngredients { errorText = "Snap the ingredients for a surer answer." }
            await loadPicksAndPrices(for: scan.product.key)
        } catch {
            handle(error)
        }
    }

    private func loadPicksAndPrices(for productKey: String) async {
        guard let picks = try? await client.picks(productKey: productKey, household: household) else { return }
        self.picks = picks
        let keys = Array(picks.items.prefix(3).map(\.product.key))
        guard !keys.isEmpty else { return }
        prices = try? await client.prices(productKeys: keys)
    }

    func ask(_ text: String, language: String, askerId: String?) async {
        do {
            let reply = try await client.ask(text: text, language: language, lastProductKey: result?.product.key,
                                             household: household, askerId: askerId)
            answer = reply
            if let scan = reply.scanResult { result = scan }
            if let picks = reply.picks { self.picks = picks }
            if reply.next == .scanNeeded { showCamera = true }
            speak(reply.answer)
        } catch {
            handle(error)
        }
    }

    func explain() async {
        guard let key = result?.product.key else { return }
        explanations = [:]
        do {
            for try await event in client.explain(productKey: key, household: household) {
                explanations[event.memberId, default: ""] += event.text
            }
        } catch {
            handle(error)
        }
    }

    private func speak(_ answer: Answer) {
        let utterance = AVSpeechUtterance(string: answer.text)
        utterance.voice = AVSpeechSynthesisVoice(language: answer.language)
        speech.speak(utterance)
    }

    private func handle(_ error: any Error) {
        guard let error = error as? PearAPIError else {
            errorText = error.localizedDescription
            return
        }
        if error.isCancelled { return }
        errorText = error.userMessage
        switch error.nextAction {
        case .takePhoto?, .retake?: showCamera = true
        case .signIn?: showSignIn = true
        case .retry?, .unknown?, nil: break
        }
        #if DEBUG
        print("Pear error \(error) rid=\(error.requestId ?? "-")")
        #endif
    }
}

struct PearUsageSampleView: View {
    @State private var model = PearUsageSampleModel()

    var body: some View {
        List {
            if let result = model.result {
                Text(result.headline.text)
                ForEach(result.verdicts) { verdict in
                    let name = model.household.member(id: verdict.memberId)?.name ?? verdict.memberId
                    Text("\(name): \(verdict.label)")
                }
            }
            if let picks = model.picks {
                ForEach(picks.items) { item in
                    let price = model.prices?.price(for: item.product.key)
                    Text("\(item.product.name ?? item.product.key) \(price?.displayText ?? "")")
                }
            }
            if let error = model.errorText { Text(error) }
        }
        .task { await model.warmup() }
        .refreshable { await model.scan(code: "0000000000017") }
    }
}
#endif
