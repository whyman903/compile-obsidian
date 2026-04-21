import Foundation

public enum ClaudeQueryEvent: Sendable {
    case assistantText(String)
    case toolCall(name: String)
    case toolResult(preview: String)
    case finished(text: String, costUSD: Double?, durationMs: Int?, permissionDenials: [String], sessionID: String?)
    case failed(message: String)
}

public protocol ClaudeQueryRunning: AnyObject, Sendable {
    func runQuery(
        prompt: String,
        workspaceURL: URL,
        resumeSessionID: String?,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws
}

public struct ClaudeQueryResumeUnavailableError: Error, LocalizedError, Equatable, Sendable {
    public let message: String

    public var errorDescription: String? {
        message
    }
}

public final class ClaudeQueryRunner: ClaudeQueryRunning, @unchecked Sendable {
    private let logger: AppLogger
    private let executableProvider: @Sendable () -> URL
    private let optionSupportLock = NSLock()
    private var optionSupportCache: [String: Bool] = [:]

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
        You are a read-only researcher for the user's personal Obsidian wiki. Work directly: \
        do not use subagents, Task, or interactive question tools. Search with Grep and Glob, \
        read evidence with Read, follow [[wikilinks]], and use backlink grep when useful. \
        PDF source notes may contain collapsed `> [!abstract]- Full extracted text` callouts; \
        search and read those callouts when PDF detail matters. For backlink search, Grep \
        the escaped pattern `\\[\\[Page Title\\]\\]`.

        Prefer useful, rich Markdown over a plain paragraph:
        - Use short sections when the answer has multiple parts.
        - Use markdown tables for comparisons, tradeoffs, timelines, or grouped evidence.
        - Use Mermaid diagrams for compact process flows, causal chains, or relationship maps.
        - Use Obsidian callouts for notable caveats, open questions, or recommendations.

