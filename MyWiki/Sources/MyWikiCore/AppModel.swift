import AppKit
import Foundation
import Observation

@MainActor
@Observable
public final class AppModel {
    public private(set) var workspace: WorkspaceInfo?
    public private(set) var recentWorkspacePaths: [String] = []
    public let feedStore: FeedStore
    public let querySession = QuerySession()
    public var lastError: String?
    public var statusMessage = "Preparing workspace..."
    public var launcherToast: String?

    private let runner: CompileRunning
    private let dispatcher: IngestDispatcher
    private let queryRunner: ClaudeQueryRunning
    private let logger: AppLogger
    private let defaults: UserDefaults
    private let fileManager: FileManager
    private let openWorkspaceHandler: @MainActor (URL) -> ObsidianOpener.Result
    private let openNoteHandler: @MainActor (String, URL) -> ObsidianOpener.Result
    private let openGraphHandler: @MainActor (URL) -> ObsidianOpener.Result
    private let defaultWorkspaceName = "Commonplace"
    private let recentKey = "recentWorkspacePaths"
    private let currentWorkspaceKey = "currentWorkspacePath"
    private var didBootstrap = false
    private var toastClearTask: Task<Void, Never>?
    private var activeQueryTask: Task<Void, Never>?

    public init(
        runner: CompileRunning? = nil,
        dispatcher: IngestDispatcher? = nil,
        queryRunner: ClaudeQueryRunning? = nil,
        logger: AppLogger = AppLogger(),
        defaults: UserDefaults = .standard,
        fileManager: FileManager = .default,
        openWorkspaceHandler: @escaping @MainActor (URL) -> ObsidianOpener.Result = ObsidianOpener.openWorkspace,
        openNoteHandler: @escaping @MainActor (String, URL) -> ObsidianOpener.Result = ObsidianOpener.openNote,
        openGraphHandler: @escaping @MainActor (URL) -> ObsidianOpener.Result = ObsidianOpener.openGraph
    ) {
        self.logger = logger
        let resolvedRunner = runner ?? CompileRunner(logger: logger)
        self.runner = resolvedRunner
        let resolvedDispatcher = dispatcher ?? TerminalClaudeDispatcher(logger: logger)
        self.dispatcher = resolvedDispatcher
        self.queryRunner = queryRunner ?? ClaudeQueryRunner(logger: logger)
        self.feedStore = FeedStore(dispatcher: resolvedDispatcher, logger: logger)
        self.defaults = defaults
        self.fileManager = fileManager
        self.openWorkspaceHandler = openWorkspaceHandler
        self.openNoteHandler = openNoteHandler
        self.openGraphHandler = openGraphHandler
        self.recentWorkspacePaths = defaults.stringArray(forKey: recentKey) ?? []
    }

    public func bootstrapIfNeeded() async {
        guard !didBootstrap else {
            return
        }
        didBootstrap = true
        await restoreOrCreateWorkspace()
    }

    // MARK: - Compose + dispatch

    /// Stage any dropped files and dispatch a draft Claude session. The prompt is
    /// composed from the dropped files (if any) and free-form text (if any) and
    /// lands on the clipboard — nothing is auto-submitted.
    public func launchDraftSession(files: [URL], text: String) {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }

        let trimmedText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !files.isEmpty || !trimmedText.isEmpty else {
            return
        }

        var requests: [IngestRequest] = []
        var remainingText = trimmedText
        if files.isEmpty {
            if let urlRequest = urlOnlyRequest(from: trimmedText) {
                requests.append(urlRequest)
                remainingText = ""
            } else {
                requests.append(IngestRequest(source: .query(trimmedText)))
                remainingText = ""
            }
        } else {
            for file in files {
                requests.append(IngestRequest(source: .file(file)))
            }
        }

