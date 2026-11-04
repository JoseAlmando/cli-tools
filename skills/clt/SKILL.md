---
name: clt
description: Use the clt command-line tools (clt taiga, clt gitea, clt github, clt jira, clt azdo) to read and change Taiga projects (user stories, issues, tasks), Gitea or GitHub repositories (pull requests, branches, issues, releases), Jira Cloud tickets (read, search, move between states, comment) and Azure DevOps work items and pull requests (create tasks, change states, open, review, vote, complete) through their REST APIs. Use it whenever a task needs data from or changes in Taiga, Gitea, GitHub, Jira or Azure DevOps, instead of curl, gh, az, an MCP server or a browser.
---

# clt: Taiga, Gitea, GitHub, Jira and Azure DevOps from the command line

`clt` wraps the Taiga and Gitea REST APIs (`/api/v1/`) and the GitHub API (`https://api.github.com/`) with the user's saved session. Taiga and Gitea tokens are renewed automatically. Every tool runs as `clt <tool>`; short names work too (`clt t`, `clt gt`, `clt gh`, `clt j`, `clt ad`), but write the full ones so the user can read your commands.

## Before anything else

- **Never log in yourself.** `clt taiga login`, `clt gitea login`, `clt github login`, `clt jira login` and `clt azdo login` need a person; without a terminal they fail at once. If a command exits with code 3 (not logged in), ask the user to run the login, or to set `TAIGA_TOKEN` / `GITEA_TOKEN` / `GITHUB_TOKEN` / `JIRA_EMAIL` and `JIRA_TOKEN` / `AZDO_TOKEN`.
- **Read freely, write only with the user's go-ahead.** Run reads with `--read-only` so a mistaken method cannot change anything. Before a `POST`, `PUT`, `PATCH` or `DELETE`, show the request with `--dry-run`, and send it only when the user agrees.
- **Keep answers small.** Use `--fields` and `--max-items` on lists. Whole Taiga or Gitea objects are large and fill the context.
- **Never print tokens.** Do not run `clt <tool> token` unless the user asks for a token for another script.

## Command shape

```bash
clt <tool> <METHOD> "<path relative to /api/v1/>" [-d JSON | -d @file.json] [options]
```

| Option | Use |
|---|---|
| `--json` | Errors as one JSON line on stderr: `{"error", "status", ...}`. Use it always, and parse stderr on failure. `me` prints `{"id", "username", "full_name", "email", "url", ...}`; it takes no `--fields`, which, like the other options below, belongs to API calls. |
| `--read-only` | Only `GET` is sent; any other method exits with code 4 without sending. `login`, `refresh`, `logout` and `token` are blocked too. |
| `--dry-run` | Prints `{"method", "path", "body"}` and sends nothing. |
| `--fields a,b,c.d` | Keeps only these fields of each object; `c.d` reads a nested field and comes back as a flat key, `{"c.d": ...}` (in jq, `.["c.d"]`). A list inside an object (`{"value": [...]}`, `{"issues": [...]}`) keeps its envelope: use `.value[]`, not `.[]`. |
| `--max-items N` | Keeps the first N items of a list (stderr says how many there were); with `--all` it stops fetching pages once it has N. |
| `--all` | Follows the pagination and joins the pages (`GET` only). |
| `-r` | Prints the body exactly as received. |

Exit codes: `0` success, `1` error (HTTP or network), `2` wrong usage, `3` not logged in, `4` blocked by read-only mode.

`--json` and `--read-only` can go anywhere on the command line, or be set as `CLT_JSON=1` and `CLT_READONLY=1`.

## Taiga recipes

```bash
clt taiga --json --read-only me
clt taiga --json --read-only GET "projects/by_slug?slug=<project-slug>" --fields id,name
clt taiga --json --read-only GET "issues/by_ref?ref=<ref>&project=<project-id>" --fields id,ref,subject,status_extra_info.name,version,description
clt taiga --json --read-only GET "userstories/by_ref?ref=<ref>&project=<project-id>" --fields id,ref,subject,status_extra_info.name
clt taiga --json --read-only GET "issues?project=<project-id>&order_by=-ref" --fields ref,subject,status_extra_info.name --max-items 20
clt taiga --json --read-only GET "issue-statuses?project=<project-id>" --fields id,name
```

