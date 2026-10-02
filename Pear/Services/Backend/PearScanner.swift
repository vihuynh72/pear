// PearScanner.swift
// One camera for both inputs (plan 5.4): live barcode detection plus a shutter for label photos,
// using VisionKit's DataScannerViewController. iOS 17+, real device only (DataScanner is unavailable
// in the Simulator: check PearScannerView.isAvailable and offer the demo barcodes there instead).
// Needs NSCameraUsageDescription.
//
// Usage in a SwiftUI view:
//   @State private var camera = PearScannerController()
//   PearScannerView(controller: camera, isActive: flow.phase == .idle) { code in
//       Task { await flow.scanBarcode(code) }
//   }
//   Button { Task {
//       if let photo = try? await camera.capturePhoto(), let jpeg = PearImage.jpegForUpload(photo) {
//           await flow.scanPhoto(jpeg: jpeg)
//       }
//   } } label: { Label("Snap", systemImage: "camera.fill") }

import SwiftUI
import UIKit
import Vision
import VisionKit

@MainActor
final class PearScannerController {
    fileprivate weak var scanner: DataScannerViewController?

    enum CaptureError: Error { case notReady }

    /// Full-resolution photo of what the camera sees (the ingredient panel). Shrink it with PearImage before upload.
    func capturePhoto() async throws -> UIImage {
        guard let scanner else { throw CaptureError.notReady }
        if !scanner.isScanning { try? scanner.startScanning() }
        guard scanner.isScanning else { throw CaptureError.notReady }
        return try await scanner.capturePhoto()
    }

    /// Starts the live camera if it isn't running yet (e.g. after coming back from the background).
    func startIfNeeded() {
        guard let scanner, !scanner.isScanning, scanner.view.window != nil else { return }
        do {
            try scanner.startScanning()
        } catch {
            #if DEBUG
            print("[Pear] camera startScanning failed: \(error)")
            #endif
        }
    }
}

struct PearScannerView: UIViewControllerRepresentable {
    let controller: PearScannerController
    /// false while a result or error is on screen, so the same barcode doesn't fire again.
    var isActive: Bool = true
    var onBarcode: (String) -> Void
    /// Called if the camera becomes unavailable (for example camera access denied). Show a message.
    var onUnavailable: () -> Void = {}

    static var isAvailable: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        // UPC-A arrives as EAN-13 with a leading 0; the server accepts both forms.
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.ean13, .ean8, .upce])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            // Off: VisionKit's "Find nearby barcodes" label sits under ScanView's top bar, and
            // ScanView already shows its own hint ("Point at a barcode, or snap the ingredients").
            isGuidanceEnabled: false,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        controller.scanner = scanner
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        context.coordinator.onBarcode = onBarcode
        context.coordinator.onUnavailable = onUnavailable
        context.coordinator.isActive = isActive
        if !scanner.isScanning {
            // Keep the preview live; isActive only gates the callback. startScanning() throws while the
            // view isn't in a window yet (the first update runs before it appears), so retry until it runs.
            context.coordinator.startWhenVisible(scanner)
        }
    }

    static func dismantleUIViewController(_ scanner: DataScannerViewController, coordinator: Coordinator) {
        scanner.stopScanning()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onBarcode: onBarcode)
    }

    @MainActor
    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        var onBarcode: (String) -> Void
        var onUnavailable: () -> Void = {}
        var isActive = true
        private var lastCode: String?
        private var lastTime = Date.distantPast

        private var starting = false

        init(onBarcode: @escaping (String) -> Void) {
            self.onBarcode = onBarcode
        }

        /// Calls startScanning() once the scanner's view is on screen, retrying for about 5 s.
        func startWhenVisible(_ scanner: DataScannerViewController) {
            guard !starting else { return }
            starting = true
            Task { @MainActor [weak self, weak scanner] in
                var lastError: Error?
                for _ in 0..<50 {
                    guard let scanner else { break }
                    if scanner.isScanning { lastError = nil; break }
                    if scanner.view.window != nil {
                        do {
                            try scanner.startScanning()
                            lastError = nil
                            break
                        } catch {
                            lastError = error
                        }
                    }
                    try? await Task.sleep(for: .milliseconds(100))
                }
                self?.starting = false
                if let lastError {
                    #if DEBUG
                    print("[Pear] camera startScanning failed: \(lastError)")
                    #endif
                    self?.onUnavailable()
                }
            }
        }

        func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem],
                         allItems: [RecognizedItem]) {
            for item in addedItems {
                if case .barcode(let barcode) = item, let payload = barcode.payloadStringValue {
                    handle(payload)
                }
            }
        }

        func dataScanner(_ dataScanner: DataScannerViewController, didTapOn item: RecognizedItem) {
            if case .barcode(let barcode) = item, let payload = barcode.payloadStringValue {
                lastCode = nil
                handle(payload)
            }
        }

        func dataScanner(_ dataScanner: DataScannerViewController,
                         becameUnavailableWithError error: DataScannerViewController.ScanningUnavailable) {
            onUnavailable()
        }

        private func handle(_ payload: String) {
            guard isActive else { return }
            let code = payload.filter(\.isNumber)
            guard (8...14).contains(code.count) else { return }
            let now = Date()
            if code == lastCode, now.timeIntervalSince(lastTime) < 4 { return }
            lastCode = code
            lastTime = now
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            onBarcode(code)
        }
    }
}
