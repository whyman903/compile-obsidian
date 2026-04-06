# Workspace Modes

Compile currently works with two distinct workspace layouts.

## `compile_workspace`

This is the canonical, user-facing Compile format.

Required shape:

- `.compile/config.yaml`
- `wiki/`
- usually `.obsidian/`

Properties:

- Obsidian-native markdown vault
- `[[wikilinks]]` are expected
- `index.md`, `overview.md`, `log.md`, and dashboards are maintained
- source pages should link back to raw artifacts
- health checks can legitimately judge Obsidian readiness

If a workspace is meant to be browsed in Obsidian, this is the format to optimize for.

## `backend_workspace`

This is a backend-exported page store.

Required shape:

- `workspace.json`
- `pages/`

Typical properties:

- page frontmatter contains backend ids such as `page_*` and `source_ids`
- relationships often exist as metadata such as `related_page_ids`
- pages may have no `[[wikilinks]]`
- `.obsidian/` is usually absent

These workspaces are useful for inspection, export, and backend processing, but they should not be described as Obsidian-ready unless they are upgraded with:

- `.obsidian/` config
- explicit wikilinks in page bodies
- raw-source backlinks from source pages
- navigation pages that participate in the graph

## Health Expectations

`compile health` and `compile obsidian inspect` should treat the two modes differently:

- `compile_workspace`: aim for Obsidian readiness and graph quality
- `backend_workspace`: do not call it healthy if it lacks core vault affordances such as `.obsidian/` or wikilinks

The summary rule is strict: a workspace with high-severity readiness failures is not healthy, even if its content pages were generated successfully.
