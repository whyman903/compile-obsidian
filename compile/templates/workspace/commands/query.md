Answer a question using the wiki, then offer to file the answer back.

Argument: $ARGUMENTS (the question to answer)

Workflow:

1. Run `compile obsidian search` with key terms from the question to find relevant pages.

2. Read the top 3-5 results with `compile obsidian page`. Follow `compile obsidian neighbors` if you need more context on how pages connect.

3. If the wiki pages are insufficient, read raw sources in `raw/` for additional evidence.

4. Synthesize an answer with `[[wikilinks]]` citing the wiki pages that support each claim.

5. **Choose output format.** Before presenting, decide the best saved form:
   - Comparison of 3+ items on multiple dimensions → table, or `compile render chart` if quantitative
   - Relationships between 4+ concepts, causal chains, actor maps, or dependencies → `compile render canvas`
   - Branching logic, decision tree, or small concept hierarchy (3–15 nodes) → mermaid in the answer
   - Teaching explanation or presentation request → `compile render marp`
   - Otherwise → standard text with wikilinks (the fallback)
   Use callouts (`> [!note]`, `> [!warning]`, `> [!question]`) to highlight key insights or caveats.

6. Present a brief answer in chat. If a rich format was chosen, recommend it: "This would work well as a [canvas/slide deck/chart] — want me to save it that way?"

7. If the user approves saving:
   - For canvas: write node JSON to `/tmp/nodes.json` (and optional edges to `/tmp/edges.json`) and use `compile render canvas ... --nodes-file /tmp/nodes.json`.
   - For Marp: write slide markdown to `/tmp/deck.md` and use `compile render marp ... --body-file /tmp/deck.md`.
   - For chart: write the matplotlib script to `/tmp/chart.py` and use `compile render chart ... --script-file /tmp/chart.py`.
   - Render commands create and log the output page automatically. Run `compile obsidian refresh` and `compile health`.
   - For markdown output: write the answer to a temporary file and use `compile obsidian upsert "Answer Title" --page-type output --body-file /tmp/answer.md`. Run `compile obsidian refresh` and `compile health`, then append to log.

8. If the user declines saving, move on. The chat answer stands on its own.
