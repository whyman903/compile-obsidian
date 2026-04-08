Answer a question using the wiki, then offer to file the answer back.

Argument: $ARGUMENTS (the question to answer)

Workflow:

1. Run `compile obsidian search` with key terms from the question to find relevant pages.

2. Read the top 3-5 results with `compile obsidian page`. Follow `compile obsidian neighbors` if you need more context on how pages connect.

3. If the wiki pages are insufficient, read raw sources in `raw/` for additional evidence.

4. Synthesize an answer with `[[wikilinks]]` citing the wiki pages that support each claim.

5. Present the answer to the user.

6. Ask: "Want me to save this as a wiki output page?" If yes:
   - Write the answer to a temporary markdown file and use `compile obsidian upsert "Answer Title" --page-type output --body-file /tmp/answer.md`
   - Run `compile obsidian refresh` to update navigation
   - Run `compile health` to catch unresolved links or navigation issues
   - Append to log
