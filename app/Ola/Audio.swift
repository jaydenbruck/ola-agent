import AVFoundation
import Speech
import NaturalLanguage
import SwiftUI

@MainActor
final class ReplyVoice {
    private let synthesizer = AVSpeechSynthesizer()
    private var player: AVAudioPlayer?
    private var waiting: [(String, String, API)] = []
    private var worker: Task<Void, Never>?
    private var generation = UUID()
    var recording = false { didSet { if !recording { drain() } } }

    func speak(_ text: String, fallback: String, api: API) {
        let plain = SpeechText.plain(text)
        guard !plain.isEmpty else { return }
        let recognizer = NLLanguageRecognizer(); recognizer.processString(plain)
        waiting.append((plain, recognizer.dominantLanguage?.rawValue ?? fallback, api))
        drain()
    }
    private func activate() throws {
        try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try AVAudioSession.sharedInstance().setActive(true)
    }
    private func drain() {
        guard !recording, worker == nil, !waiting.isEmpty else { return }
        let epoch = generation
        worker = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.generation == epoch {
                    self.worker = nil; self.player = nil
                    if !self.recording { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
                }
            }
            while !self.waiting.isEmpty && !self.recording && !Task.isCancelled {
                let (text, language, api) = self.waiting.removeFirst()
                do {
                    let body = try JSONSerialization.data(withJSONObject: ["text": text, "language": language])
                    let (data, response) = try await URLSession.shared.data(for: api.request("/speak", method: "POST", body: body))
                    guard !Task.isCancelled, self.generation == epoch else { return }
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw ClientError.response((response as? HTTPURLResponse)?.statusCode ?? 0) }
                    let audio = try AVAudioPlayer(data: data)
                    try self.activate(); self.player = audio
                    guard audio.play() else { throw ClientError.rejected }
                    while audio.isPlaying { try await Task.sleep(for: .milliseconds(100)) }
                } catch {
                    guard !Task.isCancelled, self.generation == epoch else { return }
                    self.player?.stop(); self.player = nil
                    let utterance = AVSpeechUtterance(string: text)
                    utterance.voice = AVSpeechSynthesisVoice(language: language)
                    try? self.activate(); self.synthesizer.speak(utterance)
                    do {
                        try await Task.sleep(for: .milliseconds(100))
                        while self.synthesizer.isSpeaking { try await Task.sleep(for: .milliseconds(100)) }
                    } catch { return }
                }
            }
        }
    }
    func stop() {
        generation = UUID(); worker?.cancel(); worker = nil; waiting = []
        player?.stop(); player = nil; synthesizer.stopSpeaking(at: .immediate)
        if !recording { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    }
}

@MainActor
final class Dictation: ObservableObject {
    @Published var active = false
    @Published var starting = false
    @Published var finishing = false
    @Published var transcript = ""
    @Published var levels: [CGFloat] = Array(repeating: 0, count: 30)
    @Published var error: String?
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?
    private var finishTimeout: Task<Void, Never>?
    private var tapInstalled = false
    private var generation = UUID()
    private var completion: ((String) -> Void)?
    private var interruption: NSObjectProtocol?
    var busy: Bool { active || starting || finishing }

    init() {
        interruption = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.active else { return }
                self.finish(self.completion ?? { _ in })
            }
        }
    }
    deinit { if let interruption { NotificationCenter.default.removeObserver(interruption) } }

    func start(language: String, onDraft: @escaping (String) -> Void) async {
        guard !busy else { return }
        cancel(); starting = true; error = nil; transcript = ""
        completion = onDraft
        levels = Array(repeating: 0, count: 30)
        let session = generation
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0 == .authorized) }
        }
        guard generation == session else { return }
        let microphone = await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard generation == session else { return }
        guard speech && microphone else {
            starting = false
            error = copy(language, "Erlaube Mikrofon und Spracherkennung in den iPhone-Einstellungen.", "Allow microphone and speech recognition in iPhone Settings.")
            return
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language == "de" ? "de-DE" : "en-US")),
              recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
            starting = false
            error = copy(language, "Spracherkennung auf diesem Gerät ist für diese Sprache nicht verfügbar.", "On-device dictation isn't available for this language on this device.")
            return
        }
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.record, mode: .measurement, options: [.duckOthers])
            try audio.setActive(true)
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.requiresOnDeviceRecognition = true
            request.shouldReportPartialResults = true
            self.request = request
            recognition = recognizer.recognitionTask(with: request) { [weak self] result, failure in
                let text = result?.bestTranscription.formattedString
                let done = result?.isFinal == true
                Task { @MainActor in
                    guard let self, self.generation == session else { return }
                    if let text { self.transcript = text }
                    if done || failure != nil {
                        if failure != nil && self.transcript.isEmpty {
                            self.error = copy(language, "Keine Wörter erkannt. Versuch es noch einmal.", "No words recognized. Please try again.")
                        }
                        self.settle()
                    }
                }
            }
            let node = engine.inputNode
            let format = node.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else { throw ClientError.configuration }
            node.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                request.append(buffer)
                guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return }
                let count = Int(buffer.frameLength)
                var sum: Float = 0
                for i in 0..<count { sum += samples[i] * samples[i] }
                let level = CGFloat(min(1, sqrt(sum / Float(count)) * 8))
                Task { @MainActor in
                    guard let self, self.generation == session, self.active else { return }
                    self.levels.append(level); self.levels.removeFirst()
                }
            }
            tapInstalled = true; engine.prepare(); try engine.start()
            starting = false; active = true
        } catch {
            cancel()
            self.error = copy(language, "Das Mikrofon konnte nicht gestartet werden.", "The microphone couldn't start.")
        }
    }

    func finish(_ completion: @escaping (String) -> Void) {
        guard active else { completion(transcript); return }
        self.completion = completion
        active = false; finishing = true
        stopCapture(); request?.endAudio()
        finishTimeout = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            self?.settle()
        }
    }
    private func settle() {
        let callback = completion, text = transcript
        completion = nil
        cancel()
        callback?(text)
    }
    private func stopCapture() {
        engine.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
    func cancel() {
        generation = UUID(); finishTimeout?.cancel(); finishTimeout = nil
        stopCapture(); recognition?.cancel(); recognition = nil; request = nil
        completion = nil; active = false; starting = false; finishing = false
    }
}
