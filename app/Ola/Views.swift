import SwiftUI
import PhotosUI
import UIKit

enum Palette {
    static let ground = Color(red: 246 / 255, green: 246 / 255, blue: 248 / 255)
    static let ink = Color(red: 20 / 255, green: 21 / 255, blue: 25 / 255)
    static let secondary = Color(red: 90 / 255, green: 94 / 255, blue: 102 / 255)
    static let edge = Color.black.opacity(0.08)
    static let frameFade = Animation.easeInOut(duration: 0.18)
}

struct OlaMark: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var phase
    @State private var blink = false
    var body: some View {
        ZStack {
            Circle().fill(Palette.ink)
            HStack(spacing: 7) {
                Capsule().frame(width: 4, height: blink ? 1 : 5)
                Capsule().frame(width: 4, height: blink ? 1 : 5)
            }.foregroundStyle(.white)
        }.frame(width: 34, height: 34).accessibilityLabel("Ola").accessibilityIdentifier("ola-mark")
            .task(id: phase) {
                blink = false
                guard phase == .active, !reduceMotion else { return }
                while !Task.isCancelled {
                    do {
                        try await Task.sleep(for: .seconds(6))
                        blink = true
                        try await Task.sleep(for: .milliseconds(140))
                        blink = false
                    } catch { blink = false; return }
                }
            }
    }
}

struct ChatView: View {
    @EnvironmentObject private var model: AppModel
    @State private var settings = false
    @State private var takeover: JobCard?
    @State private var followBottom = true
    @State private var smokeSent = false
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !model.running.isEmpty { jobStrip }
                if !model.connected {
                    Button { settings = true } label: {
                        Text(model.connection.configured ? model.words("Verbindung wird hergestellt …", "Connecting …") : model.words("Verbinde Ola in den Einstellungen.", "Connect Ola in Settings."))
                            .font(.footnote).foregroundStyle(Palette.secondary).padding(10)
                    }.buttonStyle(.plain)
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 24) {
                            if model.state.items.isEmpty {
                                Text(model.words("Was hast du vor?", "What's on your mind?"))
                                    .font(.system(size: 28, weight: .medium)).padding(.top, 80).frame(maxWidth: .infinity)
                                    .accessibilityIdentifier("empty-thread")
                            }
                            ForEach(model.state.items) { item in
                                switch item {
                                case .message(let id):
                                    if let message = model.state.messages.first(where: { $0.id == id }) { MessageRow(message: message) }
                                case .job(let id):
                                    if let job = model.state.jobs.first(where: { $0.id == id }) {
                                        JobCardView(job: job) { takeover = job }
                                    }
                                }
                            }
                            Color.clear.frame(height: 1).id("bottom")
                                .onAppear { followBottom = true }.onDisappear { followBottom = false }
                        }.padding(.horizontal, 20).padding(.bottom, 20)
                    }
                    .defaultScrollAnchor(.bottom)
                    .onChange(of: model.state.messages) { old, new in
                        if followBottom || new.last?.member == true && new.count != old.count { proxy.scrollTo("bottom", anchor: .bottom) }
                    }
                    .onChange(of: model.state.items.count) { _, _ in
                        if followBottom { proxy.scrollTo("bottom", anchor: .bottom) }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Palette.ground.ignoresSafeArea())
            .toolbarBackground(Palette.ground, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .safeAreaInset(edge: .bottom, spacing: 0) { Composer() }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { OlaMark() }
                ToolbarItem(placement: .principal) { Text("Ola").font(.system(size: 20, weight: .semibold)) }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { model.speaker.toggle() } label: { Image(systemName: model.speaker ? "speaker.wave.2" : "speaker.slash") }
                        .accessibilityLabel(model.words("Antworten vorlesen", "Speak replies"))
                        .accessibilityValue(model.speaker ? model.words("An", "On") : model.words("Aus", "Off"))
                    Button { settings = true } label: { Image(systemName: "slider.horizontal.3") }
                        .accessibilityLabel(model.words("Einstellungen", "Settings"))
                }
            }
            .sheet(isPresented: $settings) { SettingsView() }
            .fullScreenCover(item: $takeover) { TakeoverView(job: $0) }
            .alert(model.words("Hinweis", "Notice"), isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("OK") { model.error = nil }
            } message: { Text(model.error ?? "") }
            .onChange(of: model.connected) { _, connected in
                #if DEBUG
                if connected, !smokeSent, let prompt = ProcessInfo.processInfo.environment["OLA_SMOKE_PROMPT"], !prompt.isEmpty {
                    smokeSent = true; model.draft = prompt
                    Task { await model.send() }
                }
                #endif
            }
            .onChange(of: model.state.jobs) { _, jobs in
                #if DEBUG
                if ProcessInfo.processInfo.environment["OLA_SMOKE_TAKEOVER"] == "1", takeover == nil,
                   let job = jobs.first(where: { $0.state == .needsYou && $0.frameURL != nil }) { takeover = job }
                #endif
            }
        }.background(Palette.ground.ignoresSafeArea())
    }
    private var jobStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(model.running) { job in
                    Button { takeover = job } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.title).font(.subheadline.weight(.medium)).lineLimit(1)
                            Text(job.state.label(model.language)).font(.caption).foregroundStyle(Palette.secondary)
                        }.padding(.horizontal, 14).padding(.vertical, 10)
                            .background(.white.opacity(0.7), in: RoundedRectangle(cornerRadius: 16))
                            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Palette.edge))
                    }.buttonStyle(.plain).accessibilityHint(model.words("Bildschirm öffnen", "Open screen"))
                }
            }.padding(.horizontal, 20).padding(.vertical, 12)
        }
    }
}

