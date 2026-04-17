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
compile obsidian refresh
compile obsidian cleanup
compile schema
compile render canvas <title> --nodes-file /tmp/nodes.json [--edges-file /tmp/edges.json]
compile render marp <title> --body-file /tmp/deck.md
compile render chart <title> --script-file /tmp/chart.py
```

### Low-level page writes

Use this only as the executor beneath the workflows above, or for deliberate manual repair:

```bash
compile obsidian upsert "Title" \
  --page-type article \
  --status emerging \
  --body-file /tmp/page.md
```

### Connected workspace commands

- `/notion-setup` — use the connected Notion tools in Claude to save a local sync profile without asking the user for page IDs.
- `/notion-sync` — sync Notion pages into `raw/notion/`, then run the normal ingest workflow.

### Tool selection

- Search before creating pages. Prefer updating an existing page over spawning a near-duplicate.
- The slash-command workflows are the primary contract. Keep `compile obsidian upsert` below the fold: use it when a workflow needs to rewrite or retag a page.
- Prefer `compile obsidian upsert --body-file ...` when a direct page write is necessary.
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

When the user adds a source to `raw/` and asks you to process it, work through two phases. Phase A stands up the source note. Phase B wires it into the wiki and is mandatory — a source note disconnected from any article or map is incomplete.

### Parallelism

Treat ingest as phased work. Batch independent read-only steps together when helpful, such as wiki searches, reading related pages, and reading raw sources. Serialize workspace writes: do not run multiple `compile ingest` commands in parallel, do not update multiple anchor pages concurrently, and run `compile obsidian refresh` plus `compile health` once after the write phase finishes.

### Phase A — Enrich the source note

1. Search the wiki first for existing related pages.
2. Run `compile ingest <filename>` to register the source and create a first-pass source note.
3. Read the generated source note.
4. Read the raw source itself when the generated note is weak, incomplete, or needs verification.
5. When a substantial improvement is warranted, rewrite the source note in place. Use the low-level page writer with `--body-file` for the actual write. When you rewrite, add:
   - A `## Themes` section listing 1–3 broader themes this source belongs to, each with a `[[wikilink]]` to the existing article or map that covers it (or a plain theme name when no page exists yet).
   - A `## Key Claims` section naming the main arguments or findings, each with a one-line note on evidence strength (e.g., "strong: cites longitudinal study", "weak: assertion without citation").

### Phase B — Wire into the wiki

Do not skip this phase. A source note that is not linked from any article or map page, and has no outbound wikilinks to one, is incomplete.

6. **Locality guard.** During ingest, direct edits are limited to the source note plus 1–3 theme anchors identified here. If a useful change would reach beyond those anchors into a broader cluster, defer it to `/lint` or `/synthesize`.
7. **Identify themes.** Name 1–3 broader themes this source belongs to (e.g., "evaluation metrics", "animal ethics"). If step 5 added a `## Themes` section, start from those. If the source was too thin to rewrite, or you skipped the rewrite because the generated note was already faithful, derive the themes now from whatever content is available — the title, summary, and any prose already in the note.
8. **Wire into existing structure.** For each theme:
   - Search the wiki for an existing article or map.
   - If one exists and this source strengthens it, treat that page as the anchor. Update only that anchor page to incorporate the source's evidence or nuance, and ensure a `[[wikilink]]` connects the source note and the article/map (either outbound from the source note or via the anchor page citing `[[Source Title]]`).
   - If no article exists but 3+ sources now touch the theme, create a `seed` article that synthesizes across them and treat that new page as the anchor for this ingest pass.
   - If fewer than 3 sources share the theme, note the gap in the log entry rather than creating a stub article.
9. **Verify wiring.** Run `compile obsidian neighbors "Source Title"`. The source note should be connected to at least one article or map page via outbound links or inbound backlinks from an article/map. If it isn't and a plausible target exists, return to step 8. If no target exists, explain why in the log entry.
10. **Companion artifact.** Consider a richer format using the triggers below. Offer the artifact when it would add durable value. Create it only when the user asks for it or explicitly agrees.
11. Run `compile obsidian refresh` and then `compile health`.
12. **Cross-source synthesis check.** Before ending the session, if you ingested more than one source, pause and ask: does the set make a claim, pattern, or tension visible that no single source makes visible? If yes, capture it inside the same local boundary when possible. If the right change would span a broader cluster, defer that work to `/synthesize`. If two sources materially disagree — on facts, norms, or framing — surface the disagreement in the relevant anchor article with a `> [!warning] Disagreement` callout naming both sources and the specific point of contention. Do not resolve disagreements by picking a winner.

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
10. Surface disagreement. When two sources materially disagree — on factual claims, normative positions, or theoretical frameworks — update the relevant article with a `> [!warning] Disagreement` callout naming both sources and the specific disagreement. Do not resolve the disagreement by picking a winner. Present both positions with their evidence or reasoning.
11. During `/ingest`, keep edits local: the source note plus 1–3 theme anchors. Use `/lint` or `/synthesize` for broader changes.

## Status Discipline

Status is prompt-judged by you during ingest and lint, not machine-enforced. The health check flags the most egregious violations as a backstop, but you are responsible for honest assessment.

- `seed`: 0–1 sources, or a single line of evidence. Default for new articles.
- `emerging`: 2+ sources with partial synthesis. The page names its sources and attempts cross-source claims.
- `stable`: 3+ sources with genuine synthesis. The page names where sources agree, where they diverge, and what remains uncertain.

Source note status is always `stable` unless the note is a registration shell, has `review_status: needs_document_review`, or is too thin to support meaningful claims (e.g., an empty Notion stub). Thin source notes should be `seed` with a `> [!note]` callout explaining the gap.

Do not mark an article `stable` unless it meets the definition. When in doubt, use `seed`. When you update an article and it now meets a higher bar, upgrade it.

When you need to change a page's status, use the low-level page writer with `--status seed|emerging|stable`. Rewriting the body alone preserves the existing status — do not rely on body edits to promote or demote a page.

## What Good Synthesis Looks Like

A good article:

- Opens with a framing question or thesis.
- Draws claims from multiple sources, citing each with `[[Source Title]]`.
- Names tensions or disagreements between sources rather than smoothing them.
- Identifies what remains uncertain or unresolved.
- Links to related articles and maps.

A bad article restates one source in different words and calls it synthesis. If your article could be written from a single source, it is a source note, not a synthesis page.
