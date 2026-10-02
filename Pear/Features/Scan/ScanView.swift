//
//  ScanView.swift
//  Pear
//
//  The camera (design pages c-11 to c-15). One screen, four ways in:
//    Barcode  live barcode detection (PearScannerView); the shutter also snaps a label photo
//    Label    frame the ingredient panel, snap -> PearScanFlow.scanPhoto
//    Voice    hold the mic, ask in EN / VI / ES -> PearScanFlow.ask (spoken answer)
//    Type     search Open Food Facts by name (ProductSearch), each hit checked by the Pear API,
//             or paste an ingredient list (rendered to an image and read like a label photo)
//  Top: Close, "For everyone ▾" (who this check is for), flashlight. Bottom: photo library,
//  shutter, History. Results open in ResultSheetView; every result is saved to History.
//  On the Simulator, with iPhone Mirroring or with the camera off, Barcode shows demo products.
//

import AVFoundation
import PhotosUI
import SwiftUI
import VisionKit

struct ScanView: View {
    enum Mode: String, CaseIterable, Identifiable {
        case barcode, label, voice, type
        var id: String { rawValue }
        var title: String {
            switch self {
            case .barcode: "Barcode"
            case .label: "Label"
            case .voice: "Voice"
            case .type: "Type"
            }
        }
        var icon: String {
            switch self {
            case .barcode: "barcode.viewfinder"
            case .label: "text.viewfinder"
            case .voice: "mic"
            case .type: "pencil"
            }
        }
    }

    struct SearchHit: Identifiable {
        let product: FoundProduct
        var result: ScanResult?
        var missing = false
        var id: String { product.code }
    }

    @Environment(PearEnvironment.self) private var pear
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    @State private var mode: Mode
    @State private var camera = PearScannerController()
    @State private var cameraState: CameraState = .checking
    @State private var showDemo = false
    @State private var photoItem: PhotosPickerItem?
    @State private var showResult = false
    @State private var showHistory = false
    @State private var snapping = false
    @State private var snapHint = false
    @State private var torchOn = false
    @State private var screenCaptured = false
    @State private var typedCode = ""
    @FocusState private var codeFocused: Bool
    // Voice
    @State private var dictation = PearDictation()
    @State private var recorder = PearRecorder()
    @State private var holding = false
    @State private var recordingFallback = false
    @State private var voiceLanguage = "en"
    @State private var question = ""
    @State private var answer: AskResponse?
    @State private var micMessage: String?
    // Type
    @State private var query = ""
    @State private var searching = false
    @State private var hits: [SearchHit] = []
    @State private var searchMessage: String?
    @State private var pasting = false
    @State private var pasted = ""
    @FocusState private var queryFocused: Bool
    @FocusState private var pasteFocused: Bool

    private enum CameraState { case checking, live, off, unsupported }

    init(initialMode: Mode = .barcode) {
        _mode = State(initialValue: initialMode)
    }

    private var flow: PearScanFlow { pear.flow }

    private var cameraIsShown: Bool { (mode == .barcode || mode == .label) && cameraState == .live }

    private var failure: (message: String, next: String)? {
        if case .failed(let message, let next) = flow.phase { return (message, next) }
        return nil
    }

    private var loadingText: String? {
        if case .loading(let text) = flow.phase { return text }
        return nil
    }

    private var highlightShutter: Bool { snapHint || failure?.next == "take_photo" }

