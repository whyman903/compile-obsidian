import Foundation
import XCTest
@testable import MyWikiCore

private final class EventCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [ClaudeQueryEvent] = []

    func append(_ event: ClaudeQueryEvent) {
        lock.lock()
        defer { lock.unlock() }
        events.append(event)
    }

    func snapshot() -> [ClaudeQueryEvent] {
        lock.lock()
        defer { lock.unlock() }
        return events
    }
}

final class ClaudeQueryRunnerTests: XCTestCase {
    private var tempDirectory: URL!

    override func setUp() async throws {
        try await super.setUp()
        tempDirectory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appending(path: "ClaudeQueryRunnerTests-" + UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: tempDirectory, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        if let tempDirectory {
            try? FileManager.default.removeItem(at: tempDirectory)
        }
        tempDirectory = nil
        try await super.tearDown()
    }

    private func makeFakeClaude(stdout: String, exit code: Int = 0) throws -> URL {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        cat <<'MYWIKI_STREAM_EOF'
        \(stdout)
        MYWIKI_STREAM_EOF
        exit \(code)
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)
        return scriptURL
    }

    func testStreamingAssistantTextAndFinishedResultAreEmitted() async throws {
        let stdout = """
        {"type":"system","subtype":"init"}
        {"type":"assistant","message":{"content":[{"type":"text","text":"looking up"}]}}
        {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Grep","input":{"pattern":"foo"}}]}}
        {"type":"user","message":{"content":[{"type":"tool_result","content":"match: foo bar"}]}}
        {"type":"assistant","message":{"content":[{"type":"text","text":"Found [[Foo]] — here it is."}]}}
        {"type":"result","subtype":"success","is_error":false,"result":"Found [[Foo]] — here it is.","total_cost_usd":0.0123,"duration_ms":2345,"permission_denials":[]}
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query foo",
            workspaceURL: tempDirectory,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()

        var sawAssistantLookingUp = false
        var sawAssistantFinalText = false
        var sawToolCall = false
        var sawToolResult = false
        var finishedPayload: (String, Double?, Int?, [String])?

        for event in events {
            switch event {
            case .assistantText(let text):
                if text == "looking up" { sawAssistantLookingUp = true }
                if text.contains("Found [[Foo]]") { sawAssistantFinalText = true }
            case .toolCall(let name):
                if name == "Grep" { sawToolCall = true }
            case .toolResult(let preview):
                if preview.contains("match: foo bar") { sawToolResult = true }
            case .finished(let text, let cost, let duration, let denials):
                finishedPayload = (text, cost, duration, denials)
            case .failed:
                XCTFail("did not expect failed event: \(events)")
            }
        }

        XCTAssertTrue(sawAssistantLookingUp, "assistant streaming text missing: \(events)")
        XCTAssertTrue(sawAssistantFinalText, "assistant final text missing: \(events)")
        XCTAssertTrue(sawToolCall, "tool call event missing: \(events)")
        XCTAssertTrue(sawToolResult, "tool result event missing: \(events)")

        let payload = try XCTUnwrap(finishedPayload)
        XCTAssertEqual(payload.0, "Found [[Foo]] — here it is.")
        XCTAssertEqual(payload.1, 0.0123)
        XCTAssertEqual(payload.2, 2345)
        XCTAssertEqual(payload.3, [])
    }

    func testNonZeroExitEmitsFailedEventWithStderr() async throws {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        echo "permission denied" >&2
        exit 2
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-fail", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()
        let failedMessage: String? = events.compactMap { event in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        XCTAssertEqual(failedMessage, "permission denied")
    }

    func testSystemPromptDirectsSynthesisFromProvidedContext() {
        let prompt = ClaudeQueryRunner.wikiSystemPromptAddendum

        XCTAssertTrue(prompt.contains("wiki-context"), "should reference wiki-context tags")
        XCTAssertTrue(prompt.contains("wikilinks"), "should mention wikilink citations")
        XCTAssertTrue(prompt.contains("source pages"), "should identify source pages as primary evidence")
        XCTAssertTrue(prompt.contains("markdown tables"), "should encourage rich tabular answers")
        XCTAssertTrue(prompt.contains("Mermaid"), "should encourage diagram output when useful")
        XCTAssertTrue(prompt.contains("callouts"), "should encourage Obsidian callouts when useful")
    }
}
