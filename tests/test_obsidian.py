from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from compile.cli import main
from compile.obsidian import ObsidianConnector
from compile.workspace import collect_pages_by_type, init_workspace, write_index


def _write_page(path: Path, title: str, page_type: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n"
        f"title: {title}\n"
        f"type: {page_type}\n"
        f"tags:\n"
        f"  - test\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def test_obsidian_connector_inspects_compile_workspace(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")

    _write_page(
        tmp_path / "wiki" / "concepts" / "alpha.md",
        "Alpha",
        "concept",
        "See [[Beta]] and [[Missing Page]].",
    )
    _write_page(
        tmp_path / "wiki" / "concepts" / "beta.md",
        "Beta",
        "concept",
        "Beta links back to [[Alpha]].",
    )

    connector = ObsidianConnector(tmp_path)
    report = connector.inspect()

    assert report.layout == "compile_workspace"
    assert report.obsidian_enabled is True
    assert report.total_pages >= 5
    assert report.resolved_link_count >= 2
    assert report.unresolved_link_count == 1
    assert any(issue.code == "unresolved_links" for issue in report.issues)

    alpha = connector.get_page("Alpha")
    assert alpha.relative_path == "wiki/concepts/alpha.md"
    assert alpha.resolved_outbound_links == ["Beta"]
    assert alpha.unresolved_outbound_links == ["Missing Page"]
    assert alpha.inbound_links == ["Beta"]


def test_obsidian_connector_flags_backend_workspace_shape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "demo"
    pages_dir = workspace_root / "pages"
    pages_dir.mkdir(parents=True)
    (workspace_root / "workspace.json").write_text("{}")

    _write_page(
        pages_dir / "concept--thin-page.md",
        "Thin Page",
        "concept",
        "A short page with no graph links.",
    )

    connector = ObsidianConnector(workspace_root)
    report = connector.inspect()

    assert report.layout == "backend_workspace"
    assert report.obsidian_enabled is False
    assert report.pages_with_wikilinks == 0
    assert any(issue.code == "missing_obsidian_config" for issue in report.issues)
    assert any(issue.code == "no_wikilinks" for issue in report.issues)


