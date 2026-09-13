import SwiftUI
import UIKit

struct AuthenticatedFrame: View {
    @EnvironmentObject private var model: AppModel
    let path: String
    @State private var image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Text(model.words("Kein Bildschirmbild verfügbar", "No screen image available")).font(.caption).foregroundStyle(Palette.secondary).frame(maxWidth: .infinity, maxHeight: .infinity) }
        }.task(id: path) {
            image = nil
            if let data = try? await model.api.data(path), !Task.isCancelled { image = UIImage(data: data) }
        }
    }
}

@MainActor
final class TakeoverSession: ObservableObject {
    @Published var image: UIImage?
    @Published var frameUnavailable = false
    @Published var error: String?
    @Published var pending = 0
    @Published var resumed = false
    @Published var frameVersion = 0
    private var lastFrame: Data?
    private var fetching = false
    private var queue: Task<Void, Never>?
    private var stopped = false
    private var inputGeneration = UUID()

    func poll(api: API, jobID: String) async {
        stopped = false
        while !Task.isCancelled && !stopped {
            let start = ContinuousClock.now
            await refresh(api: api, path: "/jobs/\(jobID)/frame.jpg?t=\(Date().timeIntervalSince1970)")
            let remaining = Duration.milliseconds(500) - start.duration(to: .now)
            if remaining > .zero { do { try await Task.sleep(for: remaining) } catch { return } }
        }
    }
    func refresh(api: API, path: String) async {
        guard !fetching, !stopped else { return }
        fetching = true
        defer { fetching = false }
        do {
            let data = try await api.data(path)
            try Task.checkCancellation()
            guard !stopped else { return }
            guard let image = UIImage(data: data) else { throw ClientError.image }
            if data != lastFrame { self.image = image; lastFrame = data; frameVersion += 1 }
            frameUnavailable = false
        } catch { if !Task.isCancelled && !stopped { frameUnavailable = true } }
    }
    func input(api: API, jobID: String, body: [String: Any], language: String) {
        enqueue(api: api, path: "/jobs/\(jobID)/input", body: body, language: language, resume: false)
    }
    func resume(api: API, jobID: String, language: String) {
        enqueue(api: api, path: "/jobs/\(jobID)/resume", body: [:], language: language, resume: true)
    }
    private func enqueue(api: API, path: String, body: [String: Any], language: String, resume: Bool) {
        let previous = queue
        let generation = inputGeneration
        pending += 1
        queue = Task { [weak self] in
            await previous?.value
            guard let self else { return }
            defer { self.pending -= 1 }
            guard !Task.isCancelled, !self.stopped, generation == self.inputGeneration else { return }
            do {
                _ = try await api.post(path, body)
                if resume { self.resumed = true }
            } catch {
                self.inputGeneration = UUID()
                self.error = copy(language, "Die Eingabe ist nicht angekommen. Versuch es erneut.", "The input didn't arrive. Please try again.")
            }
        }
    }
    func close() { stopped = true; queue?.cancel(); queue = nil }
}

