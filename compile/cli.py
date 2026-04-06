from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from compile.config import Config, load_config
from compile.obsidian import ObsidianConnector
from compile.store import EvidenceDatabase
from compile.workspace import (
    append_log_entry,
    collect_pages_by_type,
    get_status,
    get_unprocessed,
    init_workspace,
    read_schema,
    refresh_schema,
    write_index,
    write_overview,
)

console = Console()


def _load_workspace() -> Config:
    """Load config with clean error handling."""
    try:
        return load_config()
    except FileNotFoundError:
        console.print("[red]No workspace found in current directory. Run 'compile init' first.[/red]")
        raise SystemExit(1)


def _get_compiler(config: Config):
    from compile.compiler import Compiler
    try:
        return Compiler(config)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)


def _load_compile_config_from_vault(root: Path) -> Config:
    try:
        return load_config(root)
    except FileNotFoundError:
        console.print("[red]This command requires a Compile workspace with .compile/config.yaml.[/red]")
        raise SystemExit(1)


@click.group()
def main() -> None:
    """Compile — an LLM-maintained research wiki."""
    pass


@main.command()
@click.argument("topic")
@click.option("--description", "-d", default="", help="Topic framing / description.")
@click.option("--path", "-p", default=".", help="Directory to create workspace in.")
def init(topic: str, description: str, path: str) -> None:
    """Create a new research workspace."""
    root = Path(path).resolve()
    try:
        config = init_workspace(root, topic, description)
        console.print(f"[bold green]Workspace initialized:[/bold green] {config.topic}")
        console.print(f"  Location: {root}")
        console.print(f"  Drop sources into: {config.raw_dir}")
        console.print(f"  Wiki pages at: {config.wiki_dir}")
        console.print(f"  Open in Obsidian: File > Open Vault > {root}")
        console.print("\nNext: add files to raw/ and run [bold]compile ingest[/bold]")
    except FileExistsError:
        console.print(f"[red]Workspace already exists at {root}[/red]")
        raise SystemExit(1)


@main.command()
@click.argument("path", required=False)
@click.option("--parallelism", default=4, show_default=True, help="Maximum parallel source analyses/page compiles.")
@click.option("--limit", type=int, default=0, show_default=True, help="Limit batch ingest to the first N unprocessed files.")
def ingest(path: str | None, parallelism: int, limit: int) -> None:
    """Process raw sources into the wiki.

    If PATH is given, process that specific file.
    Otherwise, process all unprocessed files in raw/.
    """
    config = _load_workspace()
    compiler = _get_compiler(config)

    from compile.ingest import ingest_source, ingest_sources, ingest_url, run_synthesis_pass

    if path and (path.startswith("http://") or path.startswith("https://")):
        # URL ingestion
        try:
            ingest_url(config, compiler, path)
        except Exception as e:
            console.print(f"[red]URL ingest error: {e}[/red]")
            raise SystemExit(1)
    elif path:
        raw_path = Path(path).resolve()
        if not raw_path.exists():
            console.print(f"[red]File not found: {raw_path}[/red]")
            raise SystemExit(1)
        try:
            ingest_source(config, compiler, raw_path)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            raise SystemExit(1)
    else:
        unprocessed = get_unprocessed(config)
        if limit > 0:
            unprocessed = unprocessed[:limit]
        if not unprocessed:
            console.print("[dim]No unprocessed files in raw/. Nothing to do.[/dim]")
            return
        console.print(f"[bold]Processing {len(unprocessed)} source(s)...[/bold]")
        for raw_path in unprocessed:
            console.print(f"  queued: {raw_path.name}", style="dim")
        try:
            ingest_sources(config, compiler, unprocessed, max_workers=max(1, parallelism))
        except Exception as e:
            console.print(f"[red]Batch ingest failed: {e}[/red]")
            raise SystemExit(1)
        if len(unprocessed) >= 2:
            try:
                run_synthesis_pass(config, compiler)
            except Exception as e:
                console.print(f"[red]Synthesis pass failed: {e}[/red]")

    if hasattr(compiler, 'usage') and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.command()
