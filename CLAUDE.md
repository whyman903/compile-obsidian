# Compile Developer Contract

This repository builds `compile`, a CLI for maintaining an Obsidian-backed wiki with LLM assistance.

## Product Boundary

- `compile` manages workspace structure, source registration, best-effort plain-text extraction, Obsidian page maintenance, and explicit rich-output commands.
- Claude handles interpretation, synthesis, figure description, and deciding when a chart, canvas, or deck is worth saving.
- Do not reintroduce automatic figure extraction, managed figure blocks, `source packet`, or separate enrich flows unless there is a very strong product reason.

## Key Commands

```bash
uv sync
uv run pytest
uv run compile --help
uv run compile init "Test Wiki" -p /tmp/test-wiki
uv run compile ingest example.md -p /tmp/test-wiki
```

## Module Map

- `compile/cli.py`: command surface and Claude setup/install logic
- `compile/text.py`: source extraction and normalization
- `compile/ingest.py`: source-note artifact assembly and rendering
- `compile/obsidian.py`: vault scanning, search, upsert, and graph helpers
- `compile/workspace.py`: workspace state, status, processing, and generated files
- `compile/outputs.py`: explicit renderers for Marp, chart, and canvas outputs
- `compile/fetch.py`: URL ingestion and optional image download for web sources
- `compile/templates/`: installed Claude command files and workspace contract

## Development Rules

- Prefer simple synchronous code and direct data flow over extra abstraction.
- Keep CLI behavior explicit. If a workflow is optional or lossy, document that honestly.
- Preserve backward compatibility only where it protects existing workspaces with low complexity.
- Use `apply_patch` for manual file edits and keep tests aligned with product behavior.

## Release Standard

Before finishing a change:

1. Run `uv run pytest`.
2. Verify the CLI surface matches the docs and installed templates.
3. Check that workspace-facing instructions in `compile/templates/workspace/CLAUDE.md` still reflect actual behavior.
