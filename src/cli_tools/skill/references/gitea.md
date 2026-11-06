# clt gitea: recipes

Issues and pull requests assigned to you in every repository, in one command:

```bash
clt gitea --json --read-only mine --table
```

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
