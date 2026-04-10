from __future__ import annotations

import json
from pathlib import Path
import re

import click
from rich.console import Console
from rich.table import Table

from compile.config import load_config
from compile.fetch import fetch_url
from compile.ingest import (
    build_ingest_artifact,
    render_source_body,
)
from compile.obsidian import ObsidianConnector
from compile.outputs import generate_canvas, generate_chart, generate_marp
from compile.text import extract_source, is_url
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
OBSOLETE_GLOBAL_COMMANDS = ("wiki-enrich.md",)
OBSOLETE_WORKSPACE_COMMANDS = ("enrich.md",)


def _load_workspace():
    try:
        return load_config()
    except FileNotFoundError:
        console.print("[red]No workspace found. Run 'compile init' first.[/red]")
        raise SystemExit(1)


def _iter_managed_templates(directory: Path, *, obsolete: tuple[str, ...] = ()) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Claude template directory not found: {directory}")
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name not in obsolete
    )


def _find_source_page_by_title(connector: ObsidianConnector, title: str):
    exact_matches = [
        hit for hit in connector.search(title, page_type="source", limit=10)
        if hit.title == title
    ]
    if len(exact_matches) > 1:
        raise ValueError(
            f"Multiple source pages titled '{title}' exist. "
            "Resolve the duplicate titles before ingesting again."
        )
    if exact_matches:
        return connector.get_page(exact_matches[0].relative_path)
    return connector.find_source_page_by_locator(title)


