# clt taiga: recipes

Your open user stories, tasks and issues in every project, in one command:

```bash
clt taiga --json --read-only mine --table
```

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

A `400` on a `PATCH` is usually a stale `version`: read the object again and retry with the new one.
