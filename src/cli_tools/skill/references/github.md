# clt github: recipes

Paths are relative to `https://api.github.com/`. `<owner>/<repo>` is the repository, for example `octo/demo`. Writes
need the user's go-ahead: show them with `--dry-run` first.

## Session and repositories

```bash
clt github --json --read-only me
clt github --json --read-only GET "user/repos?per_page=100&sort=pushed" --fields full_name,private,default_branch --all
clt github --json --read-only GET "repos/<owner>/<repo>" --fields full_name,default_branch,private,html_url
clt github --json --read-only GET "repos/<owner>/<repo>/branches?per_page=100" --fields name,commit.sha --all
```

## Issues

The issues list also returns pull requests: those have a `pull_request.url`.

```bash
clt github --json --read-only GET "repos/<owner>/<repo>/issues?state=open&per_page=100" --fields number,title,user.login,pull_request.url --all
clt github --json --read-only GET "repos/<owner>/<repo>/issues/<n>" --fields number,title,state,body,user.login,labels
clt github --json --read-only GET "repos/<owner>/<repo>/issues/<n>/comments?per_page=100" --fields user.login,created_at,body --all
```

Search returns `{"items": [...]}`, which `--fields` does not reach; use `-r` and `jq`:

```bash
clt github --json --read-only GET "search/issues?q=repo:<owner>/<repo>+is:issue+is:open+<words>" -r | jq -c '.items[] | {number, title}'
```

Create, comment, close. Write the body to a file and build the JSON with `jq`:

```bash
jq -n --arg title "<title>" --rawfile body issue.md '{title: $title, body: $body}' > issue.json
clt github --json POST "repos/<owner>/<repo>/issues" -d @issue.json --dry-run
clt github --json POST "repos/<owner>/<repo>/issues" -d @issue.json --fields number,html_url
clt github --json POST "repos/<owner>/<repo>/issues/<n>/comments" -d '{"body": "Fixed in PR 12."}' --dry-run
clt github --json POST "repos/<owner>/<repo>/issues/<n>/comments" -d '{"body": "Fixed in PR 12."}' --fields id,html_url
clt github --json PATCH "repos/<owner>/<repo>/issues/<n>" -d '{"state": "closed", "state_reason": "completed"}' --dry-run
clt github --json PATCH "repos/<owner>/<repo>/issues/<n>" -d '{"state": "closed", "state_reason": "completed"}' --fields number,state
```

## Pull requests

```bash
clt github --json --read-only GET "repos/<owner>/<repo>/pulls?state=open&per_page=100" --fields number,title,head.ref,base.ref,user.login --all
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>" --fields number,title,state,merged,mergeable,mergeable_state,head.ref,head.sha,base.ref
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/files?per_page=100" --fields filename,status,additions,deletions --all
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/reviews" --fields user.login,state,body
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/comments?per_page=100" --fields user.login,path,line,body --all
```

CI of a commit (use the pull request's `head.sha`): `check_runs` is wrapped, so `-r` and `jq`. Every run must be
`completed` with `success` (or `skipped`) before a merge.

```bash
clt github --json --read-only GET "repos/<owner>/<repo>/commits/<sha>/check-runs" -r | jq -c '.check_runs[] | {name, status, conclusion}'
clt github --json --read-only GET "repos/<owner>/<repo>/actions/runs?branch=<branch>&per_page=5" -r | jq -c '.workflow_runs[] | {name, status, conclusion, head_sha}'
```

Open, review, merge. Push the branch with git first. `sha` in the merge makes GitHub refuse it if the branch moved
since CI passed; `merge_method` is `merge`, `squash` or `rebase`.

```bash
jq -n --arg title "<title>" --arg head "<branch>" --arg base main --rawfile body pr.md '{title: $title, head: $head, base: $base, body: $body}' > pr.json
clt github --json POST "repos/<owner>/<repo>/pulls" -d @pr.json --dry-run
clt github --json POST "repos/<owner>/<repo>/pulls" -d @pr.json --fields number,html_url,head.sha
clt github --json POST "repos/<owner>/<repo>/pulls/<n>/reviews" -d '{"event": "COMMENT", "body": "Looks good."}' --dry-run
clt github --json POST "repos/<owner>/<repo>/pulls/<n>/reviews" -d '{"event": "COMMENT", "body": "Looks good."}' --fields id,state
clt github --json PUT "repos/<owner>/<repo>/pulls/<n>/merge" -d '{"merge_method": "rebase", "sha": "<head sha>"}' --dry-run
clt github --json PUT "repos/<owner>/<repo>/pulls/<n>/merge" -d '{"merge_method": "rebase", "sha": "<head sha>"}' --fields merged,sha,message
```

## Releases

```bash
clt github --json --read-only GET "repos/<owner>/<repo>/releases/latest" --fields tag_name,name,html_url,body
clt github --json --read-only GET "repos/<owner>/<repo>/releases?per_page=10" --fields tag_name,name,draft,prerelease
```

## Projects (boards) with GraphQL

Project boards exist only in GraphQL: `POST graphql` with `{"query": "..."}`. It is a `POST` even for a read, so it
does not run under `--read-only`; queries change nothing, mutations do (ask first).

```bash
echo '{"query": "{user(login: \"<owner>\") {projectV2(number: <n>) {id}}}"}' > q.json
clt github --json POST graphql -d @q.json --dry-run
clt github --json POST graphql -d @q.json -r
```

## What agents get wrong

- `--fields` on `{"items": [...]}`, `{"check_runs": [...]}` or `{"workflow_runs": [...]}` returns nulls: use `-r` and `jq`.
- The issues list mixes in pull requests; filter on `pull_request.url` being null.
- `mergeable_state: "blocked"` on an owner's own repo with green CI usually means a ruleset that only the admin
  bypasses, not a failing check: read the check runs before deciding.
- Lists stop at 30 items unless you pass `per_page=100` and `--all`.