    var body: some View {
        ZStack {
            background

            VStack(spacing: PearMetrics.Spacing.md) {
                topBar
                Spacer(minLength: 0)
                center
                Spacer(minLength: 0)
                if let failure, mode != .voice { failureCard(failure) }
                if cameraIsShown, screenCaptured { mirroringHint }
                modePicker
                bottomRow
            }
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.bottom, PearMetrics.Spacing.xs)

            if let loadingText {
                Color.black.opacity(0.55).ignoresSafeArea()
                ScanLoadingCard(kind: loadingText.lowercased().contains("label") ? .photo : .barcode,
                                peopleCount: pear.checkedCount)
                    .transition(.scale(scale: 0.95).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: loadingText)
        .sheet(isPresented: $showResult, onDismiss: { flow.reset() }) {
            ResultSheetView(
                flow: flow,
                onSnapIngredients: {
                    showResult = false
                    mode = .label
                    snapHint = true
                },
                onTypeItIn: {
                    showResult = false
                    mode = .type
                },
                onScanAnother: { showResult = false }
            )
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
            .presentationBackground(Color.cream)
        }
        .sheet(isPresented: $showHistory) {
            HistorySheet { result in flow.show(result) }
                .presentationDetents([.medium, .large])
        }
        .onChange(of: flow.phase) { _, phase in
            if phase == .result {
                if let result = flow.result { pear.history.add(result) }
                showResult = true
            }
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            photoItem = nil
            Task { await scanPicked(item) }
        }
        .onChange(of: mode) { old, new in
            if old == .voice { dictation.cancel(); _ = recorder.stop(); holding = false }
            if new == .type, !pasting { queryFocused = true }
            if new != .barcode { showDemo = false }
            if !(new == .barcode || new == .label) { setTorch(false) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                flow.warm()
                camera.startIfNeeded()
            }
        }
        .task {
            flow.warm()
            voiceLanguage = pear.displayLanguage
            showResult = flow.phase == .result
            await prepareCamera()
        }
        .task {
            // Cheap check every 2 s: iPhone Mirroring blacks out the camera and can start any time.
            while !Task.isCancelled {
                refreshCaptureState()
                try? await Task.sleep(for: .seconds(2))
            }
        }
        .onDisappear {
            setTorch(false)
            dictation.cancel()
            PearVoice.shared.stop()
        }
    }

    // MARK: Background

    @ViewBuilder
    private var background: some View {
        if cameraIsShown {
            PearScannerView(
                controller: camera,
                // Barcodes only fire in Barcode mode, and not while a result or the demo list is up.
                isActive: mode == .barcode && !showDemo && (flow.phase == .idle || failure != nil),
                onBarcode: { code in
                    snapHint = false
                    Task { await flow.scanBarcode(code) }
                },
                onUnavailable: { cameraState = .off }
            )
            .ignoresSafeArea()
        } else {
            ZStack {
                Color(hex: 0x1F1B17)
                RadialGradient(colors: [Color.avocado700.opacity(0.35), .clear], center: .topLeading,
                               startRadius: 0, endRadius: 520)
                RadialGradient(colors: [Color.avocado700.opacity(0.25), .clear], center: .bottomTrailing,
                               startRadius: 0, endRadius: 480)
            }
            .ignoresSafeArea()
        }
    }

    // MARK: Top bar

    private var topBar: some View {
        HStack {
            darkCircle("xmark", label: "Close") {
                flow.reset()
                dismiss()
            }
            Spacer()
            forMenu
            Spacer()
            if cameraIsShown {
                darkCircle(torchOn ? "flashlight.on.fill" : "flashlight.off.fill", label: torchOn ? "Flashlight off" : "Flashlight") {
                    setTorch(!torchOn)
                }
            } else {
                Color.clear.frame(width: 52, height: 52)
            }
        }
        .padding(.top, PearMetrics.Spacing.xs)
    }

    /// "For everyone ▾": which family members this check is for.
    private var forMenu: some View {
        Menu {
            Button {
                pear.checkEveryone()
            } label: {
                if pear.forMemberIDs == nil { Label("Everyone", systemImage: "checkmark") } else { Text("Everyone") }
            }
            Section("Who's this for?") {
                ForEach(pear.household.members) { member in
                    Button {
                        pear.toggleFor(member.id)
                    } label: {
                        if pear.forMemberIDs != nil, pear.isChecked(member.id) {
                            Label(member.name, systemImage: "checkmark")
                        } else {
                            Text(verbatim: member.name)
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: PearMetrics.Spacing.xs) {
                Image(systemName: "person.3")
                Text(verbatim: pear.forLabel)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Image(systemName: "chevron.down").font(.caption.weight(.bold))
            }
            .font(PearFont.headline)
            .foregroundStyle(.white)
            .padding(.horizontal, PearMetrics.Spacing.md)
            .frame(minHeight: 52)
            .background(Capsule().fill(Color.black.opacity(0.4)))
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.2), lineWidth: 1))
            .contentShape(Capsule())
        }
        .accessibilityLabel("Who's this for: \(pear.forLabel)")
    }

    // MARK: Center (per mode)

    @ViewBuilder
    private var center: some View {
        switch mode {
        case .barcode:
            if !cameraIsShown || showDemo {
                demoPanel
            } else {
                VStack(spacing: PearMetrics.Spacing.md) {
                    scanFrame(height: 210, showsLine: true)
                    darkPill(snapHint ? "Snap the ingredients" : "Point at the barcode", icon: "barcode.viewfinder")
                    Button("Try a demo product") { showDemo = true }
                        .font(PearFont.subheadline)
                        .foregroundStyle(.white.opacity(0.85))
                        .frame(minHeight: PearMetrics.minTapTarget)
                }
            }
        case .label:
            if cameraIsShown {
                VStack(spacing: PearMetrics.Spacing.md) {
                    scanFrame(height: 330, showsLine: false)
                    darkPill(snapHint ? "Snap the ingredients" : "Fit the ingredients in the frame", icon: "text.viewfinder")
                }
            } else {
                noCameraLabelPanel
            }
        case .voice:
            voiceCenter
        case .type:
            typeCard
        }
    }

    private func scanFrame(height: CGFloat, showsLine: Bool) -> some View {
        RoundedRectangle(cornerRadius: 32, style: .continuous)
            .strokeBorder(highlightShutter ? Color.honey : Color.white, lineWidth: 4)
            .frame(maxWidth: .infinity)
            .frame(height: height)
            .overlay {
                if showsLine {
                    Rectangle().fill(.avocado500).frame(height: 3).padding(.horizontal, 12)
                }
            }
            .padding(.horizontal, PearMetrics.Spacing.xs)
            .accessibilityHidden(true)
    }

    private var demoPanel: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
            Text(cameraState == .live ? "Demo products" : "No camera here")
                .font(PearFont.title)
                .foregroundStyle(.white)
            Text(cameraState == .off
                 ? "Camera is off. Turn it on in Settings, or try a demo product."
                 : "Pick a demo product, or type any barcode.")
                .font(PearFont.body)
                .foregroundStyle(.white.opacity(0.75))
                .fixedSize(horizontal: false, vertical: true)
            if cameraState == .off, let url = URL(string: UIApplication.openSettingsURLString) {
                Link(destination: url) {
                    Label("Open Settings", systemImage: "gear").font(PearFont.headline).foregroundStyle(.avocado200)
                }
            }
            ForEach(PearDemo.barcodes, id: \.code) { item in
                Button {
                    snapHint = false
                    Task { await flow.scanBarcode(item.code) }
                } label: {
                    HStack(spacing: PearMetrics.Spacing.sm) {
                        Image(systemName: "barcode.viewfinder")
                            .font(.system(size: 22, weight: .medium))
                            .foregroundStyle(.avocado200)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(verbatim: item.note).font(PearFont.headline).foregroundStyle(.white)
                                .multilineTextAlignment(.leading)
                            Text(verbatim: item.code).font(PearFont.caption).foregroundStyle(.white.opacity(0.6))
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(PearMetrics.Spacing.md)
                    .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous)
                        .fill(Color.white.opacity(0.1)))
                }
                .buttonStyle(PressableStyle())
                .disabled(loadingText != nil)
            }
            HStack(spacing: PearMetrics.Spacing.xs) {
                TextField("Barcode", text: $typedCode, prompt: Text("Type a barcode").foregroundStyle(.white.opacity(0.5)))
                    .font(PearFont.body)
                    .foregroundStyle(.white)
                    .keyboardType(.numberPad)
                    .focused($codeFocused)
                    .padding(.horizontal, PearMetrics.Spacing.md)
                    .frame(minHeight: 50)
                    .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                        .fill(Color.white.opacity(0.1)))
                Button {
                    let code = typedCode
                    codeFocused = false
                    Task { await flow.scanBarcode(code) }
                } label: {
                    Label("Check", systemImage: "magnifyingglass")
                        .font(PearFont.headline)
                        .foregroundStyle(.cocoa)
                        .padding(.horizontal, PearMetrics.Spacing.md)
                        .frame(minHeight: 50)
                        .background(Capsule().fill(Color.cream))
                }
                .buttonStyle(PressableStyle())
                .disabled(!(8...14).contains(typedCode.filter(\.isNumber).count) || loadingText != nil)
            }
            if cameraState == .live {
                Button("Back to the camera") { showDemo = false }
                    .font(PearFont.subheadline)
                    .foregroundStyle(.avocado200)
                    .frame(minHeight: PearMetrics.minTapTarget)
            }
        }
        .padding(PearMetrics.Spacing.xs)
    }

    private var noCameraLabelPanel: some View {
        VStack(spacing: PearMetrics.Spacing.md) {
            Image(systemName: "text.viewfinder").font(.system(size: 44)).foregroundStyle(.avocado200)
            Text("No camera here. Pick a photo of the ingredient panel instead.")
                .font(PearFont.body)
                .foregroundStyle(.white.opacity(0.8))
                .multilineTextAlignment(.center)
            PhotosPicker(selection: $photoItem, matching: .images) {
                Label("Pick a label photo", systemImage: "photo.on.rectangle")
                    .font(PearFont.headline)
                    .foregroundStyle(.cocoa)
                    .padding(.horizontal, PearMetrics.Spacing.lg)
                    .frame(minHeight: PearMetrics.buttonHeight)
                    .background(Capsule().fill(Color.cream))
                    .overlay { if highlightShutter { Capsule().strokeBorder(.honey, lineWidth: 4) } }
            }
        }
        .padding(.horizontal, PearMetrics.Spacing.lg)
    }

    // MARK: Voice (c-13)

    private var voiceCenter: some View {
        VStack(spacing: PearMetrics.Spacing.md) {
            ZStack {
                Circle().fill(Color.avocado500.opacity(0.12)).frame(width: 250, height: 250)
                Circle().fill(Color.avocado500.opacity(0.25)).frame(width: 190, height: 190)
                Circle().fill(Color.avocado500).frame(width: 130, height: 130)
                Image(systemName: "waveform").font(.system(size: 34, weight: .semibold)).foregroundStyle(.white)
            }
            .scaleEffect(holding ? 1.06 : (answer == nil ? 1 : 0.7))
            .animation(holding ? .easeInOut(duration: 0.7).repeatForever(autoreverses: true) : .easeOut(duration: 0.3), value: holding)
            .frame(height: answer == nil ? 250 : 180)
            .accessibilityHidden(true)

            Text(verbatim: holding || answer != nil ? "Heard in \(Self.languageName(answer?.language ?? voiceLanguage))" : "Ask in \(Self.languageName(voiceLanguage))")
                .font(PearFont.subheadline)
                .foregroundStyle(.avocado200)

            Text(verbatim: questionText)
                .font(PearFont.title)
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if flow.asking {
                HStack(spacing: PearMetrics.Spacing.xs) {
                    ProgressView().tint(.white)
                    Text("Pear is thinking…").font(PearFont.body).foregroundStyle(.white.opacity(0.85))
                }
            } else if let answer {
                Button {
                    PearVoice.shared.speak(answer.answer.text, language: answer.answer.language)
                } label: {
                    HStack(alignment: .top, spacing: PearMetrics.Spacing.sm) {
                        Image(systemName: "speaker.wave.2.fill").foregroundStyle(.avocado700)
                        Text(verbatim: answer.answer.text)
                            .font(PearFont.headline)
                            .foregroundStyle(.cocoa)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(PearMetrics.Spacing.md)
                    .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(Color.cream))
                }
                .buttonStyle(PressableStyle())
                .accessibilityHint("Plays the answer again")
            }

            HStack(spacing: PearMetrics.Spacing.xs) {
                ForEach(PearLanguage.all) { language in
                    Button {
                        voiceLanguage = language.code
                    } label: {
                        Text(verbatim: language.code.uppercased())
                            .font(PearFont.headline)
                            .foregroundStyle(voiceLanguage == language.code ? .cocoa : .white)
                            .frame(width: 56, height: 40)
                            .background(Capsule().fill(voiceLanguage == language.code ? Color.cream : Color.white.opacity(0.12)))
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityLabel(language.endonym)
                }
            }

            darkPill(holding ? "Listening, let go to send" : (micMessage ?? flow.askError ?? "Hold the mic and ask"),
                     icon: "waveform")
        }
    }

    private var questionText: String {
        if holding { return dictation.transcript.isEmpty ? "Listening…" : dictation.transcript }
        if !question.isEmpty { return question }
        return "“Can Bà eat this?”"
    }

    private static func languageName(_ code: String) -> String {
        switch code.prefix(2) {
        case "vi": "Vietnamese"
        case "es": "Spanish"
        default: "English"
        }
    }

    private func startListening() {
        PearVoice.shared.stop()
        micMessage = nil
        answer = nil
        let language = voiceLanguage
        Task {
            guard await PearDictation.requestPermissions() else {
                micMessage = "Pear needs the microphone and speech recognition. Turn them on in Settings, or type instead."
                return
            }
            guard holding else { return }
            do {
                guard PearDictation.supports(language) else { throw PearDictation.DictationError.unavailable }
                try dictation.start(language: language)
            } catch {
                dictation.cancel()
                recordingFallback = (try? recorder.start()) != nil
            }
        }
    }

    private func stopListening() {
        let language = voiceLanguage
        Task {
            if recordingFallback {
                recordingFallback = false
                guard let audio = recorder.stop() else { return }
                question = "…"
                handle(await flow.askAudio(audio, language: language))
            } else {
                let text = await dictation.stop()
                guard !text.isEmpty else { return }
                question = text
                handle(await flow.ask(text, language: language))
            }
        }
    }

    private func handle(_ response: AskResponse?) {
        guard let response else { return }
        answer = response
        if !response.transcript.isEmpty, question == "…" { question = response.transcript }
        PearVoice.shared.speak(response.answer.text, language: response.answer.language)
        if response.next == "scan_needed" {
            Task {
                try? await Task.sleep(for: .seconds(1.6))
                mode = .barcode
            }
        }
    }

    // MARK: Type (c-14)

    private var typeCard: some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.md) {
            if pasting {
                Text("Paste the ingredients")
                    .font(PearFont.title)
                    .foregroundStyle(.cocoa)
                TextEditor(text: $pasted)
                    .font(PearFont.body)
                    .foregroundStyle(.cocoa)
                    .scrollContentBackground(.hidden)
                    .focused($pasteFocused)
                    .frame(minHeight: 150, maxHeight: 220)
                    .padding(PearMetrics.Spacing.xs)
                    .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous).fill(.white))
                    .overlay {
                        RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                            .strokeBorder(.avocado500, lineWidth: 2)
                    }
                Text("Pear reads the list like a label photo and checks it for everyone.")
                    .font(PearFont.caption)
                    .foregroundStyle(.latte)
                softButton("Search by name instead", icon: "magnifyingglass") { pasting = false }
            } else {
                Text("Type a food or brand")
                    .font(PearFont.title)
                    .foregroundStyle(.cocoa)
                HStack(spacing: PearMetrics.Spacing.sm) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.avocado700)
                    TextField("Food or brand", text: $query, prompt: Text("granola").foregroundStyle(.latte))
                        .font(PearFont.body)
                        .foregroundStyle(.cocoa)
                        .submitLabel(.search)
                        .focused($queryFocused)
                        .onSubmit(runSearch)
                }
                .padding(.horizontal, PearMetrics.Spacing.md)
                .frame(minHeight: 56)
                .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous).fill(.white))
                .overlay {
                    RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous)
                        .strokeBorder(.avocado500, lineWidth: 2)
                }

                if searching, hits.isEmpty {
                    HStack(spacing: PearMetrics.Spacing.xs) {
                        ProgressView().tint(.avocado700)
                        Text("Searching Open Food Facts…").font(PearFont.body).foregroundStyle(.latte)
                    }
                }
                ScrollView {
                    VStack(spacing: PearMetrics.Spacing.sm) {
                        ForEach(hits) { hit in
                            Button { open(hit) } label: { hitRow(hit) }
                                .buttonStyle(PressableStyle())
                        }
                    }
                }
                .frame(maxHeight: 280)
                if let searchMessage {
                    Text(verbatim: searchMessage).font(PearFont.body).foregroundStyle(.latte)
                        .fixedSize(horizontal: false, vertical: true)
                }
                softButton("Paste ingredients instead", icon: "list.clipboard") {
                    pasting = true
                    pasteFocused = true
                }
                .frame(maxWidth: .infinity)
            }
        }
        .padding(PearMetrics.Spacing.lg)
        .background(RoundedRectangle(cornerRadius: 32, style: .continuous).fill(Color.cream))
    }

    private func hitRow(_ hit: SearchHit) -> some View {
        let summary = Self.summary(hit, names: { pear.household.member(id: $0)?.name ?? "" })
        return HStack(spacing: PearMetrics.Spacing.md) {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(.avocado100)
                .frame(width: 56, height: 56)
                .overlay {
                    if let url = hit.product.imageURL {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                            .frame(width: 56, height: 56)
                            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    }
                }
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: hit.product.name).font(PearFont.headline).foregroundStyle(.cocoa)
                    .multilineTextAlignment(.leading).lineLimit(2)
                if !hit.product.brand.isEmpty {
                    Text(verbatim: hit.product.brand).font(PearFont.caption).foregroundStyle(.latte).lineLimit(1)
                }
            }
            Spacer(minLength: PearMetrics.Spacing.xs)
            Text(verbatim: summary.text)
                .font(PearFont.subheadline)
                .foregroundStyle(summary.color)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    /// The short verdict next to a search hit. Never "safe" (plan 6.5).
    private static func summary(_ hit: SearchHit, names: (String) -> String) -> (text: String, color: Color) {
        if hit.missing { return ("Not in the database", .latte) }
        guard let result = hit.result, !result.verdicts.isEmpty else { return ("Checking…", .latte) }
        if result.verdicts.allSatisfy({ $0.state == .ripe }) { return ("Ripe for all", .avocado700) }
        let allergic = result.verdicts.filter { $0.state == .allergy }.map { names($0.memberId) }
        if !allergic.isEmpty { return ("Not for \(ResultCopy.and(allergic, "en"))", .berry) }
        let worst = result.verdicts.min { $0.state.rank < $1.state.rank }!
        return ("\(worst.label) · \(names(worst.memberId))", worst.state.ink)
    }

    private func runSearch() {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !searching else { return }
        queryFocused = false
        searching = true
        searchMessage = nil
        hits = []
        let api = pear.api
        let household = flow.household
        Task {
            defer { searching = false }
            let found: [FoundProduct]
            do {
                found = try await ProductSearch.search(q, limit: 5)
            } catch {
                searchMessage = "Search isn't reachable right now. Try again, or paste the ingredients."
                return
            }
            hits = found.map { SearchHit(product: $0) }
            if found.isEmpty {
                searchMessage = "Nothing found for “\(q)”. Try a brand name, or paste the ingredients."
                return
            }
            // Each hit is checked by the Pear API (the server's rules decide), all at once.
            await withTaskGroup(of: (String, ScanResult?).self) { group in
                for product in found {
                    group.addTask {
                        let result = try? await api.scanBarcode(product.code, household: household)
                        return (product.code, result)
                    }
                }
                for await (code, result) in group {
                    if let index = hits.firstIndex(where: { $0.product.code == code }) {
                        hits[index].result = result
                        hits[index].missing = result == nil
                    }
                }
            }
        }
    }

    private func open(_ hit: SearchHit) {
        if let result = hit.result {
            flow.show(result)
        } else if hit.missing {
            searchMessage = "“\(hit.product.name)” isn't in the database yet. Snap the ingredients instead."
        }
    }

    private func checkPasted() {
        let text = pasted.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let jpeg = IngredientTextImage.jpeg(for: text) else { return }
        pasteFocused = false
        Task { await flow.scanPhoto(jpeg: jpeg) }
    }

    // MARK: Bottom: modes, library, shutter, history

    private var modePicker: some View {
        HStack(spacing: 2) {
            ForEach(Mode.allCases) { item in
                Button {
                    mode = item
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: item.icon)
                            .foregroundStyle(mode == item ? Color.avocado700 : .white)
                        Text(verbatim: item.title)
                            .foregroundStyle(mode == item ? Color.cocoa : .white)
                    }
                    .font(PearFont.headline)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .padding(.horizontal, PearMetrics.Spacing.sm)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .background { if mode == item { Capsule().fill(Color.cream) } }
                    .contentShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                .accessibilityAddTraits(mode == item ? .isSelected : [])
            }
        }
        .padding(4)
        .background(Capsule().fill(Color.black.opacity(0.4)))
    }

    private var bottomRow: some View {
        HStack {
            PhotosPicker(selection: $photoItem, matching: .images) {
                darkCircleFace("photo")
            }
            .accessibilityLabel("Pick a photo")
            Spacer()
            shutter
            Spacer()
            Button {
                showHistory = true
            } label: {
                darkCircleFace("clock.arrow.circlepath")
            }
            .buttonStyle(PressableStyle())
            .accessibilityLabel("History")
        }
        .padding(.horizontal, PearMetrics.Spacing.lg)
    }

    @ViewBuilder
    private var shutter: some View {
        switch mode {
        case .barcode, .label:
            Button { snap() } label: {
                shutterFace(icon: mode.icon, filled: false)
            }
            .buttonStyle(PressableStyle())
            .disabled(!cameraIsShown || showDemo || snapping || loadingText != nil)
            .opacity(cameraIsShown && !showDemo ? 1 : 0.5)
            .accessibilityLabel("Snap")
        case .voice:
            shutterFace(icon: "mic.fill", filled: true)
                .scaleEffect(holding ? 1.1 : 1)
                .animation(.snappy(duration: 0.2), value: holding)
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { _ in
                            if !holding {
                                holding = true
                                startListening()
                            }
                        }
                        .onEnded { _ in
                            holding = false
                            stopListening()
                        }
                )
                .accessibilityLabel("Hold to talk")
                .accessibilityAddTraits(.isButton)
        case .type:
            Button {
                pasting ? checkPasted() : runSearch()
            } label: {
                shutterFace(icon: pasting ? "checkmark" : "magnifyingglass", filled: true)
            }
            .buttonStyle(PressableStyle())
            .disabled(searching || loadingText != nil)
            .accessibilityLabel(pasting ? "Check these ingredients" : "Search")
        }
    }

    private func shutterFace(icon: String, filled: Bool) -> some View {
        ZStack {
            Circle().fill(Color.white.opacity(0.25)).frame(width: 96, height: 96)
            Circle().fill(filled ? Color.avocado700 : Color.avocado50).frame(width: 80, height: 80)
            Image(systemName: icon)
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(filled ? Color.cream : Color.avocado700)
        }
        .overlay {
            if highlightShutter, mode == .barcode || mode == .label {
                Circle().strokeBorder(.honey, lineWidth: 5).frame(width: 102, height: 102)
            }
        }
    }

    // MARK: Small pieces

    private func darkCircle(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
        Button { action() } label: { darkCircleFace(symbol) }
            .buttonStyle(PressableStyle())
            .accessibilityLabel(label)
    }

    private func darkCircleFace(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 22, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: 52, height: 52)
            .background(Circle().fill(Color.black.opacity(0.4)))
            .overlay(Circle().strokeBorder(Color.white.opacity(0.2), lineWidth: 1))
    }

    private func darkPill(_ text: String, icon: String) -> some View {
        Label(text, systemImage: icon)
            .font(PearFont.headline)
            .foregroundStyle(.white)
            .multilineTextAlignment(.center)
            .padding(.horizontal, PearMetrics.Spacing.lg)
            .frame(minHeight: 48)
            .background(Capsule().fill(Color.black.opacity(0.55)))
    }

    private func softButton(_ title: String, icon: String, action: @escaping () -> Void) -> some View {
        Button { action() } label: {
            Label(title, systemImage: icon)
                .font(PearFont.headline)
                .foregroundStyle(.avocado700)
                .padding(.horizontal, PearMetrics.Spacing.lg)
                .frame(minHeight: 50)
                .background(Capsule().fill(.avocado100))
        }
        .buttonStyle(PressableStyle())
    }

    private var mirroringHint: some View {
        Label("Camera dark? iPhone Mirroring turns it off. Use the phone itself, or try a demo product.",
              systemImage: "iphone.slash")
            .font(PearFont.caption)
            .foregroundStyle(.white)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, PearMetrics.Spacing.md)
            .padding(.vertical, PearMetrics.Spacing.xs)
            .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.field, style: .continuous).fill(Color.black.opacity(0.55)))
    }

    private func failureCard(_ failure: (message: String, next: String)) -> some View {
        VStack(alignment: .leading, spacing: PearMetrics.Spacing.sm) {
            Text(verbatim: failure.message)
                .font(PearFont.headline)
                .foregroundStyle(.cocoa)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: PearMetrics.Spacing.sm) {
                if failure.next == "retry" {
                    smallButton("Try again", icon: "arrow.clockwise", filled: true) { Task { await flow.retry() } }
                }
                if failure.next == "take_photo" {
                    smallButton("Snap the ingredients", icon: "camera", filled: true) {
                        flow.reset()
                        mode = .label
                        snapHint = true
                    }
                }
                smallButton("OK", icon: "checkmark", filled: false) {
                    flow.reset()
                    snapHint = failure.next == "take_photo"
                }
            }
        }
        .padding(PearMetrics.Spacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: PearMetrics.Radius.card, style: .continuous).fill(Color.cream))
    }

    private func smallButton(_ title: String, icon: String, filled: Bool, action: @escaping () -> Void) -> some View {
        Button { action() } label: {
            Label(title, systemImage: icon)
                .font(PearFont.headline)
                .foregroundStyle(filled ? Color.cream : Color.avocado700)
                .padding(.horizontal, PearMetrics.Spacing.md)
                .frame(minHeight: PearMetrics.minTapTarget)
                .background {
                    if filled { Capsule().fill(.avocado700) } else { Capsule().strokeBorder(.avocado700, lineWidth: 1.5) }
                }
        }
        .buttonStyle(PressableStyle())
    }

    // MARK: Camera plumbing

    private func prepareCamera() async {
        guard DataScannerViewController.isSupported else {
            cameraState = .unsupported
            return
        }
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        cameraState = PearScannerView.isAvailable ? .live : .off
    }

    private func snap() {
        guard cameraIsShown, !snapping else { return }
        snapping = true
        snapHint = false
        Task {
            defer { snapping = false }
            guard let photo = try? await camera.capturePhoto(),
                  let jpeg = PearImage.jpegForUpload(photo) else { return }
            await flow.scanPhoto(jpeg: jpeg)
        }
    }

    private func scanPicked(_ item: PhotosPickerItem) async {
        snapHint = false
        guard let data = try? await item.loadTransferable(type: Data.self),
              let image = UIImage(data: data),
              let jpeg = PearImage.jpegForUpload(image) else { return }
        await flow.scanPhoto(jpeg: jpeg)
    }

    private func setTorch(_ on: Bool) {
        guard on != torchOn || !on else { return }
        guard let device = AVCaptureDevice.default(for: .video), device.hasTorch else { return }
        do {
            try device.lockForConfiguration()
            device.torchMode = on ? .on : .off
            device.unlockForConfiguration()
            torchOn = on
        } catch {
            torchOn = false
        }
    }

    private func refreshCaptureState() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let captured = scenes.contains { $0.traitCollection.sceneCaptureState == .active }
        if captured != screenCaptured { screenCaptured = captured }
    }
}

