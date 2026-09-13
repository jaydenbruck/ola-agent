import Foundation
import Security
import CryptoKit
import SwiftUI
import UIKit

struct Connection: Codable, Equatable {
    var server = "https://api.tryola.ai/agent"
    var token = ""
    var baseURL: URL? {
        guard let url = URL(string: server), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { return nil }
        return url
    }
    var configured: Bool { baseURL != nil && !token.isEmpty }
    var storageKey: String {
        SHA256.hash(data: Data((server + "\n" + token).utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

enum SecureSettings {
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "ai.tryola.agent",
         kSecAttrAccount as String: "connection"]
    }
    static func read() -> Connection {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data, let connection = try? JSONDecoder().decode(Connection.self, from: data) else { return Connection() }
        return connection
    }
    static func save(_ connection: Connection) throws {
        let data = try JSONEncoder().encode(connection)
        let changes = [kSecValueData as String: data]
        var status = SecItemUpdate(query as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query.merging(changes) { _, new in new }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw ClientError.storage }
    }
}

enum ClientError: Error { case configuration, response(Int), storage, image, rejected }

struct API {
    let connection: Connection
    func request(_ path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let base = connection.baseURL, let url = ServiceURL.resolve(base: base, path: path) else { throw ClientError.configuration }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = method
        request.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
        if let body { request.httpBody = body; request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return request
    }
    func data(_ path: String) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request(path))
        try validate(response)
        return data
    }
    func post(_ path: String, _ body: [String: Any] = [:]) async throws -> Data {
        let request = try request(path, method: "POST", body: JSONSerialization.data(withJSONObject: body))
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response)
        if WireResponse.isRejected(data) { throw ClientError.rejected }
        return data
    }
    func upload(_ data: Data) async throws -> String {
        let boundary = "ola-" + UUID().uuidString
        var request = try request("/attachments", method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"photo.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8)
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let (result, response) = try await URLSession.shared.upload(for: request, from: body)
        try validate(response)
        struct Attachment: Decodable { let id: String }
        return try JSONDecoder().decode(Attachment.self, from: result).id
    }
    func validate(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw ClientError.response((response as? HTTPURLResponse)?.statusCode ?? 0)
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var connection = SecureSettings.read()
    @Published var state = ThreadState()
    @Published var draft = ""
    @Published var attachments: [String] = []
    @Published var connected = false
    @Published var connectionIssue: String?
    @Published var sending = false
    @Published var uploading = false
    @Published var error: String?
    @Published var running: [JobCard] = []
    @Published var overview: [JobCard] = []
    @Published var language = UserDefaults.standard.string(forKey: "language") ?? "de" {
        didSet { UserDefaults.standard.set(language, forKey: "language") }
    }
    @Published var speaker = UserDefaults.standard.bool(forKey: "speaker") {
        didSet { UserDefaults.standard.set(speaker, forKey: "speaker"); if !speaker { voice.stop() } }
    }
    let voice = ReplyVoice()
    private var stream: Task<Void, Never>?
    private var refresh: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var epoch = UUID()
    private(set) var threadID = ""
    var api: API { API(connection: connection) }
    var canSend: Bool { connected && !sending && !uploading && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty) }
    func words(_ de: String, _ en: String) -> String { copy(language, de, en) }
    var connectionStatus: String {
        connection.configured ? (connectionIssue ?? words("Verbindung wird hergestellt", "Connecting")) : words("Öffne die Einstellungen (nicht eingerichtet)", "Open Settings (not configured)")
    }
    func connectionReason(_ error: Error) -> String {
        if case ClientError.configuration = error { return words("Öffne die Einstellungen (nicht eingerichtet)", "Open Settings (not configured)") }
        if case ClientError.response(let status) = error {
            return words(status == 401 ? "Prüfe den Zugangsschlüssel" : "Der Server antwortet nicht wie erwartet", status == 401 ? "Check the access token" : "The server did not respond as expected") + " (HTTP \(status))"
        }
        let detail = (error as? URLError).map { "URLError \($0.code.rawValue): \($0.localizedDescription)" } ?? error.localizedDescription
        return words("Verbindung fehlgeschlagen", "Connection failed") + " (\(detail))"
    }

    init() {
        #if DEBUG
        let env = ProcessInfo.processInfo.environment
        if let token = env["OLA_SMOKE_TOKEN"], !token.isEmpty {
            connection = Connection(server: env["OLA_SMOKE_SERVER"] ?? connection.server, token: token)
        }
        #endif
        restore()
        #if DEBUG
        if let thread = env["OLA_SMOKE_THREAD_ID"], !thread.isEmpty { threadID = thread; state = ThreadState() }
        #endif
    }
    private var cacheURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ola-" + connection.storageKey + ".json")
    }
    private func restore() {
        let key = "thread-" + connection.storageKey
        threadID = UserDefaults.standard.string(forKey: key) ?? UUID().uuidString
        UserDefaults.standard.set(threadID, forKey: key)
        state = (try? JSONDecoder().decode(ThreadState.self, from: Data(contentsOf: cacheURL))) ?? ThreadState()
    }
    private func persist() {
        do {
            try FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(state).write(to: cacheURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch { self.error = words("Der Chat konnte nicht auf diesem Gerät gespeichert werden.", "This chat couldn't be saved on this device.") }
    }
    private func scheduleSave() {
        guard saveTask == nil else { return }
        saveTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(500))
            guard !Task.isCancelled, let self else { return }
            self.persist(); self.saveTask = nil
        }
    }
    func configure(_ value: Connection) throws {
        guard value.configured else { throw ClientError.configuration }
        try SecureSettings.save(value)
        stop()
        connection = value; draft = ""; attachments = []; running = []; overview = []; error = nil
        connectionIssue = nil; restore(); start()
    }
    func newChat() {
        stop()
        threadID = UUID().uuidString
        UserDefaults.standard.set(threadID, forKey: "thread-" + connection.storageKey)
        state = ThreadState(); draft = ""; attachments = []; running = []; overview = []
        error = nil; connectionIssue = nil
        persist(); start()
    }
    func start() {
        guard stream == nil, connection.configured else { return }
        let generation = epoch, client = api, thread = threadID
        stream = Task { [weak self] in
            var delay = 1
            while !Task.isCancelled {
                guard let self, self.epoch == generation else { return }
                do {
                    var request = try client.request("/events/" + thread)
                    request.timeoutInterval = 3600
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    if let cursor = self.state.cursor, !cursor.isEmpty { request.setValue(cursor, forHTTPHeaderField: "Last-Event-ID") }
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    try client.validate(response)
                    self.connected = true; self.connectionIssue = nil; delay = 1
                    var parser = SSEParser()
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        guard self.epoch == generation else { return }
                        if let message = parser.feed(byte) {
                            if let id = message.id, !id.isEmpty, id == self.state.cursor { continue }
                            do { let event = try JSONDecoder().decode(WireEvent.self, from: Data(message.data.utf8))
                                let reply = self.state.reduce(event)
                                self.state.cursor = message.id
                                if self.speaker, let reply, !reply.isEmpty { self.voice.speak(reply, fallback: self.language) }
                                self.scheduleSave()
                            }
                        }
                        if parser.reconnectRequested { break }
                    }
                } catch {
                    if Task.isCancelled || self.epoch != generation { return }
                    self.connectionIssue = self.connectionReason(error)
                }
                self.connected = false
                do { try await Task.sleep(for: .seconds(delay)) } catch { return }
                delay = min(delay * 2, 15)
            }
        }
        refresh = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshJobs()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
    }
    func stop() {
        epoch = UUID(); stream?.cancel(); stream = nil; refresh?.cancel(); refresh = nil
        saveTask?.cancel(); saveTask = nil; connected = false; voice.stop(); persist()
    }
    func refreshJobs() async {
        let generation = epoch
        let before = state.jobs
        do {
            async let globalData = api.data("/jobs?all=1")
            async let threadData = api.data("/jobs?thread_id=\(threadID)&all=1")
            let rows = try JSONDecoder().decode([JobSnapshot].self, from: await globalData)
            let history = try JSONDecoder().decode([JobSnapshot].self, from: await threadData)
            guard epoch == generation else { return }
            overview = rows.map(\.card)
            running = overview.filter { $0.state.active }
            for row in history {
                // An event received during this request is newer than its snapshot.
                if state.jobs.first(where: { $0.id == row.job_id }) == before.first(where: { $0.id == row.job_id }) {
                    state.restore(row.card)
                }
            }
            scheduleSave()
        } catch { /* Stream status is shown separately; a failed poll cannot clear real cards. */ }
    }
    func send() async {
        guard canSend else { return }
        let text = draft, photos = attachments, generation = epoch
        sending = true; draft = ""; attachments = []
        state.addMember(text: text, attachments: photos); persist()
        defer { sending = false }
        do { _ = try await api.post("/chat", ["thread_id": threadID, "text": text, "attachments": photos]) }
        catch {
            guard epoch == generation else { return }
            self.error = words("Senden nicht bestätigt. Prüfe die Verbindung, bevor du die Nachricht erneut sendest.", "Sending wasn't confirmed. Check the connection before sending this message again.")
            if draft.isEmpty { draft = text; attachments = photos }
        }
    }
    func attach(_ data: Data) async {
        guard let image = UIImage(data: data) else { return }
        uploading = true
        let generation = epoch
        defer { uploading = false }
        do {
            let scale = min(1, 2048 / max(image.size.width, image.size.height))
            let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
            let format = UIGraphicsImageRendererFormat(); format.scale = 1
            let resized = UIGraphicsImageRenderer(size: size, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
            guard let jpeg = resized.jpegData(compressionQuality: 0.85) else { throw ClientError.image }
            let id = try await api.upload(jpeg)
            if epoch == generation { attachments.append(id) }
        } catch { self.error = words("Das Foto konnte nicht angehängt werden.", "The photo couldn't be attached.") }
    }
    func cancelJob(_ id: String) async {
        do { _ = try await api.post("/jobs/\(id)/cancel"); await refreshJobs() }
        catch { self.error = words("Der Auftrag konnte nicht gestoppt werden.", "The job couldn't be stopped.") }
    }
}
