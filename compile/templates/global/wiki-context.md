Load the current state of my personal wiki into this session.

My wiki lives at: {{wiki_path}}

Run all compile commands from the wiki directory (e.g. `cd "{{wiki_path}}" && compile ...`).

Workflow:

1. `cd "{{wiki_path}}" && compile status` — understand the workspace: how many pages, how many raw sources, what's unprocessed
2. `cd "{{wiki_path}}" && compile obsidian inspect` — understand the graph: page types, link health, orphans, thin pages
3. Read `{{wiki_path}}/wiki/index.md` — know what pages exist and what they're about
4. Read `{{wiki_path}}/wiki/overview.md` — understand the current shape and themes
5. Read `{{wiki_path}}/WIKI.md` — understand the topic-specific editorial rules for this workspace

After reading all of this, briefly summarize: what the wiki is about, how many pages exist, what the main themes are, and what work needs attention (unprocessed sources, thin pages, stale nav, etc).
