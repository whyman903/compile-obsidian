import AppKit
import Foundation
import Observation

@MainActor
@Observable
public final class AppModel {
    public private(set) var workspace: WorkspaceInfo?
    public private(set) var recentWorkspacePaths: [String] = []
    public let feedStore: FeedStore
    public private(set) var chatMessages: [ChatMessage] = [
        ChatMessage(
            role: .assistant,
            text: "Drop a file, paste a URL, or ask a question. MyWiki opens Terminal at the wiki home and hands the work to Claude."
        )
    ]
    public var urlInput = ""
    public var chatInput = ""
    public var statusMessage = "Preparing workspace..."
    public var lastError: String?

    private let runner: CompileRunning
    private let dispatcher: IngestDispatcher
    private let logger: AppLogger
    private let defaults: UserDefaults
    private let fileManager: FileManager
    private let defaultWorkspaceName = "My Wiki"
    private let recentKey = "recentWorkspacePaths"
    private let currentWorkspaceKey = "currentWorkspacePath"
    private var didBootstrap = false

    public init(
        runner: CompileRunning? = nil,
        dispatcher: IngestDispatcher? = nil,
        logger: AppLogger = AppLogger(),
        defaults: UserDefaults = .standard,
        fileManager: FileManager = .default
    ) {
        self.logger = logger
        let resolvedRunner = runner ?? CompileRunner(logger: logger)
        self.runner = resolvedRunner
        let resolvedDispatcher = dispatcher ?? TerminalClaudeDispatcher(logger: logger)
        self.dispatcher = resolvedDispatcher
        self.feedStore = FeedStore(dispatcher: resolvedDispatcher, logger: logger)
        self.defaults = defaults
        self.fileManager = fileManager
        self.recentWorkspacePaths = defaults.stringArray(forKey: recentKey) ?? []
    }

    public func bootstrapIfNeeded() async {
        guard !didBootstrap else {
            return
        }
        didBootstrap = true
        await restoreOrCreateWorkspace()
    }

    public func enqueueFiles(_ urls: [URL]) {
        guard workspace != nil else {
            lastError = "Workspace is not ready yet."
            return
        }
        feedStore.enqueue(urls.map { IngestRequest(source: .file($0)) })
    }

