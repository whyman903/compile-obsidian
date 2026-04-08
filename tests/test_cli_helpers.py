from __future__ import annotations

from compile.cli import _source_summary_from_text


class TestSourceSummary:
    def test_strips_html_comments(self) -> None:
        text = "<!-- source_url: https://example.com --> <!-- fetched: 2026-01-01 --> # Title Actual content here."
        result = _source_summary_from_text(text, title="Title")
        assert "source_url" not in result
        assert "fetched" not in result
        assert "Actual content here" in result

    def test_strips_heading_matching_title(self) -> None:
        text = "# My Page Title The real content starts here."
        result = _source_summary_from_text(text, title="My Page Title")
        assert "My Page Title" not in result
        assert "real content" in result

    def test_keeps_heading_when_no_title(self) -> None:
        text = "# Some Heading Body text follows."
        result = _source_summary_from_text(text)
        assert "Some Heading" in result

    def test_plain_text(self) -> None:
        text = "Just some normal text without any special markers."
        result = _source_summary_from_text(text)
        assert result == "Just some normal text without any special markers."

    def test_empty_after_stripping(self) -> None:
        text = "<!-- comment -->"
        result = _source_summary_from_text(text)
        assert result == "Minimal source content; no substantive summary available."

    def test_truncation(self) -> None:
        text = "A" * 300
        result = _source_summary_from_text(text)
        assert len(result) == 220
