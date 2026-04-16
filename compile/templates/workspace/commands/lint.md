Audit the wiki and fix quality issues.

Workflow:

1. Run `compile health --json-output` and read the full report. Note every issue.

2. Run `compile obsidian inspect` for graph-level problems: orphans, thin pages, unresolved links, stale navigation.
3. Run `compile suggest maps` when source notes appear disconnected from the main article/map layer.

4. For each issue found, fix it:
   - **Unresolved links**: read the page with `compile obsidian page`, either create the missing target or fix the link
   - **Orphan pages**: add links from related pages, or merge into a parent page if the orphan is too thin to stand alone
   - **Thin pages**: read them, either expand with real content or merge into a related article
   - **Stale navigation**: run `compile obsidian refresh`
   - **Malformed summaries**: read the page and rewrite the summary
   - **Premature stability**: downgrade to seed or emerging if the page doesn't actually synthesize
   - **Source notes without article/map anchors**: link them from an existing article or map, or create a lightweight map page if the broad topic clearly lacks a hub

5. Do an editorial pass on article pages. For each article, check:
   - Does it synthesize or just paraphrase one source?
   - Are there missing `[[wikilinks]]` where related pages exist?
   - Are there claims that newer sources have superseded?
   - Is the status accurate (seed/emerging/stable)?

6. Run `compile obsidian refresh` after all fixes.

7. Run `compile health` again to confirm issues are resolved.

8. Report: issues found, issues fixed, remaining issues that need user input.
