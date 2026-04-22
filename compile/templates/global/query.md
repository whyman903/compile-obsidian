Answer a question. Use the wiki first, then fill any gaps from general knowledge, and offer to file the answer back.

Argument: $ARGUMENTS (the question to answer)

My wiki lives at: {{wiki_path}}

### Target workspace

Use one `/query` command everywhere:

1. First try the current working directory: run `compile status`.
2. If that succeeds, use the reported workspace root for every command and file read. If you are not already at that root, `cd` there before running follow-up commands.
3. If that fails because there is no workspace here, use the configured wiki:
   - Run compile commands as `cd "{{wiki_path}}" && compile ...`
   - Read files from `{{wiki_path}}/...`

Do not ask the user which command to use. Only ask a follow-up if both the current workspace and the configured wiki are unavailable.

### Workflow

1. Run `compile obsidian search` with key terms from the question to find relevant pages.

2. Read the top few results with `compile obsidian page`. Follow `compile obsidian neighbors` if you need more context on how pages connect.

3. If the wiki pages are insufficient, read raw sources in `raw/` for additional evidence.

4. Always answer the question. Use `[[wikilinks]]` for claims the wiki supports. If the wiki only partially covers the question, answer the rest from general knowledge and briefly mark those claims as not in the wiki. If the wiki does not cover the question at all, say that once up front and then answer from general knowledge. Do not refuse just because the topic is outside the wiki. Do not mention your knowledge cutoff or say you cannot answer because of your role.

5. **Choose output format.** Check these triggers in order — use the first match:
   - Comparison of 3+ items on shared dimensions → table, or `compile render chart` if quantitative
   - Relationships between 4+ concepts, causal chains, actor maps, or dependencies → `compile render canvas`
   - Sequential process, argument flow, or small hierarchy (3–15 nodes) → mermaid diagram in the answer
   - Teaching explanation or presentation request → `compile render marp`
   - Quantitative data, trends, or distributions → `compile render chart`
   - None of the above → standard text, with `[[wikilinks]]` only where the wiki supports a claim
   Always use callouts (`> [!note]`, `> [!warning]`, `> [!question]`) for key insights, caveats, or definitions regardless of format.

6. Present a brief answer in chat. If a rich format would add durable value, recommend it explicitly.

7. Ask: "Want me to save this as a wiki output page?" Only save it if the user says yes:
   - For canvas: write node JSON to `/tmp/nodes.json` (and optional edges to `/tmp/edges.json`) and use `compile render canvas ... --nodes-file /tmp/nodes.json`.
   - For Marp: write slide markdown to `/tmp/deck.md` and use `compile render marp ... --body-file /tmp/deck.md`.
   - For chart: write the matplotlib script to `/tmp/chart.py` and use `compile render chart ... --script-file /tmp/chart.py`.
   - Render commands create and log the output page automatically. Run `compile obsidian refresh` and `compile health`.
   - For plain markdown output: write the answer to a temporary file, then save it as an `output` page using the low-level page writer (`compile obsidian upsert "Answer Title" --page-type output --body-file /tmp/answer.md`). Run `compile obsidian refresh` and `compile health`, then append to log.

8. If the user declines, move on.