struct MessageRow: View {
    @EnvironmentObject private var model: AppModel
    let message: ChatMessage
    var body: some View {
        HStack(alignment: .top) {
            if message.member { Spacer(minLength: 44) }
            VStack(alignment: .leading, spacing: 8) {
                if !message.attachmentIDs.isEmpty {
                    Label(model.words("Foto angehängt", "Photo attached"), systemImage: "photo").font(.footnote)
                }
                SelectableText(text: message.text, member: message.member)
            }
            .padding(message.member ? 14 : 0)
            .background(message.member ? Color.white.opacity(0.85) : Color.clear, in: RoundedRectangle(cornerRadius: 20))
        }.accessibilityElement(children: .contain)
    }
}

/// UIKit selection allows copying a word or passage while the reply continues to grow.
struct SelectableText: UIViewRepresentable {
    let text: String
    let member: Bool
    final class Coordinator {
        var source: String?
        var fontSize: CGFloat = 0
    }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.isEditable = false; view.isSelectable = true; view.isScrollEnabled = false
        view.backgroundColor = .clear; view.textContainerInset = .zero; view.textContainer.lineFragmentPadding = 0
        view.adjustsFontForContentSizeCategory = true
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        view.tintColor = UIColor(Palette.ink)
        return view
    }
    func updateUIView(_ view: UITextView, context: Context) {
        let pointSize = UIFont.preferredFont(forTextStyle: .body).pointSize
        guard context.coordinator.source != text || context.coordinator.fontSize != pointSize else { return }
        let selected = view.selectedRange
        let output = NSMutableAttributedString(string: "")
        let lines = text.components(separatedBy: "\n")
        for (index, raw) in lines.enumerated() {
            var line = raw
            if !member {
                if line.hasPrefix("- ") || line.hasPrefix("* ") { line = "• " + line.dropFirst(2) }
                while line.hasPrefix("#") { line.removeFirst() }
            }
            let font = UIFont.preferredFont(forTextStyle: .body)
            let attributed = NSMutableAttributedString(string: line)
            if !member, let markdown = try? AttributedString(markdown: line, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                attributed.setAttributedString(NSAttributedString(markdown))
                for run in markdown.runs {
                    let prefix = String(markdown[..<run.range.lowerBound].characters)
                    let part = String(markdown[run.range].characters)
                    let start = NSRange(location: prefix.utf16.count, length: part.utf16.count)
                    var traits: UIFontDescriptor.SymbolicTraits = []
                    if run.inlinePresentationIntent?.contains(.stronglyEmphasized) == true { traits.insert(.traitBold) }
                    if run.inlinePresentationIntent?.contains(.emphasized) == true { traits.insert(.traitItalic) }
                    let descriptor = font.fontDescriptor.withSymbolicTraits(traits) ?? font.fontDescriptor
                    attributed.addAttribute(.font, value: UIFont(descriptor: descriptor, size: font.pointSize), range: start)
                }
            } else { attributed.addAttribute(.font, value: font, range: NSRange(location: 0, length: attributed.length)) }
            output.append(attributed)
            if index < lines.count - 1 { output.append(NSAttributedString(string: "\n")) }
        }
        let paragraph = NSMutableParagraphStyle(); paragraph.lineSpacing = 5
        output.addAttributes([.foregroundColor: UIColor(Palette.ink), .paragraphStyle: paragraph], range: NSRange(location: 0, length: output.length))
        view.attributedText = output
        context.coordinator.source = text; context.coordinator.fontSize = pointSize
        if selected.length > 0, NSMaxRange(selected) <= output.length { view.selectedRange = selected }
    }
    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context) -> CGSize? {
        guard let width = proposal.width else { return nil }
        let fitting = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        if member {
            let natural = uiView.sizeThatFits(CGSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude))
            return CGSize(width: min(width, natural.width), height: fitting.height)
        }
        return fitting
    }
}

