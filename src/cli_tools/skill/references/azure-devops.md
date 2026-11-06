# clt azdo: recipes

Azure DevOps Services REST API. Paths are relative to `https://dev.azure.com/<org>/`: `<project>/_apis/...` or `_apis/...`. clt adds `api-version=7.1` when the path has none; keep the version a recipe gives (`-preview` APIs). Put paths with `$` in single quotes. Read with `--json --read-only`; before any `POST`, `PUT`, `PATCH` or `DELETE`, show the request with `--dry-run` and send it only with the user's go-ahead.

## Session

`AZDO_TOKEN` and the Azure CLI's `AZURE_DEVOPS_EXT_PAT` win over the saved token; `clt azdo me` (text) says which one is used. `me`'s `id` is the reviewer id used to vote.

```bash
clt azdo --json --read-only me
```

## Projects and repositories

```bash
clt azdo --json --read-only GET _apis/projects --fields id,name --all
clt azdo --json --read-only GET "<project>/_apis/git/repositories" --fields id,name,defaultBranch
```

## Work items

Field names have dots (`System.Title`), so keep `fields` whole in `--fields`.

```bash
clt azdo --json --read-only GET '_apis/wit/workitems/<id>?$expand=relations' --fields id,fields,relations
clt azdo --json --read-only GET "_apis/wit/workitems?ids=<id>,<id>&fields=System.Title,System.State,System.AssignedTo" --fields id,fields
clt azdo --json --read-only GET "<project>/_apis/wit/workitemtypes/Task" --fields name,states
clt azdo --json --read-only GET "<project>/_apis/wit/workitemtypes" --fields name,isDisabled
clt azdo --json --read-only GET '<project>/_apis/wit/workitemtypes/Task/fields?$expand=allowedValues' --fields referenceName,alwaysRequired,allowedValues
```

Before creating, check the type is not disabled and which fields it requires (`alwaysRequired`, often `Custom.*`); a failed create lists them in `body.customProperties.RuleValidationErrors`.

Your open work items, or any WIQL query, in one command: `wiql` reads the columns its `SELECT` names (people as their display name). Both are reads, fine under `--read-only`:

```bash
clt azdo --json --read-only mine --table
clt azdo --json --read-only mine --project <project> --type Bug --table
clt azdo --json --read-only wiql "SELECT [System.Id], [System.State], [System.Title] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.AssignedTo] = @Me" --project <project> --table
```

`mine` leaves out `Closed`, `Done` and `Removed`; for processes with other final states, use `wiql`. Link queries (`FROM WorkItemLinks`) need the raw `POST '<project>/_apis/wit/wiql'`.

`wiql` reads at most `--top` work items (default 200) and `mine` at most `--limit` (default 50); when stderr says it stopped there, raise it.

Create a task under a parent. The body is a JSON Patch list; `task.json`:

```json
[{"op": "add", "path": "/fields/System.Title", "value": "Write the migration"},
 {"op": "add", "path": "/fields/System.Description", "value": "<p>Details, in HTML</p>"},
 {"op": "add", "path": "/fields/System.AssignedTo", "value": "ana@example.com"},
 {"op": "add", "path": "/relations/-", "value": {"rel": "System.LinkTypes.Hierarchy-Reverse",
   "url": "https://dev.azure.com/<org>/_apis/wit/workItems/<parent id>"}}]
```

```bash
clt azdo --json POST '<project>/_apis/wit/workitems/$Task' -d @task.json --dry-run
clt azdo --json POST '<project>/_apis/wit/workitems/$Task' -d @task.json --fields id,_links.html.href
```

Change the state. The names depend on the process (Agile: New, Active, Resolved, Closed; Scrum: To Do, In Progress, Done; Basic: To Do, Doing, Done); list them with the work item type recipe above (its `states`).

```bash
clt azdo --json PATCH _apis/wit/workitems/<id> -d '[{"op": "add", "path": "/fields/System.State", "value": "Active"}]' --dry-run
clt azdo --json PATCH _apis/wit/workitems/<id> -d '[{"op": "add", "path": "/fields/System.State", "value": "Active"}]' --fields id,fields
```