        Every factual claim must be grounded in the wiki and cited inline with an Obsidian \
        [[Page Title]] wikilink to the page you read it from, for example: The policy shift \
        was driven by X ([[Policy Timeline]]). This is a hard requirement: a response with \
        no wikilinks is a failure. Do not answer from prior knowledge or training data. If \
        the wiki does not contain evidence for part of the question, say so explicitly for \
        that part and briefly list what you searched — do not fill the gap with unsourced \
        claims. Do not claim to save files or update the wiki from this query response. \
        Do not edit, write, ingest, refresh, render, save files, or otherwise mutate the workspace.
        """

    public func runQuery(
        prompt: String,
        workspaceURL: URL,
        resumeSessionID: String? = nil,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws {
        let queryStart = Date()
        logger.log("ClaudeQueryRunner: query starting")
        let executable = executableProvider()
        let process = Process()
        process.executableURL = executable
        var claudeArgs = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--settings", #"{"disableAllHooks":true}"#,
            "--allowedTools", "Read,Grep,Glob,LS",
            "--disallowedTools", "Task,AskUserQuestion,Monitor,Bash,Edit,Write,NotebookEdit,MultiEdit,WebSearch,WebFetch",
            "--model", "sonnet",
            "--append-system-prompt", Self.wikiSystemPromptAddendum,
            prompt,
        ]
        if executableSupportsOption(executable, option: "--exclude-dynamic-system-prompt-sections") {
            let insertIndex = claudeArgs.firstIndex(of: "--append-system-prompt") ?? max(claudeArgs.count - 1, 0)
            claudeArgs.insert("--exclude-dynamic-system-prompt-sections", at: insertIndex)
        }
        if let resumeSessionID, !resumeSessionID.isEmpty {
            claudeArgs.insert(contentsOf: ["--resume", resumeSessionID], at: 1)
        }
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

        let stdoutTask = Task { [logger] () -> StdoutSummary in
            var summary = StdoutSummary()
            do {
                for try await line in stdoutPipe.fileHandleForReading.bytes.lines {
                    guard !line.isEmpty else { continue }
                    let result = await Self.parseStreamLine(line: line, logger: logger, onEvent: onEvent)
                    switch result {
                    case .parsed(let lineSummary):
                        summary.sawAssistantText = summary.sawAssistantText || lineSummary.emittedAssistantText
                        summary.sawFinished = summary.sawFinished || lineSummary.emittedFinished
                        if let text = lineSummary.textOnlyAssistantText {
                            summary.lastTextOnlyAssistantText = text
                        }
                        if let preview = lineSummary.toolResultPreview {
                            summary.lastToolResultPreview = preview
                        }
                    case .unparsed:
                        summary.appendUnparsedLine(line)
                    }
                }
            } catch {
                logger.log("ClaudeQueryRunner stdout read error: \(error)")
            }
            return summary
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
        let stdoutSummary = await stdoutTask.value
        let stdoutTail = stdoutSummary.unparsedTail.trimmingCharacters(in: .whitespacesAndNewlines)
        let stderrTail = await stderrTask.value.trimmingCharacters(in: .whitespacesAndNewlines)

        if process.terminationStatus != 0 {
            let message: String
            if !stderrTail.isEmpty {
                message = stderrTail
            } else if !stdoutTail.isEmpty {
                message = stdoutTail
            } else {
                message = "claude -p exited with code \(process.terminationStatus)"
            }
            logger.log("ClaudeQueryRunner failure: \(message)")
            if resumeSessionID != nil, Self.isResumeSessionUnavailable(message) {
                throw ClaudeQueryResumeUnavailableError(message: message)
            }
            await onEvent(.failed(message: message))
        } else if !stdoutSummary.sawFinished {
            if let recoveredText = Self.recoverAssistantText(fromUnparsedTail: stdoutTail) {
                logger.log("ClaudeQueryRunner recovered final assistant text from incomplete stream JSON")
                await onEvent(.assistantText(recoveredText))
                await onEvent(.finished(
                    text: recoveredText,
                    costUSD: nil,
                    durationMs: nil,
                    permissionDenials: [],
                    sessionID: nil
                ))
            } else if stdoutTail.isEmpty,
                      let text = stdoutSummary.lastTextOnlyAssistantText?.trimmingCharacters(in: .whitespacesAndNewlines),
                      !text.isEmpty {
                logger.log("ClaudeQueryRunner: result event missing; completing from final text-only assistant message")
                await onEvent(.finished(
                    text: text,
                    costUSD: nil,
                    durationMs: nil,
                    permissionDenials: [],
                    sessionID: nil
                ))
            } else {
                let message = Self.emptySuccessfulRunMessage(
                    stdoutSummary: stdoutSummary,
                    stderrTail: stderrTail
                )
                logger.log("ClaudeQueryRunner empty response: \(message)")
                await onEvent(.failed(message: message))
            }
        } else if !stdoutTail.isEmpty {
            logger.log("ClaudeQueryRunner stdout (ignored): \(stdoutTail)")
        } else if !stderrTail.isEmpty {
            logger.log("ClaudeQueryRunner stderr (ignored): \(stderrTail)")
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

    private func executableSupportsOption(_ executable: URL, option: String) -> Bool {
        let cacheKey = "\(executable.path)\u{0}\(option)"
        optionSupportLock.lock()
        if let cached = optionSupportCache[cacheKey] {
            optionSupportLock.unlock()
            return cached
        }
        optionSupportLock.unlock()

        let process = Process()
        process.executableURL = executable
        process.arguments = executable.lastPathComponent == "env"
            ? ["claude", "--help"]
            : ["--help"]

        // Match the PATH augmentation `runQuery` applies so GUI launches — which
        // normally inherit only `/usr/bin:/bin:/usr/sbin:/sbin` from launchd —
        // can still locate `claude` via `/usr/bin/env`.
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

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        let result: Bool
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            result = String(decoding: data, as: UTF8.self).contains(option)
        } catch {
            result = false
        }

        optionSupportLock.lock()
        optionSupportCache[cacheKey] = result
        optionSupportLock.unlock()
        return result
    }

    private struct StdoutSummary: Sendable {
        var unparsedBuffer = Data()
        var sawAssistantText = false
        var sawFinished = false
        var lastTextOnlyAssistantText: String?
        var lastToolResultPreview: String?

        var unparsedTail: String {
            String(decoding: unparsedBuffer, as: UTF8.self)
        }

        mutating func appendUnparsedLine(_ line: String) {
            unparsedBuffer.append(contentsOf: line.utf8)
            unparsedBuffer.append(0x0A)
            if unparsedBuffer.count > Self.maxUnparsedBufferBytes {
                unparsedBuffer.removeFirst(unparsedBuffer.count - Self.maxUnparsedBufferBytes)
            }
        }

        private static let maxUnparsedBufferBytes = 512 * 1024
    }

    private struct StreamLineSummary: Sendable {
        var emittedAssistantText = false
        var emittedFinished = false
        var textOnlyAssistantText: String?
        var toolResultPreview: String?
    }

    private enum StreamLineParseResult: Sendable {
        case parsed(StreamLineSummary)
        case unparsed
    }

    private static func parseStreamLine(
        line: String,
        logger: AppLogger,
        onEvent: @Sendable (ClaudeQueryEvent) async -> Void
    ) async -> StreamLineParseResult {
        guard let data = line.data(using: .utf8) else { return .unparsed }
        guard let any = try? JSONSerialization.jsonObject(with: data),
              let json = any as? [String: Any],
              let type = json["type"] as? String else {
            return .unparsed
        }
        var summary = StreamLineSummary()
        switch type {
        case "assistant":
            guard let message = json["message"] as? [String: Any],
                  let content = message["content"] as? [[String: Any]] else {
                return .parsed(summary)
            }
            let texts = content.compactMap { block -> String? in
                guard (block["type"] as? String) == "text" else { return nil }
                return block["text"] as? String
            }
            let toolNames = content.compactMap { block -> String? in
                guard (block["type"] as? String) == "tool_use" else { return nil }
                return block["name"] as? String
            }
            let joined = texts.joined()
            if !joined.isEmpty && toolNames.isEmpty {
                await onEvent(.assistantText(joined))
                summary.emittedAssistantText = true
                summary.textOnlyAssistantText = joined
            }
            for name in toolNames {
                await onEvent(.toolCall(name: name))
            }
        case "user":
            guard let message = json["message"] as? [String: Any],
                  let content = message["content"] as? [[String: Any]] else {
                return .parsed(summary)
            }
            for block in content {
                guard (block["type"] as? String) == "tool_result" else { continue }
                if let textContent = block["content"] as? String {
                    let preview = String(textContent.prefix(200))
                    summary.toolResultPreview = preview
                    await onEvent(.toolResult(preview: preview))
                } else if let blocks = block["content"] as? [[String: Any]] {
                    let combined = blocks.compactMap { $0["text"] as? String }.joined()
                    if !combined.isEmpty {
                        let preview = String(combined.prefix(200))
                        summary.toolResultPreview = preview
                        await onEvent(.toolResult(preview: preview))
                    }
                }
            }
        case "result":
            let text = (json["result"] as? String) ?? ""
            let cost = json["total_cost_usd"] as? Double
            let duration = json["duration_ms"] as? Int
            logger.log("ClaudeQueryRunner result: text=\(text.prefix(80))… cost=\(cost ?? -1) duration=\(duration ?? -1)")
            let sessionID = json["session_id"] as? String
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
            await onEvent(.finished(
                text: text,
                costUSD: cost,
                durationMs: duration,
                permissionDenials: denials,
                sessionID: sessionID
            ))
            summary.emittedFinished = true
        case "system", "rate_limit_event":
            break
        default:
            logger.log("ClaudeQueryRunner unknown event: \(type)")
        }
        return .parsed(summary)
    }

    private static func recoverAssistantText(fromUnparsedTail tail: String) -> String? {
        let candidates = tail
            .split(separator: "\n", omittingEmptySubsequences: false)
            .compactMap { rawLine -> String? in
                let line = String(rawLine)
                guard line.contains(#""type":"assistant""#),
                      !line.contains(#""type":"tool_use""#) else {
                    return nil
                }
                return recoverTextBlocks(fromPartialAssistantLine: line)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
            .filter { !$0.isEmpty }

        return candidates.last
    }

    private static func recoverTextBlocks(fromPartialAssistantLine line: String) -> String? {
        var texts: [String] = []
        var cursor = line.startIndex
        let typeMarker = #""type":"text""#
        let textKey = #""text""#

        while let typeRange = line.range(of: typeMarker, range: cursor..<line.endIndex) {
            guard let textKeyRange = line.range(of: textKey, range: typeRange.upperBound..<line.endIndex),
                  let colon = line[textKeyRange.upperBound..<line.endIndex].firstIndex(of: ":"),
                  let quote = line[line.index(after: colon)..<line.endIndex].firstIndex(of: "\""),
                  let parsed = parseJSONStringLiteral(in: line, startingAt: quote) else {
                break
            }
            texts.append(parsed.value)
            cursor = parsed.end
        }

        guard !texts.isEmpty else {
            return nil
        }
        return texts.joined()
    }

    private static func parseJSONStringLiteral(
        in text: String,
        startingAt start: String.Index
    ) -> (value: String, end: String.Index)? {
        guard start < text.endIndex, text[start] == "\"" else {
            return nil
        }

        var index = text.index(after: start)
        var isEscaped = false
        while index < text.endIndex {
            let character = text[index]
            if isEscaped {
                isEscaped = false
            } else if character == "\\" {
                isEscaped = true
            } else if character == "\"" {
                let literal = String(text[start...index])
                guard let data = literal.data(using: .utf8),
                      let decoded = try? JSONDecoder().decode(String.self, from: data) else {
                    return nil
                }
                return (decoded, text.index(after: index))
            }
            index = text.index(after: index)
        }

        return nil
    }

    private static func emptySuccessfulRunMessage(
        stdoutSummary: StdoutSummary,
        stderrTail: String
    ) -> String {
        let stdoutTail = stdoutSummary.unparsedTail.trimmingCharacters(in: .whitespacesAndNewlines)
        if !stdoutTail.isEmpty {
            return """
            Claude exited before producing an answer. The last stream output was incomplete or not valid JSON:

