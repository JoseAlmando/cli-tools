# Jira and Azure DevOps in clt, and setup for AI agents

Status: approved · 2026-11-04 · issue #47

## Why

The owner works with Jira Cloud (reading tickets so an AI agent understands them, and moving them between
states) and with Azure DevOps Services (creating tasks, opening pull requests, reviewing them). Today an agent
either finds the right REST call by searching the web, which is slow and burns tokens, or guesses and fails
on the details these two APIs get wrong most often (Jira's transitions and ADF bodies, Azure DevOps'
`api-version` and JSON Patch). The owner uses Codex most, Claude Code too, and wants Gemini CLI covered.

clt already solves this for Taiga, Gitea and GitHub: it owns the login and the token, and the skill gives the
agent ready-made recipes. This work extends the same model to Jira and Azure DevOps, and, where an official
MCP server exists, lets the user connect it to their agent with one command.

## Goals

1. **`clt jira`**: a client for the Jira Cloud REST API (`https://<site>.atlassian.net/rest/api/3/`) with the
   same shape and flags as the other tools (`--json`, `--read-only`, `api`, `--fields`, `--all`,
   `--max-items`, `--dry-run`, exit codes).
2. **`clt jira issue KEY`**: one issue as a compact, agent-friendly summary, with ADF turned into plain text.
3. **`clt azdo`**: a client for the Azure DevOps Services REST API (`https://dev.azure.com/<org>/`), same
   shape and flags.
4. **`clt <tool> mcp`**: prints, or with `--install` runs, the command that connects that service's official
   MCP server to Claude Code, Codex or Gemini CLI.
5. **`clt skill install`**: links clt's skill into the skills folder of Claude Code, Codex or Gemini CLI.
6. **A recipe dictionary per tool** in the skill, loaded only when the agent works with that tool, covering
   the operations the owner actually does.

Delivery order: Jira (the owner can test it now), then Azure DevOps, then the agent setup commands. Each one
is its own pull request.

## Non-goals (for now)

- Jira Server / Data Center and Azure DevOps Server (on-premises). The design should not block them, but
  nothing is built or tested for them.
- OAuth logins for Jira or Azure DevOps (Atlassian's 3LO needs a client secret; Entra ID needs an app
  registration). Tokens only.
- Confluence, Jira Service Management, Azure Pipelines, Boards configuration, wikis, test plans.
- Commands beyond `issue` and `mcp` (no `clt jira transition`, no `clt azdo pr create`): everything else is
  `api` plus a recipe in the skill.
- Editing an agent's configuration files directly.
- Installing Node.js, the Azure CLI or any agent CLI.

## Anti-goals (it fails even if it "works")

- Any new runtime dependency, or anything that only works on Linux.
- A token printed, logged, put in a URL, written into an agent's MCP configuration, or sent to a host other
  than its own API.
- A recipe dictionary so large that every Taiga-only user pays for Jira and Azure DevOps tokens.
- A change in behaviour for `clt taiga`, `clt gitea` or `clt github` (contracts in `tests/test_contracts.py`).
- Anything about the owner's employer in the repo: use `example.atlassian.net`, `dev.azure.com/example`.

## Constraints (from the project rules)

- Python 3.9+, standard library only; works on Windows; English everywhere; `unittest` tests without network,
  each new behaviour with a test that failed first; `scripts/check_private_words.py` passes.
- Follow "Adding a tool" in `docs/architecture.md`: `tools/jira/` and `tools/azdo/`, each with `client.py` and
  `cli.py`, one line each in `registry.py`. No standalone `jira` or `azdo` console scripts (names could clash).
- Credentials with `core.credentials.write_private_json`; servers with `http.resolve_url` /
  `http.validate_base_url`; paths with `http.api_url`, so the token never leaves the configured server.

## Decided behaviour

### `clt jira`

- **Server:** `clt jira login --url https://<site>.atlassian.net`, or `JIRA_URL`, or the saved session (same
  order as Taiga and Gitea, via `http.resolve_url`). API prefix `/rest/api/3/`.
