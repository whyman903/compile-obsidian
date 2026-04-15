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

    /// Advanced URI plugin URL for opening the graph view. Returns nil if the vault
    /// name can't be percent-encoded. Requires the Advanced URI plugin to be installed
    /// inside Obsidian; otherwise it silently no-ops and falls through to a normal open.
    public static func openGraphURL(for workspaceURL: URL) -> URL? {
        let vaultName = workspaceURL.lastPathComponent
        var components = URLComponents()
        components.scheme = "obsidian"
        components.host = "advanced-uri"
        components.queryItems = [
            URLQueryItem(name: "vault", value: vaultName),
            URLQueryItem(name: "commandid", value: "graph:open"),
        ]
        return components.url
    }
}

@MainActor
public enum ObsidianOpener {
    public enum Result: Equatable {
        case opened
        case notInstalled
        case vaultMissing
        case failed(String)
    }

    @discardableResult
    public static func openWorkspace(_ workspaceURL: URL) -> Result {
        guard FileManager.default.fileExists(atPath: workspaceURL.path) else {
            return .vaultMissing
        }
        guard isObsidianInstalled() else {
            return .notInstalled
        }
        if let url = ObsidianURLBuilder.openVaultURL(for: workspaceURL),
           NSWorkspace.shared.open(url) {
            return .opened
        }
        if openWithObsidianUsingOpenCommand([workspaceURL]) {
            return .opened
        }
        return .failed("Obsidian refused to open the vault.")
    }

    @discardableResult
    public static func openNote(notePath: String, workspaceURL: URL) -> Result {
        let fileURL = workspaceURL
            .appending(path: notePath, directoryHint: .notDirectory)
            .standardizedFileURL
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return .vaultMissing
        }
        guard isObsidianInstalled() else {
            return .notInstalled
        }
        if let url = ObsidianURLBuilder.openFileURL(for: workspaceURL, relativePath: notePath),
           NSWorkspace.shared.open(url) {
            return .opened
        }
        if openWithObsidianUsingOpenCommand([fileURL]) {
            return .opened
        }
        return .failed("Obsidian refused to open the note.")
    }

    /// Open the exact vault path and let Obsidian stay in control of graph navigation.
    /// This avoids ambiguous `vault=` routing when multiple vaults share the same name.
    @discardableResult
    public static func openGraph(workspaceURL: URL) -> Result {
        return openWorkspace(workspaceURL)
    }

    public static func isObsidianInstalled() -> Bool {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: "md.obsidian") != nil
            || FileManager.default.fileExists(atPath: "/Applications/Obsidian.app")
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
