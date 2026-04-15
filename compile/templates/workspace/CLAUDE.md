# Compile — Wiki Maintainer Contract

You maintain a persistent, LLM-maintained wiki for Obsidian. The wiki compounds over time: every source processed and every useful answer saved should make it better.

Read `WIKI.md` in the workspace root for topic-specific editorial rules. If it differs from this file, follow `WIKI.md`.

## Layout

```text
raw/            Immutable source artifacts. Read freely, never modify.
wiki/
  articles/     Durable synthesis pages
  sources/      Source notes anchored to raw material
  maps/         Navigation pages
  outputs/      Saved derived artifacts
  index.md      Catalog of all pages
  overview.md   Landing page
  log.md        Append-only chronology
.compile/       Runtime state
WIKI.md         Workspace-specific schema
```

## CLI Tools

If the global `compile` entrypoint is unavailable, use `uv run compile ...`.

### Discovery and inspection

```bash
compile status
compile obsidian inspect
compile obsidian search "query"
compile obsidian page "Title"
compile obsidian neighbors "Title"
compile obsidian graph
compile suggest maps
compile health
compile health --json-output
```

### Writing and maintenance

```bash
compile ingest <source>
compile obsidian upsert "Title" \
  --page-type article \
  --body-file /tmp/page.md
compile obsidian refresh
compile obsidian cleanup
compile schema
compile render canvas <title> --nodes-file /tmp/nodes.json [--edges-file /tmp/edges.json]
compile render marp <title> --body-file /tmp/deck.md
compile render chart <title> --script-file /tmp/chart.py
```

### Connected workspace commands

- `/notion-setup` — use the connected Notion tools in Claude to save a local sync profile without asking the user for page IDs.
- `/notion-sync` — sync Notion pages into `raw/notion/`, then run the normal ingest workflow.

### Tool selection

- Search before creating pages. Prefer updating an existing page over spawning a near-duplicate.
- Prefer `compile obsidian upsert --body-file ...` for substantial edits.
- Run `compile obsidian refresh` after page changes, then `compile health`.
- Prefer file-backed render inputs (`--body-file`, `--script-file`, `--nodes-file`) over large inline shell strings.

## Rich Formatting

The wiki is not text-only. Markdown paragraphs are the fallback, but richer formats should be used selectively when they materially improve comprehension or navigation.

Format triggers — use the first match:

| Trigger | Format |
|---|---|
| Comparison of 3+ items on shared dimensions | Table (or `compile render chart` if quantitative) |
| Relationships between 4+ concepts, causal chains, actor maps, dependencies | `compile render canvas` |
| Sequential process, argument flow, or small hierarchy (3–15 nodes) | Mermaid diagram in-page |
| Teaching explanation, briefing, or walkthrough | `compile render marp` |
| Quantitative data, trends, distributions | `compile render chart` |
| Notable caveat, definition, or strong claim | Callout (`> [!note]`, `> [!warning]`, etc.) |
| None of the above | Plain prose with wikilinks |

Use callouts freely alongside any format — they are always appropriate for caveats and definitions.

Do not create rendered artifacts by default for routine notes or answers. Offer them when they would clearly help, and create them when the user asks for them or explicitly agrees.

## Ingest Workflow

When the user adds a source to `raw/` and asks you to process it:

1. Search the wiki first for existing related pages.
2. Run `compile ingest <filename>` to register the source and create a first-pass source note.
3. Read the generated source note.
4. Read the raw source itself when the generated note is weak, incomplete, or needs verification.
5. Rewrite the source note in place with `compile obsidian upsert --body-file ...` when a substantial improvement is warranted.
6. Update existing pages that should absorb the source. After `compile ingest`, inspect the source note and search the wiki for durable pages or navigation pages that should incorporate it. If a map, index, or overview should point to the source, add the link. If an article gains meaningful evidence, nuance, or correction from the source, integrate it into the body. Every source note should end up with at least one meaningful wikilink to an article or map page when such a page already exists.
7. Create a new article only when the topic deserves its own durable page.
8. If no article or map fits, note that gap in the log entry and use `compile suggest maps` to surface existing map candidates or confirm that a broad topic still has no hub.
9. Run `compile obsidian refresh` and then `compile health`.
10. Before ending the session, if you ingested more than one source, pause and ask: does the set make a claim, pattern, or tension visible that no single source makes visible? If yes, capture it — extend an existing article, update a map, or draft a synthesis seed in `wiki/maps/`. If no, end the session. This is a cross-source check; per-source absorption belongs in step 6.

### PDF sources

PDF handling is best-effort.

- If your Claude client/session supports direct PDF or document understanding, prefer that for real comprehension.
- If not, use the extracted text from `compile ingest` as a starting point.
- If extraction fails, `compile ingest` creates a registration shell. Replace that shell with a proper source note after reading the raw document through whatever tool access is available.
- If a figure, chart, or relationship diagram is worth saving, use an explicit render command instead of relying on automatic media extraction:
  - `compile render chart` for quantitative visuals
  - `compile render canvas` for relationship structure
  - `compile render marp` for teaching or briefing artifacts

## Query Workflow

When answering questions against the wiki:

1. Search the wiki first.
2. Read wiki pages before raw files.
3. Pull raw sources only when needed for verification or missing detail.
4. Before presenting an answer, check the format triggers above and pick the best fit.
5. Save durable answers back into the wiki when they will be useful later.
6. When saving durable material, wire it into the existing wiki structure by updating the relevant article, map, index, or overview page rather than leaving it isolated.

## Lint Workflow

Periodically, or when asked:

1. Run `compile health --json-output`.
2. Run `compile obsidian inspect`.
3. Fix broken links, stale navigation, weak summaries, orphan pages, duplicate pages, and premature `stable` labels.
4. Run `compile obsidian refresh` after edits.

## Frontmatter Contract

Every maintained page should include:

```yaml
title: "Page Title"
type: article
status: seed
summary: "One-line description for index and overview."
created: 2026-04-07 00:00
updated: 2026-04-07 00:00
```

Optional when relevant: `tags`, `aliases`, `sources`, `source_ids`, `cssclasses`.

## Editorial Rules

1. Prefer updating over creating.
2. Keep source notes faithful to the source.
3. Make synthesis pages actually synthesize across sources.
4. State contradictions directly instead of smoothing them away.
5. Use `seed`, `emerging`, and `stable` honestly.
6. Save durable outputs back into the wiki.
7. Keep navigation current.
8. Verify quote-sensitive material against the raw source.
9. Keep map pages lightweight and navigational; use whatever structure helps readers browse the topic.
