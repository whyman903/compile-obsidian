from __future__ import annotations

import json
from pathlib import Path
import re

import click
from rich.console import Console
from rich.table import Table

from compile.config import load_config
from compile.obsidian import ObsidianConnector
from compile.text import extract_text
from compile.workspace import (
    append_log_entry,
    collect_pages_by_type,
    get_status,
    init_workspace,
    mark_processed,
    read_schema,
    write_index,
    write_overview,
)

console = Console()


def _load_workspace():
    try:
        return load_config()
    except FileNotFoundError:
        console.print("[red]No workspace found. Run 'compile init' first.[/red]")
        raise SystemExit(1)


@click.group()
def main() -> None:
    """Compile — an LLM-maintained wiki workspace."""


@main.command()
@click.argument("topic")
@click.option("--description", "-d", default="", help="Topic description.")
@click.option("--path", "-p", default=".", help="Directory to create workspace in.")
def init(topic: str, description: str, path: str) -> None:
    """Create a new wiki workspace."""
    root = Path(path).resolve()
    try:
        config = init_workspace(root, topic, description)
        console.print(f"[green]Workspace initialized:[/green] {config.topic}")
        console.print(f"  Drop sources into: {config.raw_dir}")
        console.print(f"  Wiki pages at: {config.wiki_dir}")
        console.print(f"  Open in Obsidian: File > Open Vault > {root}")
    except FileExistsError:
        console.print(f"[red]Workspace already exists at {root}[/red]")
        raise SystemExit(1)


@main.command()
def status() -> None:
    """Show workspace status."""
    config = _load_workspace()
    info = get_status(config)
    table = Table(title=info["topic"])
    table.add_column("", style="bold")
    table.add_column("")
    for key in ("topic", "description", "workspace_root", "raw_files", "processed", "unprocessed", "wiki_pages"):
        table.add_row(key.replace("_", " ").title(), str(info[key]))
    console.print(table)


@main.command()
@click.argument("source")
@click.option("--path", "-p", default=".", help="Workspace root.")
@click.option("--title", default=None, help="Optional override title for the generated source note.")
def ingest(source: str, path: str, title: str | None) -> None:
    """Create a minimal source-note scaffold for a raw artifact."""
    config = load_config(Path(path).resolve())
    raw_path = _resolve_raw_source(config.workspace_root, source)
    if not raw_path.exists() or not raw_path.is_file():
        console.print(f"[red]Raw source not found:[/red] {raw_path}")
        raise SystemExit(1)
    try:
        raw_path.relative_to(config.workspace_root)
    except ValueError:
        console.print("[red]Raw source must live inside the workspace root.[/red]")
        raise SystemExit(1)

    extracted_title, extracted_text = extract_text(raw_path)
    source_title = title or extracted_title
    connector = ObsidianConnector(config.workspace_root)
    related_pages = [
        hit.title for hit in connector.search(source_title, limit=8)
        if hit.page_type not in {"source", "index", "overview", "log"} and hit.title != source_title
    ][:5]

    raw_relative = str(raw_path.relative_to(config.workspace_root)).replace("\\", "/")
    summary = _source_summary_from_text(extracted_text)
    body = _build_source_body(raw_relative, summary, related_pages)
    page = connector.upsert_page(
        title=source_title,
        body=body,
        page_type="source",
        summary=summary,
        sources=[raw_relative],
    )

    mark_processed(config, raw_path, [page.relative_path])
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    log_lines = [
        f"Raw source: {raw_relative}",
        f"Source page: {page.relative_path}",
    ]
    if related_pages:
        log_lines.extend(f"Review related page: {page_title}" for page_title in related_pages)
    append_log_entry(config, "ingest", source_title, log_lines)

    console.print(f"[green]Ingest scaffold created:[/green] {page.relative_path}")
    if related_pages:
        console.print("  Review these existing pages next:")
        for page_title in related_pages:
            console.print(f"  - {page_title}")