def _humanize_source_label(value: str) -> str:
    cleaned = value.replace("-", " ").replace("_", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.title() or "Source"


def _candidate_source_titles(base_title: str, raw_relative: str):
    relative = Path(raw_relative).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[0].lower() == "raw":
        parts = parts[1:]
    parents = parts[:-1]
    labels: list[str] = []
    for parent in reversed(parents):
        labels.insert(0, _humanize_source_label(parent))
        yield f"{base_title} ({' - '.join(labels)})"
    for index in range(2, 11):
        yield f"{base_title} ({index})"


def _resolve_source_title(
    connector: ObsidianConnector,
    *,
    desired_title: str,
    raw_relative: str,
):
    # Primary key: raw source path.  One raw file → one source page.
    existing_by_path = connector.find_source_page_by_raw_path(raw_relative)
    if existing_by_path is not None:
        return desired_title, existing_by_path

    # Fallback: title-based disambiguation (for new sources).
    existing_page = _find_source_page_by_title(connector, desired_title)
    if existing_page is None:
        return desired_title, None

    # Title is taken by a different source — disambiguate.
    for candidate in _candidate_source_titles(desired_title, raw_relative):
        candidate_page = _find_source_page_by_title(connector, candidate)
        if candidate_page is None:
            return candidate, None

    raise ValueError(f"Could not find a unique source title for '{desired_title}'.")


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
@click.option("--images/--no-images", default=False, help="Download referenced images when ingesting a URL.")
def ingest(source: str, path: str, title: str | None, images: bool) -> None:
    """Create a source note for a raw artifact or URL."""
    config = load_config(Path(path).resolve())

    if is_url(source):
        try:
            raw_path, fetched_title = fetch_url(
                source, config.raw_dir, download_images=images,
            )
        except Exception as exc:
            console.print(f"[red]Failed to fetch URL:[/red] {exc}")
            raise SystemExit(1)
        if title is None and fetched_title:
            title = fetched_title
        console.print(f"[green]Fetched URL → [/green]{raw_path.relative_to(config.workspace_root)}")
    else:
        raw_path = _resolve_raw_source(config.workspace_root, source)
        if not raw_path.exists() or not raw_path.is_file():
            console.print(f"[red]Raw source not found:[/red] {raw_path}")
            raise SystemExit(1)
        try:
            raw_path.relative_to(config.workspace_root)
        except ValueError:
            console.print("[red]Raw source must live inside the workspace root.[/red]")
            raise SystemExit(1)
        try:
            raw_path.relative_to(config.raw_dir)
        except ValueError:
            console.print("[red]Source files must be in the raw/ directory.[/red]")
            console.print(f"  Move the file to {config.raw_dir} and retry.")
            raise SystemExit(1)

    connector = ObsidianConnector(config.workspace_root)
    raw_relative = str(raw_path.relative_to(config.workspace_root)).replace("\\", "/")
    extracted = extract_source(raw_path)
    desired_title = title or extracted.title
    try:
        effective_title, existing_source_page = _resolve_source_title(
            connector,
            desired_title=desired_title,
            raw_relative=raw_relative,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    # Guard against re-clobbering an enriched source note.
    # If a source page already exists for this title and has been enriched
    # beyond the registration shell, skip the overwrite and mark it processed.
    if (
        existing_source_page is not None
        and "This is a registration shell." not in existing_source_page.body
    ):
        mark_processed(config, raw_path, [existing_source_page.relative_path])
        console.print(f"[yellow]Source already enriched:[/yellow] {existing_source_page.relative_path}")
        console.print("  Marked as processed. Skipping re-ingest.")
        return

    artifact = build_ingest_artifact(
        raw_relative=raw_relative,
        extracted=extracted,
        connector=connector,
        title=effective_title,
    )
    # When replacing a registration shell with a new title, remove the old file
    # so upsert_page creates the page at a path matching the new title.
    pin_path: str | None = None
    if existing_source_page is not None:
        if effective_title == existing_source_page.title:
            pin_path = existing_source_page.relative_path
        else:
            old_file = config.workspace_root / existing_source_page.relative_path
            if old_file.exists():
                old_file.unlink()
    page = connector.upsert_page(
        title=artifact.title,
        body=render_source_body(artifact),
        page_type="source",
        summary=artifact.page_summary,
        sources=[raw_relative],
        relative_path=pin_path,
    )

    if not artifact.metadata_only:
        mark_processed(config, raw_path, [page.relative_path])
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    log_lines = [
        f"Raw source: {raw_relative}",
        f"Source page: {page.relative_path}",
    ]
    if artifact.related_pages:
        log_lines.extend(f"Review related page: {related_page.title}" for related_page in artifact.related_pages)
    append_log_entry(config, "ingest", artifact.title, log_lines)

    console.print(f"[green]Source note created:[/green] {page.relative_path}")
    if artifact.metadata_only:
        console.print("  Source content could not be extracted. Read the raw file and replace this note.")
    if artifact.related_pages:
        console.print("  Review these existing pages next:")
        for related_page in artifact.related_pages:
            console.print(f"  - {related_page.title}")


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
@click.option("--body", default=None, help="Markdown body for the page.")
@click.option(
    "--body-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read markdown body from a UTF-8 file.",
)
@click.option("--summary", default=None, help="Optional frontmatter summary.")
@click.option("--relative-path", default=None, help="Optional target path relative to the vault root.")
@click.option("--tag", "tags", multiple=True, help="Repeat to add tags.")
@click.option("--source", "sources", multiple=True, help="Repeat to add supporting sources.")
@click.option("--alias", "aliases", multiple=True, help="Repeat to add aliases.")
def obsidian_upsert(
    title: str,
    path: str,
    page_type: str,
    body: str | None,
    body_file: Path | None,
    summary: str | None,
    relative_path: str | None,
    tags: tuple[str, ...],
    sources: tuple[str, ...],
    aliases: tuple[str, ...],
) -> None:
    """Create or update a maintained page."""
    if body is not None and body_file is not None:
        console.print("[red]Use either --body or --body-file, not both.[/red]")
        raise SystemExit(1)

    if body_file is not None:
        try:
            body_text = body_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            console.print(f"[red]Failed to read body file as UTF-8:[/red] {body_file}")
            raise SystemExit(1)
        except OSError as exc:
            console.print(f"[red]Failed to read body file:[/red] {exc}")
            raise SystemExit(1)
    else:
        body_text = body or ""

    root = Path(path).resolve()
    connector = ObsidianConnector(root)

    # Capture whether the existing page is a registration shell before upserting.
    existing_was_shell = False
    if page_type == "source":
        try:
            existing = connector.get_page(title)
            if existing.page_type == "source" and "This is a registration shell." in existing.body:
                existing_was_shell = True
        except (FileNotFoundError, ValueError):
            pass

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

    # When a registration shell is replaced with real content, mark raw sources processed.
    if existing_was_shell and "This is a registration shell." not in body_text:
        try:
            config = load_config(root)
            raw_sources = page.frontmatter.get("sources", [])
            for raw_rel in raw_sources:
                raw_path = root / raw_rel
                if raw_path.exists():
                    mark_processed(config, raw_path, [page.relative_path])
                    console.print(f"[green]Marked processed:[/green] {raw_rel}")
        except Exception:
            pass  # non-fatal: source tracking is best-effort

    console.print(f"[green]Upserted[/green] {page.relative_path}")


# --- Rich output rendering ---


def _read_utf8_text_file(path: Path, *, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        console.print(f"[red]Failed to read {label} file as UTF-8:[/red] {path}")
        raise SystemExit(1)
    except OSError as exc:
        console.print(f"[red]Failed to read {label} file:[/red] {exc}")
        raise SystemExit(1)


def _resolve_inline_or_file(
    *,
    inline_value: str | None,
    file_value: Path | None,
    inline_flag: str,
    file_flag: str,
    label: str,
) -> str:
    if inline_value is not None and file_value is not None:
        console.print(f"[red]Use either {inline_flag} or {file_flag}, not both.[/red]")
        raise SystemExit(1)
    if file_value is not None:
        return _read_utf8_text_file(file_value, label=label)
    if inline_value is not None:
        return inline_value
    console.print(f"[red]Provide either {inline_flag} or {file_flag}.[/red]")
    raise SystemExit(1)


@main.group()
def render() -> None:
    """Generate rich output formats (Marp slides, charts, canvas)."""


@render.command("marp")
@click.argument("title")
@click.option("--path", "-p", default=".", help="Workspace root.")
@click.option("--body", default=None, help="Slide markdown (use --- for slide separators).")
@click.option(
    "--body-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read slide markdown from a UTF-8 file.",
)
@click.option("--theme", default="default", help="Marp theme name.")
@click.option("--summary", default=None, help="Frontmatter summary.")
@click.option("--tag", "tags", multiple=True)
def render_marp(
    title: str,
    path: str,
    body: str | None,
    body_file: Path | None,
    theme: str,
    summary: str | None,
    tags: tuple[str, ...],
) -> None:
    """Generate a Marp slide deck and save as a wiki output page."""
    root = Path(path).resolve()
    config = load_config(root)
    connector = ObsidianConnector(root)
    body_text = _resolve_inline_or_file(
        inline_value=body,
        file_value=body_file,
        inline_flag="--body",
        file_flag="--body-file",
        label="body",
    )
    marp_body, marp_fm = generate_marp(title, body_text, theme=theme)
    page = connector.upsert_page(
        title=title,
        body=marp_body,
        page_type="output",
        summary=summary or f"Marp slide deck: {title}",
        tags=list(tags),
        extra_frontmatter=marp_fm,
        ensure_title_heading=False,
    )
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    append_log_entry(config, "render", title, ["Format: marp", f"Page: {page.relative_path}"])
    console.print(f"[green]Marp deck created:[/green] {page.relative_path}")


@render.command("chart")
@click.argument("title")
@click.option("--path", "-p", default=".", help="Workspace root.")
@click.option("--script", default=None, help="Python matplotlib script.")
@click.option(
    "--script-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the matplotlib script from a UTF-8 file.",
)
@click.option("--summary", default=None, help="Frontmatter summary.")
@click.option("--tag", "tags", multiple=True)
def render_chart(
    title: str,
    path: str,
    script: str | None,
    script_file: Path | None,
    summary: str | None,
    tags: tuple[str, ...],
) -> None:
    """Execute a matplotlib script and save the chart as a wiki output page."""
    root = Path(path).resolve()
    config = load_config(root)
    connector = ObsidianConnector(root)
    output_dir = config.wiki_dir / "outputs"
    script_text = _resolve_inline_or_file(
        inline_value=script,
        file_value=script_file,
        inline_flag="--script",
        file_flag="--script-file",
        label="script",
    )
    try:
        image_path = generate_chart(title, script_text, output_dir)
    except RuntimeError as exc:
        console.print(f"[red]Chart generation failed:[/red] {exc}")
        raise SystemExit(1)
    rel_image = str(image_path.relative_to(config.workspace_root)).replace("\\", "/")
    body = f"![[{rel_image}]]\n\n## Script\n\n```python\n{script_text}\n```\n"
    page = connector.upsert_page(
        title=title,
        body=body,
        page_type="output",
        summary=summary or f"Chart: {title}",
        tags=list(tags),
    )
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    append_log_entry(config, "render", title, ["Format: chart", f"Image: {rel_image}", f"Page: {page.relative_path}"])
    console.print(f"[green]Chart created:[/green] {image_path.name} → {page.relative_path}")


@render.command("canvas")
@click.argument("title")
@click.option("--path", "-p", default=".", help="Workspace root.")
@click.option("--nodes", default=None, help='JSON array of node objects (each with "text" key).')
@click.option(
    "--nodes-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read node JSON from a UTF-8 file.",
)
@click.option("--edges", default=None, help='Optional JSON array of edge objects (each with "from" and "to" keys).')
@click.option(
    "--edges-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read edge JSON from a UTF-8 file.",
)
@click.option("--summary", default=None, help="Frontmatter summary.")
def render_canvas(
    title: str,
    path: str,
    nodes: str | None,
    nodes_file: Path | None,
    edges: str | None,
    edges_file: Path | None,
    summary: str | None,
) -> None:
    """Generate an Obsidian Canvas file and a companion wiki output page."""
    root = Path(path).resolve()
    config = load_config(root)
    connector = ObsidianConnector(root)
    nodes_text = _resolve_inline_or_file(
        inline_value=nodes,
        file_value=nodes_file,
        inline_flag="--nodes",
        file_flag="--nodes-file",
        label="nodes",
    )
    if edges is not None and edges_file is not None:
        console.print("[red]Use either --edges or --edges-file, not both.[/red]")
        raise SystemExit(1)
    edge_text = _read_utf8_text_file(edges_file, label="edges") if edges_file is not None else edges
    try:
        node_list = json.loads(nodes_text)
        edge_list = json.loads(edge_text) if edge_text else []
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise SystemExit(1)
    try:
        canvas_json = generate_canvas(title, node_list, edge_list)
    except ValueError as exc:
        console.print(f"[red]Invalid canvas payload:[/red] {exc}")
        raise SystemExit(1)

    # Write the .canvas file
    from compile.text import slugify
    slug = slugify(title) or "canvas"
    canvas_path = config.wiki_dir / "outputs" / f"{slug}.canvas"
    canvas_path.parent.mkdir(parents=True, exist_ok=True)
    canvas_path.write_text(canvas_json)

    # Create a companion markdown page
    rel_canvas = str(canvas_path.relative_to(config.workspace_root)).replace("\\", "/")
    body = f"Canvas file: [[{rel_canvas}]]\n\nNodes: {len(node_list)} | Edges: {len(edge_list)}\n"
    page = connector.upsert_page(
        title=title,
        body=body,
        page_type="output",
        summary=summary or f"Canvas: {title}",
    )
    pages_by_type = collect_pages_by_type(config)
    write_index(config, pages_by_type)
    write_overview(config, pages_by_type)
    append_log_entry(config, "render", title, ["Format: canvas", f"Canvas: {rel_canvas}", f"Page: {page.relative_path}"])
    console.print(f"[green]Canvas created:[/green] {rel_canvas} → {page.relative_path}")


# --- Claude Code integration ---

@main.group()
def claude() -> None:
    """Claude Code integration — install commands and workspace files."""


@claude.command("setup")
@click.argument("path", default=".")
@click.option("--force", is_flag=True, help="Overwrite existing files without prompting.")
def claude_setup(path: str, force: bool) -> None:
    """Install Claude Code commands for a wiki workspace.

    PATH is the wiki workspace directory (default: current directory).

    Installs global bridge commands (~/.claude/commands/) so the wiki is
    accessible from any Claude Code session, and workspace-local commands
    so the wiki itself has the full editing toolset.
    """
    wiki_path = Path(path).expanduser().resolve()
    config_path = wiki_path / ".compile" / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]No compile workspace at {wiki_path}[/red]")
        console.print("Run 'compile init' in that directory first.")
        raise SystemExit(1)

    result = install_claude_files(wiki_path, Path.home(), force)

    if result["installed"]:
        console.print(f"[green]Installed {len(result['installed'])} file(s):[/green]")
        for f in result["installed"]:
            console.print(f"  + {f}")
    if result["mispointed"]:
        console.print(f"[red]Warning: {len(result['mispointed'])} global command(s) point at a different wiki:[/red]")
        for f in result["mispointed"]:
            console.print(f"  ! {f}")
        console.print("[red]Use --force to rebind them to this workspace.[/red]")
    if result["obsolete"]:
        console.print(f"[yellow]Obsolete managed file(s) detected:[/yellow]")
        for f in result["obsolete"]:
            console.print(f"  - {f}")
        console.print("[yellow]Re-run with --force to remove them.[/yellow]")
    if result["removed"]:
        console.print(f"[green]Removed obsolete managed file(s):[/green]")
        for f in result["removed"]:
            console.print(f"  - {f}")
    if result["skipped"]:
        console.print(f"[yellow]Skipped {len(result['skipped'])} existing file(s) (use --force to overwrite):[/yellow]")
        for f in result["skipped"]:
            console.print(f"  ~ {f}")
    if (
        not result["installed"]
        and not result["skipped"]
        and not result["mispointed"]
        and not result["obsolete"]
        and not result["removed"]
    ):
        console.print("[dim]Nothing to install.[/dim]")


def install_claude_files(
    wiki_path: Path, home: Path, force: bool,
) -> dict[str, list[str]]:
    """Install Claude Code commands for a wiki workspace.

    Returns a dict with keys: installed, skipped, mispointed, obsolete, removed.
    """
    templates = Path(__file__).parent / "templates"
    if not templates.is_dir():
        raise FileNotFoundError(
            f"Template directory not found at {templates}. "
            "This may indicate a packaging or installation problem."
        )
    wiki_path_str = str(wiki_path)
    installed: list[str] = []
    skipped: list[str] = []
    mispointed: list[str] = []
    obsolete: list[str] = []
    removed: list[str] = []

    # --- Global bridge commands ---
    global_dir = home / ".claude" / "commands"
    global_dir.mkdir(parents=True, exist_ok=True)

    for template_file in _iter_managed_templates(
        templates / "global",
        obsolete=OBSOLETE_GLOBAL_COMMANDS,
    ):
        dest = global_dir / template_file.name
        if dest.exists() and not force:
            existing = dest.read_text()
            marker = f"My wiki lives at: {wiki_path_str}\n"
            if marker not in existing:
                mispointed.append(str(dest))
            else:
                skipped.append(str(dest))
            continue
        content = template_file.read_text().replace("{{wiki_path}}", wiki_path_str)
        dest.write_text(content)
        installed.append(str(dest))

    for obsolete_name in OBSOLETE_GLOBAL_COMMANDS:
        dest = global_dir / obsolete_name
        if not dest.exists():
            continue
        if force:
            dest.unlink()
            removed.append(str(dest))
        else:
            obsolete.append(str(dest))

    # --- Workspace-local files ---
    workspace_claude_dir = wiki_path / ".claude" / "commands"
    workspace_claude_dir.mkdir(parents=True, exist_ok=True)

    # CLAUDE.md
    claude_md_src = templates / "workspace" / "CLAUDE.md"
    claude_md_dest = wiki_path / "CLAUDE.md"
    if claude_md_dest.exists() and not force:
        skipped.append(str(claude_md_dest))
    else:
        claude_md_dest.write_text(claude_md_src.read_text())
        installed.append(str(claude_md_dest))

    # settings.local.json
    settings_src = templates / "workspace" / "settings.local.json"
    settings_dest = wiki_path / ".claude" / "settings.local.json"
    if settings_dest.exists() and not force:
        skipped.append(str(settings_dest))
    else:
        settings_dest.write_text(settings_src.read_text())
        installed.append(str(settings_dest))

    # Workspace commands
    for template_file in _iter_managed_templates(
        templates / "workspace" / "commands",
        obsolete=OBSOLETE_WORKSPACE_COMMANDS,
    ):
        dest = workspace_claude_dir / template_file.name
        if dest.exists() and not force:
            skipped.append(str(dest))
            continue
        dest.write_text(template_file.read_text())
        installed.append(str(dest))

    for obsolete_name in OBSOLETE_WORKSPACE_COMMANDS:
        dest = workspace_claude_dir / obsolete_name
        if not dest.exists():
            continue
        if force:
            dest.unlink()
            removed.append(str(dest))
        else:
            obsolete.append(str(dest))

    return {
        "installed": installed,
        "skipped": skipped,
        "mispointed": mispointed,
        "obsolete": obsolete,
        "removed": removed,
    }


def _resolve_raw_source(workspace_root: Path, source: str) -> Path:
    candidate = Path(source)
    if candidate.is_absolute():
        return candidate

    direct = (workspace_root / candidate).resolve()
    if direct.exists():
        return direct

    raw_relative = (workspace_root / "raw" / candidate).resolve()
    return raw_relative

if __name__ == "__main__":
    main()
