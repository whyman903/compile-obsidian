import Foundation
import XCTest
@testable import MyWikiCore

private final class FakeCompileRunner: CompileRunning, @unchecked Sendable {
    var workspaceInfo: WorkspaceInfo
    var pageResult: WikiPage
    private(set) var requestedPageLocators: [String] = []

    init(workspaceInfo: WorkspaceInfo, pageResult: WikiPage) {
        self.workspaceInfo = workspaceInfo
        self.pageResult = pageResult
    }

    func initWorkspace(name: String, at path: URL) async throws -> WorkspaceInfo {
        workspaceInfo
    }

    func status(at path: URL) async throws -> WorkspaceInfo {
        workspaceInfo
    }

    func prepareWorkspaceForClaude(at path: URL, force: Bool) async throws {}

    func search(query: String, at path: URL, limit: Int) async throws -> [SearchHit] {
        []
    }

    func page(locator: String, at path: URL) async throws -> WikiPage {
        requestedPageLocators.append(locator)
        return pageResult
    }

    func ingest(
        source: String,
        at path: URL,
        jobID: String,
        onEvent: @escaping @Sendable (CompileEvent) -> Void
    ) async throws -> String? {
        nil
    }
}

@MainActor
private final class NoopDispatcher: IngestDispatcher {
    func dispatch(prompt: String, workspaceURL: URL) throws {}
}

private final class NoopQueryRunner: ClaudeQueryRunning, @unchecked Sendable {
    func runQuery(
        prompt: String,
        workspaceURL: URL,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws {}
}

private final class SuccessfulQueryRunner: ClaudeQueryRunning, @unchecked Sendable {
    let onRun: @Sendable () -> Void

    init(onRun: @escaping @Sendable () -> Void = {}) {
        self.onRun = onRun
    }

    func runQuery(
        prompt: String,
        workspaceURL: URL,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws {
        onRun()
        await onEvent(.finished(text: "answer", costUSD: 0.01, durationMs: 250, permissionDenials: []))
    }
}

private final class DelayedQueryRunner: ClaudeQueryRunning, @unchecked Sendable {
    let delayNanoseconds: UInt64

    init(delayNanoseconds: UInt64 = 300_000_000) {
        self.delayNanoseconds = delayNanoseconds
    }

    func runQuery(
        prompt: String,
        workspaceURL: URL,
        onEvent: @escaping @Sendable (ClaudeQueryEvent) async -> Void
    ) async throws {
        try await Task.sleep(nanoseconds: delayNanoseconds)
    }
}

private final class DynamicCompileRunner: CompileRunning, @unchecked Sendable {
    var pageResult: WikiPage
    private(set) var requestedPageLocators: [String] = []

    init(pageResult: WikiPage) {
        self.pageResult = pageResult
    }

    func initWorkspace(name: String, at path: URL) async throws -> WorkspaceInfo {
        workspaceInfo(for: path)
    }

    func status(at path: URL) async throws -> WorkspaceInfo {
        workspaceInfo(for: path)
    }

    func prepareWorkspaceForClaude(at path: URL, force: Bool) async throws {}

    func search(query: String, at path: URL, limit: Int) async throws -> [SearchHit] {
        []
    }

    func page(locator: String, at path: URL) async throws -> WikiPage {
        requestedPageLocators.append(locator)
        return pageResult
    }

    func ingest(
        source: String,
        at path: URL,
        jobID: String,
        onEvent: @escaping @Sendable (CompileEvent) -> Void
    ) async throws -> String? {
        nil
    }

    private func workspaceInfo(for path: URL) -> WorkspaceInfo {
        WorkspaceInfo(
            path: path.path,
            topic: "My Wiki",
            description: "Test workspace",
            rawFiles: 0,
            processed: 0,
            unprocessed: 0,
            needsDocumentReview: 0,
            wikiPageCount: 1
        )
    }
}

@MainActor
final class AppModelTests: XCTestCase {
    private var tempDirectory: URL!
    private var defaults: UserDefaults!
    private var defaultsSuiteName: String!

    override func setUp() async throws {
        try await super.setUp()
        tempDirectory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appending(path: "AppModelTests-" + UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: tempDirectory, withIntermediateDirectories: true)
        defaultsSuiteName = "AppModelTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: defaultsSuiteName)
        defaults.removePersistentDomain(forName: defaultsSuiteName)
    }

    override func tearDown() async throws {
        if let defaults {
            defaults.removePersistentDomain(forName: defaultsSuiteName)
        }
        if let tempDirectory {
            try? FileManager.default.removeItem(at: tempDirectory)
        }
        defaults = nil
        defaultsSuiteName = nil
        tempDirectory = nil
        try await super.tearDown()
    }

