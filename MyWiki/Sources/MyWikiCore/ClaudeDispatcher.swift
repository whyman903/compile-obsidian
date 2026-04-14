import Foundation

@MainActor
public final class TerminalClaudeDispatcher: IngestDispatcher {
    private let logger: AppLogger

    public init(logger: AppLogger) {
        self.logger = logger
    }

    public func dispatch(prompt: String, workspaceURL: URL) throws {
        let claudeCommand = FeedStore.claudeCommand(for: prompt)
        logger.log("Launching Terminal at \(workspaceURL.path) with: \(claudeCommand)")
        try TerminalLauncher.launch(directory: workspaceURL, runningCommand: claudeCommand)
    }
}

public struct ClaudeChatResponse: Equatable, Sendable {
    public let text: String
    public let stderrTail: String?

    public init(text: String, stderrTail: String? = nil) {
        self.text = text
        self.stderrTail = stderrTail
    }
}

public protocol ClaudeRunning: Sendable {
    func runPrompt(
        _ prompt: String,
        workspaceURL: URL
    ) async throws -> ClaudeChatResponse
}

public final class ClaudeRunner: ClaudeRunning, @unchecked Sendable {
    private let logger: AppLogger
    private let executableProvider: @Sendable () -> URL

    public init(
        logger: AppLogger,
        executableProvider: @escaping @Sendable () -> URL = ClaudeRunner.defaultExecutableURL
    ) {
        self.logger = logger
        self.executableProvider = executableProvider
    }

    public static func defaultExecutableURL() -> URL {
        if let override = ProcessInfo.processInfo.environment["MYWIKI_CLAUDE_PATH"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return URL(fileURLWithPath: "/usr/bin/env")
    }

    public func runPrompt(_ prompt: String, workspaceURL: URL) async throws -> ClaudeChatResponse {
        let process = Process()
        let executable = executableProvider()
        process.executableURL = executable
        if executable.lastPathComponent == "env" {
            process.arguments = ["claude", "-p", prompt]
        } else {
            process.arguments = ["-p", prompt]
        }
        process.currentDirectoryURL = workspaceURL

        var env = ProcessInfo.processInfo.environment
        let homePath = FileManager.default.homeDirectoryForCurrentUser.path
        let existingPath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = [
            "\(homePath)/.claude/local",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            existingPath,
        ].joined(separator: ":")
        process.environment = env

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        try process.run()
        process.waitUntilExit()

        let stdoutData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        let stderrData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
        let stdout = String(decoding: stdoutData, as: UTF8.self)
        let stderr = String(decoding: stderrData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)

        if process.terminationStatus != 0 {
            let message = stderr.isEmpty
                ? "claude exited with code \(process.terminationStatus)"
                : stderr
            logger.log("claude -p failed (\(process.terminationStatus)): \(message)")
            throw CompileCommandError(message)
        }

        return ClaudeChatResponse(
            text: stdout.trimmingCharacters(in: .whitespacesAndNewlines),
            stderrTail: stderr.isEmpty ? nil : stderr
        )
    }
}