@click.argument("question")
@click.option("--save/--no-save", default=False, help="Save the answer as a wiki output page.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["auto", "note", "comparison", "marp", "mermaid", "chart-spec"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Requested output shape for the answer.",
)
@click.option("--verbose", is_flag=True, default=False, help="Print which pages were selected at each tier.")
def query(question: str, save: bool, output_format: str, verbose: bool) -> None:
    """Ask a question against the wiki."""
    config = _load_workspace()
    compiler = _get_compiler(config)

    from compile.query import answer_query
    try:
        answer_query(config, compiler, question, save=save, output_format=output_format, verbose=verbose)
    except Exception as e:
        console.print(f"[red]Query failed: {e}[/red]")
        raise SystemExit(1)

    if hasattr(compiler, 'usage') and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.command()
def lint() -> None:
    """Run the LLM content audit on the wiki."""
    config = _load_workspace()
    compiler = _get_compiler(config)

    from compile.lint import lint_wiki
    try:
        lint_wiki(config, compiler)
    except Exception as e:
        console.print(f"[red]Lint failed: {e}[/red]")
        raise SystemExit(1)

    if hasattr(compiler, 'usage') and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.command("health")
@click.option("--path", "-p", default=".", help="Workspace, vault, or backend workspace path.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
@click.option("--content-audit/--no-content-audit", default=False, help="Include the LLM content audit for Compile workspaces.")
@click.option("--write-snapshot/--no-write-snapshot", default=False, help="Write the health report to health/latest.json under the target root.")
def health(path: str, json_output: bool, content_audit: bool, write_snapshot: bool) -> None:
    """Run the canonical workspace health report."""
    root = Path(path).resolve()

    content_issues = None
    compiler = None
    config: Config | None = None
    if content_audit:
        config = _load_compile_config_from_vault(root)
        compiler = _get_compiler(config)
        from compile.lint import collect_lint_issues

        content_issues = collect_lint_issues(config, compiler)

    from compile.health import build_health_report, write_health_snapshot

    report = build_health_report(root, content_issues=content_issues)
    snapshot_path: Path | None = None
    if write_snapshot:
        snapshot_path = write_health_snapshot(root, report)

    if json_output:
        click.echo(json.dumps(report, indent=2))
        return

    console.print()
    console.print(f"[bold]{report['overall_status']}[/bold]")
    console.print(f"  Root: {report['root']}")
    console.print(f"  Layout: {report['layout']}")
    console.print(f"  Summary: {report['summary']}")

    summary = Table(title="Health Summary")
    summary.add_column("Section", style="bold")
    summary.add_column("Status")
    summary.add_column("Counts")
    summary.add_row(
        "Obsidian readiness",
        str(report["obsidian_readiness"]["status"]),
        ", ".join(f"{key}={value}" for key, value in report["obsidian_readiness"]["counts"].items()),
    )
    summary.add_row(
        "Graph health",
        str(report["graph_health"]["status"]),
        ", ".join(f"{key}={value}" for key, value in report["graph_health"]["counts"].items()),
    )
    summary.add_row(
        "Content health",
        str(report["content_health"]["status"]),
        ", ".join(f"{key}={value}" for key, value in report["content_health"]["counts"].items()),
    )
    console.print()
    console.print(summary)

    metrics = Table(title="Key Metrics")
    metrics.add_column("Metric", style="bold")
    metrics.add_column("Value")
    for key, value in report["metrics"].items():
        metrics.add_row(key.replace("_", " "), str(value))
    console.print()
    console.print(metrics)

    if report["issues"]:
        issues = Table(title="Issues")
        issues.add_column("Category", style="bold", width=20)
        issues.add_column("Severity", width=8)
        issues.add_column("Code", width=28)
        issues.add_column("Message", min_width=40)
        for issue in report["issues"]:
            issues.add_row(
                str(issue.get("category") or ""),
                str(issue.get("severity") or ""),
                str(issue.get("code") or ""),
                str(issue.get("message") or ""),
            )
        console.print()
        console.print(issues)

    if snapshot_path is not None:
        console.print()
        console.print(f"[green]Wrote health snapshot:[/green] {snapshot_path}")

    if compiler is not None and config is not None and hasattr(compiler, "usage") and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.command()
def watch() -> None:
    """Watch raw/ for new files and auto-ingest them."""
    config = _load_workspace()
    compiler = _get_compiler(config)

    from compile.watcher import watch_raw
    watch_raw(config, compiler)


@main.command()
def status() -> None:
    """Show workspace status."""
    config = _load_workspace()
    info = get_status(config)

    table = Table(title=info["topic"])
    table.add_column("", style="bold")
    table.add_column("")

    table.add_row("Topic", info["topic"])
    table.add_row("Description", info["description"])
    table.add_row("Location", info["workspace_root"])
    table.add_row("Raw files", str(info["raw_files"]))
    table.add_row("Processed", str(info["processed"]))
    table.add_row("Unprocessed", str(info["unprocessed"]))
    table.add_row("Wiki pages", str(info["wiki_pages"]))

    console.print()
    console.print(table)
    console.print()

    if info["unprocessed"] > 0:
        console.print(f"[yellow]Run 'compile ingest' to process {info['unprocessed']} new source(s).[/yellow]")


@main.command("synthesize")
@click.option("--max-concepts", default=3, show_default=True, help="Maximum concept pages to rewrite in one pass.")
@click.option("--max-comparisons", default=3, show_default=True, help="Maximum comparison pages to generate in one pass.")
@click.option("--cleanup-empty-notes/--no-cleanup-empty-notes", default=True, help="Quarantine empty root-level markdown artifacts after synthesis.")
def synthesize(max_concepts: int, max_comparisons: int, cleanup_empty_notes: bool) -> None:
    """Run a synthesis maintenance pass across the existing evidence graph."""
    config = _load_workspace()
    compiler = _get_compiler(config)

    from compile.ingest import run_synthesis_pass

    try:
        touched = run_synthesis_pass(
            config,
            compiler,
            max_concepts=max_concepts,
            max_comparisons=max_comparisons,
            cleanup_empty_notes=cleanup_empty_notes,
        )
    except Exception as e:
        console.print(f"[red]Synthesis failed: {e}[/red]")
        raise SystemExit(1)

    console.print(f"[green]Synthesis touched {len(touched)} page(s).[/green]")

    if hasattr(compiler, 'usage') and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.group()
def schema() -> None:
    """View or refresh the workspace WIKI.md schema."""
    pass


@schema.command("show")
def schema_show() -> None:
    """Print the current WIKI.md schema."""
    config = _load_workspace()
    content = read_schema(config)
    if not content:
        console.print("[dim]No WIKI.md found. Run 'compile init' to create one.[/dim]")
        return
    console.print()
    console.print(Markdown(content))


@schema.command("refresh")
def schema_refresh() -> None:
    """Ask the LLM whether the schema should be updated, and apply changes."""
    config = _load_workspace()
    compiler = _get_compiler(config)

    current_schema = read_schema(config)
    if not current_schema:
        console.print("[dim]No WIKI.md found. Run 'compile init' to create one.[/dim]")
        return

    # Gather wiki state from page_catalog if available, else fall back to directory counts
    source_count = 0
    concept_count = 0
    entity_count = 0
    question_count = 0

    try:
        from compile.store import EvidenceDatabase
        store = EvidenceDatabase(config.evidence_db_path)
        catalog = store.page_catalog()
        for entry in catalog:
            pt = entry.get("page_type", "")
            if pt == "source":
                source_count += 1
            elif pt == "concept":
                concept_count += 1
            elif pt == "entity":
                entity_count += 1
            elif pt == "question":
                question_count += 1
    except Exception:
        # Fall back to directory counting
        pages_by_type = collect_pages_by_type(config)
        source_count = len(pages_by_type.get("sources", []))
        concept_count = len(pages_by_type.get("concepts", []))
        entity_count = len(pages_by_type.get("entities", []))
        question_count = len(pages_by_type.get("questions", []))

    prompt = (
        f'You are reviewing the workspace schema for a research wiki on "{config.topic}".\n'
        f"\n"
        f"Current schema:\n{current_schema}\n\n"
        f"Current wiki state:\n"
        f"- {source_count} source pages\n"
        f"- {concept_count} concept pages\n"
        f"- {entity_count} entity pages\n"
        f"- {question_count} question pages\n\n"
        f'If the schema should be updated to better reflect the current state '
        f'(new conventions, new page types, adjusted synthesis heuristics), '
        f'output ONLY the new or changed sections. '
        f'If no changes are needed, output exactly "NO_CHANGES".'
    )

    console.print("[dim]Asking the LLM to review the schema...[/dim]")
    response = compiler._call_text(
        system="You are a research wiki schema advisor.",
        prompt=prompt,
        max_tokens=2048,
        method_name="schema_refresh",
    )

    if response.strip() == "NO_CHANGES":
        console.print("[green]The LLM found no schema changes needed.[/green]")
        return

    written = refresh_schema(config, response)
    if written:
        append_log_entry(config, "schema", "Schema refresh", ["LLM-suggested schema revision applied."])
        console.print("[green]Schema updated. New revision appended to WIKI.md.[/green]")
        console.print()
        console.print(Markdown(read_schema(config)))
    else:
        console.print("[dim]No changes were written.[/dim]")

    if hasattr(compiler, 'usage') and compiler.usage.calls > 0:
        console.print(f"\n[dim]{compiler.usage.summary(config.anthropic_model)}[/dim]")


@main.group()
def obsidian() -> None:
    """Inspect Obsidian vault metadata and graph quality."""
    pass


@obsidian.command("inspect")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
def obsidian_inspect(path: str, json_output: bool) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    report = connector.inspect()

    if json_output:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return

    console.print()
    console.print(f"[bold]{report.layout}[/bold]")
    console.print(f"  Root: {report.root}")
    console.print(f"  Page root: {report.page_root}")
    console.print(f"  Obsidian config: {'yes' if report.obsidian_enabled else 'no'}")
    if report.obsidian_files:
        console.print(f"  .obsidian files: {', '.join(report.obsidian_files)}")

    summary = Table(title="Vault Summary")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value")
    summary.add_row("Pages", str(report.total_pages))
    summary.add_row("Page types", ", ".join(f"{key}={value}" for key, value in report.page_type_counts.items()) or "none")
    summary.add_row("Pages with frontmatter", str(report.pages_with_frontmatter))
    summary.add_row("Pages with wikilinks", str(report.pages_with_wikilinks))
    summary.add_row("Outbound links", str(report.total_outbound_links))
    summary.add_row("Resolved links", str(report.resolved_link_count))
    summary.add_row("Resolved file links", str(report.resolved_file_link_count))
    summary.add_row("Unresolved links", str(report.unresolved_link_count))
    summary.add_row("Orphan pages", str(report.orphan_page_count))
    summary.add_row("Knowledge pages", str(report.knowledge_page_count))
    if report.knowledge_page_count:
        summary.add_row(
            "With non-nav backlinks",
            f"{report.knowledge_pages_with_non_nav_inbound}/{report.knowledge_page_count}",
        )
    summary.add_row("Single-source synthesis", str(len(report.single_source_synthesis_pages)))
    summary.add_row("Raw files", str(report.raw_file_count))
    summary.add_row("Raw files without source notes", str(len(report.raw_files_without_source_notes)))
    summary.add_row("Stale nav pages", str(len(report.stale_navigation_pages)))
    summary.add_row("Auxiliary markdown files", str(len(report.auxiliary_markdown_files)))
    console.print()
    console.print(summary)

    if report.issues:
        issues = Table(title="Quality Signals")
        issues.add_column("Severity", style="bold", width=8)
        issues.add_column("Code", width=22)
        issues.add_column("Message", min_width=48)
        for issue in report.issues:
            issues.add_row(issue.severity, issue.code, issue.message)
        console.print()
        console.print(issues)

    issue_codes = {issue.code for issue in report.issues}
    details = Table(title="Focused Audit")
    details.add_column("Signal", style="bold", width=28)
    details.add_column("Examples", min_width=48)
    has_details = False

    if "navigation_bottlenecks" in issue_codes and report.navigation_bottlenecks:
        details.add_row(
            "Navigation bottlenecks",
            ", ".join(report.navigation_bottlenecks[:5]),
        )
        has_details = True
    if "limited_cross_source_synthesis" in issue_codes and report.single_source_synthesis_pages:
        details.add_row(
            "Single-source synthesis",
            ", ".join(report.single_source_synthesis_pages[:5]),
        )
        has_details = True
    if "raw_files_without_source_notes" in issue_codes and report.raw_files_without_source_notes:
        details.add_row(
            "Untracked raw files",
            ", ".join(report.raw_files_without_source_notes[:5]),
        )
        has_details = True
    if "source_pages_without_raw_links" in issue_codes and report.source_pages_without_raw_links:
        details.add_row(
            "Source pages missing raw links",
            ", ".join(report.source_pages_without_raw_links[:5]),
        )
        has_details = True
    if "stale_navigation_pages" in issue_codes and report.stale_navigation_pages:
        details.add_row(
            "Stale nav pages",
            ", ".join(report.stale_navigation_pages[:5]),
        )
        has_details = True
    if "empty_markdown_files" in issue_codes and report.empty_markdown_files:
        details.add_row(
            "Empty markdown files",
            ", ".join(report.empty_markdown_files[:5]),
        )
        has_details = True

    if has_details:
        console.print()
        console.print(details)


@obsidian.command("page")
@click.argument("locator")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
@click.option("--body/--no-body", default=True, help="Include page body in the output.")
def obsidian_page(locator: str, path: str, json_output: bool, body: bool) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    try:
        page = connector.get_page(locator)
    except FileNotFoundError:
        console.print(f"[red]Page not found:[/red] {locator}")
        suggestions = connector.search(locator, limit=5)
        if suggestions:
            console.print("Closest matches:")
            for hit in suggestions:
                console.print(f"  - {hit.title} ({hit.relative_path})")
        raise SystemExit(1)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise SystemExit(1)

    if json_output:
        click.echo(json.dumps(page.to_dict(include_body=body), indent=2))
        return

    console.print()
    console.print(f"[bold]{page.title}[/bold]")
    console.print(f"  Path: {page.relative_path}")
    console.print(f"  Type: {page.page_type}")
    console.print(f"  Words: {page.word_count}")
    if page.tags:
        console.print(f"  Tags: {', '.join(page.tags)}")
    console.print(f"  Frontmatter: {'yes' if page.has_frontmatter else 'no'}")
    console.print(f"  Outbound links: {len(page.resolved_outbound_links)}")
    if page.resolved_outbound_links:
        console.print(f"  Resolved targets: {', '.join(page.resolved_outbound_links)}")
    if page.resolved_file_links:
        console.print(f"  File links: {', '.join(page.resolved_file_links)}")
    if page.unresolved_outbound_links:
        console.print(f"  Unresolved targets: {', '.join(page.unresolved_outbound_links)}")
    if page.inbound_links:
        console.print(f"  Inbound links: {', '.join(page.inbound_links)}")
    if body and page.body:
        console.print()
        console.print(Markdown(page.body))


@obsidian.command("search")
@click.argument("query")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--limit", "-n", default=10, show_default=True, help="Maximum number of hits.")
@click.option("--page-type", default=None, help="Filter hits to a specific page type.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
def obsidian_search(
    query: str,
    path: str,
    limit: int,
    page_type: str | None,
    json_output: bool,
) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    hits = connector.search(query, limit=limit, page_type=page_type)

    if json_output:
        click.echo(json.dumps([hit.to_dict() for hit in hits], indent=2))
        return

    if not hits:
        console.print(f"[yellow]No pages matched:[/yellow] {query}")
        return

    table = Table(title=f"Search Results ({len(hits)})")
    table.add_column("Title", style="bold")
    table.add_column("Type", width=14)
    table.add_column("Score", width=7)
    table.add_column("Path", min_width=28)
    table.add_column("Snippet", min_width=40)
    for hit in hits:
        table.add_row(
            hit.title,
            hit.page_type,
            str(hit.score),
            hit.relative_path,
            hit.snippet,
        )
    console.print()
    console.print(table)


@obsidian.command("neighbors")
@click.argument("locator")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
@click.option("--body/--no-body", default=False, help="Include page body in JSON output.")
def obsidian_neighbors(locator: str, path: str, json_output: bool, body: bool) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    try:
        neighborhood = connector.get_neighborhood(locator)
    except FileNotFoundError:
        console.print(f"[red]Page not found:[/red] {locator}")
        suggestions = connector.search(locator, limit=5)
        if suggestions:
            console.print("Closest matches:")
            for hit in suggestions:
                console.print(f"  - {hit.title} ({hit.relative_path})")
        raise SystemExit(1)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise SystemExit(1)

    if json_output:
        click.echo(json.dumps(neighborhood.to_dict(include_body=body), indent=2))
        return

    console.print()
    console.print(f"[bold]{neighborhood.page.title}[/bold]")
    console.print(f"  Path: {neighborhood.page.relative_path}")

    table = Table(title="Neighborhood")
    table.add_column("Connection", style="bold", width=22)
    table.add_column("Targets")
    table.add_row("Backlinks", ", ".join(neighborhood.backlinks) or "none")
    table.add_row("Outbound pages", ", ".join(neighborhood.outbound_pages) or "none")
    table.add_row("Outbound files", ", ".join(neighborhood.outbound_files) or "none")
    table.add_row("Supporting sources", ", ".join(neighborhood.supporting_source_pages) or "none")
    table.add_row("Related pages", ", ".join(neighborhood.related_pages) or "none")
    table.add_row("Cited source pages", ", ".join(neighborhood.cited_source_pages) or "none")
    table.add_row("Unresolved targets", ", ".join(neighborhood.unresolved_targets) or "none")
    console.print(table)


@obsidian.command("graph")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
def obsidian_graph(path: str, json_output: bool) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    graph = connector.graph()

    if json_output:
        click.echo(json.dumps(graph.to_dict(), indent=2))
        return

    summary = Table(title="Graph Summary")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value")
    summary.add_row("Nodes", str(len(graph.nodes)))
    summary.add_row("Edges", str(len(graph.edges)))
    summary.add_row(
        "Files linked",
        str(sum(1 for edge in graph.edges if edge.target_kind == "file")),
    )
    console.print()
    console.print(summary)

    top_nodes = sorted(
        graph.nodes,
        key=lambda node: (node.inbound_count + node.outbound_count, node.title.lower()),
        reverse=True,
    )[:12]
    table = Table(title="Top Connected Pages")
    table.add_column("Title", style="bold")
    table.add_column("Type", width=14)
    table.add_column("Inbound", width=7)
    table.add_column("Outbound", width=8)
    table.add_column("Path", min_width=28)
    for node in top_nodes:
        table.add_row(
            node.title,
            node.page_type,
            str(node.inbound_count),
            str(node.outbound_count),
            node.relative_path,
        )
    console.print()
    console.print(table)


@obsidian.command("refresh")
@click.option("--path", "-p", default=".", help="Compile workspace root or vault path.")
@click.option("--log-message", default="Refreshed index and overview after Obsidian maintenance.", help="Log entry to append.")
def obsidian_refresh(path: str, log_message: str) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    config = _load_compile_config_from_vault(connector.root)
    from compile.ingest import refresh_navigation_and_dashboards

    db = EvidenceDatabase(config.evidence_db_path)
    refresh_navigation_and_dashboards(config, db.materialize_evidence_store(), db=db)
    append_log_entry(config, "maint", "Obsidian refresh", [log_message])
    console.print("[green]Refreshed:[/green] dashboards, index.md, overview.md, log.md")


@obsidian.command("dashboards")
@click.option("--path", "-p", default=".", help="Compile workspace root or vault path.")
@click.option("--log-message", default="Refreshed dashboards after Obsidian maintenance.", help="Log entry to append.")
def obsidian_dashboards(path: str, log_message: str) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    config = _load_compile_config_from_vault(connector.root)
    from compile.ingest import refresh_navigation_and_dashboards

    db = EvidenceDatabase(config.evidence_db_path)
    touched = refresh_navigation_and_dashboards(config, db.materialize_evidence_store(), db=db)
    append_log_entry(config, "maint", "Dashboard refresh", [log_message, *touched])
    console.print("[green]Refreshed:[/green] dashboards, index.md, overview.md")


@obsidian.command("cleanup")
@click.option("--path", "-p", default=".", help="Workspace, vault, or page directory to inspect.")
@click.option("--log-message", default="Cleaned empty auxiliary markdown files created by Obsidian.", help="Log entry to append.")
def obsidian_cleanup(path: str, log_message: str) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    moved = connector.cleanup_empty_auxiliary_markdown_files()
    if not moved:
        console.print("[dim]No empty auxiliary markdown files needed cleanup.[/dim]")
        return

    if connector.layout == "compile_workspace":
        config = _load_compile_config_from_vault(connector.root)
        append_log_entry(config, "maint", "Obsidian cleanup", [log_message, *moved])
    console.print(f"[green]Quarantined {len(moved)} empty markdown file(s).[/green]")
    for relative_path in moved[:10]:
        console.print(f"  - {relative_path}")


@obsidian.command("upsert")
@click.argument("title")
@click.option("--page-type", required=True, help="Page type: source, concept, entity, question, output, overview, index, or log.")
@click.option("--path", "-p", default=".", help="Compile workspace root or vault path.")
@click.option("--body", default=None, help="Inline markdown body. If omitted, use --body-file or stdin.")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None, help="Read markdown body from a file.")
@click.option("--tag", "tags", multiple=True, help="Repeatable frontmatter tag.")
@click.option("--source", "sources", multiple=True, help="Repeatable source title for frontmatter.")
@click.option("--alias", "aliases", multiple=True, help="Repeatable alias.")
@click.option("--summary", default=None, help="Frontmatter summary override.")
@click.option("--relative-path", default=None, help="Explicit relative markdown path inside the vault.")
def obsidian_upsert(
    title: str,
    page_type: str,
    path: str,
    body: str | None,
    body_file: Path | None,
    tags: tuple[str, ...],
    sources: tuple[str, ...],
    aliases: tuple[str, ...],
    summary: str | None,
    relative_path: str | None,
) -> None:
    connector = ObsidianConnector(Path(path).resolve())
    if body_file is not None:
        body_text = body_file.read_text()
    elif body is not None:
        body_text = body
    else:
        body_text = click.get_text_stream("stdin").read()

    if not body_text.strip():
        console.print("[red]Page body is required. Use --body, --body-file, or stdin.[/red]")
        raise SystemExit(1)

    page = connector.upsert_page(
        title=title,
        body=body_text,
        page_type=page_type,
        tags=list(tags),
        sources=list(sources),
        aliases=list(aliases),
        summary=summary,
        relative_path=relative_path,
    )

    if connector.layout == "compile_workspace":
        config = _load_compile_config_from_vault(connector.root)
        from compile.ingest import refresh_navigation_and_dashboards

        db = EvidenceDatabase(config.evidence_db_path)
        refresh_navigation_and_dashboards(config, db.materialize_evidence_store(), db=db)
        append_log_entry(
            config,
            "maint",
            title,
            [f"Upserted {page.relative_path}", f"type={page_type}"],
        )

    console.print(f"[green]Upserted:[/green] {page.title}")
    console.print(f"  Path: {page.relative_path}")


@main.command("eval")
@click.option("--rebuild/--no-rebuild", default=False, help="Init fresh workspace, copy benchmark PDFs, ingest, then score.")
@click.option("--limit", type=int, default=0, show_default=True, help="Limit rebuild to the first N corpus PDFs.")
@click.option("--parallelism", default=4, show_default=True, help="Max parallel ingest workers for rebuild.")
@click.option("--workspace", "-w", default=None, type=click.Path(path_type=Path), help="Workspace dir to score (or rebuild into).")
@click.option("--json-output/--no-json-output", default=False, help="Emit machine-readable JSON.")
def eval_cmd(rebuild: bool, limit: int, parallelism: int, workspace: Path | None, json_output: bool) -> None:
    """Score wiki quality against the benchmark corpus.

    Without --rebuild, scores the current workspace (or --workspace).
    With --rebuild, creates a fresh workspace from the benchmark PDFs, ingests, and scores.
    """
    from compile.eval import evaluate_workspace, run_benchmark

    if rebuild:
        console.print("[bold]Running full benchmark rebuild...[/bold]")
        try:
            report = run_benchmark(
                workspace_dir=workspace,
                limit=limit,
                parallelism=max(1, parallelism),
            )
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)
        except Exception as e:
            console.print(f"[red]Benchmark failed: {e}[/red]")
            raise SystemExit(1)
    else:
        root = workspace or Path(".")
        if not (root / "wiki").exists():
            console.print("[red]No wiki/ directory found. Use --rebuild or run from a workspace.[/red]")
            raise SystemExit(1)
        report = evaluate_workspace(root.resolve())

    if json_output:
        import json as json_mod
        click.echo(json_mod.dumps(report.to_dict(), indent=2))
        return

    console.print()
    grade = report.grade()
    grade_colors = {"A": "green", "B": "blue", "C": "yellow", "D": "red", "F": "red bold"}
    console.print(f"[bold]Grade: [{grade_colors.get(grade, 'white')}]{grade}[/][/bold]")
    console.print()

    table = Table(title="Benchmark Scores")
    table.add_column("Metric", style="bold")
    table.add_column("Score")
    table.add_column("Detail")

    for line in report.summary_lines():
        parts = line.split(":", 1)
        if len(parts) == 2:
            metric = parts[0].strip()
            rest = parts[1].strip()
            # Split score from detail
            score_parts = rest.split("(", 1)
            score = score_parts[0].strip()
            detail = f"({score_parts[1]}" if len(score_parts) > 1 else ""
            table.add_row(metric, score, detail)
        else:
            table.add_row(line, "", "")
    console.print(table)

    if report.duplicate_concept_pairs:
        console.print()
        console.print("[yellow]Duplicate concept pairs:[/yellow]")
        for a, b in report.duplicate_concept_pairs[:10]:
            console.print(f"  - {a}  ↔  {b}")

    console.print(f"\n  Workspace: {report.workspace_root}")


if __name__ == "__main__":
    main()
