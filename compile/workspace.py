from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import re

import yaml

from compile.config import Config, save_config


COMPILE_CSS = """\
/* Compile wiki styles */

/* Page type badges in frontmatter */
.frontmatter-container .metadata-property[data-property-key="type"] .metadata-property-value {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.85em;
}

/* Page-type accents. Obsidian applies cssclasses to the note view/root. */
.markdown-preview-view.source,
.workspace-leaf-content.source .markdown-preview-view,
.markdown-source-view.mod-cm6.source .cm-contentContainer,
.workspace-leaf-content.source .cm-contentContainer {
  border-left: 3px solid #5078c0;
  padding-left: 12px;
}

.markdown-preview-view.concept,
.workspace-leaf-content.concept .markdown-preview-view,
.markdown-source-view.mod-cm6.concept .cm-contentContainer,
.workspace-leaf-content.concept .cm-contentContainer {
  border-left: 3px solid #00a59a;
  padding-left: 12px;
}

.markdown-preview-view.provisional,
.workspace-leaf-content.provisional .markdown-preview-view,
.markdown-source-view.mod-cm6.provisional .cm-contentContainer,
.workspace-leaf-content.provisional .cm-contentContainer {
  border-left: 3px dashed #ff9800;
  padding-left: 12px;
}

.markdown-preview-view.dashboard,
.workspace-leaf-content.dashboard .markdown-preview-view,
.markdown-source-view.mod-cm6.dashboard .cm-contentContainer,
.workspace-leaf-content.dashboard .cm-contentContainer {
  border-left: 3px solid #8e44ad;
  padding-left: 12px;
}

.compile-lede {
  font-size: 1.04rem;
  line-height: 1.6;
  margin: 0.35rem 0 1rem;
  color: var(--text-normal);
  border-left: 4px solid var(--interactive-accent);
  padding: 0.15rem 0 0.15rem 0.9rem;
}

.markdown-preview-view .callout,
.workspace-leaf-content .callout {
  margin: 1rem 0;
}

.markdown-preview-view table {
  width: 100%;
}

.markdown-preview-view h3 {
  margin-bottom: 0.35rem;
}

/* Callout styling enhancements */
.callout[data-callout="note"] {
  --callout-color: 68, 138, 255;
}
.callout[data-callout="warning"] {
  --callout-color: 255, 145, 0;
}
.callout[data-callout="claim"] {
  --callout-color: 0, 200, 83;
  --callout-icon: lucide-quote;
}
.callout[data-callout="tension"] {
  --callout-color: 255, 82, 82;
  --callout-icon: lucide-alert-triangle;
}
.callout[data-callout="open-question"] {
  --callout-color: 156, 39, 176;
  --callout-icon: lucide-help-circle;
}

/* Math block spacing */
mjx-container[display="true"] {
  margin: 1em 0;
  overflow-x: auto;
  background: color-mix(in srgb, var(--background-secondary) 88%, white 12%);
  border-radius: 8px;
  padding: 0.75rem 1rem;
}

/* Tag pills */
.tag {
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 0.8em;
}

/* Graph view — larger nodes for pages with many links */
.graph-view.color-fill-focused {
  opacity: 1;
}
"""

