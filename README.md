# Compile

Compile is a small CLI for bootstrapping and inspecting an LLM-maintained wiki workspace.

The intended shape is general-purpose, not research-only: philosophy notes, personal material, project documentation, reading notes, and source-backed articles should all fit without forcing everything into `concept/entity/question`.

## Scope

This repo currently ships:

- workspace initialization
- a minimal raw-source ingest scaffold that creates source notes and refreshes navigation
- Obsidian-ready vault setup
- deterministic `index.md` and `overview.md` refresh
- vault inspection, graph/search/navigation helpers
- health and quality evaluation

This repo does not currently ship a fully automated ingest, query, or watch pipeline. The current `ingest` command is a scaffold: it creates a provenance-aware source note, refreshes navigation, and suggests likely follow-up pages for the LLM to update.

## Install

```bash
uv sync
```

## Quick Start

Create a workspace:

```bash
uv run compile init "Walker Wiki" -d "General-purpose personal knowledge base"
```

That creates:

```text
.
├── raw/
├── wiki/
│   ├── articles/
│   ├── sources/
│   ├── maps/
│   ├── outputs/
│   ├── index.md
│   ├── overview.md
│   └── log.md
├── .compile/
└── .obsidian/
```

Add or update a page:

```bash
uv run compile obsidian upsert "Friendship" \
  --page-type article \
  --body "Aristotle treats friendship as a shared practice of the good."
```

Create a source-note scaffold from a raw artifact:

```bash
uv run compile ingest example-paper.pdf
```

Refresh navigation pages after edits:

```bash
uv run compile obsidian refresh
```

Inspect the vault:

```bash
uv run compile obsidian inspect
uv run compile obsidian search "friendship virtue"
uv run compile obsidian page "Friendship"
uv run compile obsidian neighbors "Friendship"
uv run compile obsidian graph
```

Run higher-level checks:

```bash
uv run compile health
uv run compile status
uv run compile schema
```

## Recommended Flow

1. Run `compile init`.
2. Put raw material in `raw/` when provenance matters.
3. Maintain durable pages in `wiki/articles/`, `wiki/sources/`, `wiki/maps/`, and `wiki/outputs/`.
4. Run `compile obsidian refresh` to rebuild `wiki/index.md` and `wiki/overview.md`.
5. Use `compile obsidian inspect` and `compile health` to catch unresolved links, orphans, stale nav, and provenance gaps.
6. Use `compile ingest <raw-file>` to scaffold a source note before doing deeper LLM maintenance work on related pages.

## Page Model

New workspaces are organized around a small generic set of page types:

- `article`: the default durable wiki page
- `source`: a note that anchors provenance back to `raw/`
- `map`: a navigation or map-of-content page
- `output`: a saved derived artifact
- `index`, `overview`, `log`: navigation and maintenance pages

Legacy `concept`, `entity`, `question`, and `dashboard` layouts are still understood by the inspector and evaluator so older workspaces continue to work.

## Obsidian

Compile writes normal markdown plus standard YAML frontmatter and `[[wikilinks]]`. The vault opens directly in Obsidian with:

- `.obsidian/` config included
- graph/backlinks working normally
- navigation pages readable without chat context
- no custom database or proprietary viewer required

## Notes

- `raw/` is optional for personal writing, but useful when you want explicit provenance.
- `compile obsidian cleanup` quarantines empty stray markdown files outside the maintained wiki.
- `compile health` now combines structural Obsidian checks with content-oriented quality checks.
