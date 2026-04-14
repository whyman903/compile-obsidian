import AppKit
import Foundation

public enum ObsidianURLBuilder {
    public static func openVaultURL(for workspaceURL: URL) -> URL? {
        var components = URLComponents()
        components.scheme = "obsidian"
        components.host = "open"
        components.queryItems = [
            URLQueryItem(name: "path", value: workspaceURL.path)
        ]
        return components.url
    }

    public static func openFileURL(for workspaceURL: URL, relativePath: String) -> URL? {
        let absoluteFile = workspaceURL
            .appending(path: relativePath, directoryHint: .notDirectory)
            .standardizedFileURL
        var components = URLComponents()
        components.scheme = "obsidian"
        components.host = "open"
        components.queryItems = [
            URLQueryItem(name: "path", value: absoluteFile.path)
        ]
        return components.url
    }
}

@MainActor
public enum ObsidianOpener {
    @discardableResult
    public static func openWorkspace(_ workspaceURL: URL) -> Bool {
        guard FileManager.default.fileExists(atPath: workspaceURL.path) else {
            return false
        }
        if openWithObsidianUsingOpenCommand([workspaceURL]) {
            return true
        }
        if let url = ObsidianURLBuilder.openVaultURL(for: workspaceURL),
           NSWorkspace.shared.open(url) {
            return true
        }
        return false
    }

    @discardableResult
    public static func openNote(notePath: String, workspaceURL: URL) -> Bool {
        let fileURL = workspaceURL
            .appending(path: notePath, directoryHint: .notDirectory)
            .standardizedFileURL
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return false
        }
        if openWithObsidianUsingOpenCommand([fileURL]) {
            return true
        }
        if let url = ObsidianURLBuilder.openFileURL(for: workspaceURL, relativePath: notePath),
           NSWorkspace.shared.open(url) {
            return true
        }
        return false
    }

    @discardableResult
    private static func openWithObsidianUsingOpenCommand(_ urls: [URL]) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-a", "Obsidian"] + urls.map(\.path)
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }
}
