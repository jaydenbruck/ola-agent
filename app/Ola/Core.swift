import Foundation

enum SpeechText {
    static func plain(_ text: String) -> String {
        let rules = [(#"(?s)```.*?```"#, " "), (#"`([^`]*)`"#, "$1"),
            (#"!?\[([^\]]*)\]\([^)]*\)"#, "$1"), (#"<https?://[^>]+>"#, " "), (#"https?://\S+"#, " "),
            (#"(?m)^\s{0,3}#{1,6}\s+"#, ""), (#"(?m)^\s*(?:[-*+•]|\d+[.)])\s+"#, ""),
            (#"(?s)(\*\*|__)(.*?)\1"#, "$2"), (#"(?s)(?<!\w)[*_](.+?)[*_](?!\w)"#, "$1"),
            (#"(?m)^\s*>\s?"#, ""), (#"[ \t]+"#, " "), (#"\n{2,}"#, "\n")]
        return rules.reduce(text) { $0.replacingOccurrences(of: $1.0, with: $1.1, options: .regularExpression) }
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

struct SSEMessage: Equatable {
    var id: String?
    var event: String?
    var data: String
}

struct SpeechSentences {
    private struct Turn { var text = ""; var consumed = 0 }
    private var turns: [String: Turn] = [:]
    private var cancelled: Set<String> = []

    mutating func receive(_ event: WireEvent, finalText: String? = nil) -> [String] {
        guard let id = event.turn_id, ["assistant.delta", "assistant.done"].contains(event.type) else { return [] }
        guard !cancelled.contains(id) else { return [] }
        var turn = turns[id] ?? Turn()
        if event.type == "assistant.delta" { turn.text += event.text ?? "" }
        else if let full = finalText ?? event.text { turn.text = full }
        let remaining = Array(turn.text.dropFirst(turn.consumed))
        var start = 0, chunks: [String] = []
        for index in remaining.indices where index - start + 1 >= 40 {
            let char = remaining[index]
            guard ".!?\n".contains(char) else { continue }
            let next = index + 1
            if char == ".", index > 0, remaining[index - 1].isNumber,
               next == remaining.count || remaining[next].isNumber { continue }
            guard char == "\n" || next == remaining.count || remaining[next].isWhitespace else { continue }
            chunks.append(String(remaining[start...index])); start = next
        }
        turn.consumed += start
        if event.type == "assistant.done" {
            let tail = String(remaining.dropFirst(start))
            if !tail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { chunks.append(tail) }
            turns.removeValue(forKey: id)
        } else { turns[id] = turn }
        return chunks
    }
    mutating func cancel() { cancelled.formUnion(turns.keys); turns.removeAll() }
}

/// Bytes, rather than decoded chunks, keep split UTF-8 characters intact.
struct SSEParser {
    private(set) var reconnectRequested = false
    private var line: [UInt8] = []
    private var data: [String] = []
    private var id: String?
    private var event: String?
    private var afterCR = false
    private var firstLine = true

    mutating func feed(_ byte: UInt8) -> SSEMessage? {
        if afterCR {
            afterCR = false
            if byte == 10 { return nil }
        }
        if byte == 13 || byte == 10 {
            afterCR = byte == 13
            return consumeLine()
        }
        line.append(byte)
        return nil
    }

    mutating func feed(_ bytes: Data) -> [SSEMessage] { bytes.compactMap { feed($0) } }

    private mutating func consumeLine() -> SSEMessage? {
        var value = String(decoding: line, as: UTF8.self)
        line.removeAll(keepingCapacity: true)
        if firstLine { value = value.replacingOccurrences(of: "\u{FEFF}", with: ""); firstLine = false }
        if value.isEmpty {
            defer { data.removeAll(keepingCapacity: true); event = nil }
            guard !data.isEmpty else { return nil }
            return SSEMessage(id: id, event: event, data: data.joined(separator: "\n"))
        }
        if value.hasPrefix(":") {
            if value.dropFirst().trimmingCharacters(in: .whitespaces) == "bye" { reconnectRequested = true }
            return nil
        }
        let parts = value.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        var field = parts.count > 1 ? String(parts[1]) : ""
        if field.hasPrefix(" ") { field.removeFirst() }
        switch String(parts[0]) {
        case "data": data.append(field)
        case "id": if !field.contains("\0") { id = field }
        case "event": event = field
        default: break
        }
        return nil
    }
}

struct WireEvent: Codable, Equatable {
    var type: String
    var turn_id: String?
    var job_id: String?
    var title: String?
    var text: String?
    var frame_url: String?
    var reason: String?
    var url: String?
    var result: String?
    var code: String?
    var code_hint: String?
    var price: String?
    var detail: String?
}

struct JobConfirmation: Codable, Equatable {
    var title: String
    var price: String
    var detail: String
}

enum JobState: String, Codable {
    case running, needsYou, done, failed
    var active: Bool { self == .running || self == .needsYou }
    func label(_ language: String) -> String {
        switch self {
        case .running: return copy(language, "läuft", "running")
        case .needsYou: return copy(language, "wartet auf dich", "waiting for you")
        case .done: return copy(language, "fertig", "done")
        case .failed: return copy(language, "nicht geschafft", "couldn't finish")
        }
    }
}

struct JobCard: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var state: JobState = .running
    var step = ""
    var frameURL: String?
    var code: String?
    var codeHint: String?
    var confirm: JobConfirmation?
    var confirmAnswer: String?
}

struct JobSnapshot: Decodable {
    var job_id: String
    var title: String
    var state: String
    var last_step: String?
    var frame_url: String?
    var thread_id: String?
    var code: String?
    var code_hint: String?
    var needs_you: CodeInfo?
    var confirm: JobConfirmation?
    struct CodeInfo: Decodable { var code: String?; var code_hint: String? }

    var card: JobCard {
        let status: JobState
        switch state {
        case "needs_you", "waiting", "needsYou": status = .needsYou
        case "done", "completed": status = .done
        case "failed", "cancelled", "canceled": status = .failed
        default: status = .running
        }
        return JobCard(id: job_id, title: title, state: status, step: last_step ?? "", frameURL: frame_url,
                       code: status.active ? (code ?? needs_you?.code) : nil, codeHint: code_hint ?? needs_you?.code_hint,
                       confirm: status.active ? confirm : nil)
    }
}

struct ChatMessage: Codable, Identifiable, Equatable {
    var id: String
    var member: Bool
    var text: String
    var finished = false
    var attachmentIDs: [String] = []
}

enum ThreadItem: Codable, Equatable, Identifiable {
    case message(String), job(String)
    var id: String {
        switch self { case .message(let id): return "m-" + id; case .job(let id): return "j-" + id }
    }
}

struct ThreadState: Codable, Equatable {
    var messages: [ChatMessage] = []
    var jobs: [JobCard] = []
    var items: [ThreadItem] = []
    var cursor: String?

    mutating func addMember(text: String, attachments: [String]) {
        let message = ChatMessage(id: UUID().uuidString, member: true, text: text,
                                  finished: true, attachmentIDs: attachments)
        messages.append(message)
        items.append(.message(message.id))
    }

    /// Returns a reply only once, at completion, for the optional speaker.
    @discardableResult mutating func reduce(_ event: WireEvent) -> String? {
        if event.type.hasPrefix("assistant."), let id = event.turn_id {
            if !messages.contains(where: { $0.id == id }) {
                messages.append(ChatMessage(id: id, member: false, text: ""))
                items.append(.message(id))
            }
            guard let index = messages.firstIndex(where: { $0.id == id }), !messages[index].finished else { return nil }
            if event.type == "assistant.delta" { messages[index].text += event.text ?? "" }
            if event.type == "assistant.done" {
                if let full = event.text { messages[index].text = full }
                messages[index].finished = true
                return messages[index].text
            }
            return nil
        }
        guard event.type.hasPrefix("job."), let id = event.job_id else { return nil }
        guard ["job.started", "job.step", "job.needs_you", "job.confirm", "job.done", "job.failed"].contains(event.type) else { return nil }
        if !jobs.contains(where: { $0.id == id }) {
            jobs.append(JobCard(id: id, title: event.title ?? ""))
            items.append(.job(id))
        }
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return nil }
        if let title = event.title { jobs[index].title = title }
        if let frame = event.frame_url { jobs[index].frameURL = frame }
        // Late steps must never resurrect a completed card.
        guard jobs[index].state.active else { return nil }
        switch event.type {
        case "job.step":
            jobs[index].step = event.text ?? jobs[index].step
            jobs[index].confirm = nil; jobs[index].confirmAnswer = nil
        case "job.confirm":
            guard let title = event.title, let price = event.price, let detail = event.detail else { return nil }
            jobs[index].confirm = JobConfirmation(title: title, price: price, detail: detail)
            jobs[index].confirmAnswer = nil; jobs[index].state = .needsYou
        case "job.needs_you": jobs[index].state = .needsYou; jobs[index].step = event.reason ?? ""
        case "job.done": jobs[index].state = .done; jobs[index].step = event.result ?? ""
        case "job.failed": jobs[index].state = .failed; jobs[index].step = event.reason ?? ""
        default: break
        }
        if let code = event.code, ["job.step", "job.needs_you"].contains(event.type) {
            jobs[index].code = code.isEmpty ? nil : code
            jobs[index].codeHint = event.code_hint ?? jobs[index].codeHint
        }
        if !jobs[index].state.active {
            jobs[index].code = nil; jobs[index].codeHint = nil
            jobs[index].confirm = nil; jobs[index].confirmAnswer = nil
        }
        return nil
    }

    mutating func restore(_ card: JobCard) {
        if let index = jobs.firstIndex(where: { $0.id == card.id }) {
            var latest = card
            if latest.state == .needsYou && latest.code == nil { latest.code = jobs[index].code; latest.codeHint = latest.codeHint ?? jobs[index].codeHint }
            let old = jobs[index]
            if latest.state.active, latest.step == old.step, old.confirmAnswer != nil,
               latest.confirm == nil || latest.confirm == old.confirm {
                latest.confirm = old.confirm; latest.confirmAnswer = old.confirmAnswer
            }
            jobs[index] = latest
        }
        else { jobs.append(card); items.append(.job(card.id)) }
    }
    mutating func resumed(_ id: String) {
        guard let index = jobs.firstIndex(where: { $0.id == id }), jobs[index].state.active else { return }
        jobs[index].state = .running; jobs[index].code = nil; jobs[index].codeHint = nil
    }
    mutating func answered(_ id: String, confirmation: JobConfirmation, answer: String) {
        guard ["yes", "no"].contains(answer), let index = jobs.firstIndex(where: { $0.id == id }),
              jobs[index].state.active, jobs[index].confirm == confirmation, jobs[index].confirmAnswer == nil else { return }
        jobs[index].confirmAnswer = answer; jobs[index].state = .running
    }
}

func copy(_ language: String, _ german: String, _ english: String) -> String {
    language.hasPrefix("de") ? german : english
}

enum ServiceURL {
    static func resolve(base: URL, path: String) -> URL? {
        guard var parts = URLComponents(url: base, resolvingAgainstBaseURL: true),
              ["http", "https"].contains(parts.scheme ?? ""), parts.host != nil,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              !path.hasPrefix("//") else { return nil }
        if !parts.path.hasSuffix("/") { parts.path += "/" }
        guard let directory = parts.url else { return nil }
        let relative = path.hasPrefix(parts.path) && parts.path != "/" ? path : String(path.drop(while: { $0 == "/" }))
        guard let url = URL(string: relative, relativeTo: directory)?.absoluteURL.standardized,
              url.scheme == directory.scheme, url.host == directory.host, url.port == directory.port,
              url.user == nil, url.password == nil, url.path.hasPrefix(parts.path) else { return nil }
        return url
    }
}

enum WireResponse {
    static func isRejected(_ data: Data) -> Bool {
        guard let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return result["ok"] as? Bool == false
    }
}

/// Coordinates are relative to the fitted screenshot, never the surrounding letterbox.
enum FrameGeometry {
    static func point(x: Double, y: Double, width: Double, height: Double) -> (Double, Double)? {
        guard width > 0, height > 0, x.isFinite, y.isFinite else { return nil }
        let scale = min(width / 390, height / 844)
        let left = (width - 390 * scale) / 2, top = (height - 844 * scale) / 2
        let px = (x - left) / scale, py = (y - top) / scale
        guard px >= 0, px < 390, py >= 0, py < 844 else { return nil }
        return (px, py)
    }
}
