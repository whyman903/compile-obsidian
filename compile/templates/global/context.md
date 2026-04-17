Load the current wiki state into this session.

My wiki lives at: {{wiki_path}}

### Target workspace

Use one `/context` command everywhere:

1. First try the current working directory: run `compile status`.
2. If that succeeds, use the reported workspace root for every command and file read. If you are not already at that root, `cd` there before running follow-up commands.
3. If that fails because there is no workspace here, use the configured wiki:
   - Run compile commands as `cd "{{wiki_path}}" && compile ...`
   - Read files from `{{wiki_path}}/...`

Do not ask the user which command to use. Only ask a follow-up if both the current workspace and the configured wiki are unavailable.

### Workflow

Run these commands and internalize the results:

1. `compile status` — understand the workspace: how many pages, how many raw sources, what's unprocessed
2. `compile obsidian inspect` — understand the graph: page types, link health, orphans, thin pages
3. Read `wiki/index.md` — know what pages exist and what they're about
4. Read `wiki/overview.md` — understand the current shape and themes
5. Read `WIKI.md` — understand the topic-specific editorial rules for this workspace

After reading all of this, briefly summarize: what the wiki is about, how many pages exist, what the main themes are, and what work needs attention (unprocessed sources, thin pages, stale nav, etc).