Comment (HTML text; this API is a preview one):

```bash
clt azdo --json POST "<project>/_apis/wit/workItems/<id>/comments?api-version=7.1-preview.4" -d '{"text": "Fixed in PR 12."}' --dry-run
clt azdo --json POST "<project>/_apis/wit/workItems/<id>/comments?api-version=7.1-preview.4" -d '{"text": "Fixed in PR 12."}' --fields id
```

Delete (to the recycle bin; `?destroy=true` deletes it for good):

```bash
clt azdo --json DELETE "<project>/_apis/wit/workitems/<id>" --dry-run
clt azdo --json DELETE "<project>/_apis/wit/workitems/<id>"
```

## Pull requests

List, read one, its iterations and changes, and its comment threads:

```bash
clt azdo --json --read-only GET '<project>/_apis/git/repositories/<repo>/pullrequests?searchCriteria.status=active&$top=50' --fields pullRequestId,title,sourceRefName,targetRefName,createdBy.displayName --all
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullrequests/<pr>" --fields pullRequestId,title,description,status,mergeStatus,sourceRefName,targetRefName,lastMergeSourceCommit.commitId,reviewers
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/iterations" --fields id,sourceRefCommit.commitId
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/iterations/<iteration>/changes" --fields changeEntries
clt azdo --json --read-only GET "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/threads" --fields id,status,threadContext.filePath,comments
```

Create one, linked to work items. `pr.json` (the description is Markdown):

```json
{"sourceRefName": "refs/heads/feature/x", "targetRefName": "refs/heads/main", "title": "Add the migration",
 "description": "What and why", "workItemRefs": [{"id": "123"}]}
```

```bash
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullrequests" -d @pr.json --dry-run
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullrequests" -d @pr.json --fields pullRequestId,status
```

Comment: a new thread (Markdown). For a comment on a line, add `"threadContext": {"filePath": "/src/app.py", "rightFileStart": {"line": 10, "offset": 1}, "rightFileEnd": {"line": 10, "offset": 1}}`.

```bash
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/threads" -d '{"comments": [{"parentCommentId": 0, "content": "Looks good.", "commentType": 1}], "status": 1}' --dry-run
clt azdo --json POST "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/threads" -d '{"comments": [{"parentCommentId": 0, "content": "Looks good.", "commentType": 1}], "status": 1}' --fields id
```

Vote with your `me` id: `10` approve, `5` approve with suggestions, `0` no vote, `-5` wait for author, `-10` reject.

```bash
clt azdo --json PUT "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/reviewers/<my id>" -d '{"vote": 10}' --dry-run
clt azdo --json PUT "<project>/_apis/git/repositories/<repo>/pullRequests/<pr>/reviewers/<my id>" -d '{"vote": 10}' --fields displayName,vote
```

Complete (merge). `complete.json`, with the PR's current `lastMergeSourceCommit.commitId`; `mergeStrategy` is `squash`, `noFastForward`, `rebase` or `rebaseMerge`:

```json
{"status": "completed", "lastMergeSourceCommit": {"commitId": "<commit id>"},
 "completionOptions": {"mergeStrategy": "squash", "deleteSourceBranch": true, "transitionWorkItems": true}}
```

```bash
clt azdo --json PATCH "<project>/_apis/git/repositories/<repo>/pullrequests/<pr>" -d @complete.json --dry-run
clt azdo --json PATCH "<project>/_apis/git/repositories/<repo>/pullrequests/<pr>" -d @complete.json --fields pullRequestId,status,mergeStatus
```

## What agents get wrong

- Creating or updating a work item with a plain JSON object: it takes a JSON Patch list (`[{"op": "add", "path": "/fields/...", "value": ...}]`).
- Completing a PR without the current `lastMergeSourceCommit.commitId`: it is rejected.
- Writing work item descriptions and comments in Markdown: they are HTML (PR descriptions and PR comments are Markdown). A Bug's text goes in `Microsoft.VSTS.TCM.ReproSteps`, not `System.Description`.
- Listing PRs without `$top`, or with `$` inside double quotes, where the shell eats it.
