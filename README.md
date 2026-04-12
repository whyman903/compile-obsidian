# Compile

Compile is an LLM-maintained wiki for Obsidian. You curate sources and ask questions; Claude does the writing, cross-referencing, and maintenance through a disciplined CLI workflow.

## Install

Install from the local repo (not from PyPI — there's an unrelated `compile` package there):

```bash
uv tool install /path/to/obsidian-compile
```

To update after pulling new changes:

```bash
uv tool install /path/to/obsidian-compile --force
```

> **Warning:** Do not run `uv tool install compile` — that installs an unrelated PyPI package. Always install from the local path.

## Quick Start

1. **Create a workspace:**

```bash
compile init "My Wiki" -d "What this wiki is about"
```

2. **Set up Claude Code integration:**

```bash
compile claude setup .
```

This installs slash commands (`/capture`, `/wiki-query`, `/wiki-context`) that make the wiki accessible from any Claude Code session, plus workspace-local commands (`/context`, `/ingest`, `/query`, `/lint`) for the full editing toolset.

3. **Open the workspace in Obsidian** — it's already configured as a vault.

4. **Start working with Claude.** Drop sources into `raw/`, then use `/ingest` to process them. Ask questions with `/wiki-query`. For substantial page writes, prefer file-backed edits with `compile obsidian upsert --body-file ...`, then run `compile obsidian refresh` and `compile health`.

## How It Works

There are three layers:

- **`raw/`** — your source documents (articles, papers, images). Immutable. Claude reads from here but never modifies it.
- **`wiki/`** — LLM-generated markdown pages. Summaries, articles, maps, cross-references. Claude owns this layer entirely.
- **`WIKI.md`** — the schema that tells Claude how the wiki is structured and what conventions to follow.

The wiki compounds over time. Every source processed and every question answered makes it richer.

## What Claude Does

- **Ingest**: registers a source, writes a source note with provenance, and updates the index. Claude can then strengthen the note and update related articles.
- **Query**: searches the wiki, synthesizes an answer, and files durable answers back as new pages.
- **Lint**: audits for broken links, stale claims, missing cross-references, orphan pages, and contradictions.

PDF handling is intentionally simple: `compile ingest` does best-effort plain-text extraction, but Claude should use direct document understanding when the client/session supports it. Rich visuals are created explicitly with `compile render canvas`, `compile render marp`, and `compile render chart`.

## Workflow Notes

- Prefer `compile obsidian upsert --body-file ...` for multi-paragraph or web-sourced content. It is safer than large shell heredocs.
- After creating or updating multiple pages, run `compile obsidian refresh` and then `compile health`.
- New output pages may briefly show low-severity navigation bottleneck warnings until they are linked from articles or maps.
- Treat model-processed web extracts as working notes, not verified quotations. For quote-sensitive workflows, verify against the raw source.

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