def test_obsidian_connector_resolves_raw_file_links_and_json_safe(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    raw_file = tmp_path / "raw" / "source-note.md"
    raw_file.write_text("# Raw source\n\nPrimary source note.")

    _write_page(
        tmp_path / "wiki" / "sources" / "source.md",
        "Source Note",
        "source",
        "See [[raw/source-note.md]] for provenance.",
    )

    connector = ObsidianConnector(tmp_path)
    report = connector.inspect()
    page = connector.get_page("Source Note")

    assert report.unresolved_link_count == 0
    assert page.resolved_file_links == ["raw/source-note.md"]
    json.dumps(report.to_dict())
    json.dumps(page.to_dict(include_body=True))


def test_obsidian_connector_search_and_neighbors(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")

    _write_page(
        tmp_path / "wiki" / "concepts" / "planner-executor-architecture.md",
        "Planner-Executor Architecture",
        "concept",
        "Systematic debugging with [[Tool-First Architecture]].",
    )
    _write_page(
        tmp_path / "wiki" / "concepts" / "tool-first-architecture.md",
        "Tool-First Architecture",
        "concept",
        "Grounding comes from tools and logs.",
    )

    connector = ObsidianConnector(tmp_path)
    hits = connector.search("planner executor", limit=3)
    neighborhood = connector.get_neighborhood("Planner-Executor Architecture")
    fuzzy_page = connector.get_page("planner executor arch")

    assert hits
    assert hits[0].title == "Planner-Executor Architecture"
    assert fuzzy_page.title == "Planner-Executor Architecture"
    assert "Tool-First Architecture" in neighborhood.outbound_pages

    graph = connector.graph()
    assert any(edge.source == "Planner-Executor Architecture" and edge.target == "Tool-First Architecture" for edge in graph.edges)


def test_obsidian_connector_reads_backend_metadata_neighbors(tmp_path: Path) -> None:
    workspace_root = tmp_path / "demo"
    pages_dir = workspace_root / "pages"
    pages_dir.mkdir(parents=True)
    (workspace_root / "workspace.json").write_text("{}")

    (pages_dir / "source.md").write_text(
        "---\n"
        "id: page_source\n"
        "title: Source A\n"
        "page_type: source\n"
        "source_ids:\n"
        "  - source_alpha\n"
        "---\n\n"
        "# Source A\n\n"
        "Source evidence.\n"
    )
    (pages_dir / "related.md").write_text(
        "---\n"
        "id: page_related\n"
        "title: Related Page\n"
        "page_type: concept\n"
        "---\n\n"
        "# Related Page\n\n"
        "Additional synthesis.\n"
    )
    (pages_dir / "concept.md").write_text(
        "---\n"
        "id: page_concept\n"
        "title: Concept A\n"
        "page_type: concept\n"
        "source_ids:\n"
        "  - source_alpha\n"
        "related_page_ids:\n"
        "  - page_related\n"
        "citations:\n"
        "  - source_id: source_alpha\n"
        "    source_title: Source A\n"
        "---\n\n"
        "# Concept A\n\n"
        "Connected by metadata.\n"
    )

    connector = ObsidianConnector(workspace_root)
    neighborhood = connector.get_neighborhood("Concept A")

    assert neighborhood.supporting_source_pages == ["Source A"]
    assert neighborhood.related_pages == ["Related Page"]
    assert neighborhood.cited_source_pages == ["Source A"]


def test_obsidian_cli_inspect_json_output_handles_frontmatter_dates(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    result = runner.invoke(main, ["obsidian", "inspect", "--path", str(tmp_path), "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["layout"] == "compile_workspace"


def test_health_cli_reports_backend_workspace_as_not_obsidian_ready(tmp_path: Path) -> None:
    workspace_root = tmp_path / "demo"
    pages_dir = workspace_root / "pages"
    pages_dir.mkdir(parents=True)
    (workspace_root / "workspace.json").write_text('{"id": "demo-backend"}')

    _write_page(
        pages_dir / "concept--thin-page.md",
        "Thin Page",
        "concept",
        "A short page with no graph links.",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["health", "--path", str(workspace_root), "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["workspace_id"] == "demo-backend"
    assert payload["overall_status"] == "not_obsidian_ready"
    assert payload["obsidian_readiness"]["status"] == "fail"
    assert "healthy" not in payload["summary"].lower()


def test_health_cli_runs(tmp_path: Path) -> None:
    workspace_root = tmp_path / "demo"
    pages_dir = workspace_root / "pages"
    pages_dir.mkdir(parents=True)
    (workspace_root / "workspace.json").write_text('{"id": "demo-backend"}')
    _write_page(
        pages_dir / "concept--thin-page.md",
        "Thin Page",
        "concept",
        "A short page with no graph links.",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["health", "--path", str(workspace_root)])

    assert result.exit_code == 0
    assert "not_obsidian_ready" in result.output


def test_health_cli_runs_content_audit_for_clean_compile_workspace(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    _write_page(
        tmp_path / "wiki" / "articles" / "friendship.md",
        "Friendship",
        "article",
        "Friendship is a durable relation shaped by reciprocity and shared practice.\n\n"
        "It links naturally to [[Index]].",
    )
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    from compile.workspace import write_overview

    write_overview(config, pages_by_type)

    result = runner.invoke(main, ["health", "--path", str(tmp_path), "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["content_health"]["status"] == "pass"


def test_health_cli_flags_malformed_summary(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    (tmp_path / "wiki" / "articles").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "articles" / "broken.md").write_text(
        "---\n"
        "title: Broken Summary\n"
        "type: article\n"
        "status: seed\n"
        "summary: Coupled dynamics shows  at scale.\n"
        "created: 2026-01-01T00:00:00+00:00\n"
        "updated: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Broken Summary\n\n"
        "A page with enough body text to count as real content.\n\n"
        "Another paragraph to avoid thin-page heuristics.\n"
    )
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    from compile.workspace import write_overview

    write_overview(config, pages_by_type)

    result = runner.invoke(main, ["health", "--path", str(tmp_path), "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["content_health"]["status"] == "warn"
    assert any(issue["code"] == "malformed_summary" for issue in payload["issues"])


def test_obsidian_cli_neighbors_accepts_high_confidence_partial_locator(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    _write_page(
        tmp_path / "wiki" / "sources" / "planner-executor-loops.md",
        "Planner-Executor Loops In Debugging Agents",
        "source",
        "See [[Planner-Executor Architecture]].",
    )
    _write_page(
        tmp_path / "wiki" / "concepts" / "planner-executor-architecture.md",
        "Planner-Executor Architecture",
        "concept",
        "Supports execution with tools.",
    )

    result = runner.invoke(
        main,
        [
            "obsidian",
            "neighbors",
            "Planner-Executor Loops",
            "--path",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Planner-Executor Loops In Debugging Agents" in result.output
    assert "Planner-Executor Architecture" in result.output


def test_obsidian_connector_flags_navigation_provenance_and_stale_overview(tmp_path: Path) -> None:
    config = init_workspace(tmp_path, "Test Topic", "Connector coverage.")

    (tmp_path / "raw" / "tracked-source.md").write_text("# Tracked\n\nPrimary source.")
    (tmp_path / "raw" / "untracked-source.md").write_text("# Untracked\n\nMissing source note.")

    _write_page(
        tmp_path / "wiki" / "sources" / "tracked-source.md",
        "Tracked Source",
        "source",
        "Summary page with no raw backlink.",
    )
    _write_page(
        tmp_path / "wiki" / "concepts" / "alpha.md",
        "Alpha Concept",
        "concept",
        "Depends on [[Tracked Source]].",
    )
    _write_page(
        tmp_path / "wiki" / "questions" / "beta-question.md",
        "Beta Question",
        "question",
        "Investigates [[Tracked Source]].",
    )

    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)

    connector = ObsidianConnector(tmp_path)
    report = connector.inspect()

    assert any(issue.code == "stale_navigation_pages" for issue in report.issues)
    assert any(issue.code == "raw_files_without_source_notes" for issue in report.issues)
    assert any(issue.code == "source_pages_without_raw_links" for issue in report.issues)
    assert any(issue.code == "navigation_bottlenecks" for issue in report.issues)
    assert any(issue.code == "navigation_bottlenecks" for issue in report.issues)
    assert report.raw_files_without_source_notes == ["raw/tracked-source.md", "raw/untracked-source.md"]
    assert report.source_pages_without_raw_links == ["wiki/sources/tracked-source.md"]
    assert "wiki/overview.md" in report.stale_navigation_pages
    assert "wiki/concepts/alpha.md" in report.navigation_bottlenecks
    assert "wiki/questions/beta-question.md" in report.navigation_bottlenecks


def test_obsidian_cli_cleanup_quarantines_empty_auxiliary_files(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    ghost = tmp_path / "Planner-Executor Architecture.md"
    ghost.write_text("")

    result = runner.invoke(main, ["obsidian", "cleanup", "--path", str(tmp_path)])

    assert result.exit_code == 0
    assert not ghost.exists()
    quarantined = tmp_path / ".compile" / "quarantine" / "Planner-Executor Architecture.md"
    assert quarantined.exists()


def test_obsidian_cli_upsert_writes_generic_article_page(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "obsidian",
            "upsert",
            "Friendship",
            "--page-type",
            "article",
            "--body",
            "A durable page about reciprocal goodwill.",
            "--path",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    article_path = tmp_path / "wiki" / "articles" / "Friendship.md"
    assert article_path.exists()
    assert "type: article" in article_path.read_text()


def test_obsidian_cli_refresh_rebuilds_generic_navigation(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    _write_page(
        tmp_path / "wiki" / "articles" / "friendship.md",
        "Friendship",
        "article",
        "See [[Virtue]].",
    )

    result = runner.invoke(main, ["obsidian", "refresh", "--path", str(tmp_path)])

    assert result.exit_code == 0
    index_text = (tmp_path / "wiki" / "index.md").read_text()
    overview_text = (tmp_path / "wiki" / "overview.md").read_text()
    assert "[[Friendship]]" in index_text
    assert "[[Friendship]]" in overview_text
    assert "Articles: 1" in overview_text


def test_ingest_cli_creates_source_scaffold_and_updates_nav(tmp_path: Path) -> None:
    init_workspace(tmp_path, "Test Topic", "Connector coverage.")
    runner = CliRunner()

    raw_file = tmp_path / "raw" / "example-source.md"
    raw_file.write_text("# Example Source\n\nA durable raw artifact about friendship and reciprocity.")

    result = runner.invoke(main, ["ingest", "example-source.md", "--path", str(tmp_path)])

    assert result.exit_code == 0
    source_path = tmp_path / "wiki" / "sources" / "Example Source.md"
    assert source_path.exists()
    source_text = source_path.read_text()
    assert "type: source" in source_text
    assert "![[raw/example-source.md]]" in source_text

    index_text = (tmp_path / "wiki" / "index.md").read_text()
    overview_text = (tmp_path / "wiki" / "overview.md").read_text()
    log_text = (tmp_path / "wiki" / "log.md").read_text()
    assert "[[Example Source]]" in index_text
    assert "Sources: 1" in overview_text
    assert "ingest | Example Source" in log_text
