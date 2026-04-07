Process a raw source into the wiki.

Argument: $ARGUMENTS (filename in raw/, or leave blank to process all unprocessed files)

Workflow:

1. If no argument given, run `compile status` to see unprocessed files and pick the next one. If an argument is given, use that file.

2. Run `compile obsidian search` with key terms from the filename to discover what already exists in the wiki. Read any closely related pages.

3. Run `compile ingest <filename>` to create the source scaffold.

4. Read the scaffold that was created. It's a starting point — improve it:
   - Read the actual raw source (the full file, not just the scaffold)
   - Write a real synopsis, not just the first 220 characters
   - Extract the main claims, findings, or arguments
   - Note limitations or caveats
   - Make sure provenance links back to the raw file

5. Update the source page using `compile obsidian upsert` with the improved content.

6. Check if existing article pages should be updated with evidence from this source. If so, read them with `compile obsidian page` and update them.

7. If the source introduces ideas that genuinely warrant new articles (recurring across sources, central to the wiki), create them. But prefer updating existing pages.

8. Run `compile obsidian refresh` to update index and overview.

9. Run `compile health` and fix any issues.

10. Report what was done: source page created, articles updated, any new pages created.
