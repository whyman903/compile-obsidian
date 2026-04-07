# Compile Workspace Guide

This file is the Claude-facing copy of the base maintainer contract. It should stay semantically aligned with `AGENTS.md`.

Compile is a persistent, LLM-maintained wiki for Obsidian. Treat it as a knowledge codebase that compounds over time.

## Layout

```text
raw/            Immutable source artifacts when provenance matters
wiki/
  articles/     Default durable synthesis pages
  sources/      Provenance-anchored source notes
  maps/         Navigation and map-of-content pages
  outputs/      Saved derived artifacts
  index.md      Catalog
  overview.md   Landing page
  log.md        Append-only chronology
.obsidian/      Standard Obsidian configuration
.compile/       Runtime state and maintenance helpers
WIKI.md         Per-workspace schema overlay
```

## Core Editorial Rules

1. Prefer updating an existing page over spawning a new one.
2. Default durable page type is `article`; use `source`, `map`, and `output` when they are clearly better fits.
3. Use `[[Page Title]]` links for navigation and only link to non-existent pages when creating them in the same pass.
4. Keep provenance explicit for source-backed material.
5. Do not confuse paraphrase with synthesis.

## Frontmatter

Every maintained page must include:

- `title`
- `type`
- `status`
- `summary`
- `created`
- `updated`

Recommended when relevant:

- `tags`
- `aliases`
- `sources`
- `source_ids`
- `citations`
- `cssclasses`

Status meanings:

- `seed`: provisional
- `emerging`: partially synthesized
- `stable`: durable and well-supported

## Page-Type Expectations

### `source`

- capture what the artifact is
- record the main claims
- note important limitations
- link or embed the raw artifact in `raw/`

### `article`

- present a thesis or framing
- connect supporting evidence
- note disagreements, limits, or open edges when present
- link to related pages

### `map`

- define scope
- surface key pages
- highlight gaps or next useful navigation paths

### `output`

- record the question answered
- note the inputs that mattered
- explain why this output was worth saving

## Creation And Split Rules

- Create a new page only when the topic is durable, recurring, central, or too large for its current host page.
- Merge or retire weak fragments, aliases, and pages that exist only to restate one source.
- Split a page when it contains multiple separable theses or long sections that mostly belong to one subtopic.

## Contradictions And Uncertainty

- Do not flatten disagreements into false consensus.
- Keep source pages faithful to the source even when later synthesis judges the source weak or contradicted.
- Update synthesis pages to name where sources agree, where they diverge, and what remains uncertain.
- Use `seed` or `emerging` when the evidence base is thin.

## Workflow

### Ingest

1. Read the raw source.
2. Create or update a `source` page when provenance matters.
3. Update affected `article` and `map` pages.
4. Refresh `wiki/index.md` and `wiki/overview.md`.
5. Append a short entry to `wiki/log.md`.
6. Check links and quality before finishing.

### Query

1. Start from `wiki/index.md` or the Obsidian connector tools.
2. Read maintained pages before raw files.
3. Pull raw artifacts only when the wiki is insufficient or needs verification.
4. Save durable outputs to `wiki/outputs/`.

### Lint

Look for:

- unresolved links
- orphan pages
- duplicate or near-duplicate pages
- malformed summaries
- placeholder or stale nav content
- low-synthesis pages
- pages marked `stable` too early

## Tooling

Prefer the CLI connector:

- `compile obsidian inspect`
- `compile obsidian search "<query>"`
- `compile obsidian page "<locator>"`
- `compile obsidian neighbors "<locator>"`
- `compile obsidian graph`
- `compile obsidian refresh`
- `compile obsidian upsert "<title>" --page-type <type>`

If the entrypoint is not installed, use `uv run compile ...`.

## Mini Examples

Good `source` summary:

```md
summary: Reports X, argues Y, and matters because Z.
```

Good `article` framing:

```md
## Thesis

This page synthesizes how several approaches trade recall for cost under context limits.
```

Good contradiction handling:

```md
## Tensions

- [[Paper A]] finds retrieval depth is the bottleneck.
- [[Paper B]] argues compression quality dominates instead.
```
