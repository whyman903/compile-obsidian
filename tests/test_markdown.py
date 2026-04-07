from __future__ import annotations

from compile.markdown import (
    WIKILINK_RE,
    count_content_paragraphs,
    extract_wikilinks,
    parse_markdown_text,
)


class TestParseMarkdownText:
    def test_basic_frontmatter(self) -> None:
        text = "---\ntitle: Hello\ntype: article\n---\n\nBody text."
        fm, body, has_fm = parse_markdown_text(text)
        assert has_fm is True
        assert fm["title"] == "Hello"
        assert fm["type"] == "article"
        assert body == "Body text."

    def test_no_frontmatter(self) -> None:
        text = "# Just a heading\n\nSome body."
        fm, body, has_fm = parse_markdown_text(text)
        assert has_fm is False
        assert fm == {}
        assert "Just a heading" in body

    def test_malformed_yaml(self) -> None:
        text = "---\n[invalid yaml: {\n---\n\nBody."
        fm, body, has_fm = parse_markdown_text(text)
        assert has_fm is False
        assert fm == {}

    def test_incomplete_frontmatter_delimiter(self) -> None:
        text = "---\ntitle: Hello\nNo closing delimiter."
        fm, body, has_fm = parse_markdown_text(text)
        assert has_fm is False

    def test_empty_frontmatter(self) -> None:
        text = "---\n\n---\n\nBody here."
        fm, body, has_fm = parse_markdown_text(text)
        assert has_fm is True
        assert fm == {}
        assert body == "Body here."

    def test_frontmatter_with_lists(self) -> None:
        text = "---\ntags:\n  - alpha\n  - beta\n---\n\nBody."
        fm, body, has_fm = parse_markdown_text(text)
        assert fm["tags"] == ["alpha", "beta"]


class TestExtractWikilinks:
    def test_basic_links(self) -> None:
        body = "See [[Page A]] and [[Page B]]."
        links = extract_wikilinks(body)
        assert links == ["Page A", "Page B"]

    def test_pipe_aliases(self) -> None:
        body = "See [[Page A|display text]]."
        links = extract_wikilinks(body)
        assert links == ["Page A"]

    def test_no_links(self) -> None:
        assert extract_wikilinks("Plain text.") == []

    def test_empty_link_ignored(self) -> None:
        body = "See [[]] and [[Valid]]."
        links = extract_wikilinks(body)
        assert links == ["Valid"]

    def test_hash_fragment_excluded(self) -> None:
        # The regex excludes links containing # entirely (they're section refs)
        body = "See [[Page#section]]."
        links = extract_wikilinks(body)
        assert links == []


class TestCountContentParagraphs:
    def test_counts_substantial_lines(self) -> None:
        body = (
            "# Heading\n\n"
            "This is a paragraph long enough to count as content (over 30 chars easily).\n\n"
            "Another substantial paragraph with enough text to be meaningful here.\n\n"
            "- A list item\n"
            "> A blockquote\n"
        )
        assert count_content_paragraphs(body) == 2

    def test_empty_body(self) -> None:
        assert count_content_paragraphs("") == 0

    def test_only_headings(self) -> None:
        body = "# Heading 1\n## Heading 2\n### Heading 3"
        assert count_content_paragraphs(body) == 0

    def test_short_lines_not_counted(self) -> None:
        body = "Short.\nAlso short.\nTiny."
        assert count_content_paragraphs(body) == 0

    def test_mixed_content(self) -> None:
        body = (
            "# Title\n\n"
            "A very long paragraph that definitely exceeds thirty characters.\n\n"
            "Short.\n\n"
            "- list item\n"
            "Another paragraph that is long enough to be counted as a content paragraph.\n"
        )
        assert count_content_paragraphs(body) == 2


class TestWikilinkRegex:
    def test_matches_basic(self) -> None:
        match = WIKILINK_RE.search("[[Hello World]]")
        assert match is not None
        assert match.group(1) == "Hello World"

    def test_matches_with_pipe(self) -> None:
        match = WIKILINK_RE.search("[[Target|Display]]")
        assert match is not None
        assert match.group(1) == "Target"
