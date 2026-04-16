import XCTest
@testable import MyWikiApp

final class MarkdownContentViewTests: XCTestCase {
    @MainActor
    func testParseSegmentsPreservesThematicBreakInsideMarkdownSegment() {
        let segments = MarkdownContentView.parseSegments(
            """
            the three -

            are close even if they look nothing alike syntactically.

            ---

            pass@k
            """
        )

        XCTAssertEqual(segments, [
            .markdown(
                """
                the three -

                are close even if they look nothing alike syntactically.

                ---

                pass@k
                """
            )
        ])
    }

    @MainActor
    func testParseSegmentsExtractsObsidianCallouts() {
        let segments = MarkdownContentView.parseSegments(
            """
            Intro paragraph.

            > [!NOTE] Key point
            > Callouts still render in the custom style.
            >
            > - They keep wikilinks like [[Planner]]
            """
        )

        XCTAssertEqual(segments, [
            .markdown("Intro paragraph.\n"),
            .callout(
                kind: "NOTE",
                title: "Key point",
                body: "Callouts still render in the custom style.\n\n- They keep wikilinks like [[Planner]]"
            ),
        ])
    }

    @MainActor
    func testParseSegmentsExtractsCollapsibleObsidianCallouts() {
        let segments = MarkdownContentView.parseSegments(
            """
            > [!TIP]- Folded title
            > Still styled as a callout.
            """
        )

        XCTAssertEqual(segments, [
            .callout(
                kind: "TIP",
                title: "Folded title",
                body: "Still styled as a callout."
            ),
        ])
    }

    @MainActor
    func testPreprocessMarkdownConvertsWikiLinksToMarkdownLinks() {
        let processed = MarkdownContentView.preprocessMarkdown("Open [[Planner|the planner]].")
        XCTAssertEqual(processed, "Open [the planner](mywiki://page?target=Planner).")
    }
}
