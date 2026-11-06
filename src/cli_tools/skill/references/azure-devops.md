# clt azdo

Paths: `<project>/_apis/...` or `_apis/...`; clt adds `api-version=7.1` unless the path has one. Single-quote paths with `$`. Writes: drop `--dry-run` only after the user agrees.

## Work items

```bash
clt azdo --json --read-only GET "_apis/wit/workitems?ids=<id>,<id>&fields=System.Title,System.State,System.AssignedTo" --fields id,fields
clt azdo --json --read-only GET "<project>/_apis/wit/workitemtypes/Task/states" --fields name,category --table
clt azdo --json --read-only GET '<project>/_apis/wit/workitemtypes/Task/fields?$expand=allowedValues' --fields referenceName,alwaysRequired,allowedValues
clt azdo --json --read-only mine --project <project> --type Bug --table
clt azdo --json --read-only wiql "SELECT [System.Id], [System.State], [System.Title] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.State] = 'Done'" --project <project> --top 5000 --table
```

- `mine` skips `Closed`, `Done` and `Removed` only; `wiql` reads the `SELECT` columns. Raise `--top` / `--limit` when stderr says it stopped. Link queries (`FROM WorkItemLinks`) need the raw `POST '<project>/_apis/wit/wiql'`.
- States depend on the process: list them (`states` above) before a move.
- Bodies are JSON Patch lists, never plain objects. Descriptions and work item comments are HTML; a Bug's text goes in `Microsoft.VSTS.TCM.ReproSteps`. A failed create lists required fields in `body.customProperties.RuleValidationErrors`.

Create a task under a parent, comment, delete (to the recycle bin):

```bash
echo '[{"op": "add", "path": "/fields/System.Title", "value": "Write the migration"}, {"op": "add", "path": "/relations/-", "value": {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://dev.azure.com/<org>/_apis/wit/workItems/<parent id>"}}]' | clt azdo --json POST '<project>/_apis/wit/workitems/$Task' -d @- --dry-run
clt azdo --json POST "<project>/_apis/wit/workItems/<id>/comments?api-version=7.1-preview.4" -d '{"text": "<p>Fixed in PR 12.</p>"}' --dry-run
clt azdo --json DELETE "<project>/_apis/wit/workitems/<id>" --dry-run
```

## Pull requests

```bash
clt azdo --json --read-only GET "<project>/_apis/git/repositories" --fields id,name,defaultBranch --table
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullrequests/<pr>" --fields pullRequestId,title,status,mergeStatus,sourceRefName,targetRefName,lastMergeSourceCommit.commitId,reviewers
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/threads" --fields id,status,threadContext.filePath,comments
```

PR descriptions and comments are Markdown. Create (linked to a work item), comment, vote with your `me` id (`10` approve, `5` with suggestions, `-5` wait, `-10` reject), complete with the PR's current `lastMergeSourceCommit.commitId` (`mergeStrategy`: `squash`, `noFastForward`, `rebase`, `rebaseMerge`):

```bash
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullrequests" -d '{"sourceRefName": "refs/heads/<branch>", "targetRefName": "refs/heads/main", "title": "<title>", "description": "<what and why>", "workItemRefs": [{"id": "<id>"}]}' --dry-run
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/threads" -d '{"comments": [{"parentCommentId": 0, "content": "Looks good.", "commentType": 1}], "status": 1}' --dry-run
clt azdo --json PUT "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/reviewers/<my id>" -d '{"vote": 10}' --dry-run
clt azdo --json PATCH "<project>/_apis/git/repositories/<repo>/pullrequests/<pr>" -d '{"status": "completed", "lastMergeSourceCommit": {"commitId": "<commit id>"}, "completionOptions": {"mergeStrategy": "squash", "deleteSourceBranch": true}}' --dry-run
```

A comment on a line adds `"threadContext": {"filePath": "/src/app.py", "rightFileStart": {"line": 10, "offset": 1}, "rightFileEnd": {"line": 10, "offset": 1}}`.
