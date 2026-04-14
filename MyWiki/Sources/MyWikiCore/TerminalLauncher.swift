import Foundation

public struct TerminalLaunchError: Error, LocalizedError, Equatable {
    public let message: String

    public init(_ message: String) {
        self.message = message
    }

    public var errorDescription: String? {
        message
    }
}

public enum TerminalLauncher {
    public static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Build the contents of a `.command` shell script that `cd`s into `directory`,
    /// optionally runs `runningCommand`, and then hands control to an interactive login
    /// shell so the Terminal window stays open after the command finishes.
    public static func buildLaunchScript(directory: URL, runningCommand: String?) -> String {
        var lines: [String] = ["#!/bin/zsh", "set -o pipefail"]
        lines.append("cd " + shellQuote(directory.path))
        // Put the app-managed compile shim and common Claude install locations on PATH.
        lines.append("export PATH=\"$PWD/.compile/mywiki-bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:$PATH\"")
        lines.append("clear")
        if let runningCommand, !runningCommand.isEmpty {
            lines.append(runningCommand)
        }
        lines.append("exec $SHELL -l")
        return lines.joined(separator: "\n") + "\n"
    }

    private static func launchScriptsDirectory() -> URL {
        let tmp = FileManager.default.temporaryDirectory
            .appending(path: "MyWiki-launchers", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        return tmp
    }

    @discardableResult
    public static func writeLaunchScript(directory: URL, runningCommand: String?) throws -> URL {
        let dir = launchScriptsDirectory()
        let scriptURL = dir.appending(
            path: "launch-\(UUID().uuidString.prefix(8)).command",
            directoryHint: .notDirectory
        )
        let script = buildLaunchScript(directory: directory, runningCommand: runningCommand)
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: scriptURL.path
        )
        return scriptURL
    }

    /// Launches Terminal.app with a `.command` script that cd's to the workspace and runs
    /// the given command. Uses `/usr/bin/open -a Terminal <script>` instead of AppleScript
    /// to avoid the macOS Automation permission prompt.
    @discardableResult
    public static func launch(directory: URL, runningCommand: String? = nil) throws -> URL {
        let scriptURL = try writeLaunchScript(directory: directory, runningCommand: runningCommand)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-a", "Terminal", scriptURL.path]

        let errorPipe = Pipe()
        process.standardError = errorPipe

        do {
            try process.run()
        } catch {
            throw TerminalLaunchError("Failed to spawn /usr/bin/open: \(error.localizedDescription)")
        }
        process.waitUntilExit()

        if process.terminationStatus != 0 {
            let errData = errorPipe.fileHandleForReading.readDataToEndOfFile()
            let errText = String(decoding: errData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            let hint = errText.isEmpty
                ? "open exited with code \(process.terminationStatus)"
                : "open exited with code \(process.terminationStatus): \(errText)"
            throw TerminalLaunchError("Terminal launch failed: \(hint)")
        }

        return scriptURL
    }
}
