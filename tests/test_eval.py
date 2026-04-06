"""Tests for the benchmark evaluation module."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from compile.eval import (
    EvalReport,
    PageScore,
    _citation_density_for_page,
    _count_equations,
    _count_filler,
    _count_table_rows,
    _extract_paragraphs,
    _find_duplicate_concepts,
    _is_claim_sentence,
    evaluate_workspace,
    load_benchmark_corpus,
)


# ---------------------------------------------------------------------------
# Unit tests for scoring helpers
# ---------------------------------------------------------------------------


class TestEquationCounting:
    def test_block_equations(self):
        text = "Some text $$E = mc^2$$ and $$\\int_0^1 f(x) dx$$."
        assert _count_equations(text) == 2

    def test_inline_equations(self):
        text = "The value $x = 5$ and $y = 10$ are used."
        assert _count_equations(text) == 2

    def test_mixed(self):
        text = "Inline $a + b$ and block $$c + d$$ here."
        assert _count_equations(text) == 2

    def test_no_equations(self):
        assert _count_equations("No math here.") == 0


class TestTableCounting:
    def test_data_rows(self):
        text = "| Metric | Value |\n|---|---|\n| Accuracy | 95% |\n| Loss | 0.1 |"
        assert _count_table_rows(text) == 3  # header + 2 data, separator excluded

    def test_no_tables(self):
        assert _count_table_rows("Just text.") == 0


class TestParagraphExtraction:
    def test_basic(self):
        body = "First paragraph.\n\nSecond paragraph."
        paragraphs = _extract_paragraphs(body)
        assert len(paragraphs) == 2

    def test_skips_headings(self):
        body = "# Heading\n\nParagraph text."
        paragraphs = _extract_paragraphs(body)
        assert len(paragraphs) == 1
        assert "Paragraph" in paragraphs[0]

    def test_skips_code_blocks(self):
        body = "Before.\n\n```python\ncode here\n```\n\nAfter."
        paragraphs = _extract_paragraphs(body)
        assert len(paragraphs) == 2


class TestClaimSentenceDetection:
    def test_claim(self):
        assert _is_claim_sentence("The model achieves 95% accuracy on the benchmark.")

    def test_too_short(self):
        assert not _is_claim_sentence("Short.")

    def test_heading(self):
        assert not _is_claim_sentence("# This is a heading")

    def test_list_item(self):
        assert not _is_claim_sentence("- This is a list item that describes something important")


class TestCitationDensity:
    def test_cited_claim(self):
        body = "The model achieves 95% accuracy [[Source Paper]]."
        cited, total = _citation_density_for_page(body)
        assert total >= 1
        assert cited >= 1

    def test_uncited_claim(self):
        body = "The model achieves 95% accuracy on the benchmark."
        cited, total = _citation_density_for_page(body)
        assert total >= 1
        assert cited == 0


class TestFillerDetection:
    def test_detects_filler(self):
        body = "This is a significant advancement in the field."
        assert _count_filler(body) >= 1

    def test_no_filler(self):
        body = "The model uses attention with dropout rate 0.1."
        assert _count_filler(body) == 0


class TestDuplicateConcepts:
    def test_finds_duplicates(self):
        pages = [
            PageScore(path="a.md", page_type="concepts", title="Attention Mechanism in Transformers"),
            PageScore(path="b.md", page_type="concepts", title="Attention Mechanism for Transformers"),
        ]
        pairs = _find_duplicate_concepts(pages, threshold=0.5)
        assert len(pairs) == 1

    def test_no_duplicates(self):
        pages = [
            PageScore(path="a.md", page_type="concepts", title="Attention Mechanism"),
            PageScore(path="b.md", page_type="concepts", title="Reinforcement Learning"),
        ]
        pairs = _find_duplicate_concepts(pages, threshold=0.75)
        assert len(pairs) == 0


# ---------------------------------------------------------------------------
# Workspace-level evaluation test
# ---------------------------------------------------------------------------


def _make_wiki_page(wiki_dir: Path, rel_path: str, content: str) -> None:
    path = wiki_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_evaluate_workspace(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # Source page
    _make_wiki_page(wiki_dir, "sources/paper-a.md", textwrap.dedent("""\
        ---
        title: Paper A
        type: source
        status: stable
        updated: "2026-01-01"
        sources: [source_a]
        ---

        # Paper A

        The model uses $E = mc^2$ to compute energy.

        | Metric | Value |
        |---|---|
        | Accuracy | 95% |
    """))

    # Concept page with citation
    _make_wiki_page(wiki_dir, "concepts/attention.md", textwrap.dedent("""\
        ---
        title: Attention Mechanism
        type: concept
        status: stable
        updated: "2026-01-01"
        sources: [source_a, source_b]
        ---

        # Attention Mechanism

        Attention is used in transformer models [[Paper A]].
        The mechanism achieves strong performance [[Paper B]].
    """))

    # Concept page without citation (filler)
    _make_wiki_page(wiki_dir, "concepts/transformers.md", textwrap.dedent("""\
        ---
        title: Transformers
        type: concept
        status: stable
        updated: "2026-01-01"
        sources: [source_a]
        ---

        # Transformers

        This is a significant advancement in the field.
        The model provides a comprehensive overview of the area.
    """))

    # Nav pages (should be skipped)
    _make_wiki_page(wiki_dir, "index.md", "---\ntitle: Index\n---\n# Index\n")
    _make_wiki_page(wiki_dir, "overview.md", "---\ntitle: Overview\n---\n# Overview\n")
    _make_wiki_page(wiki_dir, "log.md", "---\ntitle: Log\n---\n# Log\n")

    report = evaluate_workspace(tmp_path)

    assert report.page_count == 3
    assert report.source_page_count == 1
    assert report.concept_page_count == 2
    assert report.total_equations_in_wiki >= 1
    assert report.total_tables_in_wiki >= 1
    assert report.total_filler_hits >= 1
    assert report.grade() in ("A", "B", "C", "D", "F")

    # The attention page has 2 sources and is stable = good maturity
    # The transformers page has 1 source and is stable = bad maturity
    assert report.stable_pages >= 1


# ---------------------------------------------------------------------------
# Corpus loading test
# ---------------------------------------------------------------------------


def test_load_benchmark_corpus():
    """Verify the real corpus.txt resolves to existing PDFs."""
    from compile.eval import DEFAULT_CORPUS_FILE, DEFAULT_PDF_DIR

    if not DEFAULT_CORPUS_FILE.exists():
        pytest.skip("No benchmark corpus.txt found")
    if not DEFAULT_PDF_DIR.exists():
        pytest.skip("No ArXiv PDF directory found")

    pdfs = load_benchmark_corpus()
    assert len(pdfs) >= 10
    for pdf in pdfs:
        assert pdf.exists(), f"Missing PDF: {pdf}"
        assert pdf.suffix == ".pdf"


class TestGrading:
    def test_perfect_grade(self):
        report = EvalReport(
            citation_density=1.0,
            equation_preservation=1.0,
            table_preservation=1.0,
            duplicate_concept_rate=0.0,
            maturity_accuracy=1.0,
            filler_density=0.0,
        )
        assert report.grade() == "A"

    def test_failing_grade(self):
        report = EvalReport(
            citation_density=0.0,
            equation_preservation=0.0,
            table_preservation=0.0,
            duplicate_concept_rate=1.0,
            maturity_accuracy=0.0,
            filler_density=1.0,
        )
        assert report.grade() == "F"
