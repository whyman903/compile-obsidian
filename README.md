# Compile

Compile is an LLM-maintained wiki for Obsidian. You curate sources and ask questions; Claude does all the writing, cross-referencing, and maintenance.

## Install

```bash
uv tool install .
```

## Quick Start

1. **Create a workspace:**

```bash
compile init "My Wiki" -d "What this wiki is about"
```

2. **Set up Claude Code integration:**

```bash
compile claude setup .
```

This installs slash commands (`/capture`, `/wiki-query`, `/wiki-context`) that make the wiki accessible from any Claude Code session, plus workspace-local commands (`/ingest`, `/query`, `/lint`) for the full editing toolset.

3. **Open the workspace in Obsidian** — it's already configured as a vault.

4. **Start working with Claude.** Drop sources into `raw/`, then use `/ingest` to process them. Ask questions with `/wiki-query`. Claude reads, writes, and maintains the wiki — you guide it.

## How It Works

There are three layers:

- **`raw/`** — your source documents (articles, papers, images). Immutable. Claude reads from here but never modifies it.
- **`wiki/`** — LLM-generated markdown pages. Summaries, articles, maps, cross-references. Claude owns this layer entirely.
- **`WIKI.md`** — the schema that tells Claude how the wiki is structured and what conventions to follow.

The wiki compounds over time. Every source processed and every question answered makes it richer.

## What Claude Does

- **Ingest**: reads a source, writes a summary, updates the index, revises related articles, logs what changed.
- **Query**: searches the wiki, synthesizes an answer, and files durable answers back as new pages.
- **Lint**: audits for broken links, stale claims, missing cross-references, orphan pages, and contradictions.

## Page Types

- `source` — provenance-anchored note for a raw artifact
- `article` — durable synthesis page (the default)
- `map` — navigation page that curates a region of the wiki
- `output` — saved answer, comparison, or derived artifact

## Development

```bash
uv sync
uv run pytest
```
