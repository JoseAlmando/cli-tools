# clt jira

Jira Cloud REST API v3, paths relative to `/rest/api/3/`. Writes: drop `--dry-run` only after the user agrees.

```bash
clt jira --json --read-only mine --project PROJ --table
clt jira --json --read-only issue PROJ-123 --comments 30
clt jira --json --read-only GET "search/jql?jql=project=PROJ AND sprint in openSprints()&fields=key,summary,status,assignee" --fields key,fields.summary,fields.status.name,fields.assignee.displayName --all --table
clt jira --json --read-only GET "user/search?query=ana@example.com" --fields accountId,displayName
```

- Search is `search/jql`, always with `fields` (old `search` is gone: 410). Quote the whole path.
- States change only through a transition: pick the one whose `to.name` is the target (`GET issue/PROJ-123/transitions`). A screen may need `"fields": {...}` next to `"transition"`. 204 is success.
- Comments and descriptions are ADF, not strings. People are `accountId`, not email.

```bash
clt jira --json POST issue/PROJ-123/transitions -d '{"transition": {"id": "31"}}' --dry-run
clt jira --json POST issue/PROJ-123/comment -d '{"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fixed."}]}]}}' --dry-run
clt jira --json PUT issue/PROJ-123/assignee -d '{"accountId": "<accountId>"}' --dry-run
```

Login is the user's: `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` (plus `JIRA_CLOUD_ID` for scoped tokens).