- **Both kinds of Atlassian API token (decided by the owner):** a classic token works against the site
  (`https://<site>.atlassian.net/rest/api/3/`); a token with scopes only works through the API gateway
  (`https://api.atlassian.com/ex/jira/<cloudId>/rest/api/3/`), and some organisations block classic tokens.
  The user does not have to know which one they have:
  - Login checks the token with `GET myself` on the site. On a 401 it reads the site's `cloudId` from
    `https://<site>.atlassian.net/_edge/tenant_info` (public, no token sent) and tries `myself` through the
    gateway. Whichever works is saved as `"mode": "site"` or `"mode": "scoped"` with the `cloud_id`.
    (Verify `tenant_info` against the real site during implementation; if it is not usable, ask the user for
    the cloudId with the URL where Atlassian shows it.)
  - `--scoped` skips the detection and goes straight to the gateway.
  - With `JIRA_EMAIL` + `JIRA_TOKEN` from the environment, `JIRA_CLOUD_ID` set means scoped, otherwise site;
    on a 401 in site mode the error says to set `JIRA_CLOUD_ID` if the token has scopes.
  - The token is only ever sent to the site or to `https://api.atlassian.com/ex/jira/<its cloudId>/`; paths go
    through `http.api_url` with the matching base and prefix. Links shown to the user (`/browse/KEY`) always
    use the site.
  - `--create-token` says which button to press and, for a token with scopes, which scopes to tick:
    `read:jira-work`, `write:jira-work`, `read:jira-user`.
- **Credentials:** email + API token, sent as `Authorization: Basic base64(email:token)` in both modes. The client builds
  that header itself and passes it through `json_request`'s `headers` (with `token=None`), so `core.http`
  does not change. Saved in `JIRA_CONF_DIR` (default `~/.config/jira`) as `credentials.json`
  (`{"url", "email", "token", "mode", "cloud_id"}`). `JIRA_EMAIL` + `JIRA_TOKEN` in the environment win over the saved session.
  Extra CA: `JIRA_CACERT` or `ca.pem` in the folder, as the other tools.
- **Login:** asks for the email and the token (hidden). `--create-token` first opens
  `https://id.atlassian.com/manage-profile/security/api-tokens`; `--token-stdin` reads the token from stdin
  (email from `--email`). The token is checked with `GET myself` before saving. No renewal: a 401 means
  "log in again".
- **Commands:** `login`, `logout`, `me` (same JSON shape as the others: `id` = accountId, `username` = email,
  `full_name` = displayName, `email`, `url`, `credentials_file`), `token`, `api`, `issue`, `mcp`.
- **Pagination for `--all`:** `search/jql` returns `nextPageToken` in the body (the legacy `search` endpoint
  is gone); other list endpoints use `startAt`/`maxResults`/`isLast` or `total`. See "Core change".
- **`clt jira issue KEY [--comments N]`:** fetches the issue with only the fields it needs (one
  `GET issue/KEY?fields=...`, plus `GET issue/KEY/comment` when there are more comments than the issue
  carries) and prints:

  ```json
  {"key": "PROJ-123", "summary": "...", "type": "Story", "status": "In Progress", "priority": "High",
   "assignee": "Name", "reporter": "Name", "labels": [], "parent": "PROJ-100",
   "description": "plain text, lists and code blocks kept as Markdown",
   "subtasks": [{"key": "PROJ-124", "summary": "...", "status": "To Do"}],
   "links": [{"type": "blocks", "key": "PROJ-90", "summary": "...", "status": "Done"}],
   "comments": [{"author": "Name", "created": "2026-11-01T10:00:00Z", "body": "plain text"}],
   "url": "https://example.atlassian.net/browse/PROJ-123"}
  ```

  Without `--json` it prints the same as readable text. `--comments N` keeps the last N (default 10, `0`
  for none). ADF → text is a small converter in `tools/jira/adf.py` (paragraphs, headings, lists, code
  blocks, mentions, links, tables as rows; unknown nodes fall back to their text content). Custom fields
  (acceptance criteria and the like) are out unless the owner names them during testing.
- **Moving a ticket:** no command; a recipe: `GET issue/KEY/transitions`, then
  `POST issue/KEY/transitions -d '{"transition": {"id": "<id>"}}'` with `--dry-run` first.

### `clt azdo`

