from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown

from compile.compiler import Compiler
from compile.config import Config
from compile.obsidian import ObsidianConnector
from compile.resolve import TitleResolver
from compile.store import EvidenceDatabase
from compile.text import slugify
from compile.workspace import (
    append_log_entry,
    list_wiki_pages,
    read_wiki_page,
)

console = Console()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def answer_query(
    config: Config,
    compiler: Compiler,
    question: str,
    save: bool = False,
    output_format: str = "auto",
    verbose: bool = False,
) -> str:
    """Answer a question against the wiki and optionally save the output."""
    console.print(f"\n[bold]Question:[/bold] {question}")
    console.print("  Searching wiki...", style="dim")

    relevant_pages, raw_snippets = _select_relevant_material(
        config, compiler, question, verbose=verbose,
    )

    if not relevant_pages:
        console.print("[yellow]The wiki is empty. Add sources first.[/yellow]")
        return ""

    console.print(f"  Reading {len(relevant_pages)} pages...", style="dim")

    # Ask the LLM
    console.print("  Generating answer...", style="dim")
    answer = compiler.answer_query(
        question,
        relevant_pages,
        raw_snippets=raw_snippets,
        output_format=output_format,
    )

    # Display the answer
    console.print()
    console.print(Markdown(answer))
    console.print()

    if save:
        output_path = _save_output(config, question, answer, output_format=output_format)
        console.print(f"  [green]Saved to {output_path}[/green]")

    return answer


# ---------------------------------------------------------------------------
# Save output
# ---------------------------------------------------------------------------

def _save_output(config: Config, question: str, answer: str, output_format: str = "auto") -> str:
    """Save a query answer as a wiki output page."""
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    slug = slugify(question[:60])
    filename = f"{slug}.md"
    output_dir = config.wiki_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized_format = output_format or "auto"
    content = f"""---
title: "{question[:80]}"
type: output
status: stable
created: {now}
updated: {now}
summary: Saved query output generated from the maintained wiki.
tags: [query, output, {normalized_format}]
cssclasses: [output, stable, {normalized_format}]
---

# {question}

_Generated {now} from the maintained wiki._

{answer}
"""
    path = output_dir / filename
    path.write_text(content)

    # Register the output page in the evidence database as a T4 artifact.
    # T4 pages appear in search and cross-references but NEVER raise
    # source_count or promote page maturity (enforced by empty source_ids
    # and the TIERS_CAN_AFFECT_MATURITY check in store.py).
    title = question[:80]
    db = EvidenceDatabase(config.evidence_db_path)
    db.sync_page(
        path=f"outputs/{filename}",
        title=title,
        page_type="output",
        status="stable",
        summary=answer[:120],
        source_ids=[],  # outputs don't have raw sources
        evidence_tier="T4",  # derived artifact — discoverable but not evidence
    )

    # Parse [[wikilinks]] in the answer so the output is discoverable
    # via linked pages' neighborhoods, but do NOT add this output as a
    # source to any concept/entity page.
    _register_output_cross_references(db, f"outputs/{filename}", answer)

    from compile.ingest import refresh_navigation_and_dashboards

    refresh_navigation_and_dashboards(config, db.materialize_evidence_store(), db=db)
    append_log_entry(config, "query", question[:60], [f"Saved to outputs/{filename}"])

    return f"outputs/{filename}"


# ---------------------------------------------------------------------------
# 3-tier material selection pipeline
# ---------------------------------------------------------------------------

