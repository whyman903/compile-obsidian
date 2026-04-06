# Compile

An LLM-maintained research wiki. Drop sources in, get a living knowledge base out.

Compile is built around a persistent wiki, not one-shot chat over documents. It maintains source notes, concept pages, entity pages, question pages, dashboards, and saved outputs as normal markdown that can be opened directly in Obsidian.

## Workspace Modes

Compile currently recognizes two workspace shapes:

- `compile_workspace`: the primary product. This is a native Compile vault with `.compile/`, `wiki/`, and `.obsidian/`.
- `backend_workspace`: a backend-exported page store with `workspace.json` and `pages/`. These can be inspected, searched, and health-checked, but they are not Obsidian-native by default.

If you care about Obsidian graph quality, backlinks, and `[[wikilinks]]`, treat `compile_workspace` as the canonical format.

## Install

```bash
uv sync
```

Set your API key in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Optional:

```bash
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

## Quick Start

### Create a Compile workspace

```bash
compile init "AI Agents for Debugging"
```

This creates:

```text
.
├── raw/
├── wiki/
│   ├── index.md
│   ├── overview.md
│   ├── log.md
│   ├── sources/
│   ├── concepts/
│   ├── entities/
│   ├── questions/
│   ├── outputs/
│   └── dashboards/
├── .compile/
│   ├── config.yaml
│   ├── state.json
│   ├── evidence.db
│   └── source-packets/
└── .obsidian/
```

### Add sources

Drop files into `raw/`, then run:

```bash
compile ingest
compile ingest raw/some-paper.pdf
compile ingest https://example.com/article
```

Compile will:

- extract source packets
- analyze sources with Anthropic
- maintain source, concept, entity, and question pages
- normalize frontmatter and sourcing
- refresh dashboards, `index.md`, and `overview.md`
- append to `log.md`

PDFs are analyzed with Anthropic native PDF support. Compile no longer relies on local `pypdf` extraction for runtime source analysis.

### Watch for new files

```bash
compile watch
```

### Ask questions

```bash
compile query "What architectural patterns recur across these sources?"
compile query "Where do the sources disagree?" --save
```

Saved answers are written to `wiki/outputs/` and indexed as derived artifacts.

## Health and Inspection

Compile now separates three concerns:

- `compile health`: canonical workspace health report
- `compile lint`: LLM content audit of the maintained wiki
- `compile obsidian inspect`: deterministic vault and graph audit

### Canonical health report

```bash
compile health
compile health --json-output
compile health --write-snapshot
compile health --path /path/to/backend-workspace
```

`compile health` reports:

- `obsidian_readiness`
- `graph_health`
- `content_health`

It will not label a workspace as healthy if it has high-severity readiness failures such as missing `.obsidian` config or zero wikilinks.

### Content audit

```bash
compile lint
```

This is the LLM audit pass over current wiki pages. Use it to surface contradictions, weak sourcing, thin synthesis, and missing connections in the maintained wiki content.

### Obsidian inspection

```bash
compile obsidian inspect
compile obsidian inspect --json-output
compile obsidian inspect --path data/ai-debugging
compile obsidian inspect --path /path/to/backend-workspace
compile obsidian page "Planner Executor Loops" --path data/ai-debugging
compile obsidian neighbors "Planner-Executor Architecture" --path data/ai-debugging
compile obsidian graph --path data/ai-debugging
compile obsidian refresh --path data/ai-debugging
compile obsidian upsert "Planner-Executor Loops" --page-type concept --body "# Planner-Executor Loops"
```

Use `obsidian inspect` when you want deterministic graph facts such as:

- unresolved links
- orphan pages
- stale navigation pages
- raw files without source notes
- source pages without raw backlinks
- weak cross-source synthesis
- auxiliary markdown clutter

## Storage Model

Runtime state is SQLite-backed.

- `.compile/evidence.db` is the source of truth for source packets, analyses, claims, aliases, and page catalog state.
- `page_catalog` powers query selection, search, alias resolution, and page metadata lookup.
- in-memory evidence views used during ingest and synthesis are reconstructed from SQLite at runtime.

`evidence.json` remains only as a compatibility/export layer for older tests and utilities. The normal ingest, query, synthesis, and navigation workflows no longer depend on it as a required runtime store.

## Obsidian

Open a `compile_workspace` directory as an Obsidian vault:

- graph view uses `[[wikilinks]]`
- backlinks work normally
- frontmatter stays standard YAML
- dashboards and nav pages stay readable without chat context

Backend-exported workspaces can be inspected with Compile, but they are not Obsidian-ready unless they are upgraded to include `.obsidian` config, wikilinks, and proper raw-source backlinks.

## Status and Schema

```bash
compile status
compile schema show
compile schema refresh
```

## Tests

```bash
uv run pytest -q
uv run pytest -k "not full_ingest"
```

The offline suite should pass without API access. Integration-style tests that hit the Anthropic API remain opt-in or skipped without credentials.

## Notes

- Compile-native workspaces are the mainline product.
- Backend workspaces are valid inspection targets, but lower-quality Obsidian artifacts unless explicitly converted.
- Health summaries should be read as layered status, not a single opaque score.