- **Server:** the organization: `clt azdo login --org <org>` (stored as `https://dev.azure.com/<org>`), or
  `AZDO_ORG`, or the saved session. API prefix `/` (paths are `<project>/_apis/...` or `_apis/...`).
- **Credentials:** a Personal Access Token, sent as `Authorization: Basic base64(":" + pat)`. Saved in
  `AZDO_CONF_DIR` (default `~/.config/azdo`); `AZDO_TOKEN` (or `AZURE_DEVOPS_EXT_PAT`, the name the Azure CLI
  uses) wins over it. Extra CA: `AZDO_CACERT` or `ca.pem`.
- **Login:** asks for the PAT (hidden). `--create-token` first opens
  `https://dev.azure.com/<org>/_usersSettings/tokens` and says which scopes to tick: Work Items (Read &
  write), Code (Read & write), and Project and Team (Read). `--token-stdin` as in GitHub. Checked with
  `GET _apis/connectionData` before saving. No renewal.
- **`me`:** from `_apis/connectionData` (`authenticatedUser`), same JSON shape as the others.
- **`api-version`:** if the path has no `api-version` query parameter, clt adds `api-version=7.1` (overridable
  with `AZDO_API_VERSION`). Paths that already carry one are left alone (some APIs need a `-preview` one).
- **JSON Patch:** *(narrowed in review)* the content type is `application/json-patch+json` only for creating a
  work item (`POST .../_apis/wit/workitems/$<Type>`) and updating one (`PATCH .../_apis/wit/workitems/<id>`),
  matched case-insensitively on the path without its query. Everything else, including
  `workitems/<id>/comments` and `workitemsbatch`, is `application/json`. No content-type flag is added to
  `api`.
