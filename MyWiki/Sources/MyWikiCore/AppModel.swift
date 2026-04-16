import AppKit
import Foundation
import Observation

public enum AppTheme: String, CaseIterable, Codable, Sendable {
    case ivory
    case obsidian
    case umber

    public var displayName: String {
        switch self {
        case .ivory: return "Ivory"
        case .obsidian: return "Obsidian"
        case .umber: return "Umber"
        }
    }

    public var prefersDarkMode: Bool {
        switch self {
        case .ivory: return false
        case .obsidian, .umber: return true
        }
    }
}

public enum AppFont: String, CaseIterable, Codable, Sendable {
    case serif
    case sans
    case mono

    public var displayName: String {
        switch self {
        case .serif: return "Serif"
        case .sans: return "Sans"
        case .mono: return "Mono"
        }
    }
}

@MainActor
@Observable
public final class AppModel {
    public private(set) var workspace: WorkspaceInfo?
    public private(set) var recentWorkspacePaths: [String] = []
    public let feedStore: FeedStore
    public private(set) var querySession = QuerySession()
    public private(set) var queryHistory: [QueryHistoryRecord] = []
    public var hasActiveQuerySession: Bool {
        querySession.status != .idle || !querySession.turns.isEmpty
    }
    public var sidebarQueryHistory: [QueryHistoryRecord] {
        queryHistory.filter { $0.id != querySession.id }
    }
    public var lastError: String?
    public var statusMessage = "Preparing workspace..."
    public var launcherToast: String?
    public var showGraphPluginInstallPrompt = false
    public private(set) var isInstallingGraphPlugin = false
    public var theme: AppTheme {
        didSet { defaults.set(theme.rawValue, forKey: themeKey) }
    }
    public var font: AppFont {
        didSet { defaults.set(font.rawValue, forKey: fontKey) }
    }