            \(diagnosticPreview(stdoutTail))
            """
        }
        if let preview = stdoutSummary.lastToolResultPreview?.trimmingCharacters(in: .whitespacesAndNewlines),
           !preview.isEmpty {
            return """
            Claude exited after a tool result without producing an answer. Last tool output:

            \(diagnosticPreview(preview))
            """
        }
        if !stderrTail.isEmpty {
            return """
            Claude exited before producing an answer. stderr:

            \(diagnosticPreview(stderrTail))
            """
        }
        return "Claude exited before producing an answer."
    }

    private static func diagnosticPreview(_ text: String, limit: Int = 800) -> String {
        let normalized = text
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\\n", with: "\n")
            .replacingOccurrences(of: "\\t", with: "\t")
        guard normalized.count > limit else { return normalized }
        return String(normalized.prefix(limit)) + "\n..."
    }

    private static func isResumeSessionUnavailable(_ message: String) -> Bool {
        let normalized = message.lowercased()
        guard normalized.contains("session") || normalized.contains("conversation") else {
            return false
        }
        return normalized.contains("not found")
            || normalized.contains("no conversation")
            || normalized.contains("could not find")
            || normalized.contains("cannot find")
            || normalized.contains("does not exist")
            || normalized.contains("expired")
            || normalized.contains("no such")
            || normalized.contains("unavailable")
    }
}
