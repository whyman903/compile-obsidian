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
}