SETUP_MD = """\
# Obsidian Setup for Compile Workspaces

This workspace is pre-configured as an Obsidian vault. Open it with:
**File > Open Vault > Open folder as vault** and select this directory.

## Recommended Community Plugins

Install these from **Settings > Community Plugins > Browse**:

### Essential

- **Dataview** — Query your wiki with SQL-like syntax. Examples:
  ````
  ```dataview
  TABLE tags, updated
  FROM "wiki/concepts"
  SORT updated DESC
  ```
  ````
  ````
  ```dataview
  LIST
  FROM "wiki/sources"
  ```
  ````

- **Obsidian Git** — Auto-commit changes on a timer if you want repository-backed
  history for your vault. Compile itself does not auto-commit workspace changes.

### Example Dataview Queries

Once you install Dataview, try these queries in any note:

````
```dataview
TABLE source_count, status, updated
FROM "wiki/concepts"
SORT source_count DESC
```
````

````
```dataview
LIST
FROM "wiki/sources"
WHERE source_type = "pdf"
```
````

### Recommended

- **Linter** — Auto-format markdown on save (YAML sort, heading gaps, whitespace).
- **Style Settings** — Fine-tune theme and snippet variables if using a custom theme.
- **Marp Slides** — Convert wiki pages to slide decks. Great for presenting research.
  Add `marp: true` to a page's frontmatter to enable.
- **Tag Wrangler** — Rename, merge, and reorganize tags across the whole vault.
- **Calendar** — Visual calendar for log entries if you add dates to frontmatter.

### Optional Power Tools

- **Templater** — Advanced templates with JavaScript. Useful for custom page creation flows.
- **Excalidraw** — Hand-drawn diagrams stored as markdown. Good for architecture sketches.

## Tips

- **Graph View** (left sidebar icon): Shows how all pages link to each other.
  Colors are pre-configured: blue = sources, teal = concepts, orange = entities,
  pink = questions, green = outputs.
- **Backlinks** (right sidebar): See what links TO the current page.
- **LaTeX**: This wiki uses `$inline$` and `$$block$$` math notation.
  Obsidian renders these natively — no plugin needed.
- **Callout blocks**: Pages use `> [!note]`, `> [!warning]`, `> [!claim]`,
  `> [!tension]`, and `> [!open-question]` callouts. Custom callout styles
  are defined in `.obsidian/snippets/compile.css`.
"""

WIKI_SCHEMA_MD = """\
# Workspace Schema

This file is the per-workspace contract for Compile. It should evolve with the vault.

## Topic

- Title: {topic}
- Framing: {description}

## Compiler Goals

- Build a persistent, cross-linked research wiki rather than one-shot notes
- Prefer updating existing pages when new sources add evidence
- Promote concepts from provisional to stable only when they synthesize multiple sources
- Save durable outputs back into the wiki when they add lasting value

## Page Contract

- Every maintained page must include frontmatter with `title`, `type`, `status`, `summary`, `created`, `updated`, and `cssclasses`
- Preferred page types: `source`, `concept`, `entity`, `question`, `output`, `dashboard`, `index`, `overview`, `log`
- Preferred maturity states: `seed`, `emerging`, `stable`
- Use `[[Page Title]]` wikilinks only for existing pages unless creating the linked page in the same pass
- Source pages must link back to the raw artifact and any important local assets

## Synthesis Heuristics

- A new source should usually update multiple existing pages, not just create new ones
- Concepts backed by one source are provisional and should remain marked as such
- Stable concept pages should name which sources support which claims and where sources diverge
- Dashboards should help identify thin, provisional, stale, or underlinked areas of the vault

## Output Modes

- Preferred saved output formats: note, comparison, marp, mermaid, chart-spec
- Save outputs into `wiki/outputs/`
- Promote outputs into synthesis pages only when they become durable reference material
"""


def init_workspace(root: Path, topic: str, description: str = "") -> Config:
    """Create a new workspace directory structure."""
    if (root / ".compile" / "config.yaml").exists():
        raise FileExistsError(f"Workspace already exists at {root}")

    config = Config(
        topic=topic,
        description=description,
        workspace_root=root,
    )

    # Create directories
    for subdir in [
        "raw",
        "wiki/sources",
        "wiki/concepts",
        "wiki/entities",
        "wiki/questions",
        "wiki/outputs",
        "wiki/dashboards",
        ".compile",
        ".compile/source-packets",
    ]:
        (root / subdir).mkdir(parents=True, exist_ok=True)

    save_config(config)

    # Initialize state
    _save_state(config, {"processed": {}, "created_at": _now()})

    # Create initial wiki files
    _write_initial_index(config)
    _write_initial_overview(config)
    _write_initial_log(config)
    _write_initial_schema(config)

    # Set up Obsidian vault config
    _setup_obsidian(root)

    return config


def ensure_workspace_schema(config: Config) -> bool:
    if config.wiki_schema_path.exists():
        return False
    _write_initial_schema(config)
    return True


def read_schema(config: Config) -> str:
    """Return the current contents of WIKI.md, or empty string if missing."""
    if config.wiki_schema_path.exists():
        return config.wiki_schema_path.read_text()
    return ""


