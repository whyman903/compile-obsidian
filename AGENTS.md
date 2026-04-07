# Compile Wiki Maintainer Contract

This repository is a persistent, LLM-maintained wiki system. Treat it as a knowledge codebase, not a one-shot RAG app or a chat transcript dump.

`AGENTS.md` is the base maintainer contract.
`WIKI.md` is the per-workspace overlay.
If the two differ, apply `WIKI.md` as the topic-specific override and keep the base rules everywhere else.

## Workspace Contract

- `raw/` contains curated source artifacts. Read freely. Do not rewrite them.
- `wiki/` contains maintained markdown pages intended for Obsidian. Create and update these freely.
- `.obsidian/` should remain compatible with normal Obsidian behavior.
- `wiki/index.md` is the catalog. Refresh it whenever the maintained page set changes.
- `wiki/overview.md` is the landing page. It should describe the current shape of the wiki, not the initial state.
- `wiki/log.md` is append-only and chronological. Record ingests, durable outputs, and maintenance passes.

## Canonical Page Types

Default generic workspaces should prefer a small set of page types:

- `source`: provenance-anchored note for a raw artifact
- `article`: durable synthesis page; default type for concepts, entities, themes, people, places, timelines, and questions unless a workspace-specific type is truly necessary
- `map`: navigation page that curates a region of the wiki
- `output`: saved derived artifact such as an answer page, comparison, memo, slide source, or chart specification
- `index`, `overview`, `log`: special maintenance and navigation pages

Legacy `concept`, `entity`, `question`, and `dashboard` types may exist in older workspaces. Read and maintain them correctly, but do not default to creating them in new generic workspaces unless `WIKI.md` explicitly wants that taxonomy.

## Frontmatter Contract

Every maintained page must include YAML frontmatter with at least:

- `title`
- `type`
- `status`
- `summary`
- `created`
- `updated`

Recommended optional fields when relevant:

- `tags`
- `aliases`
- `sources`
- `source_ids`
- `citations`
- `cssclasses`

Status values:

- `seed`: useful but provisional; usually one source, one line of evidence, or a page that still needs synthesis
- `emerging`: partially synthesized; more than one supporting signal but still incomplete
- `stable`: durable reference page with explicit synthesis, support, and no obvious unresolved structural gaps

Rules:

- `summary` must be navigation-safe because it may propagate into `index.md`, `overview.md`, maps, and dashboards.
- Do not leave malformed summaries, placeholders, or sentence fragments in frontmatter.
- `stable` is not a compliment. Use it only when the page is genuinely durable.

## Naming Rules

- Prefer singular, stable page names over brittle filenames or temporary labels.
- Use title case unless the canonical name is stylized differently.
- Use the most legible durable title, not the shortest slug.
- Add disambiguators only when needed.
- Acronyms may have their own page only when the acronym is itself a real retrieval target.

## Link Strategy

- Use `[[Page Title]]` wikilinks for page-to-page navigation.
- Link only to pages that already exist, unless creating the target page in the same pass.
- Link the first substantial mention of an important page in a section. Do not link every repetition.
- Add links where they improve traversal and synthesis, not as decoration.
- Prefer links between maintained pages over raw-file references, except when provenance itself matters.

## Page Creation Rules

- Default to updating an existing page rather than creating a new one.
- Create a new `article` only when at least one of these is true:
  - the topic recurs across multiple sources or pages
  - the topic is central enough to deserve durable retrieval and navigation
  - the existing page would become unfocused if expanded further
- Create a new `map` only when readers need navigation across a cluster of pages, not just another summary.
- Create a new `output` only when the result will still be useful after the current conversation.
- Do not create pages whose only purpose is to paraphrase one source in slightly different words.
- Merge, redirect, or retire pages that are weak aliases, shallow fragments, or near-duplicates.

## Sizing Guidance

- `article` pages should usually be substantial enough to say something real and small enough to stay coherent.
- If a page accumulates multiple separable theses, multiple distinct audiences, or long sections that mostly belong to one subtopic, split it.
- If a page is too small to stand on its own and has no navigation role, merge it into a broader page.

## Synthesis Standard

Good synthesis is not just compression. It should do at least one of:

- connect claims across multiple sources
- state a thesis or framing that helps the reader understand the topic
- identify tensions, disagreements, tradeoffs, or limits
- organize a cluster of related pages so the graph becomes easier to navigate

Bad synthesis looks like:

- a one-source paraphrase with a new title
- a page that only copies the source abstract in different words
- an “open question” page created just because the paper had a future-work paragraph
- many tiny pages with no strong reason to exist independently

