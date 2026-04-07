Capture something worth remembering into my personal wiki.

Argument: $ARGUMENTS (what to capture — a concept, insight, decision, pattern, or anything worth keeping)

My wiki lives at: {{wiki_path}}

Workflow:

1. Take what the user described (or what just happened in this conversation) and write it as a markdown file into the wiki's raw/ directory:
   - Filename: descriptive slug with today's date, e.g. `{{wiki_path}}/raw/caching-strategy-2026-04-07.md`
   - Content: a clear, self-contained note with enough context that it makes sense outside this conversation. Include: what the insight is, where it came from, why it matters.

2. Then run the ingest scaffold to register it in the wiki:
   ```bash
   cd "{{wiki_path}}" && compile ingest <filename> && compile obsidian refresh
   ```

3. Tell the user: "Captured to wiki. Next time you're in the wiki, review and expand the source note, and update any related articles."
