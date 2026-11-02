---
name: clt
description: Use the clt command-line tools (clt taiga, clt gitea, clt github) to read and change Taiga projects (user stories, issues, tasks) and Gitea or GitHub repositories (pull requests, branches, issues, releases) through their REST APIs. Use it whenever a task needs data from or changes in Taiga, Gitea or GitHub, instead of curl, gh or a browser.
---

# clt: Taiga, Gitea and GitHub from the command line

`clt` wraps the Taiga and Gitea REST APIs (`/api/v1/`) and the GitHub API (`https://api.github.com/`) with the user's saved session. Taiga and Gitea tokens are renewed automatically. The Taiga and Gitea tools are also installed as `taiga` and `gitea`.

## Before anything else

- **Never log in yourself.** `taiga login`, `gitea login` and `clt github login` need a person; without a terminal they fail at once. If a command exits with code 3 (not logged in), ask the user to run the login, or to set `TAIGA_TOKEN` / `GITEA_TOKEN` / `GITHUB_TOKEN`.
- **Read freely, write only with the user's go-ahead.** Run reads with `--read-only` so a mistaken method cannot change anything. Before a `POST`, `PUT`, `PATCH` or `DELETE`, show the request with `--dry-run`, and send it only when the user agrees.
- **Keep answers small.** Use `--fields` and `--max-items` on lists. Whole Taiga or Gitea objects are large and fill the context.
- **Never print tokens.** Do not run `taiga token` or `gitea token` unless the user asks for a token for another script.

## Command shape

```bash
clt <tool> <METHOD> "<path relative to /api/v1/>" [-d JSON | -d @file.json] [options]
```

| Option | Use |
|---|---|
| `--json` | Errors as one JSON line on stderr: `{"error", "status", ...}`. Use it always, and parse stderr on failure. `me` prints `{"id", "username", "full_name", "email", "url", ...}`. |
| `--read-only` | Only `GET` is sent; any other method exits with code 4 without sending. `login`, `refresh`, `logout` and `token` are blocked too. |
| `--dry-run` | Prints `{"method", "path", "body"}` and sends nothing. |
| `--fields a,b,c.d` | Keeps only these fields of each object; `c.d` reads a nested field. |
| `--max-items N` | Keeps the first N items of a list (stderr says how many there were). |
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

## When something fails

| Exit code / message | What to do |
|---|---|
| 3, "Not logged in" | Ask the user to run `taiga login` / `gitea login` in a terminal, or to set the token variable. |
| 4, "read-only mode" | Expected under `--read-only`. Drop the flag only once the user approved the write. |
| 1 with `"status": 401` | The session expired and could not be renewed: ask the user to log in again. |
| 1 with `"status": 404` | Wrong path or id. Check the recipe, or list the parent collection first. |
| 1 with `"status": 400` on a Taiga `PATCH` | Usually a stale `version`: read the object again and retry with the new `version`. |
| "No server configured" | Ask the user for the server, or which `TAIGA_URL` / `GITEA_URL` to use. |
