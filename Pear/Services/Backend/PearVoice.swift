// PearVoice.swift
// Listen and spoken answers: each line in its own language (vi-VN for Bà, es-US for Leo, en-US for Mia).
// iOS only (AVFoundation).

@preconcurrency import AVFoundation

@MainActor
final class PearVoice: NSObject, AVSpeechSynthesizerDelegate {
    static let shared = PearVoice()
    private let synthesizer = AVSpeechSynthesizer()

    private override init() {
        super.init()
        synthesizer.delegate = self
    }

    var isSpeaking: Bool { synthesizer.isSpeaking }

    /// Speak an Ask Pear answer: PearVoice.shared.speak(response.answer.text, language: response.answer.language)
    func speak(_ text: String, language: String) {
        speak([PearScanFlow.SpokenLine(text: text, language: language)])
    }

    /// Listen button: PearVoice.shared.speak(flow.listenLines())
    func speak(_ lines: [PearScanFlow.SpokenLine]) {
        stop()
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? session.setActive(true)
        for line in lines where !line.text.isEmpty {
            let utterance = AVSpeechUtterance(string: line.text)
            utterance.voice = Self.voice(for: line.language)
            utterance.rate = AVSpeechUtteranceDefaultSpeechRate
            utterance.postUtteranceDelay = 0.3
            synthesizer.speak(utterance)
        }
    }

    func stop() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
    }

    // Hand the audio back to other apps when speech ends (.duckOthers keeps them quiet while the session is active).
    // nonisolated so these compile whether or not the SDK marks the protocol @MainActor.
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.releaseSessionIfIdle() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.releaseSessionIfIdle() }
    }

    private func releaseSessionIfIdle() {
        let session = AVAudioSession.sharedInstance()
        // If dictation already switched the session to .record, it owns the session now.
        guard !synthesizer.isSpeaking, session.category == .playback else { return }
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
    }

    /// Best installed voice for "en" | "vi" | "es". Uses an enhanced or premium voice when the phone has one
    /// (Settings > Accessibility > Spoken Content > Voices), otherwise the system default for that language.
    static func voice(for language: String) -> AVSpeechSynthesisVoice? {
        let codes: [String]
        switch language.prefix(2).lowercased() {
        case "vi": codes = ["vi-VN"]
        case "es": codes = ["es-US", "es-MX", "es-ES"]
        default: codes = ["en-US"]
        }
        let installed = AVSpeechSynthesisVoice.speechVoices()
        for code in codes {
            let better = installed.filter {
                $0.language == code && $0.quality != .default
                    && !$0.voiceTraits.contains(.isNoveltyVoice) && !$0.voiceTraits.contains(.isPersonalVoice)
            }
            if let best = better.max(by: { $0.quality.rawValue < $1.quality.rawValue }) { return best }
            if let standard = AVSpeechSynthesisVoice(language: code) { return standard }
        }
        return AVSpeechSynthesisVoice(language: "en-US")
    }
}