@main.command("health")
@click.option("--path", "-p", default=".", help="Workspace or vault path.")
@click.option("--json-output/--no-json-output", default=False)
def health(path: str, json_output: bool) -> None:
    """Run workspace health report."""
    from compile.health import build_health_report
    report = build_health_report(Path(path).resolve())

    if json_output:
        click.echo(json.dumps(report, indent=2))
        return

    console.print(f"\n[bold]{report['overall_status']}[/bold]")
    console.print(f"  {report['summary']}")
    for section in ("obsidian_readiness", "graph_health", "content_health"):
        data = report[section]
        counts = ", ".join(f"{k}={v}" for k, v in data["counts"].items())
        console.print(f"  {section}: {data['status']} ({counts})")

    if report["issues"]:
        console.print()
        for issue in report["issues"][:15]:
            console.print(f"  [{issue.get('severity', '')}] {issue.get('code', '')}: {issue.get('message', '')}")


@main.command("schema")
def schema_show() -> None:
    """Print the current WIKI.md schema."""
    config = _load_workspace()
    content = read_schema(config)
    if not content:
        console.print("[dim]No WIKI.md found.[/dim]")
        return
    from rich.markdown import Markdown
    console.print(Markdown(content))


# --- Obsidian vault inspection ---

@main.group()
def obsidian() -> None:
    """Inspect Obsidian vault metadata and graph quality."""


@obsidian.command("inspect")
@click.option("--path", "-p", default=".")
@click.option("--json-output/--no-json-output", default=False)
def obsidian_inspect(path: str, json_output: bool) -> None:
    """Full vault audit."""
    connector = ObsidianConnector(Path(path).resolve())
    report = connector.inspect()

    if json_output:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return

    console.print(f"\n[bold]{report.layout}[/bold] ({report.total_pages} pages)")
    table = Table(title="Vault Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    for label, value in [
        ("Pages", report.total_pages),
        ("Types", ", ".join(f"{k}={v}" for k, v in report.page_type_counts.items()) or "none"),
        ("Resolved links", report.resolved_link_count),
        ("Unresolved links", report.unresolved_link_count),
        ("Orphan pages", report.orphan_page_count),
        ("Thin pages", len(report.thin_pages)),
    ]:
        table.add_row(label, str(value))
    console.print(table)

    if report.issues:
        console.print()
        for issue in report.issues[:15]:
            console.print(f"  [{issue.severity}] {issue.code}: {issue.message}")


@obsidian.command("search")
@click.argument("query")
@click.option("--path", "-p", default=".")
@click.option("--limit", "-n", default=10)
def obsidian_search(query: str, path: str, limit: int) -> None:
    """Search wiki pages."""
    connector = ObsidianConnector(Path(path).resolve())
    hits = connector.search(query, limit=limit)
    if not hits:
        console.print(f"[yellow]No matches:[/yellow] {query}")
        return
    for hit in hits:
        console.print(f"  {hit.title} ({hit.page_type}) — {hit.snippet[:80]}")


@obsidian.command("page")
@click.argument("locator")
@click.option("--path", "-p", default=".")
def obsidian_page(locator: str, path: str) -> None:
    """Show page metadata and body."""
    connector = ObsidianConnector(Path(path).resolve())
    try:
        page = connector.get_page(locator)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold]{page.title}[/bold] ({page.page_type}, {page.word_count} words)")
    if page.resolved_outbound_links:
        console.print(f"  Links to: {', '.join(page.resolved_outbound_links[:10])}")
    if page.inbound_links:
        console.print(f"  Linked from: {', '.join(page.inbound_links[:10])}")
    if page.body:
        from rich.markdown import Markdown
        console.print()
        console.print(Markdown(page.body))


@obsidian.command("neighbors")
@click.argument("locator")
@click.option("--path", "-p", default=".")
def obsidian_neighbors(locator: str, path: str) -> None:
    """Show page connections."""
    connector = ObsidianConnector(Path(path).resolve())
    try:
        n = connector.get_neighborhood(locator)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    console.print(f"\n[bold]{n.page.title}[/bold]")
    for label, items in [
        ("Backlinks", n.backlinks),
        ("Outbound", n.outbound_pages),
        ("Sources", n.supporting_source_pages),
        ("Related", n.related_pages),
    ]:
        if items:
            console.print(f"  {label}: {', '.join(items)}")


