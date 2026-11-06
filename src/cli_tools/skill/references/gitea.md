# clt gitea

Paths are relative to `/api/v1/`. Writes: drop `--dry-run` only after the user agrees.

```bash
clt gitea --json --read-only GET "repos/<owner>/<repo>/pulls/<n>" --fields number,state,merged,mergeable,html_url
clt gitea --json --read-only GET "repos/<owner>/<repo>/branches?limit=50" --fields name,commit.id --all --table
jq -n --rawfile body pr.md '{title: "<title>", head: "<branch>", base: "<base>", body: $body}' | clt gitea --json POST "repos/<owner>/<repo>/pulls" -d @- --dry-run
```