struct JobCardView: View {
    @EnvironmentObject private var model: AppModel
    let job: JobCard
    let open: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(job.title).font(.headline)
                Spacer()
                Text(job.state.label(model.language)).font(.caption).foregroundStyle(Palette.secondary)
            }
            if !job.step.isEmpty { Text(job.step).font(.subheadline).textSelection(.enabled) }
            if let path = job.frameURL {
                Button(action: open) { AuthenticatedFrame(path: path).frame(height: 180).clipped() }
                    .buttonStyle(.plain).accessibilityLabel(model.words("Bildschirm öffnen", "Open screen"))
            }
            HStack {
                if job.state == .needsYou {
                    Button(model.words("Übernehmen", "Take over"), action: open).buttonStyle(.borderedProminent)
                }
                Spacer()
                if job.state.active {
                    Button(model.words("Stoppen", "Stop")) { Task { await model.cancelJob(job.id) } }.buttonStyle(.bordered)
                }
            }
        }.padding(16).background(.white.opacity(0.7), in: RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Palette.edge))
    }
}

struct Composer: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var phase
    @StateObject private var dictation = Dictation()
    @State private var photo: PhotosPickerItem?
    @State private var priorDraft = ""
    var body: some View {
        VStack(spacing: 8) {
            if model.uploading { Text(model.words("Foto wird angehängt …", "Attaching photo …")).font(.caption) }
            if !model.attachments.isEmpty {
                HStack {
                    Label("\(model.attachments.count) " + model.words("Foto(s)", "photo(s)"), systemImage: "photo")
                    Button { model.attachments = [] } label: { Image(systemName: "xmark.circle") }
                        .accessibilityLabel(model.words("Fotos entfernen", "Remove photos"))
                    Spacer()
                }.font(.footnote).padding(.horizontal, 16)
            }
            HStack(alignment: .center, spacing: 12) {
                if dictation.busy {
                    Button { dictation.cancel() } label: { Image(systemName: "xmark") }.frame(width: 44, height: 44)
                        .accessibilityLabel(model.words("Diktat verwerfen", "Cancel dictation"))
                    waveform
                    Button { finish(send: false) } label: { Image(systemName: "stop.fill") }.frame(width: 44, height: 44)
                        .disabled(!dictation.active).accessibilityLabel(model.words("Diktat ins Textfeld übernehmen", "Put dictation in composer"))
                    Button { finish(send: true) } label: { Image(systemName: "arrow.up.circle.fill").font(.system(size: 30)) }
                        .disabled(!dictation.active || !model.connected || model.sending)
                        .accessibilityLabel(model.words("Diktat senden", "Send dictation"))
                } else {
                    PhotosPicker(selection: $photo, matching: .images) { Image(systemName: "plus").frame(width: 36, height: 44) }
                        .disabled(!model.connection.configured || model.uploading || model.sending)
                        .accessibilityLabel(model.words("Foto anhängen", "Attach photo"))
                    TextField(model.words("Nachricht an Ola", "Message Ola"), text: $model.draft, axis: .vertical)
                        .lineLimit(1...6).font(.body).accessibilityLabel(model.words("Nachricht", "Message"))
                        .accessibilityIdentifier("composer")
                    Button {
                        model.voice.stop(); priorDraft = model.draft
                        model.voice.recording = true
                        Task { await dictation.start(language: model.language) { transcript in putInComposer(transcript) } }
                    } label: { Image(systemName: "mic").frame(width: 32, height: 44) }
                        .accessibilityLabel(model.words("Nachricht diktieren", "Dictate message"))
                    if !model.draft.isEmpty || !model.attachments.isEmpty {
                        Button { Task { await model.send() } } label: { Image(systemName: "arrow.up.circle.fill").font(.system(size: 30)) }
                            .disabled(!model.canSend).accessibilityLabel(model.words("Senden", "Send"))
                    }
                }
            }.padding(.horizontal, 10).padding(.vertical, 6)
                .background(.white, in: RoundedRectangle(cornerRadius: 26))
                .overlay(RoundedRectangle(cornerRadius: 26).stroke(Palette.edge))
            if let error = dictation.error { Text(error).font(.footnote).foregroundStyle(Palette.secondary) }
        }.padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 8).background(Palette.ground)
            .onChange(of: photo) { _, item in
                guard let item else { return }
                Task {
                    do { if let data = try await item.loadTransferable(type: Data.self) { await model.attach(data) } }
                    catch { model.error = model.words("Das Foto konnte nicht geöffnet werden.", "The photo couldn't be opened.") }
                    photo = nil
                }
            }
            .onChange(of: phase) { _, value in if value == .background { dictation.cancel() } }
            .onChange(of: dictation.busy) { _, busy in if !busy { model.voice.recording = false } }
            .onDisappear { dictation.cancel() }
    }
    private var waveform: some View {
        VStack(spacing: 4) {
            HStack(spacing: 2) {
                ForEach(Array(dictation.levels.enumerated()), id: \.offset) { _, level in
                    Capsule().frame(maxWidth: .infinity).frame(height: max(2, level * 30))
                }
            }.frame(height: 30).accessibilityHidden(true)
            Text(dictation.starting ? model.words("Mikrofon wird geöffnet …", "Opening microphone …") : dictation.finishing ? model.words("Diktat wird beendet …", "Finishing dictation …") : dictation.transcript)
                .font(.caption).lineLimit(1)
        }.frame(maxWidth: .infinity)
    }
    private func finish(send: Bool) {
        dictation.finish { transcript in
            putInComposer(transcript)
            if send { Task { await model.send() } }
        }
    }
    private func putInComposer(_ transcript: String) {
        model.draft = [priorDraft, transcript].filter { !$0.isEmpty }.joined(separator: " ")
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var server = ""
    @State private var token = ""
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Section(model.words("Verbindung", "Connection")) {
                    TextField(model.words("Server-Adresse", "Server URL"), text: $server).keyboardType(.URL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField(model.words("Zugangsschlüssel", "Access token"), text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                Picker(model.words("Sprache", "Language"), selection: $model.language) {
                    Text("Deutsch").tag("de"); Text("English").tag("en")
                }
                if let error { Text(error).font(.footnote) }
            }
            .navigationTitle(model.words("Einstellungen", "Settings"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(model.words("Zurück", "Back")) { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(model.words("Speichern", "Save")) {
                        do {
                            try model.configure(Connection(server: server.trimmingCharacters(in: .whitespacesAndNewlines), token: token.trimmingCharacters(in: .whitespacesAndNewlines)))
                            dismiss()
                        } catch { self.error = model.words("Prüfe Adresse und Zugangsschlüssel. Die Einstellungen konnten nicht gespeichert werden.", "Check the URL and access token. Settings couldn't be saved.") }
                    }.disabled(model.sending || model.uploading)
                }
            }.onAppear { server = model.connection.server; token = model.connection.token }
        }.tint(Palette.ink)
    }
}