def refresh_schema(config: Config, proposed_changes: str) -> bool:
    """Append a schema revision section to WIKI.md and bump the version.

    If WIKI.md has no YAML frontmatter yet, one is inserted.  The
    ``schema_version`` counter in the frontmatter is incremented on
    every successful write.

    Returns True when changes were written, False otherwise.
    """
    if not proposed_changes or not proposed_changes.strip():
        return False

    now = _now()
    path = config.wiki_schema_path

    if path.exists():
        content = path.read_text()
    else:
        # Bootstrap with the static template so we have something to amend
        content = WIKI_SCHEMA_MD.format(
            topic=config.topic,
            description=config.description or "Add a short description for this workspace.",
        )

    frontmatter: dict[str, object] = {}
    body = content

    if content.startswith("---\n") and "\n---\n" in content[4:]:
        frontmatter_text, body = content[4:].split("\n---\n", 1)
        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            frontmatter = {}

    # Ensure required frontmatter keys
    frontmatter.setdefault("title", "Workspace Schema")
    frontmatter.setdefault("type", "schema")

    # Bump version
    current_version = int(frontmatter.get("schema_version") or 1)
    frontmatter["schema_version"] = current_version + 1
    frontmatter["updated"] = now

    # Build the revision section
    revision_section = (
        f"\n## Schema Revision (v{frontmatter['schema_version']}) — {now}\n\n"
        f"{proposed_changes.strip()}\n"
    )

    # Append after existing body
    updated_body = body.rstrip() + "\n" + revision_section

    frontmatter_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    path.write_text(f"---\n{frontmatter_text}\n---\n{updated_body}")
    return True


def load_state(config: Config) -> dict:
    if config.state_path.exists():
        return json.loads(config.state_path.read_text())
    return {"processed": {}, "created_at": _now()}


def save_state(config: Config, state: dict) -> None:
    _save_state(config, state)


def mark_processed(config: Config, raw_path: Path, pages_touched: list[str]) -> None:
    """Record that a raw file has been processed."""
    state = load_state(config)
    relative = str(raw_path.relative_to(config.workspace_root))
    state["processed"][relative] = {
        "processed_at": _now(),
        "pages_touched": pages_touched,
        "size": raw_path.stat().st_size,
    }
    _save_state(config, state)


def get_unprocessed(config: Config) -> list[Path]:
    """Return raw files that haven't been processed yet."""
    from compile.text import is_supported

    state = load_state(config)
    processed = state.get("processed", {})
    unprocessed = []
    for path in sorted(config.raw_dir.rglob("*")):
        if not path.is_file() or not is_supported(path):
            continue
        relative = str(path.relative_to(config.workspace_root))
        if relative not in processed:
            unprocessed.append(path)
    return unprocessed


def get_status(config: Config) -> dict:
    """Return workspace status summary."""
    from compile.text import is_supported

    state = load_state(config)
    raw_files = [p for p in config.raw_dir.rglob("*") if p.is_file() and is_supported(p)]
    wiki_pages = list(config.wiki_dir.rglob("*.md"))
    unprocessed = get_unprocessed(config)

    return {
        "topic": config.topic,
        "description": config.description,
        "raw_files": len(raw_files),
        "processed": len(state.get("processed", {})),
        "unprocessed": len(unprocessed),
        "wiki_pages": len(wiki_pages),
        "workspace_root": str(config.workspace_root),
    }


def read_wiki_page(config: Config, relative_path: str) -> str | None:
    """Read a wiki page by its path relative to wiki/."""
    path = config.wiki_dir / relative_path
    if path.exists():
        return path.read_text()
    return None


def list_wiki_pages(config: Config) -> list[str]:
    """List all wiki page paths relative to wiki/."""
    pages = []
    for path in sorted(config.wiki_dir.rglob("*.md")):
        pages.append(str(path.relative_to(config.wiki_dir)))
    return pages


def _save_state(config: Config, state: dict) -> None:
    config.compile_dir.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(json.dumps(state, indent=2))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_initial_index(config: Config) -> None:
    write_index(
        config,
        {
            "sources": [],
            "concepts": [],
            "entities": [],
            "questions": [],
            "dashboards": [],
            "outputs": [],
        },
    )


def _write_initial_overview(config: Config) -> None:
    write_overview(
        config,
        {
            "sources": [],
            "concepts": [],
            "entities": [],
            "questions": [],
            "dashboards": [],
            "outputs": [],
        },
    )


def _write_initial_schema(config: Config) -> None:
    config.wiki_schema_path.write_text(
        WIKI_SCHEMA_MD.format(
            topic=config.topic,
            description=config.description or "Add a short description for this workspace.",
        )
    )


