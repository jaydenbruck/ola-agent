import XCTest
@testable import OlaCore

final class CoreTests: XCTestCase {
    func testConfirmationFromEventAndSnapshotThenAcknowledgementAndNextStep() throws {
        let json = Data(#"{"type":"job.confirm","job_id":"pizza","title":"Margherita","price":"12,50 €","detail":"Delivery in 25 minutes"}"#.utf8)
        let event = try JSONDecoder().decode(WireEvent.self, from: json)
        var state = ThreadState()
        XCTAssertNil(state.reduce(event))
        let confirmation = try XCTUnwrap(state.jobs[0].confirm)
        XCTAssertEqual(confirmation.price, "12,50 €")
        XCTAssertNil(state.jobs[0].confirmAnswer)
        state.answered("pizza", confirmation: confirmation, answer: "yes")
        XCTAssertEqual(state.jobs[0].confirmAnswer, "yes")
        state.restore(JobCard(id: "pizza", title: "Margherita"))
        XCTAssertEqual(state.jobs[0].confirmAnswer, "yes")
        state.reduce(WireEvent(type: "job.step", job_id: "pizza", text: "Continuing"))
        XCTAssertNil(state.jobs[0].confirm)
        XCTAssertNil(state.jobs[0].confirmAnswer)
        state.answered("pizza", confirmation: confirmation, answer: "yes")
        XCTAssertNil(state.jobs[0].confirmAnswer)
        let row = try JSONDecoder().decode(JobSnapshot.self, from: Data(#"{"job_id":"ride","title":"Ride","state":"needs_you","confirm":{"title":"Uber","price":"18 €","detail":"Arrives in 4 minutes"}}"#.utf8))
        state.restore(row.card)
        let ride = try XCTUnwrap(row.card.confirm)
        state.answered("ride", confirmation: ride, answer: "no")
        XCTAssertEqual(state.jobs[1].confirmAnswer, "no")
        state.reduce(WireEvent(type: "job.done", job_id: "ride"))
        XCTAssertNil(state.jobs[1].confirm)
    }
    func testLinkCodeRotatesSurvivesWaitingSnapshotsAndClears() throws {
        var state = ThreadState()
        let event = try JSONDecoder().decode(WireEvent.self, from: Data(#"{"type":"job.needs_you","job_id":"wa","code":"ABCD-1234","code_hint":"Enter this code in WhatsApp."}"#.utf8))
        state.reduce(event)
        XCTAssertEqual(state.jobs[0].code, "ABCD-1234")
        XCTAssertEqual(state.jobs[0].codeHint, "Enter this code in WhatsApp.")
        state.restore(JobCard(id: "wa", title: "WhatsApp", state: .needsYou))
        XCTAssertEqual(state.jobs[0].code, "ABCD-1234")
        state.reduce(WireEvent(type: "job.step", job_id: "wa", code: "WXYZ-5678"))
        XCTAssertEqual(state.jobs[0].code, "WXYZ-5678")
        state.resumed("wa")
        XCTAssertNil(state.jobs[0].code)
        XCTAssertEqual(state.jobs[0].state.label("en"), "running")
        state.reduce(event)
        state.restore(JobCard(id: "wa", title: "WhatsApp", state: .running))
        XCTAssertNil(state.jobs[0].code)
        state.reduce(event)
        state.reduce(WireEvent(type: "job.done", job_id: "wa"))
        state.reduce(event)
        XCTAssertNil(state.jobs[0].code)
        let row = try JSONDecoder().decode(JobSnapshot.self, from: Data(#"{"job_id":"wa","title":"WhatsApp","state":"needs_you","needs_you":{"code":"ABCD-1234","code_hint":"Open WhatsApp"}}"#.utf8))
        XCTAssertEqual(row.card.code, "ABCD-1234")
    }
    func testSpeechRemovesMarkdownWithoutDroppingWords() {
        XCTAssertEqual(SpeechText.plain("# Hallo\n- **Guten** Tag\n1. [Hier](https://example.com)\n```swift\nCode\n```"), "Hallo\nGuten Tag\nHier")
        XCTAssertEqual(SpeechText.plain(" **Hello** and _welcome_! "), "Hello and welcome!")
        XCTAssertEqual(SpeechText.plain("• `code` und snake_case\n> https://example.com\nText"), "code und snake_case\n \nText")
    }
    func testShutdownCommentRequestsReconnectWithoutChangingCursor() {
        let bytes = Data("id: 42\ndata: saved\n\n: connected\n\n: keep\n\n: bye\r\n\r\n".utf8)
        for split in 0...bytes.count {
            var parser = SSEParser()
            let messages = parser.feed(bytes.prefix(split)) + parser.feed(bytes.suffix(bytes.count - split))
            XCTAssertEqual(messages, [SSEMessage(id: "42", data: "saved")])
            XCTAssertTrue(parser.reconnectRequested)
        }
        var parser = SSEParser()
        XCTAssertEqual(parser.feed(Data(": connected\n\n: keep\n\n".utf8)), [])
        XCTAssertFalse(parser.reconnectRequested)
    }
    func testRejectedInputCannotCountAsAnAcceptedAction() {
        XCTAssertTrue(WireResponse.isRejected(Data(#"{"ok":false,"error":"TargetClosedError"}"#.utf8)))
        XCTAssertFalse(WireResponse.isRejected(Data(#"{"ok":true}"#.utf8)))
        XCTAssertFalse(WireResponse.isRejected(Data(#"{"job_id":"one","state":"running"}"#.utf8)))
    }
    func testPublicDoorPrefixOnEveryRoute() {
        let base = URL(string: "https://api.tryola.ai/agent/")!
        for route in ["/chat", "/speak", "/events/thread", "/jobs?thread_id=thread&all=1", "/jobs/one/frame.jpg?t=2", "/jobs/one/input", "/jobs/one/resume", "/jobs/one/cancel", "/attachments"] {
            XCTAssertEqual(ServiceURL.resolve(base: base, path: route)?.absoluteString, "https://api.tryola.ai/agent" + route)
        }
        XCTAssertEqual(ServiceURL.resolve(base: base, path: "/agent/jobs/one/frame.jpg")?.path, "/agent/jobs/one/frame.jpg")
        XCTAssertEqual(ServiceURL.resolve(base: URL(string: "http://localhost:8787")!, path: "/chat")?.absoluteString, "http://localhost:8787/chat")
        XCTAssertNil(ServiceURL.resolve(base: base, path: "https://other.example/frame.jpg"))
        XCTAssertNil(ServiceURL.resolve(base: base, path: "../v0/health"))
        XCTAssertNil(ServiceURL.resolve(base: base, path: "//other.example/frame.jpg"))
    }
    func testByteBoundariesAndMultiline() {
        let bytes = Data("\u{FEFF}: heartbeat\r\nid: 12\r\nevent: message\r\ndata: Grüß dich 👋\r\ndata: again\r\n\r\n".utf8)
        for split in 0...bytes.count {
            var parser = SSEParser()
            let messages = parser.feed(bytes.prefix(split)) + parser.feed(bytes.suffix(bytes.count - split))
            XCTAssertEqual(messages, [SSEMessage(id: "12", event: "message", data: "Grüß dich 👋\nagain")])
        }
    }
    func testBlankEventsCommentsAndIncompleteDisconnect() {
        var parser = SSEParser()
        XCTAssertEqual(parser.feed(Data(": hello\n\nevent: ping\n\ndata: unfinished".utf8)), [])
        XCTAssertEqual(parser.feed(Data("\n\n".utf8)), [SSEMessage(data: "unfinished")])
    }
    func testCRAndPersistentIDAndEmptyData() {
        var parser = SSEParser()
        XCTAssertEqual(parser.feed(Data("id: 4\rdata: one\r\rdata:\r\rid:\rdata: three\r\r".utf8)), [
            SSEMessage(id: "4", data: "one"), SSEMessage(id: "4", data: ""), SSEMessage(id: "", data: "three")])
    }
    func testEveryCardTransitionAndNoDuplicate() {
        var state = ThreadState()
        state.reduce(WireEvent(type: "job.started", job_id: "a", title: "Pizza"))
        state.reduce(WireEvent(type: "job.started", job_id: "a", title: "Pizza"))
        state.reduce(WireEvent(type: "job.step", job_id: "a", text: "Menu open", frame_url: "/jobs/a/frame.jpg?t=1"))
        XCTAssertEqual(state.jobs.count, 1)
        XCTAssertEqual(state.items, [.job("a")])
        XCTAssertEqual(state.jobs[0].step, "Menu open")
        state.reduce(WireEvent(type: "job.needs_you", job_id: "a", reason: "Sign in"))
        XCTAssertEqual(state.jobs[0].state, .needsYou)
        state.reduce(WireEvent(type: "job.done", job_id: "a", result: "Ordered"))
        state.reduce(WireEvent(type: "job.step", job_id: "a", text: "Old step"))
        XCTAssertEqual(state.jobs[0].state, .done)
        XCTAssertEqual(state.jobs[0].step, "Ordered")
        XCTAssertEqual(state.jobs[0].frameURL, "/jobs/a/frame.jpg?t=1")
    }
    func testFailureAndReorderedCard() {
        var state = ThreadState()
        state.reduce(WireEvent(type: "job.needs_you", job_id: "b", reason: "Code"))
        state.reduce(WireEvent(type: "job.started", job_id: "b", title: "Ride"))
        XCTAssertEqual(state.jobs[0].state, .needsYou)
        state.reduce(WireEvent(type: "job.failed", job_id: "b", reason: "Closed"))
        XCTAssertEqual(state.jobs[0].state, .failed)
        XCTAssertEqual(state.jobs[0].step, "Closed")
    }
    func testConcurrentRepliesCompleteOnce() {
        var state = ThreadState()
        state.reduce(WireEvent(type: "assistant.delta", turn_id: "1", text: "Hallo "))
        state.reduce(WireEvent(type: "assistant.delta", turn_id: "2", text: "Done"))
        state.reduce(WireEvent(type: "assistant.delta", turn_id: "1", text: "du"))
        XCTAssertEqual(state.reduce(WireEvent(type: "assistant.done", turn_id: "1")), "Hallo du")
        XCTAssertNil(state.reduce(WireEvent(type: "assistant.done", turn_id: "1")))
        XCTAssertNil(state.reduce(WireEvent(type: "assistant.delta", turn_id: "1", text: "late")))
        XCTAssertEqual(state.messages[0].text, "Hallo du")
        XCTAssertEqual(state.reduce(WireEvent(type: "assistant.done", turn_id: "2")), "Done")
    }
    func testSnapshotAndPersistence() throws {
        let json = Data(#"{"job_id":"a","title":"Mail","state":"needs_you","last_step":"Code","frame_url":"/frame"}"#.utf8)
        var state = ThreadState()
        state.restore(try JSONDecoder().decode(JobSnapshot.self, from: json).card)
        state.restore(try JSONDecoder().decode(JobSnapshot.self, from: json).card)
        state.cursor = "13"
        XCTAssertEqual(state.jobs.count, 1)
        XCTAssertEqual(try JSONDecoder().decode(ThreadState.self, from: JSONEncoder().encode(state)), state)
    }
    func testFittedFrameRejectsLetterbox() {
        XCTAssertNil(FrameGeometry.point(x: 0, y: 0, width: 600, height: 844))
        let point = FrameGeometry.point(x: 300, y: 422, width: 600, height: 844)
        XCTAssertEqual(point?.0, 195)
        XCTAssertEqual(point?.1, 422)
        XCTAssertNil(FrameGeometry.point(x: .nan, y: 12, width: 390, height: 844))
    }
    func testCompletionRepairsMissingDeltas() {
        var state = ThreadState()
        state.reduce(WireEvent(type: "assistant.delta", turn_id: "1", text: "partial"))
        XCTAssertEqual(state.reduce(WireEvent(type: "assistant.done", turn_id: "1", text: "The whole reply")), "The whole reply")
        XCTAssertEqual(state.messages[0].text, "The whole reply")
    }
    func testCapturedRealServerEvents() throws {
        guard let path = ProcessInfo.processInfo.environment["OLA_EVENT_FIXTURE"] else {
            throw XCTSkip("Run app/Tools/probe.py against the real server and set OLA_EVENT_FIXTURE")
        }
        var parser = SSEParser(), state = ThreadState()
        let frames = parser.feed(try Data(contentsOf: URL(fileURLWithPath: path)))
        XCTAssertFalse(frames.isEmpty)
        var completions = 0
        for frame in frames {
            XCTAssertNotNil(frame.id)
            let event = try JSONDecoder().decode(WireEvent.self, from: Data(frame.data.utf8))
            if state.reduce(event) != nil { completions += 1 }
        }
        XCTAssertEqual(completions, 1)
        XCTAssertEqual(state.messages.count, 1)
        XCTAssertTrue(state.messages[0].finished)
        XCTAssertFalse(state.messages[0].text.isEmpty)
    }
}
