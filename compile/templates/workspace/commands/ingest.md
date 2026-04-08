Process a raw source into the wiki.

Argument: $ARGUMENTS (filename in raw/, or leave blank to process all unprocessed files)

Workflow:

1. If no argument given, run `compile status` to see unprocessed files and pick the next one. If an argument is given, use that file.

2. Run `compile obsidian search` with key terms from the filename to discover what already exists in the wiki. Read any closely related pages.

3. Run `compile ingest <filename>` to create the source note. For PDFs with poor filenames, pass `--title "Proper Title"`.

4. Read the source note that was created. For PDFs, the note is a registration shell — you must read the PDF directly and write the real content. For other file types, treat it as a strong first pass and improve where the raw source adds nuance:
   - Read the actual raw source (the full file, not just the note)
   - Extract the main claims, findings, or arguments
   - Note limitations or caveats
   - Verify any quote-sensitive material against the raw source before treating it as verbatim
   - Make sure provenance links back to the raw file

5. For substantial rewrites, write the improved markdown to a temporary file and update the source page using `compile obsidian upsert --body-file`.

For batch ingest of related sources (e.g., exam readings), read all sources before writing any notes. This lets you identify cross-references and tensions across the set. Run `compile obsidian refresh` and `compile health` once at the end, not after each page.

6. Check if existing article pages should be updated with evidence from this source. If so, read them with `compile obsidian page` and update them.

7. If the source introduces ideas that genuinely warrant new articles (recurring across sources, central to the wiki), create them. But prefer updating existing pages.

8. Run `compile obsidian refresh` to update index and overview.

9. Run `compile health` and fix any issues.

10. Report what was done: source page created, articles updated, any new pages created.