struct TakeoverView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var session = TakeoverSession()
    @State private var keyboard = false
    @State private var typed = ""
    @FocusState private var typing: Bool
    let job: JobCard
    private var currentJob: JobCard {
        model.state.jobs.first(where: { $0.id == job.id }) ?? model.overview.first(where: { $0.id == job.id }) ?? job
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button { dismiss() } label: { Image(systemName: "chevron.left").frame(width: 44, height: 44) }
                    .accessibilityLabel(model.words("Zurück zum Chat", "Back to chat"))
                Text(currentJob.title).font(.headline).lineLimit(1).frame(maxWidth: .infinity)
                Button { keyboard.toggle(); typing = keyboard } label: { Image(systemName: "keyboard").frame(width: 44, height: 44) }
                    .accessibilityLabel(model.words("Tastatur", "Keyboard"))
            }.padding(.horizontal, 8)
            HStack {
                Button(model.words("Fertig, mach weiter", "Done, carry on")) {
                    if !typed.isEmpty { sendTyped() }
                    session.resume(api: model.api, jobID: job.id, language: model.language)
                }.buttonStyle(.borderedProminent).disabled(session.pending > 0)
                Spacer()
            }.padding(.horizontal, 14).frame(height: 48)
            HStack {
                SecureField(model.words("Text oder Code eingeben", "Enter text or code"), text: $typed)
                    .textContentType(.oneTimeCode).textInputAutocapitalization(.never).autocorrectionDisabled().focused($typing)
                    .onSubmit { sendTyped() }
                Button(action: sendTyped) { Image(systemName: "arrow.up").frame(width: 44, height: 44) }.disabled(typed.isEmpty)
                    .accessibilityLabel(model.words("Text eingeben", "Type text"))
                Button { input(["kind": "key", "key": "Backspace"]) } label: { Image(systemName: "delete.left").frame(width: 44, height: 44) }
                    .accessibilityLabel(model.words("Zeichen löschen", "Delete character"))
                Button { input(["kind": "key", "key": "Enter"]) } label: { Image(systemName: "return").frame(width: 44, height: 44) }
                    .accessibilityLabel(model.words("Eingabetaste", "Enter key"))
            }.padding(.horizontal, 14).frame(height: 52).opacity(keyboard ? 1 : 0)
                .allowsHitTesting(keyboard).accessibilityHidden(!keyboard)
            GeometryReader { geometry in
                ZStack {
                    Color.white
                    if let image = session.image {
                        Image(uiImage: image).resizable().interpolation(.high).scaledToFit()
                            .frame(width: geometry.size.width, height: geometry.size.height)
                            .id(session.frameVersion).transition(.opacity)
                    } else { Text(model.words("Bildschirm wird geladen …", "Loading screen …")).foregroundStyle(Palette.secondary) }
                    if session.frameUnavailable {
                        VStack { Text(model.words("Bildschirm gerade nicht erreichbar", "Screen currently unavailable")).font(.caption).padding(8).background(.regularMaterial); Spacer() }
                    }
                }
                .animation(reduceMotion ? nil : Palette.frameFade, value: session.frameVersion)
                .contentShape(Rectangle())
                .gesture(SpatialTapGesture().onEnded { value in
                    guard session.image != nil, !session.frameUnavailable,
                          let point = FrameGeometry.point(x: value.location.x, y: value.location.y,
                                                          width: geometry.size.width, height: geometry.size.height) else { return }
                    input(["kind": "tap", "x": point.0, "y": point.1])
                })
                .simultaneousGesture(DragGesture(minimumDistance: 20).onEnded { value in
                    guard session.image != nil, !session.frameUnavailable else { return }
                    let scale = min(geometry.size.width / 390, geometry.size.height / 844)
                    input(["kind": "scroll", "dy": -value.translation.height / max(scale, 0.01)])
                })
                .accessibilityLabel(model.words("Live-Bildschirm. Tippen oder wischen, um die Seite zu bedienen.", "Live screen. Tap or swipe to control the page."))
                .accessibilityAction(named: Text(model.words("Nach unten scrollen", "Scroll down"))) { input(["kind": "scroll", "dy": 500]) }
                .accessibilityAction(named: Text(model.words("Nach oben scrollen", "Scroll up"))) { input(["kind": "scroll", "dy": -500]) }
            }
            Text(currentJob.state.label(model.language)).font(.caption).foregroundStyle(Palette.secondary)
                .frame(maxWidth: .infinity).frame(height: 28)
        }
        .background(Palette.ground).tint(Palette.ink)
        .overlay(alignment: .bottom) { if let error = session.error { Text(error).font(.footnote).padding().background(.regularMaterial) } }
        // The remote image rectangle stays fixed when the keyboard opens.
        .ignoresSafeArea(.keyboard)
        .task(id: phase) { if phase == .active { await session.poll(api: model.api, jobID: job.id) } }
        .task(id: currentJob.frameURL) {
            if phase == .active, let path = currentJob.frameURL { await session.refresh(api: model.api, path: path) }
        }
        .onChange(of: session.resumed) { _, resumed in if resumed { Task { await model.refreshJobs() }; dismiss() } }
        .onDisappear { typed = ""; session.close() }
    }
    private func input(_ body: [String: Any]) { session.input(api: model.api, jobID: job.id, body: body, language: model.language) }
    private func sendTyped() {
        guard !typed.isEmpty else { return }
        input(["kind": "type", "text": typed]); typed = ""
    }
}
