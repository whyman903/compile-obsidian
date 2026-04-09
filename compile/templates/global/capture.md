Capture something worth remembering into my personal wiki.

Argument: $ARGUMENTS (what to capture — a concept, insight, decision, pattern, or anything worth keeping)

My wiki lives at: {{wiki_path}}

Workflow:

1. Take what the user described (or what just happened in this conversation) and write it as a markdown file into the wiki's raw/ directory:
   - Filename: descriptive slug with today's date, e.g. `{{wiki_path}}/raw/caching-strategy-2026-04-07.md`
   - Content: a clear, self-contained note with enough context that it makes sense outside this conversation. Include: what the insight is, where it came from, why it matters.

2. Then run the ingest workflow to register it in the wiki:
   ```bash
   cd "{{wiki_path}}" && compile ingest <filename> && compile health
   ```

3. Tell the user: "Captured to wiki and ingested as a source note. If you want, I can continue now by reviewing the raw source, strengthening the source note, and updating related articles." If the captured insight involves relationships between concepts (a mental model, comparison, or dependency chain), add: "This could also work well as a canvas or diagram — ask me to visualize it."
