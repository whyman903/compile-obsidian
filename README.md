# Compile

Compile is an LLM-maintained wiki for Obsidian. You curate raw sources and ask questions; Claude does the reading, synthesis, linking, and maintenance.

The front door is **MyWiki.app**, a macOS menu-bar companion that bundles a Python CLI, opens the vault in Obsidian, and streams Claude answers in-app.

---

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| [Obsidian](https://obsidian.md) | Browsing the vault | Download from obsidian.md |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | Slash commands (`/capture`, `/query`, `/context`, …) | `npm i -g @anthropic-ai/claude-code` |
| Xcode Command Line Tools | Swift 6 toolchain for the build | `xcode-select --install` |
| [`uv`](https://docs.astral.sh/uv/) | Bundles the Python CLI into the app | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Claude Code must be authenticated (run `claude` once in any terminal) before the slash commands work.

---

## Build and install

```bash
git clone <this-repo>
cd walker-wiki
./scripts/build-mywiki-app.sh
cp -R dist/MyWiki.app /Applications/
open /Applications/MyWiki.app
```

The build is arm64-only and ad-hoc signed. If Gatekeeper blocks the first launch:

```bash
xattr -dr com.apple.quarantine /Applications/MyWiki.app
open /Applications/MyWiki.app
```

On first launch, MyWiki creates a default workspace at `~/wiki` called "Commonplace" and installs the Claude Code commands that target it. Rebuild and replace the app to pick up new changes.

---

## How it works

Three layers, one contract:

- **`raw/`** — your source documents. Immutable. Claude reads but never edits.
- **`wiki/`** — the LLM-maintained layer: source notes, articles, maps, outputs.
- **`WIKI.md`** — the schema telling Claude how this wiki is structured.

Pages are one of four types: `source` (provenance-anchored), `article` (synthesis), `map` (navigation), `output` (saved answer, deck, chart, or canvas).

### Slash commands

Use these from MyWiki.app or from a Claude Code session launched by the app:

| Command | What it does |
|---|---|
| `/capture` | Drop a thought or snippet into `~/wiki/raw/` and ingest it. Always targets the configured wiki. |
| `/query` | Search and synthesize an answer with citations. Uses the current workspace, or falls back to the configured wiki. |
| `/context` | Load wiki status, index, overview, and schema into the session. |
| `/ingest [source]` | Register a raw file or URL as a source note. Run from inside the workspace. |
| `/lint` | Audit: broken links, status honesty, dead-end notes, coverage gaps. |
| `/synthesize [theme]` | Connect accumulated sources into articles or maps. |
| `/notion-setup`, `/notion-sync` | Save a Notion scope, then pull matching pages into `raw/notion/`. |

You can hand-edit any of these at `<workspace>/.claude/commands/*.md` or `~/.claude/commands/*.md` — open them from MyWiki's Settings → Claude Commands. Your edits survive app restarts.

---

## Troubleshooting

- **MyWiki.app won't open** — `xattr -dr com.apple.quarantine /Applications/MyWiki.app`.
- **"Unable to locate bundled compile-bin"** — rebuild with `./scripts/build-mywiki-app.sh`.
- **"Obsidian is not installed"** — install from [obsidian.md](https://obsidian.md) and reopen.
- **Graph button is disabled** — install the Advanced URI plugin when prompted, then relaunch Obsidian.
- **`compile: command not found` from a slash command** — you ran Claude Code from a terminal MyWiki didn't launch. Start it from the app, or install the CLI standalone: `uv tool install /path/to/this/repo`.

---

## Development

```bash
uv sync
uv run pytest
uv run compile --help
swift test --package-path MyWiki
```

Set `MYWIKI_DEV_WORKSPACE=~/wiki` before running the build script to auto-sync template changes into your wiki on each build.

See [`CLAUDE.md`](CLAUDE.md) for the developer contract (product boundary, module map, release standard).
