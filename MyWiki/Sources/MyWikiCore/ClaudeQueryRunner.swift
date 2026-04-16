import Foundation

public enum ClaudeQueryEvent: Sendable {
    case assistantText(String)
    case toolCall(name: String)
    case toolResult(preview: String)
    case finished(text: String, costUSD: Double?, durationMs: Int?, permissionDenials: [String])
    case failed(message: String)
}

public protocol ClaudeQueryRunning: AnyObject, Sendable {
    func runQuery(
        prompt: String,
        workspaceURL: URL,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws
}

public final class ClaudeQueryRunner: ClaudeQueryRunning, @unchecked Sendable {
    private let logger: AppLogger
    private let executableProvider: @Sendable () -> URL

    public init(
        logger: AppLogger,
        executableProvider: @escaping @Sendable () -> URL = ClaudeQueryRunner.defaultExecutable
    ) {
        self.logger = logger
        self.executableProvider = executableProvider
    }

    public static func defaultExecutable() -> URL {
        if let override = ProcessInfo.processInfo.environment["MYWIKI_CLAUDE_PATH"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return URL(fileURLWithPath: "/usr/bin/env")
    }

    public static let wikiSystemPromptAddendum = """
        You answer questions about the user's personal Obsidian wiki. Relevant wiki page \
        content is provided in <wiki-context> tags before the question. Prior conversation \
        turns may appear in <conversation-history> tags. Synthesize your answer from this \
        context. If the context does not contain enough information to answer, say so briefly.

        Cite specific pages inline with Obsidian [[Page Title]] wikilinks. \
        Do not offer to save the answer.
        """

    public func runQuery(
        prompt: String,
        workspaceURL: URL,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws {
        let queryStart = Date()
        logger.log("ClaudeQueryRunner: query starting")
        let executable = executableProvider()
        let process = Process()
        process.executableURL = executable
        let claudeArgs = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", "sonnet",
            "--effort", "medium",
            "--exclude-dynamic-system-prompt-sections",
            "--append-system-prompt", Self.wikiSystemPromptAddendum,
            prompt,
        ]
        if executable.lastPathComponent == "env" {
            process.arguments = ["claude"] + claudeArgs
        } else {
            process.arguments = claudeArgs
        }
        process.currentDirectoryURL = workspaceURL

        var env = ProcessInfo.processInfo.environment
        let homePath = FileManager.default.homeDirectoryForCurrentUser.path
        let existingPath = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = [
            workspaceURL.path + "/.compile/mywiki-bin",
            "\(homePath)/.claude/local",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            existingPath,
        ].joined(separator: ":")
        process.environment = env

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        let stdoutTask = Task { [logger] () -> Void in
            do {
                for try await line in stdoutPipe.fileHandleForReading.bytes.lines {
                    guard !line.isEmpty else { continue }
                    await Self.parseStreamLine(line: line, logger: logger, onEvent: onEvent)
                }
            } catch {
                logger.log("ClaudeQueryRunner stdout read error: \(error)")
            }
        }

        let stderrTask = Task { [logger] () -> String in
            var buffer = Data()
            do {
                for try await byte in stderrPipe.fileHandleForReading.bytes {
                    buffer.append(byte)
                    if buffer.count > 32 * 1024 {
                        buffer.removeFirst(buffer.count - 32 * 1024)
                    }
                }
            } catch {
                logger.log("ClaudeQueryRunner stderr read error: \(error)")
            }
            return String(decoding: buffer, as: UTF8.self)
        }

        try await withTaskCancellationHandler {
            try await Self.runProcess(process)
        } onCancel: { [weak process] in
            guard let process, process.isRunning else { return }
            process.terminate()
        }

        stdoutPipe.fileHandleForWriting.closeFile()
        stderrPipe.fileHandleForWriting.closeFile()
        await stdoutTask.value
        let stderrTail = await stderrTask.value.trimmingCharacters(in: .whitespacesAndNewlines)

        if process.terminationStatus != 0 {
            let message = stderrTail.isEmpty
                ? "claude -p exited with code \(process.terminationStatus)"
                : stderrTail
            logger.log("ClaudeQueryRunner failure: \(message)")
            await onEvent(.failed(message: message))
        } else if !stderrTail.isEmpty {
            logger.log("ClaudeQueryRunner stderr (ignored, non-zero exit): \(stderrTail)")
        }
        let elapsed = Date().timeIntervalSince(queryStart)
        logger.log("ClaudeQueryRunner: query finished in \(String(format: "%.1f", elapsed))s (exit \(process.terminationStatus))")
    }

    private static func runProcess(_ process: Process) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            process.terminationHandler = { _ in
                continuation.resume()
            }
            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    private static func parseStreamLine(
        line: String,
        logger: AppLogger,
        onEvent: @Sendable (ClaudeQueryEvent) async -> Void
    ) async {
        guard let data = line.data(using: .utf8) else { return }
        guard let any = try? JSONSerialization.jsonObject(with: data),
              let json = any as? [String: Any],
              let type = json["type"] as? String else {
            return
        }
        switch type {
        case "assistant":
            guard let message = json["message"] as? [String: Any],
                  let content = message["content"] as? [[String: Any]] else {
                return
            }
            let texts = content.compactMap { block -> String? in
                guard (block["type"] as? String) == "text" else { return nil }
                return block["text"] as? String
            }
            let joined = texts.joined()
            if !joined.isEmpty {
                await onEvent(.assistantText(joined))
            }
            let toolNames = content.compactMap { block -> String? in
                guard (block["type"] as? String) == "tool_use" else { return nil }
                return block["name"] as? String
            }
            for name in toolNames {
                await onEvent(.toolCall(name: name))
            }
        case "user":
            guard let message = json["message"] as? [String: Any],
                  let content = message["content"] as? [[String: Any]] else {
                return
            }
            for block in content {
                guard (block["type"] as? String) == "tool_result" else { continue }
                if let textContent = block["content"] as? String {
                    await onEvent(.toolResult(preview: String(textContent.prefix(200))))
                } else if let blocks = block["content"] as? [[String: Any]] {
                    let combined = blocks.compactMap { $0["text"] as? String }.joined()
                    if !combined.isEmpty {
                        await onEvent(.toolResult(preview: String(combined.prefix(200))))
                    }
                }
            }
        case "result":
            let text = (json["result"] as? String) ?? ""
            let cost = json["total_cost_usd"] as? Double
            let duration = json["duration_ms"] as? Int
            logger.log("ClaudeQueryRunner result: text=\(text.prefix(80))… cost=\(cost ?? -1) duration=\(duration ?? -1)")
            var denials: [String] = []
            if let array = json["permission_denials"] as? [[String: Any]] {
                for item in array {
                    if let tool = item["tool"] as? String {
                        denials.append(tool)
                    } else if let name = item["name"] as? String {
                        denials.append(name)
                    }
                }
            }
            await onEvent(.finished(text: text, costUSD: cost, durationMs: duration, permissionDenials: denials))
        case "system", "rate_limit_event":
            break
        default:
            logger.log("ClaudeQueryRunner unknown event: \(type)")
        }
    }
}
