Answer a question using my personal wiki, then offer to file the answer back.

Argument: $ARGUMENTS (the question to answer)

My wiki lives at: {{wiki_path}}

Run all compile commands from the wiki directory (e.g. `cd "{{wiki_path}}" && compile ...`).

Workflow:

1. Run `cd "{{wiki_path}}" && compile obsidian search` with key terms from the question to find relevant pages.

2. Read the top 3-5 results with `cd "{{wiki_path}}" && compile obsidian page`. Follow `cd "{{wiki_path}}" && compile obsidian neighbors` if you need more context on how pages connect.

3. If the wiki pages are insufficient, read raw sources in `{{wiki_path}}/raw/` for additional evidence.

4. Synthesize an answer with `[[wikilinks]]` citing the wiki pages that support each claim.

5. Present the answer to the user.

6. Ask: "Want me to save this as a wiki output page?" If yes:
   - Use `cd "{{wiki_path}}" && compile obsidian upsert "Answer Title" --page-type output` with the answer content
   - Run `cd "{{wiki_path}}" && compile obsidian refresh` to update navigation
   - Append to `{{wiki_path}}/wiki/log.md`
