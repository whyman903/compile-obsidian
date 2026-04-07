from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from compile.fetch import fetch_url, _extract_title, _clean_markdown


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "raw"
    d.mkdir()
    return d


def _mock_response(html: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        text=html,
        request=httpx.Request("GET", "https://example.com/article"),
    )


class TestFetchUrl:
    def test_basic_fetch(self, raw_dir: Path) -> None:
        html = """
        <html>
        <head><title>Test Article</title></head>
        <body><article><h1>Test Article</h1><p>Hello world.</p></article></body>
        </html>
        """
        with patch("compile.fetch.httpx.get", return_value=_mock_response(html)):
            path, title = fetch_url("https://example.com/article", raw_dir)

        assert path.exists()
        assert title == "Test Article"
        content = path.read_text()
        assert "<!-- source_url: https://example.com/article -->" in content
        assert "Hello world" in content

    def test_deduplicates_filenames(self, raw_dir: Path) -> None:
        html = "<html><head><title>Dup</title></head><body><p>Content</p></body></html>"
        with patch("compile.fetch.httpx.get", return_value=_mock_response(html)):
            path1, _ = fetch_url("https://example.com/a", raw_dir)
            path2, _ = fetch_url("https://example.com/b", raw_dir)

        assert path1 != path2
        assert path1.exists() and path2.exists()

    def test_strips_nav_footer(self, raw_dir: Path) -> None:
        html = """
        <html><head><title>Clean</title></head>
        <body>
          <nav>Navigation</nav>
          <article><p>Main content.</p></article>
          <footer>Footer</footer>
        </body></html>
        """
        with patch("compile.fetch.httpx.get", return_value=_mock_response(html)):
            path, _ = fetch_url("https://example.com/clean", raw_dir)

        content = path.read_text()
        assert "Navigation" not in content
        assert "Footer" not in content
        assert "Main content" in content

    def test_http_error_raises(self, raw_dir: Path) -> None:
        with patch("compile.fetch.httpx.get", side_effect=httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "https://x.com"), response=_mock_response("", 404)
        )):
            with pytest.raises(httpx.HTTPStatusError):
                fetch_url("https://x.com/missing", raw_dir)


class TestExtractTitle:
    def test_og_title(self) -> None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<html><head><meta property="og:title" content="OG Title"></head></html>', "html.parser")
        assert _extract_title(soup, "https://x.com") == "OG Title"

    def test_title_tag(self) -> None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html><head><title>Page Title</title></head></html>", "html.parser")
        assert _extract_title(soup, "https://x.com") == "Page Title"

    def test_h1_fallback(self) -> None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html><body><h1>Heading</h1></body></html>", "html.parser")
        assert _extract_title(soup, "https://x.com") == "Heading"

    def test_url_fallback(self) -> None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        assert _extract_title(soup, "https://example.com/path") == "example.com"


class TestCleanMarkdown:
    def test_collapses_blank_lines(self) -> None:
        result = _clean_markdown("a\n\n\n\n\nb")
        assert result == "a\n\nb\n"

    def test_strips_trailing_whitespace(self) -> None:
        result = _clean_markdown("hello   \nworld   ")
        assert result == "hello\nworld\n"
