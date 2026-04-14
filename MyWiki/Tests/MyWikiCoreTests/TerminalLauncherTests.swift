import Foundation
import XCTest
@testable import MyWikiCore

final class TerminalLauncherTests: XCTestCase {
    func testShellQuoteWrapsPlainValue() {
        XCTAssertEqual(TerminalLauncher.shellQuote("/tmp/wiki"), "'/tmp/wiki'")
    }

    func testShellQuoteEscapesEmbeddedSingleQuote() {
        XCTAssertEqual(
            TerminalLauncher.shellQuote("/tmp/walker's wiki"),
            "'/tmp/walker'\\''s wiki'"
        )
    }

    func testBuildLaunchScriptIncludesCdAndRunCommand() {
        let url = URL(fileURLWithPath: "/tmp/wiki")
        let script = TerminalLauncher.buildLaunchScript(
            directory: url,
            runningCommand: "claude \"/ingest raw/sample.md\""
        )
        XCTAssertTrue(script.hasPrefix("#!/bin/zsh"), "script was: \(script)")
        XCTAssertTrue(script.contains("cd '/tmp/wiki'"), "script was: \(script)")
        XCTAssertTrue(script.contains("claude \"/ingest raw/sample.md\""), "script was: \(script)")
        XCTAssertTrue(script.contains("exec $SHELL -l"), "script was: \(script)")
    }

    func testBuildLaunchScriptOmitsCommandLineWhenNil() {
        let url = URL(fileURLWithPath: "/tmp/wiki")
        let script = TerminalLauncher.buildLaunchScript(
            directory: url,
            runningCommand: nil
        )
        XCTAssertTrue(script.contains("cd '/tmp/wiki'"))
        // PATH export mentions claude/local but no actual `claude` invocation should appear.
        let lines = script.split(separator: "\n")
        XCTAssertFalse(lines.contains { $0 == "claude" || $0.hasPrefix("claude ") },
                      "script was: \(script)")
        XCTAssertTrue(script.contains("exec $SHELL -l"))
    }

    func testBuildLaunchScriptQuotesWorkspaceWithSingleQuote() {
        let url = URL(fileURLWithPath: "/tmp/walker's wiki")
        let script = TerminalLauncher.buildLaunchScript(directory: url, runningCommand: nil)
        XCTAssertTrue(script.contains("cd '/tmp/walker'\\''s wiki'"), "script was: \(script)")
    }

    func testBuildLaunchScriptPrependsClaudePathEntries() {
        let url = URL(fileURLWithPath: "/tmp/wiki")
        let script = TerminalLauncher.buildLaunchScript(directory: url, runningCommand: nil)
        XCTAssertTrue(script.contains("$PWD/.compile/mywiki-bin"))
        XCTAssertTrue(script.contains("$HOME/.claude/local"))
        XCTAssertTrue(script.contains("/opt/homebrew/bin"))
    }

    func testWriteLaunchScriptProducesExecutableCommandFile() throws {
        let url = URL(fileURLWithPath: "/tmp/wiki")
        let scriptURL = try TerminalLauncher.writeLaunchScript(
            directory: url,
            runningCommand: "claude"
        )
        defer { try? FileManager.default.removeItem(at: scriptURL) }

        XCTAssertEqual(scriptURL.pathExtension, "command")
        let attributes = try FileManager.default.attributesOfItem(atPath: scriptURL.path)
        let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0
        XCTAssertEqual(permissions & 0o777, 0o755)
        let contents = try String(contentsOf: scriptURL, encoding: .utf8)
        XCTAssertTrue(contents.contains("cd '/tmp/wiki'"))
        XCTAssertTrue(contents.contains("claude"))
    }
}