/// A pasted ingredient list drawn as a plain white "label" so the server's label reader can read
/// it like a photo (the Pear API has no text-only check endpoint).
enum IngredientTextImage {
    static func jpeg(for text: String) -> Data? {
        let width: CGFloat = 1200
        let inset: CGFloat = 60
        let body = "INGREDIENTS: " + text
        let attributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: 40, weight: .regular),
            .foregroundColor: UIColor.black,
        ]
        let bounds = (body as NSString).boundingRect(with: CGSize(width: width - inset * 2, height: .greatestFiniteMagnitude),
                                                     options: [.usesLineFragmentOrigin], attributes: attributes, context: nil)
        let size = CGSize(width: width, height: min(4000, ceil(bounds.height) + inset * 2))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            (body as NSString).draw(with: CGRect(x: inset, y: inset, width: width - inset * 2, height: size.height - inset * 2),
                                    options: [.usesLineFragmentOrigin], attributes: attributes, context: nil)
        }
        return image.jpegData(compressionQuality: 0.8)
    }
}

#Preview("Scan (Simulator)") {
    ScanView()
        .environment(PearEnvironment.preview())
}

#Preview("Type") {
    ScanView(initialMode: .type)
        .environment(PearEnvironment.preview())
}

#Preview("Voice") {
    ScanView(initialMode: .voice)
        .environment(PearEnvironment.preview())
}
