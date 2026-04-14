import Foundation
import XCTest
@testable import MyWikiCore

final class ObsidianTests: XCTestCase {
    func testBuildsVaultURLUsingAbsolutePath() {
        let workspaceURL = URL(fileURLWithPath: "/tmp/My Vault", isDirectory: true)

        let url = ObsidianURLBuilder.openVaultURL(for: workspaceURL)

        XCTAssertEqual(
            url?.absoluteString,
            "obsidian://open?path=/tmp/My%20Vault"
        )
    }

    func testBuildsFileURLUsingAbsoluteNotePath() {
        let workspaceURL = URL(fileURLWithPath: "/tmp/My Vault", isDirectory: true)

        let url = ObsidianURLBuilder.openFileURL(for: workspaceURL, relativePath: "wiki/sources/Paper.md")

        XCTAssertEqual(
            url?.absoluteString,
            "obsidian://open?path=/tmp/My%20Vault/wiki/sources/Paper.md"
        )
    }
}
