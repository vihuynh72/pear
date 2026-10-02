// PearSearchSuggestions.swift
// Google Search Suggestions for grounded prices (Grounding with Google Search asks apps to show the
// suggestions that come back with a grounded result; BetterPicksView shows them under the picks),
// plus a preview helper that puts a bundled live-server fixture on screen.

import SwiftUI
import WebKit

// MARK: - Google Search Suggestions (Grounding with Google Search asks apps to show this next to grounded results)

struct PearSearchSuggestions: UIViewRepresentable {
    let html: String

    func makeUIView(context: Context) -> WKWebView {
        let web = WKWebView()
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.isScrollEnabled = false
        web.navigationDelegate = context.coordinator
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        guard context.coordinator.loaded != html else { return }
        context.coordinator.loaded = html
        let page = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>"
            + "<body style='margin:0;background:transparent'>" + html + "</body></html>"
        web.loadHTMLString(page, baseURL: nil)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        var loaded: String?

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async
            -> WKNavigationActionPolicy {
            if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url {
                UIApplication.shared.open(url, options: [:], completionHandler: nil)
                return .cancel
            }
            return .allow
        }
    }
}

// MARK: - Preview (bundled live-server fixture: Thomas' Everything Bagels for the demo family)

extension PearScanFlow {
    /// A flow showing Resources/Fixtures/<name>.json, for previews. Picks and prices come from the server's mock.
    static func previewShowing(_ fixture: String = "scan_barcode") -> PearScanFlow {
        let flow = PearScanFlow(household: PearDemo.household, api: PearAPI(useMock: true))
        if let url = Bundle.main.url(forResource: fixture, withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let result = try? PearAPI.decoder().decode(ScanResult.self, from: data) {
            flow.show(result)
        }
        return flow
    }
}