    private func makePage(title: String, relativePath: String) throws -> WikiPage {
        let payload = """
        {
          "title": "\(title)",
          "relative_path": "\(relativePath)",
          "page_type": "article",
          "word_count": 10,
          "body": "# \(title)",
          "frontmatter": {
            "summary": "summary"
          }
        }
        """
        return try JSONDecoder().decode(WikiPage.self, from: Data(payload.utf8))
    }

    private func writeHistory(_ records: [QueryHistoryRecord], to workspaceURL: URL) throws {
        let compileURL = workspaceURL.appending(path: ".compile", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: compileURL, withIntermediateDirectories: true)
        let historyURL = compileURL.appending(path: "query-history.json", directoryHint: .notDirectory)
        let data = try JSONEncoder().encode(records)
        try data.write(to: historyURL, options: .atomic)
    }

    private func waitUntil(
        timeout: TimeInterval = 1.0,
        pollIntervalNanoseconds: UInt64 = 20_000_000,
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() {
                return
            }
            try? await Task.sleep(nanoseconds: pollIntervalNanoseconds)
        }
        XCTFail("Timed out waiting for condition", file: file, line: line)
    }

    func testOpenWikiPageResolvesBareTitleToRelativePathBeforeOpeningObsidian() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)

        let info = WorkspaceInfo(
            path: workspaceURL.path,
            topic: "My Wiki",
            description: "Test workspace",
            rawFiles: 0,
            processed: 0,
            unprocessed: 0,
            needsDocumentReview: 0,
            wikiPageCount: 1
        )
        let fakeRunner = FakeCompileRunner(
            workspaceInfo: info,
            pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")
        )

        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let opened = expectation(description: "open note")
        var openedNotePath: String?
        var openedWorkspacePath: String?

        let model = AppModel(
            runner: fakeRunner,
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { notePath, workspace in
                openedNotePath = notePath
                openedWorkspacePath = workspace.path
                opened.fulfill()
                return .opened
            },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.openWikiPage(target: "Planner")

        await fulfillment(of: [opened], timeout: 1.0)
        XCTAssertEqual(fakeRunner.requestedPageLocators, ["Planner"])
        XCTAssertEqual(openedNotePath, "wiki/articles/planner.md")
        XCTAssertEqual(openedWorkspacePath, workspaceURL.path)
    }

