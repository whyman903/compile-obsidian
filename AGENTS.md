# Compile Wiki Maintenance Guide

This repository is a persistent, LLM-maintained wiki system. Treat it as a knowledge codebase, not a one-shot RAG app.

## Contract

- `raw/` contains curated source artifacts. Agents may read these files but should not rewrite them.
- `wiki/` contains maintained markdown pages intended for Obsidian. Agents may create and update these files freely.
- `.obsidian/` contains vault configuration. Keep it compatible with standard Obsidian behavior.
- `index.md` is the catalog. Update it whenever the set of maintained pages changes.
- `log.md` is chronological and append-only. Record ingests, saved outputs, and maintenance passes.
- `overview.md` is the workspace landing page. It should reflect the current wiki shape, not the initial state.

## Page Rules

- Every maintained page must have YAML frontmatter with at least `title`, `type`, and `updated`.
- Use `[[Page Title]]` wikilinks for page-to-page navigation.
- Only link to pages that already exist, unless the task is explicitly to create the missing page in the same pass.
- Keep source provenance explicit. Source notes should link back to the raw artifact when possible.
- Prefer stable page titles over brittle filename-only references.

## Operational Workflow

### Ingest

1. Read the raw source.
2. Create or update the source note.
3. Update affected concept, entity, and question pages.
4. Refresh `index.md`.
5. Refresh `overview.md`.
6. Append to `log.md`.
7. Check link health before finishing.

### Query

1. Start from `index.md` or the Obsidian connector search tools.
2. Read the most relevant maintained pages first.
3. Pull raw artifacts only when the maintained wiki is insufficient or needs verification.
4. Save durable outputs back into `wiki/outputs/` when they add lasting value.

### Lint

Look for:

- unresolved links
- orphan pages
- duplicate or near-duplicate concepts
- thin synthesis pages
- stale overview/index content
- concepts that only paraphrase one source without synthesis

## Obsidian Interaction

Use the CLI connector when exploring the vault:

- `compile obsidian inspect`
- `compile obsidian search "<query>"`
- `compile obsidian page "<locator>"`
- `compile obsidian neighbors "<locator>"`
- `compile obsidian graph`
- `compile obsidian refresh`
- `compile obsidian upsert "<title>" --page-type <type>`

These commands are the preferred way for an agent to inspect graph quality, resolve approximate page names, traverse backlinks and raw-file references, and safely maintain Compile-native pages.

## Quality Bar

- The wiki should feel navigable in Obsidian without chat context.
- Concept pages should synthesize, not merely restate.
- Source notes should anchor claims and preserve provenance.
- The graph should get denser and more useful over time.
- If a workspace is only metadata-connected but not wikilinked, call that out explicitly as lower-quality Obsidian support.