def collect_pages_by_type(config: Config) -> dict[str, list[dict[str, str]]]:
    """Collect all wiki pages organized by type for deterministic nav pages."""
    pages_by_type: dict[str, list[dict[str, str]]] = {
        "sources": [],
        "concepts": [],
        "entities": [],
        "questions": [],
        "dashboards": [],
        "outputs": [],
    }

    for page_path in list_wiki_pages(config):
        if page_path in ("index.md", "overview.md", "log.md"):
            continue

        content = read_wiki_page(config, page_path)
        if not content:
            continue

        frontmatter: dict[str, object] = {}
        body = content
        if content.startswith("---\n") and "\n---\n" in content[4:]:
            frontmatter_text, body = content[4:].split("\n---\n", 1)
            try:
                frontmatter = yaml.safe_load(frontmatter_text) or {}
            except yaml.YAMLError:
                frontmatter = {}

        page_type = "sources"
        for candidate_type in pages_by_type:
            if page_path.startswith(f"{candidate_type}/"):
                page_type = candidate_type
                break

        title = str(frontmatter.get("title") or "").strip()
        if not title:
            title = Path(page_path).stem.replace("-", " ").title()

        summary = str(frontmatter.get("summary") or "").strip()
        if not summary:
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("<!-- compile:") or stripped == "<!-- /compile:section -->":
                    continue
                if stripped.startswith(("- ", "* ")):
                    stripped = stripped[2:].strip()
                summary = stripped[:120]
                break

        pages_by_type[page_type].append(
            {
                "path": page_path,
                "title": title,
                "summary": summary,
            }
        )

    for entries in pages_by_type.values():
        entries.sort(key=lambda item: item["title"].lower())
    return pages_by_type


def write_index(config: Config, pages_by_type: dict[str, list[dict[str, str]]]) -> None:
    now = _now()
    created = now
    index_path = config.wiki_dir / "index.md"
    if index_path.exists():
        existing = index_path.read_text()
        if existing.startswith("---\n") and "\n---\n" in existing[4:]:
            frontmatter_text, _body = existing[4:].split("\n---\n", 1)
            frontmatter = yaml.safe_load(frontmatter_text) or {}
            created = str(frontmatter.get("created") or now)
    sections = [
        ("Sources", "sources", "_No sources ingested yet._"),
        ("Concepts", "concepts", "_Concepts will appear as sources are compiled._"),
        ("Entities", "entities", "_Entities will appear as sources are compiled._"),
        ("Open Questions", "questions", "_Questions will appear as sources are compiled._"),
        ("Dashboards", "dashboards", "_Dashboards will appear as the workspace matures._"),
        ("Outputs", "outputs", "_Saved query outputs will appear here._"),
    ]

    lines = [
        "---",
        "title: Index",
        "type: index",
        "status: stable",
        "summary: Top-level catalog of sources, concepts, entities, dashboards, questions, and outputs.",
        f"created: {created}",
        f"updated: {now}",
        "cssclasses:",
        "  - index",
        "  - stable",
        "---",
        "",
        f"# {config.topic} — Index",
        "",
    ]

    for heading, key, empty_message in sections:
        lines.extend([f"## {heading}", ""])
        entries = pages_by_type.get(key, [])
        if not entries:
            lines.extend([empty_message, ""])
            continue
        for entry in entries:
            summary = entry["summary"] or "No summary yet."
            lines.append(f"- [[{entry['title']}]] — {summary}")
        lines.append("")

    (config.wiki_dir / "index.md").write_text("\n".join(lines).rstrip() + "\n")


