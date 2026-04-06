from __future__ import annotations

from rich.console import Console
from rich.table import Table

from compile.compiler import Compiler
from compile.config import Config
from compile.workspace import list_wiki_pages, read_wiki_page

console = Console()

SEVERITY_STYLE = {
    "high": "bold red",
    "medium": "yellow",
    "low": "dim",
}


def collect_lint_issues(config: Config, compiler: Compiler) -> list[dict]:
    index_content = read_wiki_page(config, "index.md") or ""

    # Collect page summaries (first ~500 chars of each)
    page_summaries: dict[str, str] = {}
    for page_path in list_wiki_pages(config):
        content = read_wiki_page(config, page_path)
        if content:
            page_summaries[page_path] = content[:500]

    if len(page_summaries) <= 3:  # Just index, overview, log
        return []
    return compiler.lint_wiki(index_content, page_summaries)


def lint_wiki(config: Config, compiler: Compiler) -> list[dict]:
    """Run the LLM content audit on the wiki and report issues."""
    console.print("\n[bold]Running wiki content audit...[/bold]")

    page_count = sum(1 for page_path in list_wiki_pages(config) if read_wiki_page(config, page_path))
    if page_count <= 3:
        console.print("[yellow]Wiki is too small to lint meaningfully. Add more sources first.[/yellow]")
        return []

    console.print(f"  Auditing {page_count} pages...", style="dim")
    issues = collect_lint_issues(config, compiler)

    if not issues:
        console.print("[bold green]No content issues found.[/bold green]")
        return []

    # Display results
    table = Table(title=f"Wiki Content Audit ({len(issues)} issues)")
    table.add_column("Severity", style="bold", width=8)
    table.add_column("Type", width=14)
    table.add_column("Issue", min_width=30)
    table.add_column("Suggestion", min_width=30)

    for issue in issues:
        severity = issue.get("severity", "low")
        table.add_row(
            f"[{SEVERITY_STYLE.get(severity, 'dim')}]{severity}[/]",
            issue.get("type", ""),
            issue.get("title", ""),
            issue.get("suggestion", ""),
        )

    console.print()
    console.print(table)
    console.print()

    return issues
