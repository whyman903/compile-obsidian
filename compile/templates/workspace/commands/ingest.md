Process a raw source into the wiki.

Argument: $ARGUMENTS (filename in `raw/`, or leave blank to process the next unprocessed file)

Use the workspace `CLAUDE.md` as the canonical workflow contract. Follow this command flow:

1. If no argument is given, run `compile status` and pick the next unprocessed source. Otherwise use the provided file.
2. Run `compile obsidian search` with key terms from the source to see what already exists.
3. Run `compile ingest <filename>`. For ugly PDF filenames, pass `--title "Proper Title"`.
4. Read the created source note.
5. Read the raw source itself when the note is weak, incomplete, or needs verification.
6. For substantial improvements, rewrite the source note with `compile obsidian upsert --body-file ...`.
7. Update related articles if this source materially strengthens them.
8. Consider a companion artifact using the format triggers in the workspace CLAUDE.md:
   - Source maps 4+ related concepts or actors → `compile render canvas`
   - Source contains quantitative data, trends, or distributions → `compile render chart`
   - Source is a tutorial, lecture, or walkthrough → `compile render marp`
   - Source describes a sequential process or argument flow → add a mermaid diagram in the source note
   Offer the artifact when it would add durable value. Create it only if the user asks for it or explicitly agrees.
9. Run `compile obsidian refresh`.
10. Run `compile health` and fix any issues.
11. Report what changed.
