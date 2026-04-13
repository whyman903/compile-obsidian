Set up a saved Notion sync profile for this wiki using the connected Notion tools available in this Claude session.

Argument: $ARGUMENTS (natural-language scope like "all my product stuff", "meeting notes and PRDs", or leave blank for all accessible shared pages)

Workflow:

1. Confirm that Notion connector tools are available in this session. If they are not, stop and tell the user to connect Notion in Claude first.
2. Interpret `$ARGUMENTS` as the desired sync scope:
   - If blank, default to all accessible shared Notion pages.
   - If provided, use it as the user’s scope prompt and search hint.
3. Use the connected Notion search tools to inspect the workspace and identify a practical sync scope.
   - Prefer broad search first, then narrower searches using the user’s wording.
   - If the scope is ambiguous, summarize the ambiguity and ask one short follow-up question before writing the profile.
4. Write `.compile/notion-sync-profile.json` in the workspace root with UTF-8 JSON. Use this shape:

```json
{
  "scope_prompt": "<user wording or 'all shared pages'>",
  "query": "<search query to reuse on future syncs, or empty string for all shared pages>",
  "include_page_ids": ["<stable page ids you want pinned into scope>"],
  "exclude_page_ids": [],
  "raw_dir": "raw/notion",
  "updated_at": "<ISO-8601 timestamp>"
}
```

5. Keep `include_page_ids` concise. Use it only for pages that clearly anchor the requested scope. Do not require the user to copy or paste page IDs manually.
6. Summarize what will sync:
   - saved scope prompt
   - search query
   - pinned page titles
   - any important exclusions or caveats
7. Tell the user to run `/notion-sync` next, or let Claude schedule that command later.

Important:

- Use the connected Notion tools already available in the session. Do not ask the user for a root page ID.
- Do not write wiki pages in this setup step. This command only saves the sync profile.
- Keep the profile simple and editable by hand.
