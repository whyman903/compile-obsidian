Answer a question using my personal wiki, then offer to file the answer back.

Argument: $ARGUMENTS (the question to answer)

My wiki lives at: {{wiki_path}}

Run all compile commands from the wiki directory (e.g. `cd "{{wiki_path}}" && compile ...`).

Workflow:

1. Run `cd "{{wiki_path}}" && compile obsidian search` with key terms from the question to find relevant pages.

2. Read the top 3-5 results with `cd "{{wiki_path}}" && compile obsidian page`. Follow `cd "{{wiki_path}}" && compile obsidian neighbors` if you need more context on how pages connect.

3. If the wiki pages are insufficient, read raw sources in `{{wiki_path}}/raw/` for additional evidence.

4. Synthesize an answer with `[[wikilinks]]` citing the wiki pages that support each claim.

5. **Choose output format.** Before presenting, decide the best saved form:
   - Comparison of 3+ items on multiple dimensions → table, or `cd "{{wiki_path}}" && compile render chart` if quantitative
   - Relationships between 4+ concepts, causal chains, actor maps, or dependencies → `cd "{{wiki_path}}" && compile render canvas`
   - Branching logic, decision tree, or small concept hierarchy (3–15 nodes) → mermaid in the answer
   - Teaching explanation or presentation request → `cd "{{wiki_path}}" && compile render marp`
   - Otherwise → standard text with wikilinks (the fallback)
   Use callouts (`> [!note]`, `> [!warning]`, `> [!question]`) to highlight key insights or caveats.

6. Present a brief answer in chat. If a rich format was chosen, recommend it: "This would work well as a [canvas/slide deck/chart] — want me to save it that way?"

7. If the user approves saving:
   - For canvas: write node JSON to `/tmp/nodes.json` (and optional edges to `/tmp/edges.json`) and use `cd "{{wiki_path}}" && compile render canvas ... --nodes-file /tmp/nodes.json`.
   - For Marp: write slide markdown to `/tmp/deck.md` and use `cd "{{wiki_path}}" && compile render marp ... --body-file /tmp/deck.md`.
   - For chart: write the matplotlib script to `/tmp/chart.py` and use `cd "{{wiki_path}}" && compile render chart ... --script-file /tmp/chart.py`.
   - Render commands create and log the output page automatically. Run `cd "{{wiki_path}}" && compile obsidian refresh` and `cd "{{wiki_path}}" && compile health`.
   - For markdown output: write the answer to a temporary file and use `cd "{{wiki_path}}" && compile obsidian upsert "Answer Title" --page-type output --body-file /tmp/answer.md`. Run `cd "{{wiki_path}}" && compile obsidian refresh` and `cd "{{wiki_path}}" && compile health`, then append to `{{wiki_path}}/wiki/log.md`.

8. If the user declines saving, move on.
