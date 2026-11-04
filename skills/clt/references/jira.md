# clt jira: recipes

Jira Cloud REST API v3. Paths are relative to `/rest/api/3/`. Read with `--json --read-only`; before any `POST` or `PUT`, show the request with `--dry-run` and send it only with the user's go-ahead.

## Session

Logging in needs a person (`clt jira login --url https://<site>.atlassian.net --create-token`); if a command exits with code 3, ask the user to run it, or to set `JIRA_URL`, `JIRA_EMAIL` and `JIRA_TOKEN` (plus `JIRA_CLOUD_ID` when the token has scopes). Both token kinds work; clt picks the right host.

```bash
clt jira --json --read-only me
```

## Read a ticket

The summary, status, people, links, subtasks, description and last comments, as text:

```bash
clt jira --json --read-only issue PROJ-123
clt jira --json --read-only issue PROJ-123 --comments 30
```

## Search with JQL

`search/jql` always with `fields`, or the issues come back almost empty. Quote the whole path.

```bash
clt jira --json --read-only GET "search/jql?jql=assignee=currentUser() AND statusCategory!=Done ORDER BY updated DESC&fields=key,summary,status" --fields key,fields.summary,fields.status.name --all --max-items 50
clt jira --json --read-only GET "search/jql?jql=project=PROJ AND sprint in openSprints()&fields=key,summary,status,assignee" --fields key,fields.summary,fields.status.name,fields.assignee.displayName --all
```

## Move a ticket to another state

The state changes through a transition, never by editing `status`. List the transitions this ticket allows, pick the one whose `to.name` is the target state, then run it:

```bash
clt jira --json --read-only GET issue/PROJ-123/transitions --fields transitions
clt jira --json POST issue/PROJ-123/transitions -d '{"transition": {"id": "31"}}' --dry-run
clt jira --json POST issue/PROJ-123/transitions -d '{"transition": {"id": "31"}}'
```

A transition with a screen may need fields: add them as `"fields": {...}` next to `"transition"`. A 204 with an empty body is success.

## Comment

Bodies are ADF (Atlassian Document Format), not plain text:

```bash
clt jira --json POST issue/PROJ-123/comment -d '{"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fixed in the last release."}]}]}}' --dry-run
clt jira --json POST issue/PROJ-123/comment -d '{"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fixed in the last release."}]}]}}'
```

For a long body, write the JSON to a file and pass `-d @comment.json`.

## Assign

Jira wants the account id, not the email:

```bash
clt jira --json --read-only GET "user/search?query=ana@example.com" --fields accountId,displayName
clt jira --json PUT issue/PROJ-123/assignee -d '{"accountId": "<accountId>"}' --dry-run
clt jira --json PUT issue/PROJ-123/assignee -d '{"accountId": "<accountId>"}'
```

## What agents get wrong

- Changing `status` with `PUT issue/...`: it fails; use the transitions above.
- Calling `search`: it is gone (410). Use `search/jql`, and always pass `fields`.
- Sending descriptions or comments as plain strings: API v3 wants ADF.
- Using an email where an `accountId` is expected (assignee, mentions).
