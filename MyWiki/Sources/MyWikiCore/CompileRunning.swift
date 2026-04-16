import Foundation

public protocol CompileRunning: AnyObject, Sendable {
    func initWorkspace(name: String, at path: URL) async throws -> WorkspaceInfo
    func status(at path: URL) async throws -> WorkspaceInfo
    func prepareWorkspaceForClaude(at path: URL, force: Bool) async throws
    func search(query: String, at path: URL, limit: Int) async throws -> [SearchHit]
    func page(locator: String, at path: URL) async throws -> WikiPage
    func ingest(
        source: String,
        at path: URL,
        jobID: String,
        onEvent: @escaping @Sendable (CompileEvent) -> Void
    ) async throws -> String?
}
