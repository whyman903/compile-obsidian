from __future__ import annotations

from pathlib import Path

import pytest

from compile.text import (
    extract_source,
    extract_text,
    fix_pdf_artifacts,
    is_equation_heavy,
    is_supported,
    is_url,
    normalize_text,
    slugify,
)


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self) -> None:
        assert slugify("C++ Programming (Guide)") == "c-programming-guide"

    def test_empty_string(self) -> None:
        assert slugify("") == "untitled"

    def test_only_special_chars(self) -> None:
        assert slugify("!!!") == "untitled"

    def test_unicode(self) -> None:
        result = slugify("café latte")
        assert "caf" in result
        assert "latte" in result

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert slugify("--hello--") == "hello"


class TestNormalizeText:
    def test_collapses_whitespace(self) -> None:
        assert normalize_text("hello   world\n\nfoo") == "hello world foo"

    def test_strips_edges(self) -> None:
        assert normalize_text("  hello  ") == "hello"

    def test_empty(self) -> None:
        assert normalize_text("") == ""


class TestFixPdfArtifacts:
    def test_hyphenated_line_breaks(self) -> None:
        assert fix_pdf_artifacts("word-\nbreak") == "wordbreak"

    def test_ligatures(self) -> None:
        assert fix_pdf_artifacts("\ufb01nd") == "find"
        assert fix_pdf_artifacts("\ufb02ow") == "flow"
        assert fix_pdf_artifacts("\ufb00ect") == "ffect"
        assert fix_pdf_artifacts("\ufb03x") == "ffix"
        assert fix_pdf_artifacts("\ufb04e") == "ffle"

    def test_collapses_blank_lines(self) -> None:
        result = fix_pdf_artifacts("a\n\n\n\n\nb")
        assert result == "a\n\nb"


class TestIsEquationHeavy:
    def test_no_equations(self) -> None:
        assert is_equation_heavy("Just plain text.") is False

    def test_few_equations(self) -> None:
        text = "Some text $x = 1$ and $y = 2$ here."
        assert is_equation_heavy(text) is False

    def test_many_equations(self) -> None:
        text = "Eq: $a=1$ and $b=2$ and $c=3$ and $d=4$ and $e=5$"
        assert is_equation_heavy(text) is True

    def test_display_equations(self) -> None:
        text = "$$a=1$$ then $$b=2$$ then $$c=3$$ then $$d=4$$"
        assert is_equation_heavy(text) is True


class TestExtractText:
    def test_markdown_file(self, tmp_path: Path) -> None:
        md_file = tmp_path / "test.md"
        md_file.write_text("# My Document\n\nSome content here.")
        title, text = extract_text(md_file)
        assert title == "My Document"
        assert "Some content here" in text

    def test_markdown_no_heading(self, tmp_path: Path) -> None:
        md_file = tmp_path / "my-doc.md"
        md_file.write_text("Just body text.")
        title, text = extract_text(md_file)
        assert title == "My Doc"  # derived from filename
        assert "Just body text" in text

    def test_markdown_strips_yaml_frontmatter_from_normalized_text(self, tmp_path: Path) -> None:
        md_file = tmp_path / "frontmatter.md"
        md_file.write_text(
            "---\n"
            "title: Research on Neural Networks\n"
            "author: Jane Smith\n"
            "---\n\n"
            "# Research on Neural Networks\n\n"
            "This is the real body paragraph.\n"
        )
        extracted = extract_source(md_file)
        assert extracted.title == "Research on Neural Networks"
        assert "author: Jane Smith" not in extracted.normalized_text
        assert extracted.paragraphs == ("This is the real body paragraph.",)

    def test_markdown_ignores_fenced_code_in_headings_and_paragraphs(self, tmp_path: Path) -> None:
        md_file = tmp_path / "code.md"
        md_file.write_text(
            "# Code Notes\n\n"
            "```python\n"
            "# not a real heading\n"
            "very_long_code_identifier = 1\n"
            "```\n\n"
            "Real paragraph here with enough words to count as substantive content for the synopsis.\n"
        )
        extracted = extract_source(md_file)
        assert extracted.headings == ()
        assert extracted.paragraphs == (
            "Real paragraph here with enough words to count as substantive content for the synopsis.",
        )

    def test_txt_file(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("Plain text content.\nMultiple lines.")
        title, text = extract_text(txt_file)
        assert "Plain text content" in text

    def test_html_file(self, tmp_path: Path) -> None:
        html_file = tmp_path / "page.html"
        html_file.write_text("<html><head><title>Page Title</title></head><body><p>Body text</p></body></html>")
        title, text = extract_text(html_file)
        assert title == "Page Title"
        assert "Body text" in text

    def test_html_with_article(self, tmp_path: Path) -> None:
        html_file = tmp_path / "article.html"
        html_file.write_text(
            "<html><body><nav>Nav</nav><article><p>Article content</p></article></body></html>"
        )
        title, text = extract_text(html_file)
        assert "Article content" in text

    def test_pdf_file(self, tmp_path: Path) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        title, text = extract_text(pdf_file)
        assert title == "Paper"
        assert "PDF source" in text

    def test_image_file(self, tmp_path: Path) -> None:
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0")
        title, text = extract_text(img_file)
        assert title == "Photo"
        assert "Image asset" in text
        assert "jpg" in text.lower()

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "data.xyz"
        bad_file.write_text("data")
        with pytest.raises(ValueError, match="Unsupported"):
            extract_text(bad_file)


class TestIsUrl:
    def test_http(self) -> None:
        assert is_url("http://example.com") is True

    def test_https(self) -> None:
        assert is_url("https://example.com") is True

    def test_not_url(self) -> None:
        assert is_url("not-a-url") is False
        assert is_url("/path/to/file") is False


class TestIsSupported:
    def test_supported_extensions(self) -> None:
        for ext in (".md", ".txt", ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
            assert is_supported(f"file{ext}") is True

    def test_unsupported_extension(self) -> None:
        assert is_supported("file.xyz") is False

    def test_hidden_file(self) -> None:
        assert is_supported(".hidden.md") is False

    def test_url(self) -> None:
        assert is_supported("https://example.com/article") is True

    def test_path_object(self) -> None:
        assert is_supported(Path("test.md")) is True
        assert is_supported(Path("test.xyz")) is False
