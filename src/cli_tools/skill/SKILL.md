---
name: clt
description: Read and change Taiga, Gitea, GitHub, Jira Cloud, Azure DevOps and Jenkins (issues, work items, pull requests, builds, logs, releases) with the clt CLI, instead of curl, gh, az, an MCP server or a browser.
---

# clt

1. **Memory first.** If a `reference-clt-<tool>` memory has the command, run it and stop reading here.
2. **Fewest calls.** Use the table below. Chain steps in one shell command, pipe bodies with `-d @-`, add `--table` to lists.
3. **A reference only for a task not in the table**, and only that tool's: `references/taiga.md`, `references/gitea.md`, `references/github.md`, `references/jira.md`, `references/azure-devops.md`, `references/jenkins.md`, `references/release.md`.
4. **Save what worked.** After a command not yet in memory succeeds, put the exact line (real org, project and repo names) and any gotcha in the `reference-clt-<tool>` memory, whose index line says it is used without this skill. Update it, never duplicate it; no tokens, no run logs.

Rules: reads with `--json --read-only`. Writes only after the user agrees, shown first with `--json --dry-run` (not under `--read-only`, which blocks them). Never run `login`, `token` or `mcp`; on exit 3 ask the user to log in.

## Find the task here first

| Task | Command |
|---|---|
| Who am I | `clt <tool> --json --read-only me` |
| My open work items | `clt <tool> --json --read-only mine --table` |
| Azure DevOps: projects | `clt azdo --json --read-only GET _apis/projects --fields name --all --table` |
| Azure DevOps: a work item | `clt azdo --json --read-only GET '_apis/wit/workitems/<id>?$expand=relations' --fields id,fields,relations` |
| Azure DevOps: query (WIQL) | `clt azdo --json --read-only wiql "SELECT [System.Id], [System.Title] FROM WorkItems WHERE ..." --project <project> --top 2000 --table` |
| Azure DevOps: move a work item | `clt azdo --json PATCH _apis/wit/workitems/<id> -d '[{"op": "add", "path": "/fields/System.State", "value": "<state>"}]' --dry-run` |
| Azure DevOps: active PRs | `clt azdo --json --read-only GET '<project>/_apis/git/repositories/<repo>/pullrequests?searchCriteria.status=active&$top=50' --fields pullRequestId,title,sourceRefName --all --table` |
| Jenkins: jobs in a folder | `clt jenkins --json --read-only jobs 'Folder/Subfolder' --table` |
| Jenkins: latest build and its console | `clt jenkins --json --read-only build 'Folder/Job'` and `clt jenkins --json --read-only log 'Folder/Job'` |
| Jenkins: build queue | `clt jenkins --json --read-only queue --table` |
| Jira: a ticket as Markdown | `clt jira --json --read-only issue PROJ-123` |
| Jira: move a ticket | `clt jira --json --read-only GET issue/PROJ-123/transitions --fields transitions`, then the `POST` in `references/jira.md` |
| GitHub: open PRs | `clt github --json --read-only GET "repos/<owner>/<repo>/pulls?state=open&per_page=100" --fields number,title,head.ref --all --table` |
| GitHub: CI of a commit | `clt github --json --read-only GET "repos/<owner>/<repo>/commits/<sha or branch>/check-runs" -r \| jq -r '.check_runs[] \| [.name, .conclusion] \| @tsv'` |
| Gitea: open PRs | `clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title,head.ref,mergeable --all --table` |
| Taiga: story, task or issue by ref | `clt taiga --json --read-only issue <ref> --project <slug>` (id, version, status, description; `mine` gives ref and slug) |
| Taiga: change a status | `clt taiga --json PATCH userstories/<id> -d '{"status": <status-id>, "version": <version>}' --dry-run` (ids from `references/taiga.md`) |
| Release | `clt release --json --dry-run --bump <fix\|feat\|major>` (`references/release.md`) |

## Flags

`--fields a,b.c` (nested fields come back as the flat key `"b.c"`; `{"value": [...]}` envelopes stay), `--table`, `--max-items N`, `--all` (every page), `-r` (raw body), `--dry-run`. Exit codes: 0 ok, 1 error (`"status"` on stderr), 2 usage (a JSON line too), 3 not logged in, 4 blocked by read-only.
