import Foundation
import Observation

public struct FeedItem: Identifiable, Equatable, Sendable {
    public enum Status: String, Equatable, Sendable {
        case queued
        case staging
        case launching
        case launched
        case failed
    }

    public let id: String
    public var source: String
    public var stagedRelativePath: String?
    public var prompt: String?
    public var status: Status
    public var stage: String
    public var errorMessage: String?

    public init(
        id: String,
        source: String,
        stagedRelativePath: String? = nil,
        prompt: String? = nil,
        status: Status = .queued,
        stage: String = "Queued",
        errorMessage: String? = nil
    ) {
        self.id = id
        self.source = source
        self.stagedRelativePath = stagedRelativePath
        self.prompt = prompt
        self.status = status
        self.stage = stage
        self.errorMessage = errorMessage
    }
}

public struct IngestRequest: Equatable, Sendable {
    public enum Source: Equatable, Sendable {
        case file(URL)
        case remoteURL(String)
    }

    public let id: String
    public let source: Source

    public init(id: String = UUID().uuidString, source: Source) {
        self.id = id
        self.source = source
    }

    public var displaySource: String {
        switch source {
        case .file(let url):
            return url.lastPathComponent
        case .remoteURL(let value):
            return value
        }
    }
}

@MainActor
public protocol IngestDispatcher: AnyObject {
    func dispatch(prompt: String, workspaceURL: URL) throws
}

@MainActor
@Observable
public final class FeedStore {
    public private(set) var items: [FeedItem] = []

    private let dispatcher: IngestDispatcher
    private let logger: AppLogger
    private var activeWorkspaceURL: URL?

    public init(dispatcher: IngestDispatcher, logger: AppLogger) {
        self.dispatcher = dispatcher
        self.logger = logger
    }

    public func bindWorkspace(_ workspaceURL: URL) {
        activeWorkspaceURL = workspaceURL
    }

    public func enqueue(_ requests: [IngestRequest]) {
        guard let workspaceURL = activeWorkspaceURL else {
            for request in requests {
                var item = FeedItem(id: request.id, source: request.displaySource)
                item.status = .failed
                item.stage = "No workspace"
                item.errorMessage = "Workspace is not ready yet."
                items.append(item)
            }
            return
        }

        for request in requests {
            var item = FeedItem(id: request.id, source: request.displaySource)
            items.append(item)

            do {
                let prompt: String
                switch request.source {
                case .file(let fileURL):
                    item.status = .staging
                    item.stage = "Staging into raw/"
                    updateItem(item)
                    let stagedPath = try FeedStore.stageSource(
                        for: request,
                        workspaceURL: workspaceURL
                    )
                    let stagedRelative = FeedStore.relativePath(
                        for: stagedPath,
                        under: workspaceURL
                    ) ?? fileURL.lastPathComponent
                    item.stagedRelativePath = stagedRelative
                    prompt = FeedStore.ingestPrompt(for: stagedRelative)
                case .remoteURL(let urlString):
                    prompt = FeedStore.ingestPrompt(forURL: urlString)
                }

                item.prompt = prompt
                item.status = .launching
                item.stage = "Opening Terminal"
                updateItem(item)

                try dispatcher.dispatch(prompt: prompt, workspaceURL: workspaceURL)

                item.status = .launched
                item.stage = "Claude running in Terminal"
                updateItem(item)
            } catch {
                logger.log("Failed to launch ingest for \(request.displaySource): \(error)")
                item.status = .failed
                item.stage = "Failed"
                item.errorMessage = error.localizedDescription
                updateItem(item)
            }
        }
    }

    private func updateItem(_ item: FeedItem) {
        if let index = items.firstIndex(where: { $0.id == item.id }) {
            items[index] = item
        }
    }

    public static func ingestPrompt(for stagedRelativePath: String) -> String {
        "/ingest \(stagedRelativePath)"
    }

    public static func ingestPrompt(forURL url: String) -> String {
        "/ingest \(url)"
    }

    public static func claudeCommand(for prompt: String) -> String {
        "claude \(doubleQuoteForShell(prompt))"
    }

    public static func doubleQuoteForShell(_ value: String) -> String {
        let escaped = value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "$", with: "\\$")
            .replacingOccurrences(of: "`", with: "\\`")
        return "\"" + escaped + "\""
    }

    public static func relativePath(for absolutePath: String, under workspaceURL: URL) -> String? {
        let resolvedWorkspace = workspaceURL.resolvingSymlinksInPath().standardizedFileURL.path
        let prefix = resolvedWorkspace.hasSuffix("/") ? resolvedWorkspace : resolvedWorkspace + "/"
        let resolvedPath = URL(fileURLWithPath: absolutePath).resolvingSymlinksInPath().standardizedFileURL.path
        if resolvedPath == resolvedWorkspace {
            return ""
        }
        guard resolvedPath.hasPrefix(prefix) else {
            return nil
        }
        return String(resolvedPath.dropFirst(prefix.count))
    }

    static func stageSource(for request: IngestRequest, workspaceURL: URL) throws -> String {
        switch request.source {
        case .remoteURL(let value):
            return value
        case .file(let fileURL):
            let fileManager = FileManager.default
            let resolvedWorkspace = workspaceURL.resolvingSymlinksInPath().standardizedFileURL
            let rawDir = resolvedWorkspace
                .appending(path: "raw", directoryHint: .isDirectory)
                .standardizedFileURL
            let resolvedSource = fileURL.resolvingSymlinksInPath().standardizedFileURL

            if isContained(resolvedSource, inside: rawDir) {
                return resolvedSource.path
            }

            try fileManager.createDirectory(at: rawDir, withIntermediateDirectories: true)
            let target = uniqueDestination(in: rawDir, preferredName: fileURL.lastPathComponent)
            try fileManager.copyItem(at: fileURL, to: target)
            return target.path
        }
    }

    private static func isContained(_ child: URL, inside parent: URL) -> Bool {
        let parentPath = parent.path
        let childPath = child.path
        if childPath == parentPath {
            return true
        }
        let prefix = parentPath.hasSuffix("/") ? parentPath : parentPath + "/"
        return childPath.hasPrefix(prefix)
    }

    private static func uniqueDestination(in directory: URL, preferredName: String) -> URL {
        let fileManager = FileManager.default
        let sanitized = preferredName.isEmpty ? "source" : preferredName
        let initial = directory.appending(path: sanitized, directoryHint: .notDirectory)
        if !fileManager.fileExists(atPath: initial.path) {
            return initial
        }
        let base = (sanitized as NSString).deletingPathExtension
        let ext = (sanitized as NSString).pathExtension
        for index in 1..<1000 {
            let candidateName = ext.isEmpty ? "\(base)-\(index)" : "\(base)-\(index).\(ext)"
            let candidate = directory.appending(path: candidateName, directoryHint: .notDirectory)
            if !fileManager.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        let suffix = UUID().uuidString.prefix(8)
        let fallbackName = ext.isEmpty ? "\(base)-\(suffix)" : "\(base)-\(suffix).\(ext)"
        return directory.appending(path: fallbackName, directoryHint: .notDirectory)
    }
}