## Contradictions And Uncertainty

- Do not flatten disagreements into false consensus.
- If source B contradicts source A, update the synthesis page and name the disagreement explicitly.
- Keep source notes faithful to the source even if the wiki later concludes the source is weak or contradicted.
- Label interpretation as interpretation. Do not present inference as direct source fact.
- Prefer `seed` or `emerging` when the evidence base is still thin or contested.

## Page-Type Expectations

### `source`

Should usually answer:

- what this artifact is
- why it matters
- the main claims or findings
- the important limits or caveats
- where the raw artifact lives

Required behavior:

- preserve provenance explicitly
- link or embed the relevant raw artifact
- avoid pretending to be cross-source synthesis

### `article`

Should usually contain:

- a clear thesis or framing
- evidence or supporting claims
- tensions, disagreements, limitations, or open edges when present
- links to related pages

### `map`

Should help a reader traverse a part of the wiki:

- define scope
- highlight the key pages
- surface major gaps, unresolved questions, or next useful paths

### `output`

Should record:

- what question or task produced it
- which inputs mattered
- why it is worth saving back into the wiki

## Operational Workflow

### Ingest

1. Read the raw source.
2. Create or update a `source` page when provenance matters.
3. Update affected `article` and `map` pages.
4. Refresh `wiki/index.md`.
5. Refresh `wiki/overview.md`.
6. Append a maintenance note to `wiki/log.md`.
7. Check links and page quality before finishing.

### Query

1. Start from `wiki/index.md` or the Obsidian connector tools.
2. Read maintained pages first.
3. Pull raw artifacts only when the maintained wiki is insufficient or needs verification.
4. Save durable outputs into `wiki/outputs/` when they add long-term value.

### Lint

Look for:

- unresolved links
- orphan pages
- duplicate or near-duplicate articles
- thin or low-synthesis pages
- malformed summaries
- stale or placeholder navigation content
- pages that are marked `stable` too early
- pages that only paraphrase one source without adding synthesis or navigation value

## Obsidian Interaction

Prefer the CLI connector when exploring the vault:

- `compile obsidian inspect`
- `compile obsidian search "<query>"`
- `compile obsidian page "<locator>"`
- `compile obsidian neighbors "<locator>"`
- `compile obsidian graph`
- `compile obsidian refresh`
- `compile obsidian upsert "<title>" --page-type <type>`

If the `compile` entrypoint is not installed, use `uv run compile ...` from the repo root.

## Quality Escalation

- Fix routine page-quality issues directly.
- If the workspace contract is unclear, make the smallest safe change and record the ambiguity in `WIKI.md` or `log.md`.
- If a change would require large taxonomy churn, mass renames, or destructive consolidation, surface that explicitly before doing it.

## Examples

Good `source` note skeleton:

```md
---
title: Example Paper
type: source
status: stable
summary: Reports X, argues Y, and is relevant because Z.
created: 2026-04-06T00:00:00+00:00
updated: 2026-04-06T00:00:00+00:00
sources:
  - raw/example-paper.pdf
cssclasses:
  - source
  - stable
---

# Example Paper

## Synopsis

One-paragraph faithful summary of the artifact.

## Claims

- Claim 1
- Claim 2

## Limitations

- Caveat 1

## Provenance

- Source file: ![[raw/example-paper.pdf]]
```

Good `article` skeleton:

```md
---
title: Budget-Aware Context Management
type: article
status: emerging
summary: Synthesizes how multiple approaches handle context limits and where they diverge.
created: 2026-04-06T00:00:00+00:00
updated: 2026-04-06T00:00:00+00:00
sources:
  - Paper A
  - Paper B
cssclasses:
  - article
  - emerging
---

# Budget-Aware Context Management

## Thesis

Short framing paragraph that tells the reader what the page argues.

## Evidence

- [[Paper A]] supports ...
- [[Paper B]] differs by ...

## Tensions

- One source optimizes latency while another optimizes fidelity.

## Related

- [[Memory Systems]]
- [[Context Compression]]
```

Good `map` skeleton:

```md
---
title: Memory Systems Map
type: map
status: stable
summary: Curates the main pages and unresolved gaps around memory in agent systems.
created: 2026-04-06T00:00:00+00:00
updated: 2026-04-06T00:00:00+00:00
cssclasses:
  - map
  - stable
---

# Memory Systems Map

## Scope

What region of the wiki this page covers.

## Key Pages

- [[Long-Term Memory]]
- [[Retrieval Policy]]

## Gaps

- Missing comparison between symbolic and embedding-backed memory.
```
