# clt gitea

Paths are relative to `/api/v1/`. Writes: drop `--dry-run` only after the user agrees.

## Read

```bash
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>" --fields number,state,merged,mergeable,head.sha,html_url
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/files" --fields filename,status,additions,deletions --all --table
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/reviews" --fields id,user.login,state,body,comments_count
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>/reviews/<review id>/comments" --fields id,path,position,body
clt gitea --json --read-only GET "repos/<owner>/<repo>/branches?limit=50" --fields name,commit.id --all --table
```

## Write

```bash
jq -n --rawfile body pr.md '{title: "<title>", head: "<branch>", base: "<base>", body: $body}' | clt gitea --json POST "repos/<owner>/<repo>/pulls" -d @- --dry-run
clt gitea --json POST "repos/<owner>/<repo>/pulls/<n>/reviews" -d @review.json --dry-run
clt gitea --json POST "repos/<owner>/<repo>/pulls/<n>/comments/<comment id>/replies" -d '{"body": "Fixed in abc1234."}' --dry-run
clt gitea --json POST "repos/<owner>/<repo>/pulls/<n>/merge" -d '{"Do": "merge"}' --dry-run
```

- A review is one request: `{"event": "COMMENT", "commit_id": "<head sha>", "body": "...", "comments": [{"path": "src/a.cs", "new_position": 42, "body": "..."}]}`. `event` is `COMMENT`, `APPROVED` or `REQUEST_CHANGES`.
- `new_position` is the line number in the new file, not the position in the diff as on GitHub. Pick a line inside the diff's hunks; read the comments back to check where they landed.
- A reply stays inside the review thread. Older Gitea versions lack that endpoint: if it answers 404, check the server's `/swagger.v1.json`.
- `Do` takes `merge`, `rebase`, `rebase-merge`, `squash` or `fast-forward-only`; add `"head_commit_id": "<head sha>"` so Gitea refuses the merge if the branch moved.
