# clt taiga

A status change needs the project id, the object's `id` and current `version` (optimistic locking), and the status id: chain the reads in one shell call. Writes: drop `--dry-run` only after the user agrees.

```bash
clt taiga --json --read-only issue <ref> --project <slug>     # a story, task or issue: id, version, status, description
clt taiga --json --read-only GET "projects/by_slug?slug=<slug>" --fields id,name
clt taiga --json --read-only GET "userstories/by_ref?ref=<ref>&project=<project-id>" --fields id,version,status_extra_info.name,description
clt taiga --json --read-only GET "userstory-statuses?project=<project-id>" --fields id,name --table
clt taiga --json --read-only GET "issues?project=<project-id>&order_by=-ref" --fields ref,subject,status_extra_info.name --max-items 20 --table
clt taiga --json PATCH userstories/<id> -d '{"status": <status-id>, "version": <version>}' --dry-run
```

Issues and tasks work the same with `issues/` / `issue-statuses` and `tasks/` / `task-statuses`. A `400` on a `PATCH` is usually a stale `version`: read it again. `mine` rows show refs, not ids.
