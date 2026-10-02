// PearDictation.swift
// Hold-to-talk for Ask Pear. Apple speech recognition streams partial text while the button is held,
// so the question is ready the moment it's released; then send it with PearScanFlow.ask(text, language:).
// PearRecorder is the fallback for a language this phone can't dictate: record m4a, send with PearAPI.askAudio.
// iOS 17+ only (AVFoundation, Speech). Needs NSMicrophoneUsageDescription and NSSpeechRecognitionUsageDescription.

@preconcurrency import AVFoundation
@preconcurrency import Speech
import Observation

@MainActor
@Observable
final class PearDictation {
    enum DictationError: Error {
        case notAuthorized
        case unavailable
    }

    private(set) var transcript = ""       // live partial text, show it under the mic while listening
    private(set) var isListening = false

    @ObservationIgnored private let engine = AVAudioEngine()
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?
    @ObservationIgnored private var recognizer: SFSpeechRecognizer?   // kept alive for the whole recognition
    @ObservationIgnored private var tapInstalled = false
    @ObservationIgnored private var gotFinal = false
    @ObservationIgnored private var sessionActive = false
    @ObservationIgnored private var generation = 0      // ignores late callbacks from an earlier hold

    /// "vi" -> "vi-VN", "es" -> "es-US", anything else -> "en-US".
    nonisolated static func localeID(for language: String) -> String {
        switch language.prefix(2).lowercased() {
        case "vi": return "vi-VN"
        case "es": return "es-US"
        default: return "en-US"
        }
    }

    /// false means this phone can't dictate that language: use PearRecorder + PearAPI.askAudio instead.
    nonisolated static func supports(_ language: String) -> Bool {
        let want = localeID(for: language)
        return SFSpeechRecognizer.supportedLocales().contains {
            $0.identifier.replacingOccurrences(of: "_", with: "-") == want
        }
    }

    /// Speech recognition + microphone permission. Call once before the first hold-to-talk.
    nonisolated static func requestPermissions() async -> Bool {
        let speechOK = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
        guard speechOK else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }

    /// Mic button pressed. language: the asker's first language or the EN/VI/ES pill.
    func start(language: String) throws {
        cancel()
        generation += 1
        let gen = generation
        transcript = ""
        gotFinal = false
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { throw DictationError.notAuthorized }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: Self.localeID(for: language))),
              recognizer.isAvailable else { throw DictationError.unavailable }
        self.recognizer = recognizer

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        self.request = request

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            sessionActive = true

            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format, block: Self.tap(appendingTo: request))
            tapInstalled = true
            engine.prepare()
            try engine.start()
        } catch {
            cancel()
            throw error
        }
        isListening = true

        task = recognizer.recognitionTask(with: request, resultHandler: Self.handler { [weak self] text, isFinal in
            Task { @MainActor [weak self] in
                guard let self, self.generation == gen else { return }
                if let text, !text.isEmpty { self.transcript = text }
                if isFinal { self.gotFinal = true }
            }
        })
    }

    /// Mic button released. Returns the final text ("" if nothing was heard).
    func stop() async -> String {
        guard isListening else { return "" }
        let gen = generation
        stopEngine()                  // tap off first, then end the audio (Apple's SpokenWord sample order)
        request?.endAudio()
        // The last words arrive shortly after endAudio(). Wait for the final result, at most 0.8 s.
        for _ in 0..<16 where !gotFinal {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        guard gen == generation else { return "" }   // a new hold started meanwhile: leave it running
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        cancel()
        return text
    }

    /// Drops everything (sheet dismissed, app backgrounded).
    func cancel() {
        task?.cancel()
        task = nil
        request = nil
        recognizer = nil
        stopEngine()
        isListening = false
        if sessionActive {
            sessionActive = false
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    private func stopEngine() {
        if engine.isRunning { engine.stop() }
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
    }

    // Built outside the main actor on purpose: the audio tap and the recognizer call back on background
    // threads, and a closure created inside a @MainActor method would trap there under Swift 6.
    nonisolated private static func tap(appendingTo request: SFSpeechAudioBufferRecognitionRequest)
        -> @Sendable (AVAudioPCMBuffer, AVAudioTime) -> Void {
        return { buffer, _ in request.append(buffer) }
    }

    nonisolated private static func handler(_ onUpdate: @escaping @Sendable (String?, Bool) -> Void)
        -> @Sendable (SFSpeechRecognitionResult?, (any Error)?) -> Void {
        return { result, error in
            onUpdate(result?.bestTranscription.formattedString, error != nil || (result?.isFinal ?? false))
        }
    }
}

/// Fallback recorder: m4a (AAC, 16 kHz mono) for PearAPI.askAudio when PearDictation.supports(language) is false.
@MainActor
final class PearRecorder {
    private var recorder: AVAudioRecorder?
    private let url = FileManager.default.temporaryDirectory.appendingPathComponent("pear-question.m4a")

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .default)
        try session.setActive(true)
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]
        try? FileManager.default.removeItem(at: url)
        let recorder = try AVAudioRecorder(url: url, settings: settings)
        guard recorder.record() else { throw CocoaError(.fileWriteUnknown) }
        self.recorder = recorder
    }

    var isRecording: Bool { recorder != nil }

    /// Returns the m4a bytes for PearAPI.askAudio(m4a:...), or nil when nothing was recorded.
    func stop() -> Data? {
        guard let recorder else { return nil }
        recorder.stop()
        self.recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        defer { try? FileManager.default.removeItem(at: url) }
        return try? Data(contentsOf: url)
    }
}
