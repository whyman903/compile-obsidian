import Foundation
import XCTest
@testable import MyWikiCore

@MainActor
private final class RecordingDispatcher: IngestDispatcher {
    struct Launch: Equatable {
        let prompt: String
        let workspacePath: String
    }
    var launches: [Launch] = []
    var errorToThrow: Error?

    func dispatch(prompt: String, workspaceURL: URL) throws {
        if let errorToThrow {
            throw errorToThrow
        }
        launches.append(Launch(prompt: prompt, workspacePath: workspaceURL.path))
    }
}

@MainActor
final class FeedStoreTests: XCTestCase {
    private var tempDirectory: URL!
    private var workspaceURL: URL!
    private var rawDirectory: URL!

    override func setUp() async throws {
        try await super.setUp()
        tempDirectory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appending(path: "MyWikiTests-" + UUID().uuidString, directoryHint: .isDirectory)
        workspaceURL = tempDirectory.appending(path: "workspace", directoryHint: .isDirectory)
        rawDirectory = workspaceURL.appending(path: "raw", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: rawDirectory, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        if let cleanupURL = tempDirectory {
            try? FileManager.default.removeItem(at: cleanupURL)
        }
        tempDirectory = nil
        workspaceURL = nil
        rawDirectory = nil
        try await super.tearDown()
    }

    private func makeLogger() -> AppLogger {
        AppLogger(logDirectory: tempDirectory.appending(path: "logs-" + UUID().uuidString))
    }

    private func writeRawFile(_ name: String) -> URL {
        let url = rawDirectory.appending(path: name, directoryHint: .notDirectory)
        try? "sample".write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private func writeExternalFile(_ name: String) -> URL {
        let external = tempDirectory.appending(path: "external", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: external, withIntermediateDirectories: true)
        let url = external.appending(path: name, directoryHint: .notDirectory)
        try? "sample".write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testFileEnqueueStagesIntoRawAndDispatchesDraftPrompt() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        let external = writeExternalFile("sample.md")
        store.enqueue([IngestRequest(id: "job-1", source: .file(external))])

        XCTAssertEqual(dispatcher.launches.count, 1)
        XCTAssertEqual(dispatcher.launches.first?.prompt, "/ingest raw/sample.md")
        XCTAssertEqual(
            dispatcher.launches.first?.workspacePath,
            workspaceURL.resolvingSymlinksInPath().standardizedFileURL.path
        )

        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.status, .dispatched)
        XCTAssertEqual(item.source, "sample.md")
        XCTAssertEqual(item.stagedRelativePath, "raw/sample.md")
        XCTAssertEqual(item.prompt, "/ingest raw/sample.md")
    }

    func testFileAlreadyInRawIsDispatchedInPlace() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        let existing = writeRawFile("existing.md")
        store.enqueue([IngestRequest(id: "job-2", source: .file(existing))])

        XCTAssertEqual(dispatcher.launches.first?.prompt, "/ingest raw/existing.md")
        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.stagedRelativePath, "raw/existing.md")
        XCTAssertEqual(item.status, .dispatched)
    }

    func testURLEnqueueDispatchesIngestURL() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        store.enqueue([IngestRequest(id: "job-url", source: .remoteURL("https://example.com/article"))])

        XCTAssertEqual(dispatcher.launches.count, 1)
        XCTAssertEqual(dispatcher.launches.first?.prompt, "/ingest https://example.com/article")
        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.status, .dispatched)
        XCTAssertEqual(item.source, "https://example.com/article")
        XCTAssertEqual(item.prompt, "/ingest https://example.com/article")
        XCTAssertNil(item.stagedRelativePath)
    }

    func testQueryEnqueueDispatchesQueryPrompt() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        store.enqueue([IngestRequest(id: "job-q", source: .query("how do vaccines work"))])

        XCTAssertEqual(dispatcher.launches.first?.prompt, "/query how do vaccines work")
        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.status, .dispatched)
        XCTAssertEqual(item.prompt, "/query how do vaccines work")
    }

    func testEnqueueWithTrailingTextAppendsContextBelowSlashCommand() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        let external = writeExternalFile("notes.md")
        store.enqueue(
            [IngestRequest(id: "job-ctx", source: .file(external))],
            trailingText: "Cite the intro carefully.",
            workspaceURL: workspaceURL
        )

        let prompt = try XCTUnwrap(dispatcher.launches.first?.prompt)
        XCTAssertTrue(prompt.hasPrefix("/ingest raw/notes.md"))
        XCTAssertTrue(prompt.contains("\n\nCite the intro carefully."))
    }

    func testEnqueueMultipleFilesAppendsEachAsAdditional() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        let first = writeExternalFile("first.md")
        let second = writeExternalFile("second.md")
        store.enqueue(
            [
                IngestRequest(id: "job-a", source: .file(first)),
                IngestRequest(id: "job-b", source: .file(second)),
            ],
            trailingText: "",
            workspaceURL: workspaceURL
        )

        XCTAssertEqual(dispatcher.launches.count, 1)
        let prompt = try XCTUnwrap(dispatcher.launches.first?.prompt)
        XCTAssertTrue(prompt.contains("/ingest raw/first.md"))
        XCTAssertTrue(prompt.contains("Also ingest: raw/second.md"))
        XCTAssertEqual(store.items.count, 2)
    }

    func testDispatcherFailureIsSurfacedAsFailedItem() throws {
        let dispatcher = RecordingDispatcher()
        dispatcher.errorToThrow = NSError(
            domain: "test",
            code: 42,
            userInfo: [NSLocalizedDescriptionKey: "terminal closed"]
        )
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())
        store.bindWorkspace(workspaceURL)

        let external = writeExternalFile("boom.md")
        store.enqueue([IngestRequest(id: "job-fail", source: .file(external))])

        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.status, .failed)
        XCTAssertEqual(item.errorMessage, "terminal closed")
    }

    func testEnqueueWithoutWorkspaceFailsWithClearMessage() throws {
        let dispatcher = RecordingDispatcher()
        let store = FeedStore(dispatcher: dispatcher, logger: makeLogger())

        store.enqueue([IngestRequest(id: "job-no-ws", source: .remoteURL("https://example.com"))])

        XCTAssertTrue(dispatcher.launches.isEmpty)
        let item = try XCTUnwrap(store.items.first)
        XCTAssertEqual(item.status, .failed)
        XCTAssertEqual(item.errorMessage, "Workspace is not ready yet.")
    }

    func testStagesFileFromOutsideWorkspaceIntoRawDirectory() throws {
        let external = writeExternalFile("notes.md")
        let request = IngestRequest(id: "job-stage", source: .file(external))

        let staged = try FeedStore.stageSource(for: request, workspaceURL: workspaceURL)

        let stagedURL = URL(fileURLWithPath: staged)
        XCTAssertEqual(stagedURL.deletingLastPathComponent().lastPathComponent, "raw")
        XCTAssertTrue(FileManager.default.fileExists(atPath: staged))
        XCTAssertTrue(FileManager.default.fileExists(atPath: external.path),
                      "Staging should copy, not move, the source file")
    }

    func testStagingReusesFileAlreadyInsideRawDirectory() throws {
        let inside = writeRawFile("already.md")
        let request = IngestRequest(id: "job-in-place", source: .file(inside))

        let staged = try FeedStore.stageSource(for: request, workspaceURL: workspaceURL)

        XCTAssertEqual(
            URL(fileURLWithPath: staged).resolvingSymlinksInPath().standardizedFileURL.path,
            inside.resolvingSymlinksInPath().standardizedFileURL.path
        )
    }

    func testStagingDisambiguatesFilenameCollisions() throws {
        _ = writeRawFile("notes.md")
        let external = writeExternalFile("notes.md")
        let request = IngestRequest(id: "job-collide", source: .file(external))

        let staged = try FeedStore.stageSource(for: request, workspaceURL: workspaceURL)

        let stagedURL = URL(fileURLWithPath: staged)
        XCTAssertEqual(stagedURL.deletingLastPathComponent().lastPathComponent, "raw")
        XCTAssertNotEqual(stagedURL.lastPathComponent, "notes.md")
        XCTAssertTrue(stagedURL.lastPathComponent.hasPrefix("notes"))
        XCTAssertEqual(stagedURL.pathExtension, "md")
    }

    func testStagingPassesThroughRemoteURL() throws {
        let request = IngestRequest(id: "job-url", source: .remoteURL("https://example.com/a"))
        let staged = try FeedStore.stageSource(for: request, workspaceURL: workspaceURL)
        XCTAssertEqual(staged, "https://example.com/a")
    }

    func testBuildPromptComposesMultipleSections() {
        let prompt = FeedStore.buildPrompt(
            stagedFiles: ["raw/a.md"],
            urls: ["https://example.com"],
            query: nil,
            trailingText: "extra context"
        )
        let lines = prompt.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        XCTAssertEqual(lines[0], "/ingest raw/a.md")
        XCTAssertEqual(lines[1], "/ingest https://example.com")
        XCTAssertEqual(lines[2], "")
        XCTAssertEqual(lines[3], "extra context")
    }

    func testBuildPromptUsesQueryOnlyWhenNoFilesOrUrls() {
        let promptQueryOnly = FeedStore.buildPrompt(
            stagedFiles: [],
            urls: [],
            query: "what is compulsory vaccination",
            trailingText: ""
        )
        XCTAssertEqual(promptQueryOnly, "/query what is compulsory vaccination")

        let promptFilesWin = FeedStore.buildPrompt(
            stagedFiles: ["raw/a.md"],
            urls: [],
            query: "ignore me",
            trailingText: ""
        )
        XCTAssertEqual(promptFilesWin, "/ingest raw/a.md")
    }

    func testRelativePathReturnsNilForPathOutsideWorkspace() {
        let outside = "/tmp/elsewhere/file.md"
        XCTAssertNil(FeedStore.relativePath(for: outside, under: workspaceURL))
    }
}
