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
        {"type":"result","subtype":"success","is_error":false,"result":"Found [[Foo]] — here it is.","total_cost_usd":0.0123,"duration_ms":2345,"permission_denials":[],"session_id":"session-123"}
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query foo",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()

        var sawAssistantLookingUp = false
        var sawAssistantFinalText = false
        var sawToolCall = false
        var sawToolResult = false
        var finishedPayload: (String, Double?, Int?, [String], String?)?

        for event in events {
            switch event {
            case .assistantText(let text):
                if text == "looking up" { sawAssistantLookingUp = true }
                if text.contains("Found [[Foo]]") { sawAssistantFinalText = true }
            case .toolCall(let name):
                if name == "Grep" { sawToolCall = true }
            case .toolResult(let preview):
                if preview.contains("match: foo bar") { sawToolResult = true }
            case .finished(let text, let cost, let duration, let denials, let sessionID):
                finishedPayload = (text, cost, duration, denials, sessionID)
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
        XCTAssertEqual(payload.4, "session-123")
    }

    func testPlanningTextWithToolUseIsNotEmittedAsAnswer() async throws {
        let stdout = """
        {"type":"assistant","message":{"content":[{"type":"text","text":"Let me pull the Exam 2 source for more detail."},{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/exam.md"}}]}}
        {"type":"user","message":{"content":[{"type":"tool_result","content":"exam notes"}]}}
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-planning-tool", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()
        XCTAssertFalse(events.contains { event in
            if case .assistantText(let text) = event {
                return text.contains("Let me pull")
            }
            return false
        }, "planning text should not be treated as answer text: \(events)")

        let failedMessage = events.compactMap { event -> String? in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        XCTAssertNotNil(failedMessage)
    }

    func testIncompleteFinalAssistantJSONRecoversAnswerInsteadOfPlanningText() async throws {
        let stdout = """
        {"type":"assistant","message":{"content":[{"type":"text","text":"Let me pull the Exam 2 source for more detail."},{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/exam.md"}}]}}
        {"type":"user","message":{"content":[{"type":"tool_result","content":"exam notes"}]}}
        {"type":"assistant","message":{"content":[{"type":"text","text":"I have enough for a comparison. See [[Exam 2]].\\n\\n## Final Answer\\n\\nWarren and Marquis disagree about personhood vs. future value."}],"stop_reason":null,"usage":{"input_tokens":1
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-recover-final", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()
        let assistantTexts = events.compactMap { event -> String? in
            if case .assistantText(let text) = event { return text } else { return nil }
        }
        XCTAssertFalse(assistantTexts.contains { $0.contains("Let me pull") })
        XCTAssertTrue(assistantTexts.contains { $0.contains("Final Answer") })

        let finishedText = events.compactMap { event -> String? in
            if case .finished(let text, _, _, _, _) = event { return text } else { return nil }
        }.first
        XCTAssertEqual(
            finishedText,
            "I have enough for a comparison. See [[Exam 2]].\n\n## Final Answer\n\nWarren and Marquis disagree about personhood vs. future value."
        )
        XCTAssertFalse(events.contains { event in
            if case .failed = event { return true }
            return false
        })
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
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let events = collector.snapshot()
        let failedMessage: String? = events.compactMap { event in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        XCTAssertEqual(failedMessage, "permission denied")
    }

    func testNonZeroExitUsesPlainStdoutWhenStderrIsEmpty() async throws {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        echo 'Claude Code needs an update.'
        echo 'Please run: claude update'
        exit 1
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-stdout-fail", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let failedMessage: String? = collector.snapshot().compactMap { event in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        XCTAssertEqual(failedMessage, "Claude Code needs an update.\nPlease run: claude update")
    }

    func testZeroExitWithMalformedStreamAndNoAnswerEmitsFailedEvent() async throws {
        let stdout = """
        {"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"partial tool result
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-malformed-empty", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let failedMessage: String? = collector.snapshot().compactMap { event in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        let message = try XCTUnwrap(failedMessage)
        XCTAssertTrue(message.contains("Claude exited before producing an answer"))
        XCTAssertTrue(message.contains("not valid JSON"))
        XCTAssertTrue(message.contains("partial tool result"))
        XCTAssertFalse(message.contains("Query completed without a response"))
    }

    func testZeroExitWithOnlyToolResultEmitsFailedEvent() async throws {
        let stdout = """
        {"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"read a note but never answered"}]}}
        """
        let scriptURL = try makeFakeClaude(stdout: stdout)
        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-tool-only", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        let collector = EventCollector()
        try await runner.runQuery(
            prompt: "/query x",
            workspaceURL: tempDirectory,
            resumeSessionID: nil,
            onEvent: { collector.append($0) }
        )

        let failedMessage: String? = collector.snapshot().compactMap { event in
            if case .failed(let message) = event { return message } else { return nil }
        }.first
        let message = try XCTUnwrap(failedMessage)
        XCTAssertTrue(message.contains("Claude exited after a tool result without producing an answer"))
        XCTAssertTrue(message.contains("read a note but never answered"))
    }

    func testMissingResumeSessionThrowsRetryableError() async throws {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        echo 'Session not found: old-session' >&2
        exit 1
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-resume-missing", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        do {
            try await runner.runQuery(
                prompt: "/query x",
                workspaceURL: tempDirectory,
                resumeSessionID: "old-session",
                onEvent: { _ in }
            )
            XCTFail("Expected missing resume session to throw")
        } catch let error as ClaudeQueryResumeUnavailableError {
            XCTAssertEqual(error.message, "Session not found: old-session")
        }
    }

    func testExpiredResumeSessionThrowsRetryableError() async throws {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        echo 'Session has expired. Start a new conversation.' >&2
        exit 1
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-resume-expired", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        do {
            try await runner.runQuery(
                prompt: "/query x",
                workspaceURL: tempDirectory,
                resumeSessionID: "old-session",
                onEvent: { _ in }
            )
            XCTFail("Expected expired resume session to throw")
        } catch let error as ClaudeQueryResumeUnavailableError {
            XCTAssertTrue(error.message.contains("expired"))
        }
    }

    func testRunQueryUsesReadOnlyAgentToolArgumentsAndResumeID() async throws {
        let scriptURL = tempDirectory.appending(path: "claude", directoryHint: .notDirectory)
        let script = """
        #!/bin/zsh
        if [[ "$1" == "--help" ]]; then
          echo '--exclude-dynamic-system-prompt-sections'
          exit 0
        fi
        printf '%s\\n' "$@" > args.txt
        cat <<'MYWIKI_STREAM_EOF'
        {"type":"result","subtype":"success","is_error":false,"result":"answer","permission_denials":[],"session_id":"session-456"}
        MYWIKI_STREAM_EOF
        """
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

        let logger = AppLogger(logDirectory: tempDirectory.appending(path: "logs-args", directoryHint: .isDirectory))
        let runner = ClaudeQueryRunner(logger: logger) { scriptURL }

        try await runner.runQuery(
            prompt: "What should I read?",
            workspaceURL: tempDirectory,
            resumeSessionID: "session-123",
            onEvent: { _ in }
        )

        let argsURL = tempDirectory.appending(path: "args.txt", directoryHint: .notDirectory)
        let args = try String(contentsOf: argsURL, encoding: .utf8)
            .split(separator: "\n")
            .map(String.init)

        XCTAssertTrue(args.contains("-p"), "missing print mode: \(args)")
        XCTAssertEqual(Array(args.drop(while: { $0 != "--resume" }).prefix(2)), ["--resume", "session-123"])
        XCTAssertEqual(Array(args.drop(while: { $0 != "--settings" }).prefix(2)), ["--settings", #"{"disableAllHooks":true}"#])
        XCTAssertFalse(args.contains("--permission-mode"), "plan mode stops claude -p after the first plan statement")
        XCTAssertEqual(Array(args.drop(while: { $0 != "--allowedTools" }).prefix(2)), ["--allowedTools", "Read,Grep,Glob,LS"])
        XCTAssertTrue(args.contains("--exclude-dynamic-system-prompt-sections"))
        XCTAssertEqual(
            Array(args.drop(while: { $0 != "--disallowedTools" }).prefix(2)),
            ["--disallowedTools", "Task,AskUserQuestion,Monitor,Bash,Edit,Write,NotebookEdit,MultiEdit,WebSearch,WebFetch"]
        )
        XCTAssertTrue(args.contains { $0.contains("Task") })
        XCTAssertFalse(args.contains("--add-dir"))
        XCTAssertFalse(args.contains("--bare"))
        XCTAssertFalse(args.contains("--effort"))
        XCTAssertFalse(args.contains("--session-id"))
        XCTAssertFalse(args.contains("dontAsk"))
        XCTAssertFalse(args.contains("bypassPermissions"))
        XCTAssertFalse(args.contains { $0.contains("Bash(") })
    }

    func testSystemPromptDirectsReadOnlyAgentSearch() {
        let prompt = ClaudeQueryRunner.wikiSystemPromptAddendum

        XCTAssertTrue(prompt.contains("read-only"), "should identify the query mode as read-only")
        XCTAssertTrue(prompt.contains("do not use subagents"), "should prevent nested task runners")
        XCTAssertTrue(prompt.contains("Task"), "should explicitly forbid Task")
        XCTAssertTrue(prompt.contains("Grep"), "should direct Claude to search with Grep")
        XCTAssertTrue(prompt.contains("Read"), "should direct Claude to read evidence")
        XCTAssertTrue(prompt.contains("[!abstract]- Full extracted text"), "should mention PDF text callouts")
        XCTAssertTrue(prompt.contains(#"\[\[Page Title\]\]"#), "should show escaped backlink grep")
        XCTAssertTrue(prompt.contains("[[Policy Timeline]]"), "should include a citation example")
        XCTAssertTrue(prompt.contains("wikilinks"), "should mention wikilink citations")
        XCTAssertTrue(prompt.contains("markdown tables"), "should preserve rich Markdown guidance")
        XCTAssertTrue(prompt.contains("Mermaid"), "should preserve diagram guidance")
        XCTAssertTrue(prompt.contains("callouts"), "should preserve Obsidian callout guidance")
        XCTAssertTrue(prompt.contains("Do not claim to save files"), "should forbid false save/update claims")
        XCTAssertFalse(prompt.contains("wiki-context"), "query mode should not rely on pre-fetched context")
        XCTAssertTrue(prompt.contains("Do not edit"), "should forbid mutations")
    }
}
