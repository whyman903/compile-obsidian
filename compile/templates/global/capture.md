Capture something worth remembering into my personal wiki.

Argument: $ARGUMENTS (what to capture — a concept, insight, decision, pattern, or anything worth keeping)

My wiki lives at: {{wiki_path}}

Workflow:

1. Take what the user described (or what just happened in this conversation) and write it as a markdown file into the wiki's raw/ directory:
   - Filename MUST match `<slug>-YYYY-MM-DD.md`, where `<slug>` contains only lowercase letters, digits, and hyphens. Example: `{{wiki_path}}/raw/caching-strategy-2026-04-07.md`. Do NOT include spaces, colons, slashes, quotes, brackets, `#`, `^`, or any other punctuation — these characters break Obsidian filenames and wikilinks. `compile ingest` will rename the file if you slip up, but emit a clean name to begin with.
   - Content: a clear, self-contained note with enough context that it makes sense outside this conversation. Include: what the insight is, where it came from, why it matters.

2. Then run the ingest workflow to register it in the wiki:
   ```bash
   cd "{{wiki_path}}" && compile ingest <filename> && compile health
   ```

3. Tell the user: "Captured to wiki and ingested as a source note. I can continue now by reviewing the raw source, strengthening the source note, and updating related articles."

4. Check the captured content against format triggers:
   - Maps 4+ related concepts, actors, or dependencies → offer: "I'll also create a canvas to visualize the relationships."
   - Describes a sequential process or argument flow → offer: "I'll add a mermaid diagram to the source note."
   - Contains a comparison across 3+ items → offer: "I'll add a comparison table."
   Create the artifact only if the user asks for it or explicitly agrees.
