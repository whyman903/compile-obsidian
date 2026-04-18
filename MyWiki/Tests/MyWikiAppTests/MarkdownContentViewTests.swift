import XCTest
@testable import MyWikiApp

final class MarkdownContentViewTests: XCTestCase {
    @MainActor
    func testPreprocessMarkdownConvertsWikiLinksToMarkdownLinks() {
        let processed = MarkdownContentView.preprocessMarkdown("Open [[Planner|the planner]].")
        XCTAssertEqual(processed, "Open [the planner](mywiki://page?target=Planner).")
    }

    @MainActor
    func testRenderHTMLBodyUsesGitHubFlavoredTables() {
        let html = MarkdownContentView.renderHTMLBody(
            """
            | Name | Score |
            | --- | ---: |
            | [[Planner]] | **42** |
            """
        )

        XCTAssertTrue(html.contains("<table>"))
        XCTAssertTrue(html.contains("<th>Name</th>"))
        XCTAssertTrue(html.contains("align=\"right\""))
        XCTAssertTrue(html.contains("href=\"mywiki://page?target=Planner\""))
        XCTAssertTrue(html.contains("<strong>42</strong>"))
    }
}