    func testSendQueryAddsInAppQueryToFeedStore() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)

        let info = WorkspaceInfo(
            path: workspaceURL.path,
            topic: "My Wiki",
            description: "Test workspace",
            rawFiles: 0,
            processed: 0,
            unprocessed: 0,
            needsDocumentReview: 0,
            wikiPageCount: 1
        )
        let fakeRunner = FakeCompileRunner(
            workspaceInfo: info,
            pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")
        )
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let ranQuery = expectation(description: "query runner called")
        let model = AppModel(
            runner: fakeRunner,
            dispatcher: NoopDispatcher(),
            queryRunner: SuccessfulQueryRunner(onRun: { ranQuery.fulfill() }),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.sendQuery("What changed in SIDE?")

        await fulfillment(of: [ranQuery], timeout: 1.0)
        XCTAssertEqual(model.feedStore.items.count, 1)
        XCTAssertEqual(model.feedStore.items.first?.source, "What changed in SIDE?")
        XCTAssertEqual(model.feedStore.items.first?.prompt, "What changed in SIDE?")
    }

    func testCompletedQueriesPersistAcrossAppSessions() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)

        let info = WorkspaceInfo(
            path: workspaceURL.path,
            topic: "My Wiki",
            description: "Test workspace",
            rawFiles: 0,
            processed: 0,
            unprocessed: 0,
            needsDocumentReview: 0,
            wikiPageCount: 1
        )
        let fakeRunner = FakeCompileRunner(
            workspaceInfo: info,
            pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")
        )
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let firstModel = AppModel(
            runner: fakeRunner,
            dispatcher: NoopDispatcher(),
            queryRunner: SuccessfulQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await firstModel.bootstrapIfNeeded()
        firstModel.sendQuery("What changed in SIDE?")

        await waitUntil {
            firstModel.queryHistory.count == 1
                && firstModel.queryHistory.first?.turns.count == 1
        }

        let historyURL = workspaceURL.appending(path: ".compile/query-history.json", directoryHint: .notDirectory)
        XCTAssertTrue(FileManager.default.fileExists(atPath: historyURL.path))

        let secondModel = AppModel(
            runner: fakeRunner,
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-reloaded", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await secondModel.bootstrapIfNeeded()

        XCTAssertEqual(secondModel.queryHistory.count, 1)
        XCTAssertEqual(secondModel.queryHistory.first?.firstQuestion, "What changed in SIDE?")
        XCTAssertEqual(secondModel.queryHistory.first?.turns.count, 1)
    }

    func testFollowUpFromRestoredHistoryUpdatesExistingRecord() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let firstTurn = QueryTurn(question: "Where did I put that paper?", answer: "In the planner.")
        let originalRecord = QueryHistoryRecord(id: UUID(), turns: [firstTurn])
        try writeHistory([originalRecord], to: workspaceURL)

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: SuccessfulQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-followup", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        XCTAssertEqual(model.queryHistory.count, 1)

        model.selectHistorySession(originalRecord)
        model.sendFollowUp("What else should I check?")

        await waitUntil {
            model.queryHistory.count == 1
                && model.queryHistory.first?.turns.count == 2
        }

        XCTAssertEqual(model.queryHistory.first?.id, originalRecord.id)
        XCTAssertEqual(model.queryHistory.first?.turns.map(\.question), [
            "Where did I put that paper?",
            "What else should I check?",
        ])

        let historyURL = workspaceURL.appending(path: ".compile/query-history.json", directoryHint: .notDirectory)
        let savedData = try Data(contentsOf: historyURL)
        let savedRecords = try JSONDecoder().decode([QueryHistoryRecord].self, from: savedData)
        XCTAssertEqual(savedRecords.count, 1)
        XCTAssertEqual(savedRecords.first?.id, originalRecord.id)
        XCTAssertEqual(savedRecords.first?.turns.count, 2)
    }

    func testSidebarHistoryExcludesActiveSessionRecord() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let activeRecord = QueryHistoryRecord(id: UUID(), turns: [
            QueryTurn(question: "Active question", answer: "Active answer")
        ])
        let archivedRecord = QueryHistoryRecord(id: UUID(), turns: [
            QueryTurn(question: "Archived question", answer: "Archived answer")
        ])
        try writeHistory([activeRecord, archivedRecord], to: workspaceURL)

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-sidebar", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.selectHistorySession(activeRecord)

        XCTAssertEqual(model.queryHistory.count, 2)
        XCTAssertEqual(model.sidebarQueryHistory.map(\.id), [archivedRecord.id])
    }

    func testDismissingSelectedHistorySessionRestoresArchivedRecordToSidebar() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let record = QueryHistoryRecord(id: UUID(), turns: [
            QueryTurn(question: "Archived question", answer: "Archived answer")
        ])
        try writeHistory([record], to: workspaceURL)

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-dismiss", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.selectHistorySession(record)
        XCTAssertEqual(model.sidebarQueryHistory.count, 0)

        model.dismissQueryResponse()

        XCTAssertFalse(model.hasActiveQuerySession)
        XCTAssertEqual(model.sidebarQueryHistory.map(\.id), [record.id])
    }

    func testHasActiveQuerySessionIsTrueForInFlightFirstQueryWithoutTurns() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let info = WorkspaceInfo(
            path: workspaceURL.path,
            topic: "My Wiki",
            description: "Test workspace",
            rawFiles: 0,
            processed: 0,
            unprocessed: 0,
            needsDocumentReview: 0,
            wikiPageCount: 1
        )
        let model = AppModel(
            runner: FakeCompileRunner(
                workspaceInfo: info,
                pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")
            ),
            dispatcher: NoopDispatcher(),
            queryRunner: DelayedQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-running", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.sendQuery("Still running?")

        await waitUntil {
            model.querySession.status == .running
        }

        XCTAssertTrue(model.hasActiveQuerySession)
        XCTAssertTrue(model.querySession.turns.isEmpty)

        model.cancelQuery()
    }

    func testSwitchingWorkspacesClearsHistoryWhenDestinationHasNoHistoryFile() async throws {
        let workspaceOneURL = tempDirectory.appending(path: "wiki-one", directoryHint: .isDirectory)
        let workspaceTwoURL = tempDirectory.appending(path: "wiki-two", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceOneURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: workspaceTwoURL, withIntermediateDirectories: true)
        defaults.set(workspaceOneURL.path, forKey: "currentWorkspacePath")

        let record = QueryHistoryRecord(turns: [
            QueryTurn(question: "First workspace question", answer: "First workspace answer")
        ])
        try writeHistory([record], to: workspaceOneURL)

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-workspaces", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        XCTAssertEqual(model.workspace?.path, workspaceOneURL.path)
        XCTAssertEqual(model.queryHistory.count, 1)

        model.selectRecentWorkspace(workspaceTwoURL.path)

        await waitUntil {
            model.workspace?.path == workspaceTwoURL.path
        }

        XCTAssertTrue(model.queryHistory.isEmpty)
        XCTAssertTrue(model.querySession.turns.isEmpty)
    }

    func testOpenObsidianGraphPromptsToInstallPluginWhenMissing() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki-graph", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-graph-prompt", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened },
            isGraphPluginInstalledHandler: { _ in false }
        )

        await model.bootstrapIfNeeded()
        model.openObsidianGraph()

        XCTAssertTrue(model.showGraphPluginInstallPrompt)
        XCTAssertNil(model.lastError)
    }

    func testInstallGraphPluginOpensGraphWhenObsidianIsNotRunning() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki-install", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let installCalled = expectation(description: "plugin installed")
        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-graph-install", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened },
            isGraphPluginInstalledHandler: { _ in false },
            installGraphPluginHandler: { _ in
                installCalled.fulfill()
            },
            isObsidianRunningHandler: { false }
        )

        await model.bootstrapIfNeeded()
        await model.installGraphPluginForCurrentWorkspace()

        await fulfillment(of: [installCalled], timeout: 1.0)
        XCTAssertEqual(model.launcherToast, "Installed Advanced URI and opened graph")
        XCTAssertFalse(model.isInstallingGraphPlugin)
        XCTAssertFalse(model.showGraphPluginInstallPrompt)
    }

    func testInstallGraphPluginRequestsRelaunchWhenObsidianAlreadyRunning() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki-relaunch", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let installCalled = expectation(description: "plugin installed")
        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-graph-relaunch", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened },
            isGraphPluginInstalledHandler: { _ in false },
            installGraphPluginHandler: { _ in
                installCalled.fulfill()
            },
            isObsidianRunningHandler: { true }
        )

        await model.bootstrapIfNeeded()
        await model.installGraphPluginForCurrentWorkspace()

        await fulfillment(of: [installCalled], timeout: 1.0)
        XCTAssertEqual(model.launcherToast, "Advanced URI installed. Relaunch Obsidian once, then use Graph.")
        XCTAssertFalse(model.isInstallingGraphPlugin)
    }

    func testInstallGraphPluginHandlesVaultRegistrationBootstrap() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki-registration", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceURL, withIntermediateDirectories: true)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let installCalled = expectation(description: "plugin installed")
        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-graph-registration", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .openedVaultForRegistration },
            isGraphPluginInstalledHandler: { _ in false },
            installGraphPluginHandler: { _ in
                installCalled.fulfill()
            },
            isObsidianRunningHandler: { false }
        )

        await model.bootstrapIfNeeded()
        await model.installGraphPluginForCurrentWorkspace()

        await fulfillment(of: [installCalled], timeout: 1.0)
        XCTAssertEqual(
            model.launcherToast,
            "Installed Advanced URI and opened the vault. Click Graph again after Obsidian finishes loading."
        )
        XCTAssertNil(model.lastError)
    }

    func testOpenObsidianGraphPromptSuppressionIsScopedPerWorkspace() async throws {
        let workspaceOneURL = tempDirectory.appending(path: "wiki-one", directoryHint: .isDirectory)
        let workspaceTwoURL = tempDirectory.appending(path: "wiki-two", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: workspaceOneURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: workspaceTwoURL, withIntermediateDirectories: true)
        defaults.set(workspaceOneURL.path, forKey: "currentWorkspacePath")

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-graph-suppression", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .opened },
            openGraphHandler: { _ in .opened },
            isGraphPluginInstalledHandler: { _ in false }
        )

        await model.bootstrapIfNeeded()
        model.dismissGraphPluginInstallPrompt()

        model.selectRecentWorkspace(workspaceTwoURL.path)
        await waitUntil {
            model.workspace?.path == workspaceTwoURL.path
        }

        model.openObsidianGraph()

        XCTAssertTrue(model.showGraphPluginInstallPrompt)
        XCTAssertNil(model.lastError)
    }

    func testOpenWikiPageDirectPathSurfacesOpenNoteErrors() async throws {
        let workspaceURL = tempDirectory.appending(path: "wiki-direct", directoryHint: .isDirectory)
        let pageURL = workspaceURL
            .appending(path: "wiki/articles/planner.md", directoryHint: .notDirectory)
        try FileManager.default.createDirectory(at: pageURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "# Planner".write(to: pageURL, atomically: true, encoding: .utf8)
        defaults.set(workspaceURL.path, forKey: "currentWorkspacePath")

        let model = AppModel(
            runner: DynamicCompileRunner(pageResult: try makePage(title: "Planner", relativePath: "wiki/articles/planner.md")),
            dispatcher: NoopDispatcher(),
            queryRunner: NoopQueryRunner(),
            logger: AppLogger(logDirectory: tempDirectory.appending(path: "logs-open-note-error", directoryHint: .isDirectory)),
            defaults: defaults,
            fileManager: .default,
            openWorkspaceHandler: { _ in .opened },
            openNoteHandler: { _, _ in .notInstalled },
            openGraphHandler: { _ in .opened }
        )

        await model.bootstrapIfNeeded()
        model.openWikiPage(target: "wiki/articles/planner.md")

        XCTAssertEqual(model.lastError, "Obsidian is not installed. Install it from obsidian.md.")
    }
}