def write_overview(config: Config, pages_by_type: dict[str, list[dict[str, str]]]) -> None:
    now = _now()
    created = now
    overview_path = config.wiki_dir / "overview.md"
    if overview_path.exists():
        existing = overview_path.read_text()
        if existing.startswith("---\n") and "\n---\n" in existing[4:]:
            frontmatter_text, _body = existing[4:].split("\n---\n", 1)
            frontmatter = yaml.safe_load(frontmatter_text) or {}
            created = str(frontmatter.get("created") or now)
    sources = pages_by_type.get("sources", [])
    concepts = pages_by_type.get("concepts", [])
    entities = pages_by_type.get("entities", [])
    questions = pages_by_type.get("questions", [])
    dashboards = pages_by_type.get("dashboards", [])
    outputs = pages_by_type.get("outputs", [])

    key_theme_lines = [
        f"- [[{page['title']}]] — {page['summary'] or 'Key concept tracked in this workspace.'}"
        for page in concepts[:6]
    ]
    if entities:
        key_theme_lines.extend(
            f"- [[{page['title']}]] — {page['summary'] or 'Important entity referenced across the wiki.'}"
            for page in entities[:3]
        )

    source_lines = [
        f"- [[{page['title']}]] — {page['summary'] or 'Source note in the workspace.'}"
        for page in sources[:5]
    ]
    question_lines = [
        f"- [[{page['title']}]] — {page['summary'] or 'Open investigation thread.'}"
        for page in questions[:5]
    ]
    dashboard_lines = [
        f"- [[{page['title']}]] — {page['summary'] or 'Workspace dashboard or map of content.'}"
        for page in dashboards[:5]
    ]
    output_lines = [
        f"- [[{page['title']}]] — {page['summary'] or 'Saved synthesis generated from a query or downstream task.'}"
        for page in outputs[:5]
    ]

    has_material = any((sources, concepts, entities, questions, dashboards, outputs))

    if not has_material:
        current_state = (
            "This workspace was just initialized. Add sources to `raw/` and run "
            "`compile ingest` to start building the wiki."
        )
    else:
        current_state = "\n".join(
            [
                f"- Source notes: {len(sources)}",
                f"- Concept pages: {len(concepts)}",
                f"- Entity pages: {len(entities)}",
                f"- Open questions: {len(questions)}",
                f"- Dashboards: {len(dashboards)}",
                f"- Saved outputs: {len(outputs)}",
            ]
        )

    content = f"""---
title: "{config.topic} Overview"
type: overview
status: stable
summary: Workspace landing page summarizing the current state of the maintained wiki.
created: {created}
updated: {now}
tags: [overview]
cssclasses: [overview, stable]
---

# {config.topic}

{config.description or "A research wiki maintained by Compile."}

## Current State

{current_state}

## Key Themes

{chr(10).join(key_theme_lines) if key_theme_lines else "_Themes will emerge as sources are compiled._"}

## Source Highlights

{chr(10).join(source_lines) if source_lines else "_Source highlights will appear after the first ingest._"}

## Open Questions

{chr(10).join(question_lines) if question_lines else "_See [[Index]] for the full list._"}

## Dashboards

{chr(10).join(dashboard_lines) if dashboard_lines else "_Dashboards will appear as the workspace matures._"}

## Recent Outputs

{chr(10).join(output_lines) if output_lines else "_Saved outputs will appear here after the first query is written._"}

## Navigation

- Browse the full catalog in [[Index]]
- Review the chronology in [[Log]]
"""
    (config.wiki_dir / "overview.md").write_text(content)


def append_log_entry(
    config: Config,
    kind: str,
    title: str,
    lines: list[str] | None = None,
) -> None:
    log_path = config.wiki_dir / "log.md"
    now = _now()
    body_lines = lines or []
    body = "\n".join(f"- {line}" for line in body_lines) if body_lines else "- No details recorded."
    entry = f"\n## [{now}] {kind} | {title}\n{body}\n"

    if log_path.exists():
        existing = log_path.read_text()
        frontmatter: dict[str, object] = {}
        body_text = existing
        if existing.startswith("---\n") and "\n---\n" in existing[4:]:
            frontmatter_text, body_text = existing[4:].split("\n---\n", 1)
            try:
                frontmatter = yaml.safe_load(frontmatter_text) or {}
            except yaml.YAMLError:
                frontmatter = {}
        created = str(frontmatter.get("created") or frontmatter.get("created_at") or now)
        frontmatter["title"] = "Log"
        frontmatter["type"] = "log"
        frontmatter["status"] = "stable"
        frontmatter["summary"] = "Append-only record of ingests, outputs, and maintenance activity."
        frontmatter["created"] = created
        frontmatter["updated"] = now
        frontmatter["cssclasses"] = ["log", "stable"]
        if not body_text.strip():
            body_text = "# Compile Log\n"
        frontmatter_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
        log_path.write_text(
            f"---\n{frontmatter_text}\n---\n\n{body_text.rstrip()}{entry}"
        )
    else:
        log_path.write_text(
            "---\n"
            "title: Log\n"
            "type: log\n"
            "status: stable\n"
            "summary: Append-only record of ingests, outputs, and maintenance activity.\n"
            f"created: {now}\n"
            f"updated: {now}\n"
            "cssclasses:\n"
            "  - log\n"
            "  - stable\n"
            "---\n\n"
            f"# Compile Log\n{entry}"
        )


