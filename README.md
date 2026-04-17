# Compile

Compile is an LLM-maintained wiki for Obsidian. You curate raw sources and ask questions; Claude does the reading, synthesis, linking, and maintenance through a disciplined CLI.

Two front doors:

- **`compile` CLI** — use from any terminal or from Claude Code.
- **MyWiki.app** (macOS) — a menu-bar app that wraps the CLI, opens the vault in Obsidian, and streams Claude answers in-app.

---

## Prerequisites

Install these once before anything else:

| Tool | Required for | How to install |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Building and running the Python CLI | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Obsidian](https://obsidian.md) | Browsing the vault visually | Download from obsidian.md |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | Slash commands (`/capture`, `/query`, `/ingest`, …) | `npm i -g @anthropic-ai/claude-code` or the native installer |
| Swift 6 toolchain | Only if you want to build `MyWiki.app` | Comes with recent Xcode / Command Line Tools |
| Python 3.11+ | Auto-managed by `uv` | — |

Claude Code must be authenticated (`claude` in any terminal) before the slash commands or the Mac app's in-process query feature will work.

---

## Quick start (CLI only)

```bash
# 1. Install the CLI from this repo
git clone <this-repo> obsidian-compile
cd obsidian-compile
uv tool install .

# 2. Create a workspace
compile init "My Wiki" -d "What this wiki is about" -p ~/wiki

# 3. Wire it into Claude Code
compile claude setup ~/wiki

# 4. Open the vault in Obsidian (File > Open Vault > ~/wiki)
#    and start working in Claude Code from the workspace:
cd ~/wiki
claude
```

From Claude Code you can now use `/query` and `/context` anywhere. They use the current directory when it is a compile workspace; otherwise they fall back to this configured wiki. From inside the wiki workspace, you can also use `/ingest`, `/lint`, `/synthesize`, `/notion-setup`, and `/notion-sync`. The global `/capture` command always targets this configured wiki.

Those slash commands are the primary UX. They decide when to create, update, or synthesize pages; the low-level page writer stays underneath them.

> ⚠️ Do **not** run `uv tool install compile` — that pulls an unrelated PyPI package. Always install from the local path (`.` or `/path/to/obsidian-compile`).

---

## Installing and updating the CLI

The CLI ships as `compile`. Install it as a `uv` tool so it's on your `PATH` globally:

```bash
uv tool install /path/to/obsidian-compile
```

To update after pulling new changes in the repo:

```bash
uv tool install /path/to/obsidian-compile --force
```

To uninstall:

```bash
uv tool uninstall compile-wiki
```

For local development (iterating on the CLI source without reinstalling), run `uv run compile …` from inside the repo — this skips the global install and uses the repo's live code.

### Refreshing Claude Code integration

Templates under `compile/templates/` evolve. After a CLI update, push the latest commands and workspace contract into your wiki:

```bash
compile claude setup ~/wiki --force
```

`--force` overwrites the global commands (`~/.claude/commands/{capture,query,context}.md`), the workspace `CLAUDE.md`, and the workspace-local `.claude/commands/*.md`. Settings (`.claude/settings.local.json`) are merged, not overwritten — your custom permissions survive.

Without `--force`, `compile claude setup` only installs files that are missing and warns if any global command points at a different wiki.

---

## Quick start (macOS app)

`MyWiki.app` is a menu-bar companion that wraps the CLI, opens the vault in Obsidian, and streams Claude answers directly in the app without a Terminal round-trip.

There is no hosted release yet — build the app locally.

### Build

```bash
# from the repo root
./scripts/build-mywiki-app.sh
```

This will:

1. Bundle the Python CLI into a single `compile-bin` executable via PyInstaller.
2. Build the SwiftPM target `MyWiki` in release mode.
3. Assemble `dist/MyWiki.app` with the sidecar, templates, Info.plist, and icon.
4. Ad-hoc sign the bundle so Gatekeeper lets it launch locally.

The build is arm64-only today (see `MyWiki/support/compile-bin.spec`).

### Install

```bash
# Drag to /Applications, or run in place
cp -R dist/MyWiki.app /Applications/
open /Applications/MyWiki.app
```

Because the build is ad-hoc signed (not notarized), macOS may refuse the first launch. If that happens:

```bash
xattr -dr com.apple.quarantine /Applications/MyWiki.app
open /Applications/MyWiki.app
```

On first launch, MyWiki creates a default workspace at `~/wiki` called "Commonplace" and runs `compile claude setup` against it automatically. You can switch to another workspace later from the menu bar.

### Update

Rebuild after pulling new changes:

```bash
./scripts/build-mywiki-app.sh
cp -R dist/MyWiki.app /Applications/
```

The embedded CLI and templates are refreshed every build.

---

## How it works

Three layers, one contract:

- **`raw/`** — your source documents (articles, papers, PDFs, images). Immutable. Claude reads but never edits.
- **`wiki/`** — the LLM-maintained layer: source notes, synthesis articles, maps, outputs. Claude owns this layer.
- **`WIKI.md`** — the schema that tells Claude how this specific wiki is structured and what conventions to follow.

Every source you process and every question you keep makes the wiki richer. The wiki compounds.

### Page types

- `source` — provenance-anchored note for a raw artifact.
- `article` — durable synthesis across multiple sources (the default knowledge page).
- `map` — navigation page curating a region of the wiki.
- `output` — saved answer, comparison, Marp deck, chart, or canvas.

### Everyday commands

Use MyWiki.app or these Claude commands for normal work:

| Command | What it does |
|---|---|
| `/capture` | Write a thought or snippet into `~/wiki/raw/` and ingest it. |
| `/query` | Search and synthesize an answer with citations. Uses the current wiki when you are in one; otherwise uses the configured default wiki. |
| `/context` | Load wiki status, index, overview, and schema into the current session. Uses the current wiki when you are in one; otherwise uses the configured default wiki. |
| `/ingest [source]` | Register a raw file or URL as a source note and wire it into related articles/maps. Run from inside the wiki workspace. |
| `/lint` | Full audit: broken links, status honesty, dead-end source notes, coverage gaps. |
| `/synthesize [theme]` | Deliberately connect accumulated sources into articles or maps. |
| `/notion-setup` | Save a natural-language Notion sync scope to `.compile/notion-sync-profile.json`. |
| `/notion-sync` | Pull matching Notion pages into `raw/notion/` and ingest them. |

---

## Automation / Scripting CLI

Use `compile ...` when you want deterministic operations without an LLM loop: scripts, CI, cron jobs, debugging, or precise repair work. Run commands from inside a workspace, or pass `-p /path/to/wiki`.

For everyday use, prefer MyWiki.app and the slash commands above. The CLI remains a supported automation surface, and `compile obsidian upsert` is the low-level page writer behind those workflows.

```bash
compile status                          # Workspace summary.
compile ingest raw/paper.pdf            # Create a source note.
compile ingest https://example.com/post # Fetch and ingest a URL.
compile obsidian inspect                # Full vault audit.
compile obsidian search "query"         # Search pages.
compile obsidian page "Title"           # Dump page metadata + body.
compile obsidian neighbors "Title"      # Show inbound/outbound links.
compile obsidian refresh                # Rebuild wiki/index.md + overview.md.
compile health                          # Structural + editorial health.
compile health --json-output            # Full report for tooling.
compile suggest maps                    # Existing maps that could absorb orphans.
compile schema                          # Print the workspace's WIKI.md.
compile render marp "Title" --body-file /tmp/deck.md
compile render chart "Title" --script-file /tmp/chart.py
compile render canvas "Title" --nodes-file /tmp/nodes.json
compile review mark-reviewed "Source Title"
compile index rebuild                   # Rebuild the PDF search index.
compile claude setup .                  # Install/update Claude Code files.
```

### Low-level page writer

Use this only for deliberate manual repair or when building a higher-level workflow:

```bash
compile obsidian upsert "Title" \
  --page-type article --status emerging \
  --body-file /tmp/page.md
```

### Notion flow

If Notion is connected in your Claude session:

1. `/notion-setup` — describe the scope in plain language (for example: *"all my product stuff"*). Claude saves the scope to `.compile/notion-sync-profile.json`.
2. `/notion-sync` — pulls matching pages into `raw/notion/<page_id>.md` with provenance comments, then runs the usual ingest workflow.

Removing `notion_page_id` from a source note marks it "user-claimed" and future syncs preserve it.

---

## Workflow notes

- Prefer the higher-level workflows for normal use. When you do need a direct page write, use `compile obsidian upsert --body-file …` instead of shell heredocs.
- After creating or updating several pages, run `compile obsidian refresh` then `compile health`.
- New output pages may briefly show low-severity navigation warnings until they're linked from an article or map.
- Treat web extracts as working notes, not verified quotations. For quote-sensitive workflows, verify against the raw source.
- PDF handling is intentionally simple. `compile ingest` does best-effort text extraction; Claude should use direct PDF understanding when the session supports it. Rich visuals are created explicitly via `compile render …`.

---

## Troubleshooting

- **`compile: command not found`** — the tool isn't on your `PATH`. Re-run `uv tool install .` from the repo, or use `uv run compile …` from inside the repo.
- **"No workspace found. Run 'compile init' first."** — you're outside any wiki. `cd ~/wiki` or pass `-p ~/wiki`.
- **`compile claude setup` says "global command(s) point at a different wiki"** — another workspace is currently bound. Re-run with `--force` to rebind `~/.claude/commands/*.md` to this wiki.
- **MyWiki.app won't open** — clear the quarantine flag: `xattr -dr com.apple.quarantine /Applications/MyWiki.app`.
- **MyWiki.app shows "Unable to locate bundled compile-bin"** — the sidecar is missing from the bundle. Rebuild with `./scripts/build-mywiki-app.sh`.
- **"Obsidian is not installed"** from the app — install Obsidian from [obsidian.md](https://obsidian.md) and reopen the workspace.
- **Graph button in the app is disabled** — install the Advanced URI plugin when prompted, then relaunch Obsidian once.
- **`compile ingest` creates a registration shell for a PDF** — text extraction failed. Open the raw PDF via a Claude session that supports direct document understanding, then replace the shell by rewriting the source note. If you're doing that manually, the low-level writer is `compile obsidian upsert --body-file …`.

---

## Development

```bash
uv sync                                   # Install dev deps, create .venv
uv run pytest                             # Full test suite
uv run compile --help                     # CLI surface smoke test
uv run compile init "Test" -p /tmp/test-wiki
uv run compile claude setup /tmp/test-wiki

# Mac app tests
swift test --package-path MyWiki
```

See [`CLAUDE.md`](CLAUDE.md) for the developer contract (product boundary, module map, and release standard).
