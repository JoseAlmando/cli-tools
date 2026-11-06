---
name: clt
description: Use the clt command-line tools (clt taiga, clt gitea, clt github, clt jira, clt azdo) to read and change Taiga projects (user stories, issues, tasks), Gitea or GitHub repositories (pull requests, branches, issues, releases), Jira Cloud tickets (read, search, move between states, comment) and Azure DevOps work items and pull requests (create tasks, change states, open, review, vote, complete) through their REST APIs. Use it whenever a task needs data from or changes in Taiga, Gitea, GitHub, Jira or Azure DevOps, instead of curl, gh, az, an MCP server or a browser.
---

# clt: Taiga, Gitea, GitHub, Jira and Azure DevOps from the command line

`clt <tool>` calls each service's REST API with the user's saved session. Write the full tool names (`clt azdo`, not `clt ad`) so the user can read your commands.

## Cheapest path first

Every tool call resends the whole context, so the number of calls is what costs.

1. **Memory first.** If your persistent memory has a clt recipe for this task (a `reference-clt-<tool>` memory), run it as is, without reading the references.
2. **One shell call.** Use the one-command helpers (`mine`, `wiql`, `jira issue`). Chain the steps in one command, and pipe JSON bodies with `-d @-` instead of writing a file. Add `--table` to lists: tab-separated lines cost far fewer tokens than JSON.
3. **Read one reference only when neither memory nor the table below has the task**, and only the reference for that tool.

## Save what worked

When a clt task succeeded with a command your memory does not hold yet, and your harness has persistent memory, save it before you finish:

- One memory per tool, `reference-clt-<tool>` (e.g. `reference-clt-azdo`). Update it if it exists; never duplicate it.
- Store the exact command as one copy-paste line, with the real organization, project and repository names, plus any gotcha you hit (custom state names, required fields, quoting).
- In its index line, name the tasks it covers and say that it is used without loading this skill.
- Save only what changes the next run: a new command, a fix or a new gotcha. Never log runs, dates or "it worked again". Never store tokens.

## Before anything else

- **Never log in yourself.** `clt <tool> login` needs a person. On exit code 3, ask the user to run it, or to set `TAIGA_TOKEN` / `GITEA_TOKEN` / `GITHUB_TOKEN` / `JIRA_EMAIL` and `JIRA_TOKEN` / `AZDO_TOKEN`.
- **Read freely, write only with the user's go-ahead.** Run reads with `--read-only`. Before a `POST`, `PUT`, `PATCH` or `DELETE`, show the request with `--dry-run`, and send it only when the user agrees. `mine` and `wiql` are reads and run under `--read-only`.
- **Keep answers small:** `--table`, `--fields`, `--max-items`.
- **Never print tokens** (`clt <tool> token`) unless the user asks for one.
- Never run `clt jira mcp` / `clt azdo mcp`; the user sets those up. When an MCP server is connected, still prefer clt for anything below.

## Find the task here first

The commands are ready, checked and small. Writes are shown with `--dry-run`; drop it only after the user agrees. The last column names the reference with the rest.

