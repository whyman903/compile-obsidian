# Compile — Wiki Maintainer Contract

You maintain a persistent, LLM-maintained wiki for Obsidian. The wiki compounds over time — every source processed and every question answered should make it richer.

Read `WIKI.md` in the workspace root for topic-specific editorial rules. If it differs from this file, apply `WIKI.md` as the override.

## Layout

```
raw/            Immutable source artifacts. Read freely, never modify.
wiki/
  articles/     Durable synthesis pages (default page type)
  sources/      Source notes anchored to raw material
  maps/         Navigation and map-of-content pages
  outputs/      Saved derived artifacts (answers, comparisons, memos)
  index.md      Catalog of all pages with summaries
  overview.md   Landing page reflecting current wiki shape
  log.md        Append-only chronology
.compile/       Runtime state
WIKI.md         Per-workspace schema (topic-specific editorial rules)
```

## CLI Tools

Use these tools throughout your work. If the entrypoint is not installed, use `uv run compile ...`.

### Discovery and inspection

```bash
compile status                        # workspace stats: page counts, unprocessed sources
compile obsidian inspect              # full vault audit: link health, orphans, thin pages, graph density
compile obsidian search "query"       # find pages by title, tags, aliases, or body content
compile obsidian page "Title"         # read a page with metadata, links, and body
compile obsidian neighbors "Title"    # see what links to and from a page
compile obsidian graph                # top connected nodes, edge count, hub structure
compile health                        # structural + content health report (broken links, stale nav, malformed summaries)
compile health --json-output          # machine-readable version for programmatic checks
```

### Writing and maintenance

```bash
compile ingest <source>               # create a source-note scaffold for a raw file, update nav
compile obsidian upsert "Title" \
  --page-type article \
  --body "markdown content" \
  --tag "topic" --source "Source A"    # create or update any page with frontmatter
compile obsidian refresh              # regenerate index.md and overview.md from current pages
compile obsidian cleanup              # quarantine empty stub files created by Obsidian
compile schema                        # print the current WIKI.md schema
```

### When to use which tool

- **Before writing**: run `compile obsidian search` or `compile obsidian inspect` to see what already exists. Don't create pages that duplicate existing content.
- **After creating/updating pages**: run `compile obsidian refresh` to keep index and overview current, then `compile health` to catch issues.
- **To understand a page's context**: run `compile obsidian neighbors "Title"` to see backlinks, outbound links, and supporting sources.
- **To find problems**: run `compile health --json-output` for a comprehensive audit, or `compile obsidian inspect` for graph-level issues.

## Ingest Workflow

When the user adds a source to `raw/` and asks you to process it:

1. **Discover context first.** Run `compile obsidian search` with key terms from the source to find related existing pages.
2. **Create the source scaffold.** Run `compile ingest <filename>` to create a source note with provenance. Then read the scaffold and improve it — add the main claims, key findings, limitations. The scaffold is a starting point, not the finished page.
3. **Update existing articles.** If the source adds evidence to existing articles, update those articles — don't spawn new ones. Run `compile obsidian page "Title"` to read the current content before editing.
4. **Create new articles only when warranted.** A new article is justified when: the topic recurs across sources, it's central enough for standalone navigation, or an existing page would lose focus.
5. **Refresh navigation.** Run `compile obsidian refresh` to update index and overview.
6. **Append to the log.** Record what was processed, what pages were created or updated.
7. **Check quality.** Run `compile health` and fix any issues before finishing.

## Query Workflow

When the user asks a question against the wiki:

1. **Start from the index.** Run `compile obsidian search "query"` or read `wiki/index.md` to find relevant pages.
2. **Read wiki pages first**, not raw files. The wiki is the synthesized layer.
3. **Pull raw sources only when needed** — when the wiki is insufficient or you need to verify a specific claim.
4. **File durable answers back.** If the answer would be useful later, save it as a page in `wiki/outputs/` using `compile obsidian upsert`. This is how queries compound into the knowledge base.

## Lint Workflow

Periodically, or when the user asks, audit the wiki:

1. **Run structural checks.** `compile health --json-output` catches: broken links, orphan pages, stale navigation, malformed summaries, thin pages, premature stability.
2. **Run graph inspection.** `compile obsidian inspect` shows: page type distribution, link density, unresolved targets, orphan count.
3. **Do editorial review.** Read through articles looking for:
   - Pages that just paraphrase one source instead of synthesizing
   - Outdated claims that newer sources have superseded
   - Missing cross-references (an article mentions a topic that has its own page but doesn't link to it)
   - Pages marked `stable` that don't actually demonstrate synthesis
   - Duplicate or near-duplicate pages that should be merged
4. **Fix what you can.** Update stale pages, add missing links, merge duplicates, downgrade premature stability.
5. **Run `compile obsidian refresh`** after making changes.

## Frontmatter Contract

Every maintained page must include:

```yaml
title: "Page Title"
type: article          # article, source, map, output, index, overview, log
status: seed           # seed, emerging, stable
summary: "One-line description for index and overview."
created: 2026-04-07T00:00:00+00:00
updated: 2026-04-07T00:00:00+00:00
```

Optional when relevant: `tags`, `aliases`, `sources`, `source_ids`, `cssclasses`.

### Status meanings

- **seed**: Provisional. Usually one source or one line of evidence. Useful but incomplete.
- **emerging**: Multiple signals or sources, partially synthesized. Getting there.
- **stable**: Durable reference. Well-supported, explicitly sourced, no obvious structural gaps.

## Page-Type Expectations

### source
Faithful compressed notes from a raw artifact. Capture what it says, not what you think about it. Must link back to the raw file in `raw/`. Include: synopsis, main claims, limitations, provenance.

### article
Synthesis page. Connects ideas across sources. Must have a thesis or framing, supporting evidence with `[[Source]]` citations, and explicit limitations or tensions. This is where the wiki's value lives — articles should tell you something you can't get from reading any single source.

### map
Navigation page. Defines a region of the wiki, surfaces the important pages, highlights gaps. Creates navigable structure for a cluster of related articles.

### output
Saved answer, comparison, or derived artifact. Records: what question was answered, what inputs mattered, why it was worth saving. Filed back from queries to make the wiki compound.

## Editorial Rules

1. **Prefer updating over creating.** Before making a new page, search for existing pages that could absorb the content.
2. **Synthesis means comparison.** An article that synthesizes should name where sources agree, where they diverge, and what remains uncertain. Restating one source in different words is paraphrase, not synthesis.
3. **Don't flatten contradictions.** When sources disagree, say so explicitly. Keep source pages faithful to their source even when synthesis judges them weak.
4. **Link generously but not decoratively.** Link the first substantial mention of an important topic. Don't link every repeated occurrence.
5. **Use seed/emerging honestly.** Don't mark pages `stable` until they genuinely synthesize and have no obvious structural gaps.
6. **File outputs back.** Durable answers to questions should become wiki pages, not disappear into chat history. This is how the wiki compounds.
7. **Keep navigation current.** After changes, run `compile obsidian refresh`. Stale index/overview pages make the wiki feel abandoned.