def _select_relevant_material(
    config: Config,
    compiler: Compiler,
    question: str,
    *,
    verbose: bool = False,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Select relevant wiki pages using a 3-tier token-budgeted approach.

    L1 -- Catalog scan:  ask LLM to pick pages from a compact catalog.
    L2 -- FTS + graph:   search evidence DB + expand via Obsidian graph.
    L3 -- Deep read:     read full content, bounded by token budget.
    """
    connector = ObsidianConnector(config.workspace_root)
    db = EvidenceDatabase(config.evidence_db_path)
    resolver = TitleResolver.from_catalog(db, db.page_catalog())
    page_catalog = db.page_catalog()

    # For small wikis (< 15 pages), skip tiers and read everything
    if len(page_catalog) < 15:
        if verbose:
            console.print(
                f"  [dim]Small wiki ({len(page_catalog)} pages) -- reading all pages[/dim]"
            )
        pages: dict[str, str] = {}
        for page_path in list_wiki_pages(config):
            if page_path == "log.md":
                continue
            content = read_wiki_page(config, page_path)
            if content:
                pages[page_path] = content
        return pages, []

    # ---- L1: Catalog scan ------------------------------------------------
    l1_titles = _l1_catalog_scan(
        compiler, question, page_catalog, config.topic, verbose=verbose,
    )

    # ---- L2: FTS search + graph expansion --------------------------------
    l2_titles = _l2_fts_and_graph(
        compiler, db, connector, question, l1_titles, config.topic, verbose=verbose,
    )

    # ---- L3: Deep read (token-budgeted) ----------------------------------
    relevant_pages, raw_snippets = _l3_deep_read(
        config, db, connector, resolver, question, l2_titles, verbose=verbose,
    )
    return relevant_pages, raw_snippets


# ---------------------------------------------------------------------------
# L1 -- Catalog scan (~1-2K tokens)
# ---------------------------------------------------------------------------

def _l1_catalog_scan(
    compiler: Compiler,
    question: str,
    page_catalog: list[dict[str, Any]],
    topic: str,
    *,
    verbose: bool = False,
) -> list[str]:
    """Ask the LLM which pages to consult based on a compact catalog."""
    catalog_lines: list[str] = []
    for entry in page_catalog:
        title = entry["title"]
        page_type = entry.get("page_type", "")
        summary = (entry.get("summary") or "")[:60]
        source_count = len(entry.get("source_ids") or [])
        catalog_lines.append(
            f"- {title} | {page_type} | {summary} | sources={source_count}"
        )

    catalog_text = "\n".join(catalog_lines)

    prompt = (
        f'You are selecting pages to consult from a research wiki on "{topic}".\n\n'
        f"Question: {question}\n\n"
        f"Page catalog (title | type | summary | source_count):\n"
        f"{catalog_text}\n\n"
        f"Return a JSON array of page titles (strings) to consult in order to "
        f"answer the question.\n"
        f"Select at most 12 pages. Prefer pages that are directly relevant.\n"
        f"Return ONLY a JSON array of strings, nothing else."
    )

    try:
        result = compiler._call_json(
            system=(
                "You select relevant wiki pages for a research question. "
                "Return a JSON array of page title strings."
            ),
            prompt=prompt,
            max_tokens=512,
            method_name="l1_catalog_scan",
        )
        titles = _extract_title_list(result, max_items=12)
    except Exception:
        # Fallback: return nothing; L2 will still find pages via FTS
        titles = []

    if verbose:
        console.print(f"  [dim]L1 catalog scan -> {len(titles)} pages: {titles}[/dim]")

    return titles


# ---------------------------------------------------------------------------
# L2 -- FTS search + graph expansion (~2-5K tokens)
# ---------------------------------------------------------------------------

def _l2_fts_and_graph(
    compiler: Compiler,
    db: EvidenceDatabase,
    connector: ObsidianConnector,
    question: str,
    l1_titles: list[str],
    topic: str,
    *,
    verbose: bool = False,
) -> list[str]:
    """Use FTS5 search + graph expansion to find additional pages, then ask
    the LLM which ones need full reading."""
    # Start with L1 selections
    candidate_titles: set[str] = set(l1_titles)

    # Always include high-level navigation anchors
    candidate_titles.add("Index")
    candidate_titles.add(f"{topic} Overview")

    # FTS search across pages, chunks, and claims
    fts_results = db.search(question, scope="all", limit=12)
    fts_page_titles: list[str] = []
    for hit in fts_results:
        if hit.title and hit.path:
            candidate_titles.add(hit.title)
            fts_page_titles.append(hit.title)

    # Graph expansion via ObsidianConnector
    graph_titles: list[str] = []
    for title in list(candidate_titles):
        try:
            neighborhood = connector.get_neighborhood(title)
        except (FileNotFoundError, ValueError):
            continue
        for t in neighborhood.outbound_pages[:3]:
            candidate_titles.add(t)
            graph_titles.append(t)
        for t in neighborhood.supporting_source_pages[:3]:
            candidate_titles.add(t)
            graph_titles.append(t)
        for t in neighborhood.related_pages[:2]:
            candidate_titles.add(t)
            graph_titles.append(t)
        for t in neighborhood.cited_source_pages[:2]:
            candidate_titles.add(t)
            graph_titles.append(t)

    if verbose:
        console.print(f"  [dim]L2 FTS hits: {fts_page_titles}[/dim]")
        console.print(
            f"  [dim]L2 graph expansion added: {len(graph_titles)} titles[/dim]"
        )
        console.print(
            f"  [dim]L2 total candidates: {len(candidate_titles)}[/dim]"
        )

    # Build compact summaries of all candidates for LLM filtering
    page_catalog = db.page_catalog()
    catalog_by_title = {entry["title"]: entry for entry in page_catalog}

    summary_lines: list[str] = []
    for title in sorted(candidate_titles):
        entry = catalog_by_title.get(title)
        if entry:
            summary = (entry.get("summary") or "")[:80]
            page_type = entry.get("page_type", "")
            summary_lines.append(f"- {title} | {page_type} | {summary}")
        else:
            summary_lines.append(f"- {title} | unknown | (no catalog entry)")

    summaries_text = "\n".join(summary_lines)

    # Ask LLM to filter down to pages that need full reading
    prompt = (
        f'You are filtering wiki pages for deep reading to answer a research '
        f'question on "{topic}".\n\n'
        f"Question: {question}\n\n"
        f"Candidate pages (title | type | summary):\n"
        f"{summaries_text}\n\n"
        f"Return a JSON array of page titles (strings) that need full reading "
        f"to answer the question.\n"
        f"Select at most 8 pages. Exclude pages unlikely to contain relevant "
        f"content.\n"
        f"Return ONLY a JSON array of strings, nothing else."
    )

    try:
        result = compiler._call_json(
            system=(
                "You filter wiki pages for deep reading. "
                "Return a JSON array of page title strings."
            ),
            prompt=prompt,
            max_tokens=512,
            method_name="l2_filter_pages",
        )
        filtered = _extract_title_list(result, max_items=8)
    except Exception:
        # Fallback: take the L1 titles + first few FTS results
        filtered = list(l1_titles[:8])

    # Ensure we always have something
    if not filtered:
        filtered = sorted(candidate_titles)[:8]

    if verbose:
        console.print(
            f"  [dim]L2 filtered for deep read -> {len(filtered)} pages: "
            f"{filtered}[/dim]"
        )

    return filtered


# ---------------------------------------------------------------------------
# L3 -- Deep read (token-budgeted)
# ---------------------------------------------------------------------------

def _l3_deep_read(
    config: Config,
    db: EvidenceDatabase,
    connector: ObsidianConnector,
    resolver: TitleResolver,
    question: str,
    selected_titles: list[str],
    *,
    verbose: bool = False,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Read full page content for selected pages, bounded by token budget."""
    token_budget = config.query_token_budget
    approx_tokens = 0
    relevant_pages: dict[str, str] = {}

    for title in selected_titles:
        resolved = resolver.resolve_wikilink(title) or title
        try:
            page = connector.get_page(resolved)
        except (FileNotFoundError, ValueError):
            continue
        relative_path = page.relative_path.removeprefix("wiki/")
        if relative_path == "log.md":
            continue
        content = read_wiki_page(config, relative_path)
        if not content:
            continue

        content_tokens = len(content) // 4
        if approx_tokens + content_tokens > token_budget and relevant_pages:
            # Already have some pages; stop adding to stay within budget
            if verbose:
                console.print(
                    f"  [dim]L3 token budget reached ({approx_tokens} tokens) "
                    f"-- skipping remaining pages[/dim]"
                )
            break
        relevant_pages[relative_path] = content
        approx_tokens += content_tokens

    # Add raw chunk snippets from FTS
    question_terms = [term for term in question.split() if len(term) > 2]
    raw_snippets = db.get_source_chunk_snippets(question_terms, limit=6)

    for snippet in raw_snippets:
        snippet_tokens = len(snippet.get("text", "")) // 4
        if approx_tokens + snippet_tokens > token_budget:
            break
        approx_tokens += snippet_tokens

    # Enrich snippets with source titles
    source_title_map = {
        record["source_id"]: record["title"] or record["raw_title"]
        for record in db.list_source_records()
    }
    for snippet in raw_snippets:
        snippet["source_title"] = source_title_map.get(
            snippet["source_id"], snippet["source_id"]
        )

    if verbose:
        console.print(
            f"  [dim]L3 deep read: {len(relevant_pages)} pages, "
            f"{len(raw_snippets)} snippets, ~{approx_tokens} tokens[/dim]"
        )

    return relevant_pages, raw_snippets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_title_list(result: Any, *, max_items: int = 12) -> list[str]:
    """Extract a list of title strings from an LLM JSON response."""
    if isinstance(result, list):
        return [str(t) for t in result[:max_items]]
    if isinstance(result, dict):
        for key in ("pages", "titles", "results"):
            if key in result and isinstance(result[key], list):
                return [str(t) for t in result[key][:max_items]]
        # Take all string values
        return [str(v) for v in result.values() if isinstance(v, str)][:max_items]
    return []


# ---------------------------------------------------------------------------
# Cross-reference helpers
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")


def _register_output_cross_references(
    db: EvidenceDatabase, output_path: str, text: str,
) -> None:
    """Register wikilinks found in saved output text as aliases.

    This makes the output discoverable when searching for pages it
    references, but does NOT add source_ids to any concept page --
    preserving the T4 trust constraint (outputs never raise source
    counts or promote maturity).
    """
    catalog = {entry["title"]: entry for entry in db.page_catalog()}
    linked_titles: list[str] = []
    for match in _WIKILINK_RE.finditer(text):
        target = match.group(1).strip()
        if target in catalog:
            linked_titles.append(target)

    if linked_titles:
        # Register the output's own aliases so it is discoverable via
        # the page catalog search (FTS) when someone looks for related
        # pages.  The output already has its title registered; adding
        # the linked page titles as aliases makes the neighborhood
        # connection explicit without mutating any other page's data.
        db.register_aliases(
            "output",
            catalog[output_path]["title"] if output_path in catalog else output_path,
            linked_titles,
        )
