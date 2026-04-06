# Compile Knowledge Compiler Implementation Spec

## Goal

Compile should behave like a knowledge compiler, not a markdown generator.

The system must optimize for:

- explicit provenance from every maintained page back to raw artifacts
- compressed, high-signal notes instead of fluffy prose
- cross-source synthesis that compounds over time
- deterministic maintenance of wiki structure
- bounded LLM responsibilities at ambiguity boundaries

## Non-Negotiable Invariants

1. `raw/` is immutable source of truth.
2. Every substantive claim in compiler-owned pages must be attributable to one or more source artifacts.
3. Source pages must include an exact raw artifact link or embed.
4. Single-source concept pages must never present as mature synthesis.
5. Compiler-owned pages must be patchable at section granularity.
6. Navigation pages and dashboards are materialized views, not freeform prose.

## Target Architecture

### Layer 1: Raw Sources

Inputs:

- markdown
- html
- pdf
- text
- local images and attachments

The compiler never mutates this layer.

### Layer 2: Source Packets

Each raw source is normalized into a structured packet.

Required fields:

- `source_id`
- `raw_path`
- `source_type`
- `title`
- `summary`
- `key_claims`
- `concepts`
- `entities`
- `methods`
- `metrics`
- `equations`
- `limitations`
- `open_questions`
- `local_assets`
- `analysis_warnings`

This layer is the first place where equations, numbers, tables, and figure refs should be preserved.

### Layer 3: Evidence Graph

The evidence graph persists canonical knowledge objects:

- claims
- concepts
- entities
- support relationships
- source coverage
- page references

Runtime storage is SQLite-backed for transactional merges and indexed retrieval.
Compatibility helpers may still materialize an in-memory evidence view when older code paths or tests require it.

### Layer 4: Page Artifacts

The LLM does not directly own markdown files.

The LLM emits typed page artifacts:

- `PageDraft` for new pages
- `PagePatch` for existing compiler-managed pages

`PageDraft` contains:

- frontmatter fields
- ordered managed sections

`PagePatch` contains:

- frontmatter updates
- section-level insert/replace/delete operations keyed by stable section ids

Markdown rendering and patch application are deterministic code.

### Layer 5: Materialized Wiki

Markdown files under `wiki/` are rendered from page artifacts and refreshed by maintenance passes.

Compiler-owned sections use explicit markers:

```md
## Claims by Source
<!-- compile:section id=claims_by_source -->
...
<!-- /compile:section -->
```

These markers make updates robust without requiring brittle whole-file rewrites.

## Compiler Phases

### Phase A: Extract

For each new raw artifact:

- extract text
- identify local assets
- capture warnings for truncation or failed extraction

### Phase B: Analyze

LLM extraction prompt returns a structured source packet.

Rules:

- compression-first
- no framing or hype
- preserve numbers and equations when present
- no synthesis

### Phase C: Plan

Planner consumes:

- source packet
- evidence context
- current page catalog

It emits page operations:

- create
- update
- merge
- split
- deprecate

Short-term implementation may still plan per source.
Long-term target is a global batch planner over staged source packets.

### Phase D: Compile

For each target page:

- new compiler-managed page -> emit `PageDraft`
- existing compiler-managed page -> emit `PagePatch`
- legacy unmanaged page -> emit `PageDraft` and migrate to managed sections

### Phase E: Verify

Before write:

- check provenance
- check citation density
- check maturity overclaim
- check that numbers/equations were not dropped when present
- check for filler/generic language

### Phase F: Materialize

Apply patches deterministically, refresh dashboards/nav pages, and append log entries.

## Prompt Contracts

### Source Packet Extraction

The model must:

- extract only what the source actually says
- preserve exact metrics
- preserve equations as LaTeX when possible
- list limitations directly
- avoid generic introductions and significance framing

### Source Page Compilation

Source pages are compressed notes, not essays.

Preferred sections:

- Core Contribution
- Claims
- Key Numbers
- Equations
- Method / Setup
- Limitations
- Open Questions
- Provenance

### Concept Page Compilation

Concept pages are synthesis artifacts.

Preferred sections:

- Definition
- Claims by Source
- Agreements
- Tensions
- Key Numbers
- Open Questions
- Related

If the concept has only one source, the page must say so explicitly and remain provisional.

### Patch Generation

Patch prompts may only update affected sections.
They must not rewrite untouched sections.

## Concurrency Model

### Safe To Parallelize

- raw extraction
- source packet analysis
- candidate page retrieval
- compilation for disjoint target pages
- verification for disjoint target pages

### Must Remain Serialized Or Transactional

- evidence graph merge
- writes to the same page
- nav/dashboard refresh
- final materialization step

## Migration Plan

### Phase 1: Landed In This Refactor

- typed page artifacts (`PageDraft`, `PagePatch`)
- compiler-managed section markers
- deterministic markdown rendering and patch application
- compression-first source/synthesis prompts
- deterministic provenance section insertion

### Phase 2

- complete the SQLite evidence-store transition
- global batch planner
- verifier/judge pass
- async ingestion pipeline

### Phase 3

- multi-source microbatch compilation
- stronger equation/table extraction
- richer output renderers

## Acceptance Criteria

1. Source pages always link or embed the raw artifact exactly.
2. Compiler-managed pages contain managed section markers.
3. Existing managed pages can be updated via section patch instead of whole-file rewrite.
4. Source-page prose is shorter and denser than the previous prompt family.
5. Single-source concept pages are explicitly provisional.
6. Navigation refresh still works after managed section rendering.