    private let runner: CompileRunning
    private let dispatcher: IngestDispatcher
    private let queryRunner: ClaudeQueryRunning
    private let logger: AppLogger
    private let defaults: UserDefaults
    private let fileManager: FileManager
    private let openWorkspaceHandler: @MainActor (URL) -> ObsidianOpener.Result
    private let openNoteHandler: @MainActor (String, URL) -> ObsidianOpener.Result
    private let openGraphHandler: @MainActor (URL) -> ObsidianOpener.Result
    private let isGraphPluginInstalledHandler: @MainActor (URL) -> Bool
    private let installGraphPluginHandler: @MainActor (URL) async throws -> Void
    private let isObsidianRunningHandler: @MainActor () -> Bool
    private let defaultWorkspaceName = "Commonplace"
    private let recentKey = "recentWorkspacePaths"
    private let currentWorkspaceKey = "currentWorkspacePath"
    private let themeKey = "appTheme"
    private let fontKey = "appFont"
    private let graphPluginPromptSuppressedKeyPrefix = "graphPluginPromptSuppressed."
    private let maxQueryHistoryRecords = 50
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
        openGraphHandler: @escaping @MainActor (URL) -> ObsidianOpener.Result = ObsidianOpener.openGraph,
        isGraphPluginInstalledHandler: @escaping @MainActor (URL) -> Bool = {
            ObsidianAdvancedURIInstaller.isInstalledAndEnabled(in: $0)
        },
        installGraphPluginHandler: @escaping @MainActor (URL) async throws -> Void = {
            try await ObsidianAdvancedURIInstaller.installAndEnable(in: $0)
        },
        isObsidianRunningHandler: @escaping @MainActor () -> Bool = ObsidianOpener.isObsidianRunning
    ) {
        self.logger = logger
        let resolvedRunner = runner ?? CompileRunner(logger: logger)
        self.runner = resolvedRunner
        let resolvedDispatcher = dispatcher ?? TerminalClaudeDispatcher(logger: logger)
        self.dispatcher = resolvedDispatcher
        self.queryRunner = queryRunner ?? ClaudeQueryRunner(logger: logger)
        self.feedStore = FeedStore(dispatcher: resolvedDispatcher, logger: logger, defaults: defaults)
        self.defaults = defaults
        self.fileManager = fileManager
        self.openWorkspaceHandler = openWorkspaceHandler
        self.openNoteHandler = openNoteHandler
        self.openGraphHandler = openGraphHandler
        self.isGraphPluginInstalledHandler = isGraphPluginInstalledHandler
        self.installGraphPluginHandler = installGraphPluginHandler
        self.isObsidianRunningHandler = isObsidianRunningHandler
        self.recentWorkspacePaths = defaults.stringArray(forKey: recentKey) ?? []
        self.theme = AppTheme(rawValue: defaults.string(forKey: "appTheme") ?? "") ?? .umber
        self.font = AppFont(rawValue: defaults.string(forKey: "appFont") ?? "") ?? .serif
    }

    public var isGraphPluginInstalled: Bool {
        guard let workspace else { return false }
        return isGraphPluginInstalledHandler(workspace.url)
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
        archiveSessionIfNeeded()
        querySession = QuerySession()
        querySession.start(question: trimmed)
        let feedItemID = feedStore.recordLocalQuery(trimmed)

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
                let hits = try await compileRunner.search(query: trimmed, at: workspaceURL, limit: 10)
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
                await MainActor.run { [weak self] in
                    log.log("sendQuery: runQuery returned — status=\(session.status), text=\(session.assistantText.count) chars")
                    if session.status == .running {
                        session.fail("Query completed without a response")
                    }
                    self?.saveCurrentSession()
                }
            } catch is CancellationError {
                await MainActor.run { session.cancel() }
            } catch {
                await MainActor.run {
                    session.fail(error.localizedDescription)
                    if let feedItemID {
                        self?.feedStore.markFailed(id: feedItemID, message: error.localizedDescription)
                    }
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
        activeQueryTask?.cancel()
        activeQueryTask = nil
        querySession = QuerySession()
    }

    /// Send a follow-up question in the current conversation. Re-searches the wiki
    /// with the new question and includes prior turns as conversation history.
    public func sendFollowUp(_ question: String) {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        activeQueryTask?.cancel()
        querySession.startFollowUp(question: trimmed)

        let workspaceURL = workspace.url
        let session = querySession
        let claudeRunner = queryRunner
        let compileRunner = runner
        let log = logger

        let history = session.turns.map { "Q: \($0.question)\nA: \($0.answer)" }
            .joined(separator: "\n\n")

        activeQueryTask = Task { [weak self] in
            log.log("sendFollowUp: follow-up — \"\(trimmed.prefix(60))\"")

            var wikiContext = ""
            do {
                await MainActor.run { session.updateStatusDetail("Searching wiki…") }
                let hits = try await compileRunner.search(query: trimmed, at: workspaceURL, limit: 10)
                if !hits.isEmpty {
                    await MainActor.run { session.updateStatusDetail("Reading pages…") }
                    var pages: [WikiPage] = []
                    for hit in hits {
                        if let page = try? await compileRunner.page(locator: hit.relativePath, at: workspaceURL) {
                            pages.append(page)
                        }
                    }
                    wikiContext = Self.assembleWikiContext(pages)
                }
            } catch is CancellationError {
                await MainActor.run { session.cancel() }
                await MainActor.run { [weak self] in self?.activeQueryTask = nil }
                return
            } catch {
                log.log("Wiki search failed for follow-up, proceeding without context: \(error)")
            }

            await MainActor.run { session.updateStatusDetail("Asking Claude…") }
            var prompt = ""
            if !history.isEmpty {
                prompt += "<conversation-history>\n\(history)\n</conversation-history>\n\n"
            }
            if !wikiContext.isEmpty {
                prompt += "\(wikiContext)\n\n"
            }
            prompt += trimmed

            do {
                try await claudeRunner.runQuery(
                    prompt: prompt,
                    workspaceURL: workspaceURL,
                    onEvent: { event in
                        await MainActor.run { session.handle(event) }
                    }
                )
                await MainActor.run { [weak self] in
                    if session.status == .running {
                        session.fail("Query completed without a response")
                    }
                    self?.saveCurrentSession()
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

    public func selectHistorySession(_ record: QueryHistoryRecord) {
        activeQueryTask?.cancel()
        activeQueryTask = nil
        archiveSessionIfNeeded()
        let session = QuerySession(id: record.id)
        session.restore(turns: record.turns)
        querySession = session
    }

    public func startNewQuery() {
        activeQueryTask?.cancel()
        activeQueryTask = nil
        archiveSessionIfNeeded()
        querySession = QuerySession()
    }

    /// Persist the current session into history immediately (called after each completed query).
    private func saveCurrentSession() {
        guard !querySession.turns.isEmpty else { return }
        upsertHistoryRecord(QueryHistoryRecord(id: querySession.id, turns: querySession.turns))
    }

    private func archiveSessionIfNeeded() {
        guard !querySession.turns.isEmpty else { return }
        upsertHistoryRecord(QueryHistoryRecord(id: querySession.id, turns: querySession.turns))
    }

    private var historyFileURL: URL? {
        workspace?.url.appending(path: ".compile/query-history.json", directoryHint: .notDirectory)
    }

    private func upsertHistoryRecord(_ record: QueryHistoryRecord) {
        queryHistory.removeAll { $0.id == record.id }
        queryHistory.insert(record, at: 0)
        queryHistory = normalizedHistory(queryHistory)
        saveHistory()
    }

    private func normalizedHistory(_ records: [QueryHistoryRecord]) -> [QueryHistoryRecord] {
        var seenIDs: Set<UUID> = []
        let deduped = records
            .filter { !$0.turns.isEmpty }
            .sorted { $0.archivedAt > $1.archivedAt }
            .filter { seenIDs.insert($0.id).inserted }
        return Array(deduped.prefix(maxQueryHistoryRecords))
    }

    private func saveHistory() {
        guard let url = historyFileURL else { return }
        do {
            try fileManager.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let normalized = normalizedHistory(queryHistory)
            let data = try JSONEncoder().encode(normalized)
            try data.write(to: url, options: .atomic)
        } catch {
            logger.log("Failed to save query history: \(error)")
        }
    }

    func loadHistory() {
        queryHistory = []
        guard let url = historyFileURL,
              fileManager.fileExists(atPath: url.path) else { return }
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode([QueryHistoryRecord].self, from: data)
            queryHistory = normalizedHistory(decoded)
        } catch {
            logger.log("Failed to load query history: \(error)")
        }
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
                let result = openNoteHandler(relative, workspace.url)
                lastError = noteOpenErrorMessage(for: result, relativePath: relative)
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
                    self.lastError = self.noteOpenErrorMessage(for: result, relativePath: page.relativePath)
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
        case .openedVaultForRegistration:
            flashToast("Opened vault in Obsidian")
        case .notInstalled:
            lastError = "Obsidian is not installed. Install it from obsidian.md."
        case .requiresAdvancedURI:
            lastError = "Obsidian graph support needs the Advanced URI plugin."
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
        guard isGraphPluginInstalledHandler(workspace.url) else {
            if defaults.bool(forKey: graphPluginPromptSuppressedKey(for: workspace.url)) {
                lastError = "Graph view needs the Advanced URI plugin. Open Settings to install it for this vault."
            } else {
                showGraphPluginInstallPrompt = true
            }
            return
        }
        switch openGraphHandler(workspace.url) {
        case .opened:
            flashToast("Opening graph in Obsidian")
        case .openedVaultForRegistration:
            flashToast("Opened vault in Obsidian. Click Graph again after it finishes loading.")
        case .notInstalled:
            lastError = "Obsidian is not installed. Install it from obsidian.md."
        case .requiresAdvancedURI:
            showGraphPluginInstallPrompt = true
        case .vaultMissing:
            lastError = "The workspace folder no longer exists."
        case .failed(let message):
            lastError = "Obsidian refused to open: \(message)"
        }
    }

    public func dismissGraphPluginInstallPrompt() {
        if let workspace {
            defaults.set(true, forKey: graphPluginPromptSuppressedKey(for: workspace.url))
        }
        showGraphPluginInstallPrompt = false
    }

    public func installGraphPluginForCurrentWorkspace() async {
        guard let workspace else {
            lastError = "Workspace is not ready yet."
            return
        }
        showGraphPluginInstallPrompt = false
        lastError = nil
        isInstallingGraphPlugin = true
        let workspaceURL = workspace.url
        let obsidianWasRunning = isObsidianRunningHandler()

        do {
            try await installGraphPluginHandler(workspaceURL)
            defaults.removeObject(forKey: graphPluginPromptSuppressedKey(for: workspaceURL))
            isInstallingGraphPlugin = false

            if obsidianWasRunning {
                flashToast("Advanced URI installed. Relaunch Obsidian once, then use Graph.")
                return
            }

            switch openGraphHandler(workspaceURL) {
            case .opened:
                flashToast("Installed Advanced URI and opened graph")
            case .openedVaultForRegistration:
                flashToast("Installed Advanced URI and opened the vault. Click Graph again after Obsidian finishes loading.")
            case .notInstalled:
                lastError = "Obsidian is not installed. Install it from obsidian.md."
            case .requiresAdvancedURI:
                lastError = "Advanced URI was installed, but Obsidian has not loaded it yet. Launch Obsidian once and try Graph again."
            case .vaultMissing:
                lastError = "The workspace folder no longer exists."
            case .failed(let message):
                lastError = "Installed Advanced URI, but Obsidian refused to open the graph: \(message)"
            }
        } catch {
            isInstallingGraphPlugin = false
            lastError = "Could not install Advanced URI: \(error.localizedDescription)"
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
        activeQueryTask?.cancel()
        activeQueryTask = nil
        workspace = info
        querySession = QuerySession()
        queryHistory = []
        showGraphPluginInstallPrompt = false
        isInstallingGraphPlugin = false
        statusMessage = "Ready"
        lastError = nil
        defaults.set(info.path, forKey: currentWorkspaceKey)
        rememberWorkspacePath(info.path)
        feedStore.bindWorkspace(info.url)
        loadHistory()
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
                let truncated = String(body.prefix(5000))
                section += "\n\n\(truncated)"
                if body.count > 5000 {
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

    private func graphPluginPromptSuppressedKey(for workspaceURL: URL) -> String {
        graphPluginPromptSuppressedKeyPrefix
            + workspaceURL.resolvingSymlinksInPath().standardizedFileURL.path
    }

    private func noteOpenErrorMessage(
        for result: ObsidianOpener.Result,
        relativePath: String
    ) -> String? {
        switch result {
        case .opened:
            return nil
        case .openedVaultForRegistration:
            return "Opened the vault in Obsidian. Try opening \(relativePath) again once Obsidian finishes loading."
        case .notInstalled:
            return "Obsidian is not installed. Install it from obsidian.md."
        case .requiresAdvancedURI:
            return "Obsidian graph support needs the Advanced URI plugin."
        case .vaultMissing:
            return "The wiki page could not be found at \(relativePath)."
        case .failed(let message):
            return "Obsidian refused to open: \(message)"
        }
    }
}
