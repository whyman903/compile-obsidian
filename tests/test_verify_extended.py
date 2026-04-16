from __future__ import annotations

from pathlib import Path

from compile.verify import (
    VerificationIssue,
    _has_empty_section,
    audit_vault_content,
    verify_page_content,
)
from compile.workspace import init_workspace, collect_pages_by_type, write_index, write_overview


def _write_page(path: Path, title: str, page_type: str, body: str, **extra_fm: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [f"title: {title}", f"type: {page_type}"]
    for k, v in extra_fm.items():
        fm_lines.append(f"{k}: {v}")
    fm = "\n".join(fm_lines)
    path.write_text(f"---\n{fm}\n---\n\n# {title}\n\n{body}\n")


class TestVerifyPageContent:
    def test_missing_title(self) -> None:
        content = "---\ntype: article\nupdated: '2026-01-01'\n---\n\nBody text."
        issues = verify_page_content(page_type="article", content=content)
        assert any(i.code == "missing_frontmatter" and "title" in i.message for i in issues)

    def test_missing_type(self) -> None:
        content = "---\ntitle: Test\nupdated: '2026-01-01'\n---\n\nBody text."
        issues = verify_page_content(page_type="article", content=content)
        assert any(i.code == "missing_frontmatter" and "type" in i.message for i in issues)

    def test_missing_updated(self) -> None:
        content = "---\ntitle: Test\ntype: article\n---\n\nBody text."
        issues = verify_page_content(page_type="article", content=content)
        assert any(i.code == "missing_frontmatter" and "updated" in i.message for i in issues)

    def test_all_frontmatter_present(self) -> None:
        content = "---\ntitle: Test\ntype: article\nstatus: seed\nsummary: A test page\ncreated: '2026-01-01'\nupdated: '2026-01-01'\n---\n\nBody text that is long enough to count as a real paragraph in this test."
        issues = verify_page_content(page_type="article", content=content)
        assert not any(i.code == "missing_frontmatter" for i in issues)

    def test_source_provenance_present(self) -> None:
        content = (
            "---\ntitle: Source\ntype: source\nupdated: '2026-01-01'\n---\n\n"
            "# Source\n\nSummary. See ![[raw/paper.pdf]] for the original.\n\n"
            "More text to avoid thin content warning easily."
        )
        issues = verify_page_content(
            page_type="source",
            content=content,
            raw_source_path="raw/paper.pdf",
        )
        assert not any(i.code == "missing_provenance" for i in issues)

    def test_resolved_wikilinks_pass(self) -> None:
        content = "---\ntitle: Test\ntype: article\nupdated: '2026-01-01'\n---\n\nSee [[Known Page]] for more information and context."
        issues = verify_page_content(
            page_type="article",
            content=content,
            valid_link_targets=["Known Page"],
        )
        assert not any(i.code == "unresolved_wikilink" for i in issues)

    def test_raw_links_not_flagged(self) -> None:
        content = "---\ntitle: Test\ntype: source\nupdated: '2026-01-01'\n---\n\nSee [[raw/file.md]]."
        issues = verify_page_content(
            page_type="source",
            content=content,
            valid_link_targets=["Other"],
        )
        assert not any(i.code == "unresolved_wikilink" for i in issues)

    def test_thin_content(self) -> None:
        content = "---\ntitle: Test\ntype: article\nupdated: '2026-01-01'\n---\n\n# Test\n\nShort."
        issues = verify_page_content(page_type="article", content=content)
        assert any(i.code == "thin_content" for i in issues)

    def test_sufficient_content(self) -> None:
        content = (
            "---\ntitle: Test\ntype: article\nupdated: '2026-01-01'\n---\n\n"
            "This is a substantial first paragraph with enough length to be counted.\n\n"
            "And this is a second paragraph, also with enough text to be meaningful.\n"
        )
        issues = verify_page_content(page_type="article", content=content)
        assert not any(i.code == "thin_content" for i in issues)


class TestHasEmptySection:
    def test_no_sections(self) -> None:
        assert _has_empty_section("Just body text.") is False

    def test_populated_section(self) -> None:
        body = "## Section\n\nContent here."
        assert _has_empty_section(body) is False

    def test_empty_middle_section(self) -> None:
        body = "## Section A\n\n## Section B\n\nContent."
        assert _has_empty_section(body) is True

    def test_empty_trailing_section(self) -> None:
        body = "## Section\n\nContent.\n\n## Empty Section\n"
        assert _has_empty_section(body) is True

    def test_section_with_only_comment(self) -> None:
        body = "## Section\n\n<!-- just a comment -->\n"
        assert _has_empty_section(body) is True

    def test_multiple_populated_sections(self) -> None:
        body = "## A\n\nContent A.\n\n## B\n\nContent B.\n"
        assert _has_empty_section(body) is False


class TestAuditVaultContent:
    def test_flags_malformed_summary(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        (tmp_path / "wiki" / "articles" / "broken.md").write_text(
            "---\ntitle: Broken\ntype: article\nstatus: seed\n"
            "summary: 'Words followed  by double space.'\n"
            "updated: 2026-01-01 00:00\n---\n\n"
            "# Broken\n\nEnough body text here.\n\nAnother paragraph.\n"
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "malformed_summary" for i in issues)

    def test_flags_placeholder_content(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "stub.md",
            "Stub", "article",
            "_Saved outputs will appear here as queries are filed._\n\nMore text.",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "placeholder_content" for i in issues)

    def test_flags_empty_section(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "hollow.md",
            "Hollow", "article",
            "## Introduction\n\nReal content here.\n\n## Empty Part\n",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "empty_section" for i in issues)

    def test_flags_premature_stability(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "sources" / "only-source.md",
            "Only Source", "source",
            "Source body.\n\nAnother paragraph.",
        )
        _write_page(
            tmp_path / "wiki" / "articles" / "premature.md",
            "Premature", "article",
            "Content here citing [[Only Source]].\n\nAnother paragraph.",
            status="stable",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "premature_stability" for i in issues)

    def test_stable_article_with_multiple_source_links_does_not_flag(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        for name in ("Alpha", "Beta", "Gamma"):
            _write_page(
                tmp_path / "wiki" / "sources" / f"{name.lower()}.md",
                name, "source",
                f"{name} source body.\n\nAnother paragraph.",
            )
        _write_page(
            tmp_path / "wiki" / "articles" / "mature.md",
            "Mature", "article",
            "Synthesis across [[Alpha]], [[Beta]], and [[Gamma]].\n\nAnother paragraph.",
            status="stable",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(
            i["type"] == "premature_stability" and "Mature" in i["title"]
            for i in issues
        )

    def test_stable_article_supported_by_inbound_source_backlinks(self, tmp_path: Path) -> None:
        """An article with 3 source notes linking TO it (and none cited in the body)
        is still well-supported and must not be flagged premature_stability."""
        init_workspace(tmp_path, "Test")
        for name in ("Alpha", "Beta", "Gamma"):
            _write_page(
                tmp_path / "wiki" / "sources" / f"{name.lower()}.md",
                name, "source",
                f"{name} source body. See [[Hub]] for synthesis.\n\nAnother paragraph.",
            )
        _write_page(
            tmp_path / "wiki" / "articles" / "hub.md",
            "Hub", "article",
            "Synthesis prose with no wikilinks.\n\nSecond paragraph of real content.",
            status="stable",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(
            i["type"] == "premature_stability" and "Hub" in i["title"]
            for i in issues
        )

    def test_stable_article_supported_by_source_ids_frontmatter(self, tmp_path: Path) -> None:
        """An article whose provenance lives in ``source_ids`` frontmatter
        (rather than body wikilinks) is still well-supported."""
        init_workspace(tmp_path, "Test")
        tmp_path.joinpath("wiki", "sources").mkdir(parents=True, exist_ok=True)
        for name, source_id in (("Alpha", "sid-1"), ("Beta", "sid-2"), ("Gamma", "sid-3")):
            (tmp_path / "wiki" / "sources" / f"{name.lower()}.md").write_text(
                f"---\ntitle: {name}\ntype: source\nstatus: stable\n"
                f"source_ids:\n  - {source_id}\n---\n\n# {name}\n\n{name} body.\n\nPara two.\n"
            )
        (tmp_path / "wiki" / "articles").mkdir(parents=True, exist_ok=True)
        (tmp_path / "wiki" / "articles" / "structured.md").write_text(
            "---\ntitle: Structured\ntype: article\nstatus: stable\n"
            "source_ids:\n  - sid-1\n  - sid-2\n  - sid-3\n---\n\n"
            "# Structured\n\nSynthesis referencing all three via frontmatter.\n\nPara two.\n"
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(
            i["type"] == "premature_stability" and "Structured" in i["title"]
            for i in issues
        )

    def test_stable_article_supported_by_raw_source_paths_frontmatter(self, tmp_path: Path) -> None:
        """Articles written with ``sources: [raw/...]`` should still resolve to
        their corresponding source notes for editorial support counting."""
        init_workspace(tmp_path, "Test")
        tmp_path.joinpath("raw").mkdir(parents=True, exist_ok=True)
        tmp_path.joinpath("raw", "paper-a.pdf").write_text("stub")
        tmp_path.joinpath("raw", "paper-b.pdf").write_text("stub")
        tmp_path.joinpath("raw", "paper-c.pdf").write_text("stub")
        tmp_path.joinpath("wiki", "sources").mkdir(parents=True, exist_ok=True)
        for slug, title in (("paper-a", "Paper A"), ("paper-b", "Paper B"), ("paper-c", "Paper C")):
            (tmp_path / "wiki" / "sources" / f"{slug}.md").write_text(
                f"---\ntitle: {title}\ntype: source\nstatus: stable\n"
                f"sources:\n  - raw/{slug}.pdf\n---\n\n# {title}\n\n{title} body.\n\nPara two.\n"
            )
        tmp_path.joinpath("wiki", "articles").mkdir(parents=True, exist_ok=True)
        (tmp_path / "wiki" / "articles" / "raw-backed.md").write_text(
            "---\ntitle: Raw Backed\ntype: article\nstatus: stable\n"
            "sources:\n  - raw/paper-a.pdf\n  - raw/paper-b.pdf\n  - raw/paper-c.pdf\n---\n\n"
            "# Raw Backed\n\nSynthesis carried in raw-path provenance.\n\nPara two.\n"
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(
            i["type"] == "premature_stability" and "Raw Backed" in i["title"]
            for i in issues
        )

    def test_flags_source_without_topic_anchor(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "sources" / "lonely.md",
            "Lonely Source",
            "source",
            "Source note body.\n\nAnother paragraph.",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "source_without_topic_anchor" for i in issues)

    def test_source_to_source_link_does_not_satisfy_topic_anchor(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "sources" / "source-a.md",
            "Source A",
            "source",
            "See [[Source B]] for related context.\n\nAnother paragraph.",
        )
        _write_page(
            tmp_path / "wiki" / "sources" / "source-b.md",
            "Source B",
            "source",
            "Related source note.\n\nAnother paragraph.",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert any(i["type"] == "source_without_topic_anchor" and "Source A" in i["title"] for i in issues)
        assert any(i["type"] == "source_without_topic_anchor" and "Source B" in i["title"] for i in issues)

    def test_article_link_satisfies_topic_anchor(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "topic.md",
            "Topic",
            "article",
            "Topic overview.\n\nAnother paragraph.",
        )
        _write_page(
            tmp_path / "wiki" / "sources" / "source.md",
            "Anchored Source",
            "source",
            "Connects to [[Topic]].\n\nAnother paragraph.",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(i["type"] == "source_without_topic_anchor" for i in issues)

    def test_clean_vault_no_issues(self, tmp_path: Path) -> None:
        config = init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "clean.md",
            "Clean", "article",
            "A well-written paragraph with enough content to pass checks.\n\n"
            "A second paragraph that adds substance to the article.",
            status="seed",
        )
        _refresh(tmp_path)

        issues = audit_vault_content(tmp_path)
        assert not any(
            i["type"] in ("malformed_summary", "placeholder_content", "empty_section", "premature_stability")
            for i in issues
        )


def _refresh(tmp_path: Path) -> None:
    from compile.config import load_config
    config = load_config(tmp_path)
    pages = collect_pages_by_type(config)
    write_index(config, pages)
    write_overview(config, pages)
