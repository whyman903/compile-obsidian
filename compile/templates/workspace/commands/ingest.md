Process a raw source into the wiki.

Argument: $ARGUMENTS (filename in `raw/`, a URL, or leave blank to process the next unprocessed file)

Use the workspace `CLAUDE.md` as the canonical workflow contract. Work through two phases. Phase A creates the source note. Phase B wires it into the wiki and is mandatory — do not stop after Phase A.

### Phase A — Create and enrich the source note

1. If no argument is given, run `compile status` and pick the next unprocessed source. If the argument is a URL, use that URL directly. Otherwise use the provided `raw/` file.
2. Run `compile obsidian search` with key terms from the source to see what already exists.
3. Run `compile ingest <source>`, where `<source>` is the URL or `raw/` filename. For ugly PDF filenames, pass `--title "Proper Title"`.
4. Read the created source note. If the raw source is itself thin — an empty Notion stub, a stub page with no substantive content, a failed extraction shell — mark the note `status: seed` and add a `> [!note]` callout explaining the gap, then carry into Phase B with whatever themes you can defensibly identify from the title, summary, or surrounding context. Use the low-level page writer (`compile obsidian upsert --status seed`) only as the executor for that status change.
5. Read the raw source itself when the note is weak, incomplete, or needs verification.
6. For substantial improvements, rewrite the source note in place. Use the low-level page writer with `--body-file` for the actual write. When you rewrite, include:
   - A `## Themes` section listing 1–3 broader themes this source belongs to, each with a `[[wikilink]]` to the existing article or map that covers it (or a plain theme name when no page exists yet).
   - A `## Key Claims` section naming the main arguments or findings, each with a one-line note on evidence strength.

### Phase B — Wire into the wiki

7. Locality guard: direct edits during `/ingest` are limited to the source note plus 1–3 theme anchors identified here. If the right fix would spread beyond those anchors into a broader cluster, defer it to `/lint` or `/synthesize`.
8. Identify 1–3 broader themes for this source. If step 6 added a `## Themes` section, use those. If you skipped the rewrite (either because the generated note was already faithful or the source was too thin to expand), derive themes now from the title, summary, and whatever prose exists in the note. For each theme:
   - Search the wiki for an existing article or map.
   - If one exists, treat it as the theme anchor. Update only that anchor page and ensure a `[[wikilink]]` connects the source note and the article/map.
   - If no article exists but 3+ sources now touch the theme, create a `seed` article that synthesizes across them and use that new page as the anchor for this ingest pass.
   - If fewer than 3 sources share the theme, note the gap in the log entry and run `compile suggest maps` to check for existing hubs before creating anything new.
9. Verify wiring with `compile obsidian neighbors "Source Title"`. The source note should be connected to at least one article or map page (outbound links, or backlinks from an article/map). If it isn't and a plausible target exists, return to step 8. If no target exists yet, document why in the log.
10. If two sources materially disagree, update the relevant anchor article with a `> [!warning] Disagreement` callout naming both sources and the specific disagreement. Do not resolve it by picking a winner.

### Phase C — Artifacts, refresh, report

11. Consider a companion artifact using the format triggers in the workspace `CLAUDE.md`:
    - Source maps 4+ related concepts or actors → `compile render canvas`
    - Source contains quantitative data, trends, or distributions → `compile render chart`
    - Source is a tutorial, lecture, or walkthrough → `compile render marp`
    - Source describes a sequential process or argument flow → add a mermaid diagram in the source note
    Offer the artifact when it would add durable value. Create it only if the user asks for it or explicitly agrees.
12. Run `compile obsidian refresh`.
13. Run `compile health` and fix any issues.
14. Set article status honestly per the Status Discipline rules in the workspace `CLAUDE.md`. Upgrade articles whose body now clears a higher bar.
15. Report what changed: source note path, articles/maps updated or created, any disagreement callouts added, any gaps logged.