def write_dashboards(config: Config, dashboard_pages: dict[str, str]) -> list[str]:
    dashboards_dir = config.wiki_dir / "dashboards"
    dashboards_dir.mkdir(parents=True, exist_ok=True)

    written_paths: list[str] = []
    for title, body in dashboard_pages.items():
        path = dashboards_dir / f"{title}.md"
        path.write_text(body.rstrip() + "\n")
        written_paths.append(str(path.relative_to(config.wiki_dir)).replace("\\", "/"))

    return written_paths


def _setup_obsidian(root: Path) -> None:
    """Create .obsidian/ config so the workspace is a ready-to-open vault."""
    obsidian_dir = root / ".obsidian"
    obsidian_dir.mkdir(exist_ok=True)

    # Core app settings: enable wikilinks, set attachment folder
    (obsidian_dir / "app.json").write_text(json.dumps({
        "useMarkdownLinks": False,
        "showLineNumber": True,
        "strictLineBreaks": True,
        "attachmentFolderPath": "raw/assets",
        "newLinkFormat": "shortest",
        "alwaysUpdateLinks": True,
    }, indent=2))

    # Enable essential core plugins
    (obsidian_dir / "core-plugins.json").write_text(json.dumps([
        "file-explorer",
        "global-search",
        "graph",
        "backlink",
        "outgoing-link",
        "tag-pane",
        "page-preview",
        "note-composer",
        "command-palette",
        "editor-status",
        "markdown-importer",
        "outline",
    ], indent=2))

    # Empty community plugins list (user installs their own)
    (obsidian_dir / "community-plugins.json").write_text("[]")

    # Graph view config with reasonable defaults
    (obsidian_dir / "graph.json").write_text(json.dumps({
        "collapse-filter": False,
        "search": "",
        "showTags": True,
        "showAttachments": False,
        "hideUnresolved": False,
        "showOrphans": True,
        "collapse-color-groups": False,
        "colorGroups": [
            {"query": "path:wiki/sources", "color": {"a": 1, "rgb": 5275647}},
            {"query": "path:wiki/concepts", "color": {"a": 1, "rgb": 40959}},
            {"query": "path:wiki/entities", "color": {"a": 1, "rgb": 16753920}},
            {"query": "path:wiki/questions", "color": {"a": 1, "rgb": 16711935}},
            {"query": "path:wiki/dashboards", "color": {"a": 1, "rgb": 9323693}},
            {"query": "path:wiki/outputs", "color": {"a": 1, "rgb": 65280}},
        ],
        "collapse-display": False,
        "showArrow": True,
        "textFadeMultiplier": -2,
        "nodeSizeMultiplier": 1.1,
        "lineSizeMultiplier": 1,
        "collapse-forces": False,
        "centerStrength": 0.5,
        "repelStrength": 10,
        "linkStrength": 1,
        "linkDistance": 250,
    }, indent=2))

    # Appearance — enable CSS snippets
    (obsidian_dir / "appearance.json").write_text(json.dumps({
        "accentColor": "#5b8def",
        "enabledCssSnippets": ["compile"],
    }, indent=2))

    # Hotkeys (empty - user customizes)
    (obsidian_dir / "hotkeys.json").write_text("{}")

    # CSS snippet for wiki styling
    snippets_dir = obsidian_dir / "snippets"
    snippets_dir.mkdir(exist_ok=True)
    (snippets_dir / "compile.css").write_text(COMPILE_CSS)

    # SETUP.md with recommended plugins
    (root / "SETUP.md").write_text(SETUP_MD)


def _write_initial_log(config: Config) -> None:
    now = _now()
    content = f"""---
title: Log
type: log
status: stable
summary: Append-only record of ingests, outputs, and maintenance activity.
created: {now}
updated: {now}
cssclasses: [log, stable]
---

# Compile Log

## [{now}] init | {config.topic}
- Workspace initialized
- Topic: {config.topic}
"""
    (config.wiki_dir / "log.md").write_text(content)