- **Pagination for `--all`:** *(builder's call)* the `x-ms-continuationtoken` response header, sent back as
  the `continuationToken` query parameter; when there is none and the path has `$top=N` and the page
  returned N items, the next path is the same with `$skip` increased by N (pull request lists page this
  way). The recipes always pass `$top` on such lists. See "Core change".
- **Commands:** `login`, `logout`, `me`, `token`, `api`, `mcp`. No `issue`-style summary in this round.

### Core change: pagination and lists wrapped in an object

`api.run_api(args, call, ctx, next_page)` today calls `next_page(headers)`. It becomes
`next_page(headers, body, path)` returning the next path or `None`; the existing tools' functions get the
two new arguments and ignore them, so their behaviour does not change.

Jira and Azure DevOps return most lists inside an object (`{"issues": [...]}`, `{"values": [...]}`,
`{"comments": [...]}`, `{"count": n, "value": [...]}`). *(Builder's call, made in review.)* `run_api` takes
an optional `items_key(body)` from the tool, returning the key that holds the list or `None`:

- Jira: the first of `issues`, `values`, `comments`, `worklogs` present with a list value.
- Azure DevOps: `value` when present with a list value.
- Taiga, Gitea, GitHub: none, so nothing changes for them.

When a key is found, `--fields` and `--max-items` act on that list and the envelope is kept (so
`--fields id,name` on `_apis/projects` works). With `--all`, the items of every page are joined into the
first page's envelope (`count` updated for Azure DevOps; Jira's `nextPageToken`/`isLast`/`startAt` dropped),
so the output has the same shape for one page or many.

### `clt <tool> mcp --agent claude|codex|gemini [--install]`

Without `--install` it prints the command and what to do next; with it, it runs that command (argument list,
no shell) if the agent's CLI is on the PATH, and fails with a clear message if not. clt never writes an
agent's configuration file and never puts a token in it.

| Tool | Agent | Command clt prints / runs | Then |
|---|---|---|---|
| jira | claude | `claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v1/mcp` | `/mcp` in Claude Code to sign in |
| jira | codex | `codex mcp add atlassian --url https://mcp.atlassian.com/v1/mcp` | `codex mcp login atlassian` |
| jira | gemini | `gemini mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v1/mcp` | `/mcp auth atlassian` in Gemini |
| azdo | claude | `claude mcp add --scope user --transport stdio azure-devops -- npx -y @azure-devops/mcp <org>` | sign in in the browser on first use |
| azdo | codex | `codex mcp add azure-devops -- npx -y @azure-devops/mcp <org>` | same |
| azdo | gemini | `gemini mcp add --scope user azure-devops npx -y @azure-devops/mcp <org>` | same |

- *(Builder's call, made in review.)* Servers are registered for the **user**, not the current project, so
  they work in every folder and the three agents behave the same (Codex registers globally already).
- On Windows the `npx` commands become `cmd /c npx ...`, which Claude Code needs to start a stdio server there.
- `--read-only` blocks `--install` (it changes the agent's configuration); printing is always allowed.
- Agent CLIs are found with `shutil.which`, which also finds the `.cmd` shims npm installs on Windows.

- Azure DevOps uses Microsoft's **local** MCP server (needs Node.js 20+): Microsoft's remote server
  (`https://mcp.dev.azure.com/<org>`) does not work with Codex, and with Claude Code it needs an Entra ID app
  registration with admin consent. The help text mentions the remote one for users whose tenant has it.
- `<org>` comes from the saved session or `--org`.
- Taiga, Gitea and GitHub get no `mcp` command in this work.

### `clt skill install --agent claude|codex|gemini [--link]`

Installs clt's skill folder into:

| Agent | Folder |
|---|---|
| claude | `~/.claude/skills/clt` |
| codex | `~/.agents/skills/clt` |
| gemini | `~/.agents/skills/clt` (Gemini CLI reads `.agents/skills` as an alias of `.gemini/skills`) |

- The skill must ship inside the installed package (today it is only in the repo), so `skills/clt` moves to
  `src/cli_tools/skill/` as package data. The repo keeps `skills/clt` as a symlink to it, so the owner's
  existing link (`~/.claude/skills/clt` → repo `skills/clt`) and the README's old instructions keep working.
- *(Builder's call, made in review.)* **Copy by default**, not a link: a link into the installed package
  breaks when `clt update` moves it (the path includes the Python version), and links need privileges on
  Windows. Each copy carries a marker file `.clt-skill` with clt's version. `--link` links instead, for
  people developing clt from a checkout.
- A target is clt's if it has the marker, or is a link to a folder named `skills/clt` or
  `cli_tools/skill`. clt's targets are replaced; anything else stops with a message and is never touched.
- `clt update` refreshes every copy that has the marker after installing the new version, and says which.
- `clt skill install` with no `--agent` installs for every agent whose CLI is on the PATH and says which.
  `clt skill uninstall --agent ...` removes only clt's targets.

### The recipe dictionary

- `SKILL.md` stays short: rules, flags, exit codes, and one line per tool saying which reference file to
  read. Its description mentions Jira and Azure DevOps so agents pick it up.
- `references/jira.md` and `references/azure-devops.md` hold the recipes: one line of intent, the exact
  command, the `--fields` worth keeping. Target size: each under about 2,000 tokens.
- Jira recipes: read an issue (`clt jira issue`), search with JQL (`search/jql` with `fields`), my open
  issues, list transitions, move an issue, add a comment (ADF body), assign.
- Azure DevOps recipes: list projects and repos, get a work item, query with WIQL, create a task (JSON
  Patch, with parent link), change a work item's state, add a comment, list PRs, get a PR with its changes
  and threads, create a PR (linked work items), comment on a PR, vote (approve / wait / reject), complete a
  PR.
- Each reference ends with the two or three things agents get wrong for that API.

## Testing

- Unit tests against the fake `http.send`, as the existing tools: login (each way), credentials precedence,
  Basic header built correctly and never printed, `api-version` added once, JSON Patch content type,
  pagination of both kinds, ADF conversion (fixtures of real-shaped ADF), `issue` output, `mcp` commands
  per agent (the subprocess is faked), `skill install` (in a temporary home).
- Contract tests for the new public shapes (`me` JSON, `issue` JSON, exit codes).
- The owner tests Jira against a real Jira Cloud site with `--json --read-only` reads, then one transition
  with their OK; Azure DevOps the same once it is built.

## Left to the builder

- Exact help texts and messages; internal structure of the ADF converter; how `issue` lays out its text
  mode; how the reference files are worded, within the size target.
