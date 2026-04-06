from __future__ import annotations

from compile.verify import verify_page_content


def test_verify_page_content_flags_metadata_leaks() -> None:
    content = """---
title: Retrieval-Augmented Generation
type: concept
status: stable
updated: "2026-01-01"
---

# Retrieval-Augmented Generation

PDF source named 047_the-expert-strikes-back.pdf says retrieval improves results.
"""

    issues = verify_page_content(
        page_type="concept",
        content=content,
        source_count=2,
        valid_link_targets=[],
    )

    assert any(issue.code == "metadata_leak" for issue in issues)


def test_verify_page_content_allows_source_pdf_provenance() -> None:
    content = """---
title: Brief Is Better
type: source
status: stable
updated: "2026-01-01"
---

# Brief Is Better

> [!note] Raw Artifact
> ![[raw/brief-is-better.pdf]]
"""

    issues = verify_page_content(
        page_type="source",
        content=content,
        raw_source_path="raw/brief-is-better.pdf",
        valid_link_targets=["raw/brief-is-better.pdf"],
    )

    assert not any(issue.code == "metadata_leak" for issue in issues)
