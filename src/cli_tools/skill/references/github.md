# clt github

Paths are relative to `https://api.github.com/`. Lists stop at 30 without `per_page=100` and `--all`. Wrapped lists (`items`, `check_runs`, `workflow_runs`) need `-r` and `jq`: `--fields` returns nulls there. Writes: drop `--dry-run` only after the user agrees.

## Read

```bash
clt github --json --read-only GET "user/repos?per_page=100&sort=pushed" --fields full_name,default_branch --all --table
clt github --json --read-only GET "repos/<owner>/<repo>/issues?state=open&per_page=100" --fields number,title,pull_request.url --all --table
clt github --json --read-only GET "repos/<owner>/<repo>/issues/<n>/comments?per_page=100" --fields user.login,body --all
clt github --json --read-only GET "search/issues?q=repo:<owner>/<repo>+is:open+<words>" -r | jq -r '.items[] | [.number, .title] | @tsv'
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>" --fields number,state,mergeable,mergeable_state,head.ref,head.sha,base.ref
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/files?per_page=100" --fields filename,status,additions,deletions --all --table
clt github --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/reviews" --fields user.login,state,body
clt github --json --read-only GET "repos/<owner>/<repo>/actions/runs?branch=<branch>&per_page=5" -r | jq -r '.workflow_runs[] | [.name, .status, .conclusion] | @tsv'
clt github --json --read-only GET "repos/<owner>/<repo>/releases/latest" --fields tag_name,html_url
```

- The issues list mixes in pull requests (those have `pull_request.url`).
- `mergeable_state: "blocked"` with green CI usually means a ruleset only the admin bypasses.

## Write

Bodies with Markdown: build them with `jq` and pipe them in.

```bash
jq -n --arg title "<title>" --rawfile body issue.md '{title: $title, body: $body}' | clt github --json POST "repos/<owner>/<repo>/issues" -d @- --dry-run
clt github --json POST "repos/<owner>/<repo>/issues/<n>/comments" -d '{"body": "Fixed in PR 12."}' --dry-run
clt github --json PATCH "repos/<owner>/<repo>/issues/<n>" -d '{"state": "closed", "state_reason": "completed"}' --dry-run
jq -n --arg head "<branch>" --rawfile body pr.md '{title: "<title>", head: $head, base: "main", body: $body}' | clt github --json POST "repos/<owner>/<repo>/pulls" -d @- --dry-run
clt github --json POST "repos/<owner>/<repo>/pulls/<n>/reviews" -d '{"event": "COMMENT", "body": "Looks good."}' --dry-run
clt github --json PUT "repos/<owner>/<repo>/pulls/<n>/merge" -d '{"merge_method": "rebase", "sha": "<head sha>"}' --dry-run
```

Merge only when every check run is `success` or `skipped`; `sha` makes GitHub refuse it if the branch moved.

Project boards are GraphQL only (`POST graphql`, so not under `--read-only`; queries change nothing):

```bash
echo '{"query": "{user(login: \"<owner>\") {projectV2(number: <n>) {id}}}"}' | clt github --json POST graphql -d @- --dry-run
```
