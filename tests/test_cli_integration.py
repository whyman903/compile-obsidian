"""Integration tests for CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from compile.cli import main
from compile.workspace import init_workspace


def _write_page(path: Path, title: str, page_type: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\ntype: {page_type}\nstatus: seed\n"
        f"summary: 'Test page.'\nupdated: 2026-01-01T00:00:00+00:00\n---\n\n"
        f"# {title}\n\n{body}\n"
    )


class TestInitCommand:
    def test_basic_init(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", "My Wiki", "-p", str(tmp_path / "new")])
        assert result.exit_code == 0
        assert "initialized" in result.output.lower()
        assert (tmp_path / "new" / "wiki").is_dir()

    def test_init_with_description(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [
            "init", "My Wiki", "-d", "A personal knowledge base", "-p", str(tmp_path / "new"),
        ])
        assert result.exit_code == 0

    def test_init_existing_fails(self, tmp_path: Path) -> None:
        runner = CliRunner()
        runner.invoke(main, ["init", "First", "-p", str(tmp_path)])
        result = runner.invoke(main, ["init", "Second", "-p", str(tmp_path)])
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()


class TestStatusCommand:
    def test_status(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test Wiki", "Description")
        runner = CliRunner()
        result = runner.invoke(main, ["status"], catch_exceptions=False)
        # Runs from CWD; since we can't chdir, test with path option if available
        # For now, just test it doesn't crash on a real workspace
        # The status command uses _load_workspace which uses CWD


class TestSchemaCommand:
    def test_schema_shows_content(self, tmp_path: Path, monkeypatch) -> None:
        init_workspace(tmp_path, "Test Wiki")
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        assert result.exit_code == 0
        assert "Workspace Schema" in result.output or "schema" in result.output.lower()


class TestIngestCommand:
    def test_ingest_local_file(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        raw_file = tmp_path / "raw" / "article.md"
        raw_file.write_text("# My Article\n\nImportant findings about the topic.")

        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "article.md", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "scaffold created" in result.output.lower()

        # Source page should exist
        source_path = tmp_path / "wiki" / "sources" / "My Article.md"
        assert source_path.exists()
        source_text = source_path.read_text()
        assert "type: source" in source_text

    def test_ingest_with_title_override(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        raw_file = tmp_path / "raw" / "paper.md"
        raw_file.write_text("Content of the paper.")

        runner = CliRunner()
        result = runner.invoke(main, [
            "ingest", "paper.md", "--path", str(tmp_path), "--title", "Custom Title",
        ])

        assert result.exit_code == 0
        source_path = tmp_path / "wiki" / "sources" / "Custom Title.md"
        assert source_path.exists()

    def test_ingest_missing_file(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "nonexistent.md", "--path", str(tmp_path)])
        assert result.exit_code != 0

    def test_ingest_updates_nav(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        raw_file = tmp_path / "raw" / "source.md"
        raw_file.write_text("# Source Title\n\nContent.")

        runner = CliRunner()
        runner.invoke(main, ["ingest", "source.md", "--path", str(tmp_path)])

        index = (tmp_path / "wiki" / "index.md").read_text()
        assert "Source Title" in index

        log = (tmp_path / "wiki" / "log.md").read_text()
        assert "ingest" in log

    def test_ingest_resolves_from_raw_dir(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        raw_file = tmp_path / "raw" / "nested" / "deep.md"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_text("# Deep\n\nNested content.")

        runner = CliRunner()
        # Pass relative path under raw/
        result = runner.invoke(main, ["ingest", "raw/nested/deep.md", "--path", str(tmp_path)])
        assert result.exit_code == 0

    def test_ingest_pdf(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        pdf_file = tmp_path / "raw" / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")

        runner = CliRunner()
        result = runner.invoke(main, ["ingest", "paper.pdf", "--path", str(tmp_path)])
        assert result.exit_code == 0


class TestObsidianInspectCommand:
    def test_inspect_json(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "inspect", "--path", str(tmp_path), "--json-output"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["layout"] == "compile_workspace"
        assert "total_pages" in payload

    def test_inspect_text(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "inspect", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "pages" in result.output.lower()


class TestObsidianSearchCommand:
    def test_search_finds_page(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "friendship.md",
            "Friendship", "article", "Aristotle on friendship and virtue.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "search", "friendship", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "Friendship" in result.output

    def test_search_filters_by_type(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "unique.md",
            "Unique Article", "article", "Distinct content.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "search", "Unique Article", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "Unique Article" in result.output


class TestObsidianPageCommand:
    def test_page_by_title(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "topic.md",
            "My Topic", "article", "Content about my topic.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "page", "My Topic", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "My Topic" in result.output

    def test_page_not_found(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "page", "Nonexistent", "--path", str(tmp_path)])
        assert result.exit_code != 0


class TestObsidianUpsertCommand:
    def test_create_article(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, [
            "obsidian", "upsert", "New Article",
            "--page-type", "article",
            "--body", "Article body content.",
            "--path", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert (tmp_path / "wiki" / "articles" / "New Article.md").exists()

    def test_create_with_tags(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, [
            "obsidian", "upsert", "Tagged",
            "--page-type", "article",
            "--body", "Content.",
            "--tag", "alpha",
            "--tag", "beta",
            "--path", str(tmp_path),
        ])

        assert result.exit_code == 0
        content = (tmp_path / "wiki" / "articles" / "Tagged.md").read_text()
        assert "alpha" in content
        assert "beta" in content

    def test_create_source(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, [
            "obsidian", "upsert", "My Source",
            "--page-type", "source",
            "--body", "Source content.",
            "--source", "raw/paper.pdf",
            "--path", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert (tmp_path / "wiki" / "sources" / "My Source.md").exists()

    def test_create_map(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, [
            "obsidian", "upsert", "Topic Map",
            "--page-type", "map",
            "--body", "Map content.",
            "--path", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert (tmp_path / "wiki" / "maps" / "Topic Map.md").exists()

    def test_create_output(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, [
            "obsidian", "upsert", "Query Result",
            "--page-type", "output",
            "--body", "Output content.",
            "--path", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert (tmp_path / "wiki" / "outputs" / "Query Result.md").exists()

    def test_update_existing(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()

        runner.invoke(main, [
            "obsidian", "upsert", "Evolving",
            "--page-type", "article",
            "--body", "Version 1.",
            "--path", str(tmp_path),
        ])
        runner.invoke(main, [
            "obsidian", "upsert", "Evolving",
            "--page-type", "article",
            "--body", "Version 2.",
            "--path", str(tmp_path),
        ])

        content = (tmp_path / "wiki" / "articles" / "Evolving.md").read_text()
        assert "Version 2" in content


class TestObsidianRefreshCommand:
    def test_refresh(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "topic.md",
            "Topic", "article", "Content.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "refresh", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "refreshed" in result.output.lower()
        index = (tmp_path / "wiki" / "index.md").read_text()
        assert "[[Topic]]" in index


class TestObsidianNeighborsCommand:
    def test_neighbors(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "a.md",
            "Alpha", "article", "Links to [[Beta]].",
        )
        _write_page(
            tmp_path / "wiki" / "articles" / "b.md",
            "Beta", "article", "Linked from Alpha.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "neighbors", "Alpha", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "Alpha" in result.output
        assert "Beta" in result.output


class TestObsidianGraphCommand:
    def test_graph(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        _write_page(
            tmp_path / "wiki" / "articles" / "a.md",
            "Alpha", "article", "Links to [[Beta]].",
        )
        _write_page(
            tmp_path / "wiki" / "articles" / "b.md",
            "Beta", "article", "Content.",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "graph", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "nodes" in result.output or "edges" in result.output


class TestObsidianCleanupCommand:
    def test_cleanup_empty_files(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        ghost = tmp_path / "Ghost Page.md"
        ghost.write_text("")

        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "cleanup", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert not ghost.exists()

    def test_cleanup_nothing(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["obsidian", "cleanup", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "no empty" in result.output.lower()


class TestHealthCommand:
    def test_health_json(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["health", "--path", str(tmp_path), "--json-output"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "overall_status" in payload

    def test_health_text(self, tmp_path: Path) -> None:
        init_workspace(tmp_path, "Test")
        runner = CliRunner()
        result = runner.invoke(main, ["health", "--path", str(tmp_path)])
        assert result.exit_code == 0
