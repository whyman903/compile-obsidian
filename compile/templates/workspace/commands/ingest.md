Process a raw source into the wiki.

Argument: $ARGUMENTS (filename in raw/, or leave blank to process all unprocessed files)

Workflow:

1. If no argument given, run `compile status` to see unprocessed files and pick the next one. If an argument is given, use that file.

2. Run `compile obsidian search` with key terms from the filename to discover what already exists in the wiki. Read any closely related pages.

3. Run `compile ingest <filename>` to create the source note. For PDFs with poor filenames, pass `--title "Proper Title"`.

4. Read the source note that was created. For PDFs, treat it as a first pass when text or figures were extracted; if it is still a registration shell, read the PDF directly and write the real content. For other file types, treat it as a strong first pass and improve where the raw source adds nuance:
   - Read the actual raw source (the full file, not just the note)
   - Extract the main claims, findings, or arguments
   - Note limitations or caveats
   - Verify any quote-sensitive material against the raw source before treating it as verbatim
   - Make sure provenance links back to the raw file

5. For substantial rewrites, write the improved markdown to a temporary file and update the source page using `compile obsidian upsert --body-file`.

For batch ingest of related sources (e.g., exam readings), read all sources before writing any notes. This lets you identify cross-references and tensions across the set. Run `compile obsidian refresh` and `compile health` once at the end, not after each page.

6. Check if existing article pages should be updated with evidence from this source. If so, read them with `compile obsidian page` and update them.

7. If the source introduces ideas that genuinely warrant new articles (recurring across sources, central to the wiki), create them. But prefer updating existing pages.

8. **Consider one companion artifact.** If the source materially benefits from a visual, create at most one:
    - Relationships between 4+ concepts the wiki now covers → `compile render canvas`
    - Quantitative data (percentages, timelines, distributions) → `compile render chart`
    - Complex argument with branching logic → add a mermaid diagram to the source note or relevant article
    - PDF figures the extractor missed or rendered poorly → add a "Figures worth revisiting" section with page numbers and descriptions, or `> [!note] Figure: ...` callouts inline
    - Web source with meaningful images → re-run with `compile ingest <url> --images` if not already done
    Skip this step if nothing applies. Most ingests are text-only.

9. Run `compile obsidian refresh` to update index and overview.

10. Run `compile health` and fix any issues.

11. Report what was done: source page created, articles updated, any new pages created.