Changing an object needs its current `version` (optimistic locking). Read it first, then send it with the change:

```bash
clt taiga --json PATCH issues/<id> -d '{"status": <status-id>, "version": <version>}' --dry-run
clt taiga --json PATCH issues/<id> -d '{"status": <status-id>, "version": <version>}'
```

## Gitea recipes

```bash
clt gitea --json --read-only GET user --fields login,full_name
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title,head.ref,base.ref,mergeable --all
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>" --fields number,state,merged,mergeable,html_url
clt gitea --json --read-only GET "repos/<owner>/<repo>/branches?limit=50" --fields name,commit.id --all
```

Opening a pull request: write the body to a file, build the JSON with `jq`, check it with `--dry-run`, then send it:

```bash
jq -n --rawfile body pr.md '{title: "<title>", head: "<branch>", base: "<base>", body: $body}' > pr.json
clt gitea --json POST "repos/<owner>/<repo>/pulls" -d @pr.json --dry-run
clt gitea --json POST "repos/<owner>/<repo>/pulls" -d @pr.json --fields number,html_url,mergeable
```

## GitHub recipes

```bash
clt github --json --read-only me
clt github --json --read-only GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title,head.ref,base.ref --all
clt github --json --read-only GET "repos/<owner>/<repo>/releases" --fields tag_name,name,html_url --max-items 10
```

## Jira

`clt jira` calls Jira Cloud (`https://<site>.atlassian.net/rest/api/3/`, or `api.atlassian.com` for tokens with scopes) with the same flags as the other tools. **Read [`references/jira.md`](references/jira.md) before any Jira call**: it has the exact recipes (JQL search, transitions, comments in ADF, assignee) and what agents get wrong. To read a ticket, use `clt jira --json --read-only issue PROJ-123`, not the raw issue JSON.

## Azure DevOps

`clt azdo` calls Azure DevOps Services (`https://dev.azure.com/<org>/`) with a personal access token and the same flags. **Read [`references/azure-devops.md`](references/azure-devops.md) before any Azure DevOps call**: it has the exact recipes (work items with JSON Patch, WIQL, pull requests, threads, votes, completing) and what agents get wrong.

## Releases

`clt release` runs inside the project's repo. Never push or publish without the user's go-ahead, and never use `--skip-tests` unless the user asks for it.

```bash
clt release --json --dry-run --bump <fix|feat|major>          # show the plan to the user
clt release --json --bump <fix|feat|major> --yes --no-push    # tests, version, CHANGELOG.md, commit and tag, locally
clt release --json --publish                                  # only after the user agrees: push and publish
```

If `--publish` fails halfway, running it again finishes the job; it skips what is already done.

The release commit and the annotated tag's message are `chore(release): X.Y.Z` (older releases used `Release X.Y.Z`; both are left out of the next changelog). With Conventional Commits prefixes, the changelog groups the commits under *Breaking changes*, *Features*, *Fixes* and *Other*.

`clt update --check` is a read; `clt update [VERSION]` reinstalls clt itself, so run it only when the user asks.

## When something fails

| Exit code / message | What to do |
|---|---|
| 3, "Not logged in" | Ask the user to run `clt <tool> login` in a terminal, or to set the token variable. |
| 4, "read-only mode" | Expected under `--read-only`. Drop the flag only once the user approved the write. |
| 1 with `"status": 401` | The session expired and could not be renewed: ask the user to log in again. |
| 1 with `"status": 404` | Wrong path or id. Check the recipe, or list the parent collection first. |
| 1 with `"status": 400` on a Taiga `PATCH` | Usually a stale `version`: read the object again and retry with the new `version`. |
| "No server configured" / "No organization configured" | Ask the user for the server, or which `TAIGA_URL` / `GITEA_URL` / `JIRA_URL` / `AZDO_ORG` to use. |