@obsidian.command("graph")
@click.option("--path", "-p", default=".")
def obsidian_graph(path: str) -> None:
    """Graph structure summary."""
    connector = ObsidianConnector(Path(path).resolve())
    graph = connector.graph()
    console.print(f"\n{len(graph.nodes)} nodes, {len(graph.edges)} edges")
    top = sorted(graph.nodes, key=lambda n: n.inbound_count + n.outbound_count, reverse=True)[:10]
    for node in top:
        console.print(f"  {node.title} ({node.page_type}) — {node.inbound_count} in, {node.outbound_count} out")


@obsidian.command("cleanup")
@click.option("--path", "-p", default=".")
def obsidian_cleanup(path: str) -> None:
    """Remove empty stub markdown files created by Obsidian."""
    connector = ObsidianConnector(Path(path).resolve())
    moved = connector.cleanup_empty_auxiliary_markdown_files()
    if not moved:
        console.print("[dim]No empty files to clean up.[/dim]")
        return
    console.print(f"[green]Quarantined {len(moved)} empty file(s).[/green]")
    for p in moved[:10]:
        console.print(f"  - {p}")


@obsidian.command("refresh")
@click.option("--path", "-p", default=".")
def obsidian_refresh(path: str) -> None:
    """Refresh index and overview pages from the current wiki."""
    connector = ObsidianConnector(Path(path).resolve())
    if connector.layout != "compile_workspace":
        console.print("[red]Refresh is only supported for compile workspaces.[/red]")
        raise SystemExit(1)

    config = load_config(connector.root)
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    console.print("[green]Refreshed[/green] wiki/index.md and wiki/overview.md")


@obsidian.command("upsert")
@click.argument("title")
@click.option("--path", "-p", default=".")
@click.option("--page-type", required=True, help="Page type, for example article, source, map, or output.")
@click.option("--body", default="", help="Markdown body for the page.")
@click.option("--summary", default=None, help="Optional frontmatter summary.")
@click.option("--relative-path", default=None, help="Optional target path relative to the vault root.")
@click.option("--tag", "tags", multiple=True, help="Repeat to add tags.")
@click.option("--source", "sources", multiple=True, help="Repeat to add supporting sources.")
@click.option("--alias", "aliases", multiple=True, help="Repeat to add aliases.")
def obsidian_upsert(
    title: str,
    path: str,
    page_type: str,
    body: str,
    summary: str | None,
    relative_path: str | None,
    tags: tuple[str, ...],
    sources: tuple[str, ...],
    aliases: tuple[str, ...],
) -> None:
    """Create or update a maintained page."""
    connector = ObsidianConnector(Path(path).resolve())
    page = connector.upsert_page(
        title=title,
        body=body or "",
        page_type=page_type,
        tags=list(tags),
        sources=list(sources),
        aliases=list(aliases),
        summary=summary,
        relative_path=relative_path,
    )
    console.print(f"[green]Upserted[/green] {page.relative_path}")


def _resolve_raw_source(workspace_root: Path, source: str) -> Path:
    candidate = Path(source)
    if candidate.is_absolute():
        return candidate

    direct = (workspace_root / candidate).resolve()
    if direct.exists():
        return direct

    raw_relative = (workspace_root / "raw" / candidate).resolve()
    return raw_relative


def _source_summary_from_text(text: str) -> str:
    summary = re.sub(r"\s+", " ", text).strip()
    return summary[:220] or "Source scaffold created. Add a concise, source-backed summary."


def _build_source_body(raw_relative: str, summary: str, related_pages: list[str]) -> str:
    lines = [
        "## Synopsis",
        "",
        summary,
        "",
        "## Provenance",
        "",
        f"- Source file: ![[{raw_relative}]]",
    ]
    if related_pages:
        lines.extend([
            "",
            "## Likely Related Pages",
            "",
            *[f"- [[{page_title}]]" for page_title in related_pages],
        ])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
