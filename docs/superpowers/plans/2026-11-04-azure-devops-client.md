# clt azdo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `clt azdo`, an Azure DevOps Services client with the same shape and flags as the other tools (PAT login, `me`, `token`, `api` with `--all`, `--fields`, `--max-items`, `--dry-run`), plus the recipe dictionary agents use to create tasks, open, review and complete pull requests. Second of the three pull requests of issue #47.

**Architecture:** A new package `tools/azdo/` (`client.py`, `cli.py`) registered with one line in `registry.py`. The client adds `api-version` when the path has none, picks the JSON Patch content type only for creating and updating work items, and pages through `x-ms-continuationtoken` or `$top`/`$skip` using the core hooks from PR #48 (`next_page(headers, body, path)`, `items_key`, `join`). Jira's `safe_path` moves to `core/http.py` so both tools encode typed paths the same way. The skill gains `skills/clt/references/azure-devops.md`.

**Tech Stack:** Python 3.9+ standard library (`urllib`, `base64`, `json`, `re`, `getpass`, `webbrowser`, `argparse`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-04-jira-azure-devops-design.md` (sections "`clt azdo`", "Core change", "The recipe dictionary"). Out of this PR: `clt azdo mcp`, `clt skill install`, the aliases of #49 (`clt ad` comes with #49).

**Branch:** `feature/azure-devops-client`, stacked on `feature/jira-client` (PR #48). Once #48 is merged with rebase, rebase this branch onto `main` before opening its PR.

## Global Constraints

- Python 3.9+, standard library only; works on Windows; English in code, help, messages, docs and tests.
- **No comments or docstrings in new code** (the owner's rule). Existing comments stay.
- The PAT is never printed (except by `clt azdo token`), logged, put in a URL, or sent anywhere but `https://dev.azure.com/<org>/`. The org is a name matching `^[A-Za-z0-9][A-Za-z0-9-]{0,49}$`; anything else is refused before any request.
- Tests never touch the network or `~/.config`: fake `cli_tools.core.http.send`, `tests.helpers.TempConf`. Test org: `example` (`https://dev.azure.com/example`); test project `Demo Project`, repo `web`.
- `python scripts/check_private_words.py` prints `OK` before every commit.
- Existing behaviour and contracts of `taiga`, `gitea`, `github`, `jira`, `release`, `update` do not change.
- Conventional Commits (`feat(azdo): …`, `refactor(http): …`, `docs: …`); no attribution lines; every commit goes through the owner's approval prompt; never push or open the PR without the owner's OK.
- Checks before each commit: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`, `PYTHONPATH=src uv run --no-project --python 3.9 python -m unittest discover -s tests -t .`, `uvx ruff check .`, `python scripts/check_private_words.py`.

## Decisions this plan adds to the spec

- `--org` and `AZDO_ORG` take the org name or `https://dev.azure.com/<org>` (trailing slash allowed); the saved URL is always `https://dev.azure.com/<org>`. Old `https://<org>.visualstudio.com` URLs are refused with a message that names the org form.
- Every request carries `X-TFS-FedAuthRedirect: Suppress`, so a bad PAT gets a 401 instead of a sign-in page. A 203 answer (the sign-in page, when that header is ignored) or a 3xx is treated as a rejected token: `ToolError("Azure DevOps did not accept the token …")`, exit 1.
- `api-version` is appended as text (`&api-version=7.1`), not by re-encoding the user's query, so what the user typed reaches the server unchanged. The check for an existing one is case-insensitive.
- `me` and login call `_apis/connectionData?api-version=7.1-preview` (a preview API; the path carries its own version). `id` = `authenticatedUser.id`, `full_name` = `authenticatedUser.providerDisplayName`, `email` and `username` = `authenticatedUser.properties.Account.$value` (falls back to `null`).
- Token precedence: `AZDO_TOKEN`, then `AZURE_DEVOPS_EXT_PAT`, then the saved session.

## Review Focus

- **A rejected or expired PAT answered with a 203 sign-in page or a 302** instead of a 401: `me` and `api` fail with a clear message and exit 1; `login` saves nothing → tests in Task 1 and Task 3.
- **Project or repo names with spaces or accents** (`Demo Project`, `Café`): the path is percent-encoded once and reaches the right URL; `$Task` and `$Product Backlog Item` keep their `$` → test in Task 1.
- **A path that already has `api-version`**, in any case (`API-Version=7.1-preview.4`): not added again → test in Task 1.
- **`--org` as a URL, with a trailing slash, or as something that is not an org** (`evil.example.com/x`, `https://example.visualstudio.com`, `a/../b`): normalized or refused before any request → test in Task 1.
- **`--all` on a `$top` list whose last page has exactly `$top` items, or a repeated continuation token**: one extra request that returns an empty `value` then stop, or stop at once; never loop to `MAX_PAGES` → test in Task 2.

---

### Task 1: Azure DevOps client: org, credentials, requests

**Files:**
- Modify: `src/cli_tools/core/http.py` (add `safe_path`), `src/cli_tools/tools/jira/client.py` (import it instead of defining it)
- Create: `src/cli_tools/tools/azdo/__init__.py` (empty), `src/cli_tools/tools/azdo/client.py`
- Modify: `tests/helpers.py` (`TempConf` strips `AZDO_` and `AZURE_DEVOPS_EXT_PAT`, sets `AZDO_CONF_DIR`)
- Test: `tests/test_azdo.py`, `tests/test_jira.py` (existing `safe_path` tests keep passing through the import)

**Interfaces:**
- Produces:
  - `http.safe_path(path: str) -> str` — Jira's function moved verbatim (refuses `.`/`..` segments, percent-encodes with `PATH_SAFE` / `QUERY_SAFE`); `jira.client` does `from cli_tools.core.http import safe_path`.
  - In `tools/azdo/client.py`: `PROG = "clt azdo"`, `ORG_VAR = "AZDO_ORG"`, `API_VERSION = "7.1"`, `TOKEN_SCOPES = ("Work Items (Read & write)", "Code (Read & write)", "Project and Team (Read)")`, `CONNECTION_DATA = "_apis/connectionData?api-version=7.1-preview"`.
  - `org_url(value: str) -> str` — `https://dev.azure.com/<org>` or `ToolError`.
  - `basic_header(token: str) -> str` — `"Basic " + base64(":" + token)`.
  - `with_api_version(path: str, version: str) -> str`.
  - `content_type(method: str, path: str) -> str` — `application/json-patch+json` for `POST …/_apis/wit/workitems/$<Type>` and `PATCH …/_apis/wit/workitems/<digits>` (path without query, case-insensitive), else `application/json`.
  - `class AzureDevOps(org=None, require_url=True)` with `url`, `credentials_file` (`AZDO_CONF_DIR` or `~/.config/azdo`, `credentials.json` = `{"url", "token"}`), `cacert` (`AZDO_CACERT` or `ca.pem`), `request(method, path, body=None, token=None) -> (status, raw, headers)`, `check_token(token) -> dict` (the `authenticatedUser`), `save_credentials(token)`, `load_credentials() -> {"url", "token", "env"?}`, `call(method, path, body)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_azdo.py` (a `FakeAzdo` like `FakeJira` in `tests/test_jira.py`, returning per-route headers too):
  - `test_org_url_accepts_a_name_or_the_url`: `org_url("example")`, `org_url("https://dev.azure.com/example/")` → `"https://dev.azure.com/example"`; `org_url` raises `ToolError` for `"evil.example.com/x"`, `"https://example.visualstudio.com"`, `"a/../b"`, `""`, `"-x"`.
  - `test_basic_header`: `basic_header("pat")` == `"Basic " + base64.b64encode(b":pat").decode()`.
  - `test_api_version_added_once`: `with_api_version("_apis/projects", "7.1") == "_apis/projects?api-version=7.1"`; `with_api_version("_apis/projects?$top=5", "7.1") == "_apis/projects?$top=5&api-version=7.1"`; a path with `API-Version=7.1-preview.4` comes back unchanged.
  - `test_json_patch_only_for_work_item_create_and_update`: `content_type("POST", "Demo/_apis/wit/workitems/$Task?api-version=7.1")` and `content_type("PATCH", "_apis/wit/WorkItems/42")` → patch; `content_type("POST", "Demo/_apis/wit/workitems/42/comments")`, `content_type("POST", "_apis/wit/workitemsbatch")`, `content_type("PATCH", "Demo/_apis/git/repositories/web/pullrequests/7")` → `application/json`.
  - `test_request_url_headers_and_encoding`: with `AZDO_ORG=example`, `AZDO_TOKEN=pat`, `call("GET", "Demo Project/_apis/wit/workitems/$Task", None)` sends to `https://dev.azure.com/example/Demo%20Project/_apis/wit/workitems/$Task?api-version=7.1` with `Authorization` = `basic_header("pat")`, `X-tfs-fedauthredirect` = `Suppress`, and the token absent from the URL.
  - `test_env_token_precedence`: `AZDO_TOKEN` beats `AZURE_DEVOPS_EXT_PAT`, which beats the saved file; with neither and no file → `NotLoggedIn` (exit 3) whose message names `clt azdo login` and `AZDO_TOKEN`.
  - `test_sign_in_page_is_a_rejected_token`: a route answering `(203, "<html>…", {})`, and one raising `HttpError(302, "", {"Location": "https://login…"})`, both make `call` raise `ToolError` containing `did not accept the token`.
  - `test_saved_session_for_another_org_is_refused`: saved `{"url": "https://dev.azure.com/other", "token": "x"}` with `AZDO_ORG=example` → `ToolError` naming both.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_azdo -v` → FAIL (`No module named cli_tools.tools.azdo`).
- [ ] **Step 3: Implement** `http.safe_path` (move), `tools/azdo/client.py` with the interfaces above. `request` builds the URL as `http.api_url(self.url, safe_path(with_api_version(path, version)), "/")`, where the version is `AZDO_API_VERSION` or `API_VERSION`, and passes `{"Authorization": …, "X-TFS-FedAuthRedirect": "Suppress", "Content-Type": content_type(method, path)}` to `http.json_request` with `token=None`. A 203 status, or an `HttpError` with a 3xx status, becomes the `ToolError`. `AzureDevOps.__init__` resolves the URL with `http.resolve_url(org_url(org) if org else None, org_url(env) if env else "", …)` and then checks the result with `org_url` again (a hand-edited saved URL to another host is refused).
- [ ] **Step 4: Run** the full suite → PASS (Jira's tests unchanged).
- [ ] **Step 5: Commit** `refactor(http): share the typed-path encoding` (the `safe_path` move alone, Jira suite green), then `feat(azdo): Azure DevOps client with PAT, api-version and JSON Patch`.

### Task 2: Paging and wrapped lists

**Files:**
- Modify: `src/cli_tools/tools/azdo/client.py`
- Test: `tests/test_azdo.py`

**Interfaces:**
- Consumes: `api.set_query(path, params)`, `api.run_api(args, call, ctx, next_page, items_key, join)` from PR #48.
- Produces: `items_key(data) -> "value" | None`; `next_page(headers, body=None, path=None) -> str | None`; `join(pages: list) -> object`.

- [ ] **Step 1: Write the failing tests**, calling the three functions directly (the CLI comes in Task 3):
  - `test_continuation_token_header`: headers `{"X-MS-ContinuationToken": "abc"}`, path `"_apis/projects?$top=2"` → `"_apis/projects?%24top=2&continuationToken=abc"`; the same token already in the path → `None`.
  - `test_top_and_skip`: body `{"count": 2, "value": [1, 2]}`, path `"Demo/_apis/git/repositories/web/pullrequests?$top=2"` → path with `$skip` = 2 (assert via `urllib.parse.parse_qs`, not the string); then with `$skip=2` → `$skip` = 4; a body with 1 item and `$top=2` → `None`; no `$top` and no header → `None`; a non-JSON body → `None`.
  - `test_join_updates_count`: `join([{"count": 2, "value": [1, 2]}, {"count": 1, "value": [3]}]) == {"count": 3, "value": [1, 2, 3]}`; `items_key({"value": []}) == "value"`, `items_key({"id": 1}) is None`, `items_key([1]) is None`.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_azdo -v` → the three new tests FAIL (`AttributeError`).
- [ ] **Step 3: Implement** the three functions. Header lookup is case-insensitive. `$top`/`$skip` are read with `urllib.parse.parse_qsl` on the path's query; the next path is built with `api.set_query`.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(azdo): follow continuation tokens and $top/$skip pages`.

### Task 3: `clt azdo` commands: login, logout, me, token, api

**Files:**
- Create: `src/cli_tools/tools/azdo/cli.py`
- Modify: `src/cli_tools/registry.py` (`("azdo", "cli_tools.tools.azdo.cli", "client for the Azure DevOps Services API, with a personal access token")`, after `jira`)
- Test: `tests/test_azdo.py`, `tests/test_contracts.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: Task 1 and Task 2; `api.add_api_parser(sub, prog, "<project>/_apis/wit/workitems/<id>", func, api_prefix="/")`; `context.require_terminal`, `ctx.check_session_command`.
- Produces: `cli.main(argv, ctx=None, prog="clt azdo") -> int`; commands `login [--org ORG] [--create-token | --token-stdin] [--no-browser]`, `logout`, `me`, `token`, `api`.

- [ ] **Step 1: Write the failing tests:**
  - `test_login_with_token_stdin_checks_and_saves`: stdin `"pat\n"`, `--org example --token-stdin`, route `GET https://dev.azure.com/example/_apis/connectionData?api-version=7.1-preview` → 200 with `{"authenticatedUser": {"id": "u-1", "providerDisplayName": "Bot User", "properties": {"Account": {"$value": "me@example.com"}}}}` → exit 0, the file holds `{"url": "https://dev.azure.com/example", "token": "pat"}`, stderr says `Bot User` and never `pat`.
  - `test_login_rejected_saves_nothing`: the same route raising `HttpError(401, …)` → exit 1, no credentials file.
  - `test_login_without_terminal_fails_at_once` (no `--token-stdin`, stdin not a TTY) → exit 1 with the `require_terminal` message, no request.
  - `test_create_token_prints_page_and_scopes`: `--create-token --no-browser` with a fake terminal → stderr has `https://dev.azure.com/example/_usersSettings/tokens` and each of `TOKEN_SCOPES`; `webbrowser.open` not called.
  - `test_me_json_shape`: `--json me` → `{"id": "u-1", "username": "me@example.com", "full_name": "Bot User", "email": "me@example.com", "url": "https://dev.azure.com/example", "credentials_file": null}` with `AZDO_TOKEN` set; a missing `properties` gives `null` email and username, no traceback.
  - `test_api_get_all_fields`: `GET "Demo/_apis/git/repositories/web/pullrequests?$top=2" --all --fields pullRequestId` over two pages (2 then 1 items) → `{"count": 3, "value": [{"pullRequestId": 1}, {"pullRequestId": 2}, {"pullRequestId": 3}]}`, 2 requests.
  - `test_api_patch_work_item_sends_json_patch`: `PATCH _apis/wit/workitems/42 -d '[{"op": "add", "path": "/fields/System.State", "value": "Active"}]'` → `Content-type: application/json-patch+json`; with `--read-only` → exit 4, no request.
  - `tests/test_contracts.py`: add `azdo` to the tool list, usage (`usage: clt azdo `), the command list `"login logout me token api"`, the not-logged-in exit 3 (`("clt", ["azdo", "me"], {"AZDO_ORG": "example"})`), and read-only blocking `login`, `logout`, `token`. `tests/test_registry.py`: the new row.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_azdo tests.test_contracts tests.test_registry -v` → FAIL (`Unknown tool 'azdo'`, missing module).
- [ ] **Step 3: Implement** `cli.py` following `tools/jira/cli.py`: same `prompt`, `cmd_*`, epilog with examples, token help and environment variables (`AZDO_ORG`, `AZDO_TOKEN`, `AZURE_DEVOPS_EXT_PAT`, `AZDO_API_VERSION`, `AZDO_CONF_DIR`, `AZDO_CACERT`, `NO_COLOR`) and the agents block; `run_api(args, a.call, args.ctx, client.next_page, client.items_key, client.join)`. `logout` builds the client with `require_url=False`.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(azdo): login, logout, me, token and api commands`.

### Task 4: Docs and the recipe dictionary

**Files:**
- Create: `skills/clt/references/azure-devops.md`
- Modify: `skills/clt/SKILL.md` (description and a short Azure DevOps section pointing to the reference; the session rules mention `clt azdo login` and `AZDO_TOKEN`), `README.md`, `docs/architecture.md` (tree and tool list), `AGENTS.md` (project line), `pyproject.toml` (description)
- Test: `tests/test_skill.py`

- [ ] **Step 1: Write the failing tests:** generalize `tests/test_skill.py` to run each check over `{"jira": "references/jira.md", "azdo": "references/azure-devops.md"}` (recipe lines start with `clt <tool>`; known commands gain `PATCH` and `DELETE`, which, like `POST`/`PUT`, need a `--dry-run` line first for the same method and path); the SKILL description must mention `Azure DevOps`; each reference stays under 8000 characters.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_skill -v` → FAIL (no reference file).
- [ ] **Step 3: Write** `references/azure-devops.md` with the spec's recipes: session (exit 3 → ask the user; `AZDO_TOKEN`), list projects and repos, get a work item (`$expand=relations`), query with WIQL (`POST <project>/_apis/wit/wiql`, then `GET _apis/wit/workitems?ids=…&fields=…`), create a task with a parent link (JSON Patch, `System.LinkTypes.Hierarchy-Reverse`), change a work item's state, comment on a work item (`7.1-preview.4`), list PRs with `$top`, get a PR with its iterations, changes and threads, create a PR with `workItemRefs`, comment on a PR (a thread), vote (10 / 5 / 0 / -5 / -10 on `reviewers/<my id from me>`), complete a PR (`lastMergeSourceCommit`, `completionOptions`). Paths with `$` in single quotes. It ends with what agents get wrong: `PUT`/`POST` a plain JSON object to a work item instead of a JSON Patch list; dropping `lastMergeSourceCommit` when completing; reading `System.Description` as text when it is HTML; forgetting `$top` on PR lists. Then the docs: README section and examples, architecture tree, AGENTS project line, `pyproject.toml` description.
- [ ] **Step 4: Run** the full suite, ruff and the private-words check → PASS / OK.
- [ ] **Step 5: Commit** `docs: Azure DevOps in the README, architecture and the skill's recipes`.

### Task 5: Hand-off for the owner's real test

- [ ] Final whole-branch review (executing-plans); fixes in one pass.
- [ ] Give the owner the install-from-branch commands and a Codex prompt like Jira's: `clt azdo login --org <org> --create-token` by the owner, then reads only (`me`, projects, repos, a work item, a WIQL query, PR list with `--all --max-items`, one PR's threads), and writes (create a task, move it, comment, open a PR, vote) only with `--dry-run` first and the owner's "adelante" for each.