| Task | Command | More |
|---|---|---|
| Who am I on a service | `clt <tool> --json --read-only me` | |
| My open items on any service | `clt <tool> --json --read-only mine --table` (rows: kind, id, state, title, where) | |
| Azure DevOps: list projects | `clt azdo --json --read-only GET _apis/projects --fields id,name --all --table` | `references/azure-devops.md` |
| Azure DevOps: read a work item | `clt azdo --json --read-only GET '_apis/wit/workitems/<id>?$expand=relations' --fields id,fields,relations` | `references/azure-devops.md` |
| Azure DevOps: query work items (WIQL) | `clt azdo --json --read-only wiql "SELECT [System.Id], [System.Title] FROM WorkItems WHERE ..." --project <project> --table` | `references/azure-devops.md` |
| Azure DevOps: move a work item | `clt azdo --json PATCH _apis/wit/workitems/<id> -d '[{"op": "add", "path": "/fields/System.State", "value": "Active"}]' --dry-run` | `references/azure-devops.md` |
| Azure DevOps: list active pull requests | `clt azdo --json --read-only GET '<project>/_apis/git/repositories/<repo>/pullrequests?searchCriteria.status=active&$top=50' --fields pullRequestId,title,sourceRefName,targetRefName --all --table` | `references/azure-devops.md` |
| Azure DevOps: create, comment on, vote on or complete a pull request | see the reference | `references/azure-devops.md` |
| Jira: read a ticket | `clt jira --json --read-only issue PROJ-123` | `references/jira.md` |
| Jira: move a ticket | `clt jira --json --read-only GET issue/PROJ-123/transitions --fields transitions`, then the `POST` in the reference | `references/jira.md` |
| Jira: search, comment or assign | see the reference (comments are ADF) | `references/jira.md` |
| GitHub: my repositories | `clt github --json --read-only GET "user/repos?per_page=100&sort=pushed" --fields full_name,private,default_branch --all --table` | `references/github.md` |
| GitHub: open pull requests | `clt github --json --read-only GET "repos/<owner>/<repo>/pulls?state=open&per_page=100" --fields number,title,head.ref,base.ref --all --table` | `references/github.md` |
| GitHub: CI of a commit | `clt github --json --read-only GET "repos/<owner>/<repo>/commits/<sha>/check-runs" -r \| jq -c '.check_runs[] \| {name, status, conclusion}'` | `references/github.md` |
| GitHub: open an issue or a pull request, comment, review, merge | see the reference | `references/github.md` |
| Gitea: open pull requests | `clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title,head.ref,base.ref,mergeable --all --table` | `references/gitea.md` |
| Gitea: open a pull request | see the reference | `references/gitea.md` |
| Taiga: read an issue or a user story by its number | `clt taiga --json --read-only GET "issues/by_ref?ref=<ref>&project=<project-id>" --fields id,ref,subject,status_extra_info.name,version` | `references/taiga.md` |
| Taiga: change an issue's status | `clt taiga --json PATCH issues/<id> -d '{"status": <status-id>, "version": <version>}' --dry-run` | `references/taiga.md` |
| Cut or publish a release, update clt | `clt release --json --dry-run --bump <fix\|feat\|major>` | `references/release.md` |

## Command shape

```bash
clt <tool> <METHOD> "<path>" [-d JSON | -d @file.json | -d @-] [options]
```

| Option | Use |
|---|---|
| `--json` | Errors as one JSON line on stderr (`{"error", "status", ...}`). Always use it. `me` prints JSON, and `mine` and `wiql` print a JSON list unless `--table` is given. |
| `--read-only` | Only reads are sent; anything else exits 4. `login`, `refresh`, `logout` and `token` are blocked too. |
| `--dry-run` | Prints `{"method", "path", "body"}` and sends nothing. |
| `--table` | A list of objects as tab-separated lines under a header; objects in cells are compact JSON. |
| `--fields a,b,c.d` | Keeps only these fields of each object; `c.d` comes back as the flat key `"c.d"` (jq: `.["c.d"]`). Envelopes stay: `.value[]`, `.issues[]`. |
| `--max-items N` | Keeps the first N items of a list; with `--all` it stops fetching pages once it has N. |
| `--all` | Follows the pagination and joins the pages (`GET` only). |
| `-r` | Prints the body exactly as received. |

Exit codes: `0` success, `1` error (HTTP or network), `2` wrong usage, `3` not logged in, `4` blocked by read-only mode. `--json` and `--read-only` go anywhere on the command line, or as `CLT_JSON=1` and `CLT_READONLY=1`.

## When something fails

| Exit code / message | What to do |
|---|---|
| 3, "Not logged in" | Ask the user to run `clt <tool> login` in a terminal, or to set the token variable. |
| 4, "read-only mode" | Expected under `--read-only`. Drop the flag only once the user approved the write. |
| 1 with `"status": 401` | The session expired and could not be renewed: ask the user to log in again. |
| 1 with `"status": 404` | Wrong path or id. Check the recipe, or list the parent collection first. |
| 1 with `"status": 400` on a Taiga `PATCH` | Usually a stale `version`: read the object again and retry with the new `version`. |
| "No server configured" / "No organization configured" | Ask the user for the server, or which `TAIGA_URL` / `GITEA_URL` / `JIRA_URL` / `AZDO_ORG` to use. |
