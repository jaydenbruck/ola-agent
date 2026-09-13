import XCTest
@testable import OlaCore

final class CoreTests: XCTestCase {
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
}