    public func chooseFilesForIngest() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add Files"
        panel.message = "Choose files to ingest into MyWiki."
        guard panel.runModal() == .OK else {
            return
        }
        enqueueFiles(panel.urls)
    }

    public func enqueueCurrentURL() {
        let trimmed = urlInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        guard workspace != nil else {
            lastError = "Workspace is not ready yet."
            return
        }
        feedStore.enqueue([IngestRequest(source: .remoteURL(trimmed))])
        urlInput = ""
    }

    public func openWorkspaceInObsidian() {
        guard let workspace else {
            return
        }
        guard ObsidianOpener.openWorkspace(workspace.url) else {
            NSWorkspace.shared.activateFileViewerSelecting([workspace.url])
            lastError = "Could not open Obsidian. I revealed the workspace in Finder instead."
            return
        }
    }

    public func openFeedItem(_ item: FeedItem) {
        guard let workspace, let relativePath = item.stagedRelativePath else {
            return
        }
        let fileURL = workspace.url
            .appending(path: relativePath, directoryHint: .notDirectory)
            .standardizedFileURL
        guard fileManager.fileExists(atPath: fileURL.path) else {
            lastError = "The staged file no longer exists: \(relativePath)"
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([fileURL])
    }

    public func openWorkspaceInTerminal() {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        do {
            try TerminalLauncher.launch(directory: workspace.url, runningCommand: "claude")
        } catch {
            logger.log("Failed to open terminal: \(error)")
            lastError = "Could not open Terminal: \(error.localizedDescription)"
        }
    }

    @discardableResult
    public func askClaudeInTerminal(_ question: String) -> Bool {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return false
        }
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return false
        }
        let prompt = "/query \(trimmed)"
        let claudeCommand = FeedStore.claudeCommand(for: prompt)
        do {
            try TerminalLauncher.launch(directory: workspace.url, runningCommand: claudeCommand)
            return true
        } catch {
            logger.log("Failed to dispatch question to Terminal: \(error)")
            lastError = "Could not open Terminal: \(error.localizedDescription)"
            return false
        }
    }

    public func sendChatToClaude() {
        let question = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else {
            return
        }
        guard workspace != nil else {
            lastError = "Workspace is not ready yet."
            return
        }
        chatInput = ""
        chatMessages.append(ChatMessage(role: .user, text: question))
        if askClaudeInTerminal(question) {
            chatMessages.append(ChatMessage(
                role: .assistant,
                text: "Opened Terminal at the wiki home and sent this to Claude."
            ))
        }
    }

    public func sendChat() {
        let question = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else {
            return
        }
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }

        chatInput = ""
        chatMessages.append(ChatMessage(role: .user, text: question))

        Task {
            do {
                let hits = try await runner.search(query: question, at: workspace.url, limit: 5)
                let reply = try await buildChatReply(for: question, workspaceURL: workspace.url, hits: hits)
                chatMessages.append(reply)
            } catch {
                logger.log("Failed to answer chat prompt '\(question)': \(error)")
                chatMessages.append(ChatMessage(
                    role: .assistant,
                    text: "I hit an error while searching the wiki: \(error.localizedDescription)"
                ))
            }
        }
    }

    public func selectRecentWorkspace(_ path: String) {
        Task {
            await loadWorkspace(at: URL(fileURLWithPath: path, isDirectory: true))
        }
    }

    public func chooseOtherWorkspace() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Open Workspace"
        panel.message = "Choose an existing compile workspace."
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        Task {
            await loadWorkspace(at: url)
        }
    }

    private func restoreOrCreateWorkspace() async {
        if let storedPath = defaults.string(forKey: currentWorkspaceKey) {
            await loadWorkspace(at: URL(fileURLWithPath: storedPath, isDirectory: true), shouldFallbackToDefault: true)
            if workspace != nil {
                return
            }
        }
        await ensureDefaultWorkspace()
    }

    private func ensureDefaultWorkspace() async {
        let workspaceURL = defaultWorkspaceURL()
        do {
            if fileManager.fileExists(atPath: workspaceURL.path) {
                let info = try await runner.status(at: workspaceURL)
                try await runner.prepareWorkspaceForClaude(at: workspaceURL, force: false)
                setWorkspace(info)
            } else {
                let info = try await runner.initWorkspace(name: defaultWorkspaceName, at: workspaceURL)
                try await runner.prepareWorkspaceForClaude(at: workspaceURL, force: false)
                setWorkspace(info)
            }
            statusMessage = "Ready"
        } catch {
            logger.log("Failed to prepare default workspace: \(error)")
            lastError = "Failed to prepare workspace: \(error.localizedDescription)"
            statusMessage = "Workspace setup failed"
        }
    }

    private func loadWorkspace(at url: URL, shouldFallbackToDefault: Bool = false) async {
        do {
            let info = try await runner.status(at: url)
            try await runner.prepareWorkspaceForClaude(at: url, force: false)
            setWorkspace(info)
            statusMessage = "Ready"
        } catch {
            logger.log("Failed to load workspace at \(url.path): \(error)")
            lastError = "Could not open workspace: \(error.localizedDescription)"
            if shouldFallbackToDefault {
                await ensureDefaultWorkspace()
            }
        }
    }

    private func setWorkspace(_ info: WorkspaceInfo) {
        workspace = info
        statusMessage = "Ready"
        lastError = nil
        defaults.set(info.path, forKey: currentWorkspaceKey)
        rememberWorkspacePath(info.path)
        feedStore.bindWorkspace(info.url)
    }

    private func rememberWorkspacePath(_ path: String) {
        recentWorkspacePaths.removeAll { $0 == path }
        recentWorkspacePaths.insert(path, at: 0)
        recentWorkspacePaths = Array(recentWorkspacePaths.prefix(5))
        defaults.set(recentWorkspacePaths, forKey: recentKey)
    }

    private func defaultWorkspaceURL() -> URL {
        fileManager.homeDirectoryForCurrentUser
            .appending(path: "wiki", directoryHint: .isDirectory)
    }

    private func buildChatReply(for question: String, workspaceURL: URL, hits: [SearchHit]) async throws -> ChatMessage {
        guard !hits.isEmpty else {
            return ChatMessage(
                role: .assistant,
                text: "I couldn't find matching pages for \"\(question)\". Open Terminal with Claude for a deeper answer."
            )
        }

        var lines: [String] = []
        lines.append("Top matches from the local index:")
        lines.append("")
        for hit in hits.prefix(3) {
            let page = try? await runner.page(locator: hit.relativePath, at: workspaceURL)
            let summary = hit.summary.isEmpty ? (page?.summary ?? hit.snippet) : hit.summary
            lines.append("- \(hit.title): \(summary)")
            if let body = page?.body, let excerpt = firstExcerpt(from: body), !excerpt.isEmpty {
                lines.append("  \(excerpt)")
            } else if !hit.snippet.isEmpty {
                lines.append("  \(hit.snippet)")
            }
        }
        lines.append("")
        lines.append("Tap \"Ask Claude in Terminal\" to get a synthesized answer.")
        return ChatMessage(role: .assistant, text: lines.joined(separator: "\n"), references: Array(hits.prefix(5)))
    }

    private func firstExcerpt(from body: String) -> String? {
        let paragraphs = body
            .components(separatedBy: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !$0.hasPrefix("#") && !$0.hasPrefix("```") }
        guard let first = paragraphs.first else {
            return nil
        }
        return String(first.prefix(240))
    }
}