        feedStore.enqueue(requests, trailingText: remainingText, workspaceURL: workspace.url)
        flashToast("Sent to Claude — check Terminal")
    }

    /// Run a plain-text question through `claude -p --output-format stream-json`
    /// and stream the answer into the popover via `querySession`. No Terminal window,
    /// no manual paste — the response appears in-app with tappable `[[wikilinks]]`.
    public func sendQuery(_ question: String) {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        activeQueryTask?.cancel()
        querySession.start(question: trimmed)

        let workspaceURL = workspace.url
        let session = querySession
        let claudeRunner = queryRunner
        let compileRunner = runner
        let log = logger

        activeQueryTask = Task { [weak self] in
            log.log("sendQuery: starting query — \"\(trimmed.prefix(60))\"")

            // Phase 1: Pre-fetch wiki context
            var wikiContext = ""
            do {
                await MainActor.run { session.updateStatusDetail("Searching wiki…") }
                let hits = try await compileRunner.search(query: trimmed, at: workspaceURL, limit: 3)
                log.log("sendQuery: search returned \(hits.count) hits")
                if !hits.isEmpty {
                    await MainActor.run { session.updateStatusDetail("Reading pages…") }
                    var pages: [WikiPage] = []
                    for hit in hits {
                        if let page = try? await compileRunner.page(locator: hit.relativePath, at: workspaceURL) {
                            pages.append(page)
                        }
                    }
                    wikiContext = Self.assembleWikiContext(pages)
                    log.log("sendQuery: assembled context from \(pages.count) pages (\(wikiContext.count) chars)")
                }
            } catch is CancellationError {
                await MainActor.run { session.cancel() }
                await MainActor.run { [weak self] in self?.activeQueryTask = nil }
                return
            } catch {
                log.log("Wiki search failed, proceeding without context: \(error)")
            }

            // Phase 2: Ask Claude with pre-fetched context
            await MainActor.run { session.updateStatusDetail("Asking Claude…") }
            let prompt = wikiContext.isEmpty ? trimmed : "\(wikiContext)\n\n\(trimmed)"

            do {
                try await claudeRunner.runQuery(
                    prompt: prompt,
                    workspaceURL: workspaceURL,
                    onEvent: { event in
                        await MainActor.run {
                            switch event {
                            case .assistantText(let t):
                                log.log("sendQuery: got assistantText (\(t.count) chars)")
                            case .finished(let t, let c, _, _):
                                log.log("sendQuery: got finished — text=\(t.count) chars, cost=\(c ?? -1)")
                            case .failed(let m):
                                log.log("sendQuery: got failed — \(m)")
                            default:
                                break
                            }
                            session.handle(event)
                        }
                    }
                )
                await MainActor.run {
                    log.log("sendQuery: runQuery returned — status=\(session.status), text=\(session.assistantText.count) chars")
                    if session.status == .running {
                        session.fail("Query completed without a response")
                    }
                }
            } catch is CancellationError {
                await MainActor.run { session.cancel() }
            } catch {
                await MainActor.run {
                    session.fail(error.localizedDescription)
                }
            }
            await MainActor.run { [weak self] in
                self?.activeQueryTask = nil
            }
        }
    }

    public func cancelQuery() {
        activeQueryTask?.cancel()
        activeQueryTask = nil
        querySession.cancel()
    }

    public func dismissQueryResponse() {
        querySession.clear()
    }

    /// Open a `[[wikilink]]` reference in Obsidian. Path-style targets open directly,
    /// and bare titles are first resolved through the sidecar so we can open the
    /// exact file path instead of relying on ambiguous vault-name routing.
    public func openWikiPage(target: String) {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        let trimmed = target.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }

        if trimmed.contains("/") || trimmed.hasSuffix(".md") {
            let relative = trimmed.hasSuffix(".md") ? trimmed : trimmed + ".md"
            let candidate = workspace.url
                .appending(path: relative, directoryHint: .notDirectory)
                .standardizedFileURL
            if fileManager.fileExists(atPath: candidate.path) {
                _ = openNoteHandler(relative, workspace.url)
                return
            }
        }
        let workspaceURL = workspace.url
        let runner = self.runner
        Task { [weak self] in
            do {
                let page = try await runner.page(locator: trimmed, at: workspaceURL)
                await MainActor.run {
                    guard let self else { return }
                    let result = self.openNoteHandler(page.relativePath, workspaceURL)
                    if case .failed(let message) = result {
                        self.lastError = "Obsidian refused to open: \(message)"
                    } else if case .vaultMissing = result {
                        self.lastError = "The wiki page could not be found at \(page.relativePath)."
                    }
                }
            } catch {
                await MainActor.run {
                    self?.lastError = "Could not resolve wiki page '\(trimmed)': \(error.localizedDescription)"
                }
            }
        }
    }

    public func launchBareClaude() {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        do {
            try TerminalLauncher.launch(directory: workspace.url, runningCommand: "claude")
            flashToast("Claude launched in Terminal")
        } catch {
            logger.log("Failed to open terminal: \(error)")
            lastError = "Could not open Terminal: \(error.localizedDescription)"
        }
    }

    public func openWorkspaceInObsidian() {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        switch openWorkspaceHandler(workspace.url) {
        case .opened:
            flashToast("Opened in Obsidian")
        case .notInstalled:
            lastError = "Obsidian is not installed. Install it from obsidian.md."
        case .vaultMissing:
            lastError = "The workspace folder no longer exists."
        case .failed(let message):
            lastError = "Obsidian refused to open: \(message)"
        }
    }

    public func openObsidianGraph() {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        switch openGraphHandler(workspace.url) {
        case .opened:
            flashToast("Press ⌘G inside Obsidian if graph doesn't appear")
        case .notInstalled:
            lastError = "Obsidian is not installed. Install it from obsidian.md."
        case .vaultMissing:
            lastError = "The workspace folder no longer exists."
        case .failed(let message):
            lastError = "Obsidian refused to open: \(message)"
        }
    }

    public func chooseFilesForIngest() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add Files"
        panel.message = "Choose files to hand off to Claude."
        panel.level = .modalPanel
        guard panel.runModal() == .OK else {
            return
        }
        launchDraftSession(files: panel.urls, text: "")
    }

    public func revealWorkspaceInFinder() {
        guard let workspace else {
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([workspace.url])
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
        panel.level = .modalPanel
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        Task {
            await loadWorkspace(at: url)
        }
    }

    // MARK: - Workspace bootstrap

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
            if fileManager.fileExists(atPath: workspaceURL.appending(path: ".compile/config.yaml").path) {
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

    private static func assembleWikiContext(_ pages: [WikiPage]) -> String {
        guard !pages.isEmpty else { return "" }
        var sections: [String] = []
        for page in pages {
            var section = "## \(page.title)"
            if let summary = page.summary, !summary.isEmpty {
                section += "\n\(summary)"
            }
            if let body = page.body, !body.isEmpty {
                let truncated = String(body.prefix(2000))
                section += "\n\n\(truncated)"
                if body.count > 2000 {
                    section += "\n[truncated]"
                }
            }
            sections.append(section)
        }
        return "<wiki-context>\n\(sections.joined(separator: "\n\n"))\n</wiki-context>"
    }

    private func urlOnlyRequest(from text: String) -> IngestRequest? {
        guard text.lowercased().hasPrefix("http://") || text.lowercased().hasPrefix("https://") else {
            return nil
        }
        let tokens = text.split(whereSeparator: { $0.isWhitespace })
        guard tokens.count == 1 else {
            return nil
        }
        return IngestRequest(source: .remoteURL(String(tokens[0])))
    }

    private func flashToast(_ message: String) {
        launcherToast = message
        toastClearTask?.cancel()
        toastClearTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_400_000_000)
            if !Task.isCancelled {
                await MainActor.run {
                    self?.launcherToast = nil
                }
            }
        }
    }
}
