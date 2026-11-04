# clt jira Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `clt jira`, a Jira Cloud client with the same shape and flags as the other tools, that accepts classic and scoped API tokens, prints an issue as a compact summary (`clt jira issue KEY`), and comes with a recipe dictionary for agents. First of the three pull requests of issue #47.

**Architecture:** A new package `tools/jira/` (`client.py`, `adf.py`, `cli.py`) registered with one line in `registry.py`. `core/api.py` gets two tool hooks (`items_key`, `join`), a body-aware `next_page(headers, body, path)` and a `set_query` helper; Taiga, Gitea and GitHub keep their output byte for byte. The skill gains `skills/clt/references/jira.md`, read only when the agent works with Jira.

**Tech Stack:** Python 3.9+ standard library (`urllib`, `base64`, `json`, `getpass`, `webbrowser`, `argparse`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-04-jira-azure-devops-design.md` (sections "`clt jira`", "Core change", "The recipe dictionary"). Out of this PR: `clt azdo`, `clt jira mcp`, `clt skill install`, moving the skill into the package.

## Global Constraints

- Python 3.9+, standard library only; works on Windows; English in code, help, messages, docs and tests.
- **No comments or docstrings in new code** (the owner's rule); explanations go in the PR and commit messages. Existing comments stay.
- Tokens are never printed (except by `clt jira token`), logged, put in a URL, or sent anywhere but `https://<site>.atlassian.net/rest/api/3/` or `https://api.atlassian.com/ex/jira/<cloudId>/rest/api/3/`. `_edge/tenant_info` is called with no `Authorization` header.
- Tests never touch the network or `~/.config`: fake `cli_tools.core.http.send`, `tests.helpers.TempConf`. Test site: `https://example.atlassian.net`; test cloudId: `11111111-2222-3333-4444-555555555555`.
- `python scripts/check_private_words.py` prints `OK` before every commit.
- Existing behaviour and contracts of `taiga`, `gitea`, `github`, `release`, `update` do not change.
- Branch `feature/jira-client`; Conventional Commits (`feat(jira): …`, `refactor(api): …`, `docs: …`, `test: …`); no attribution lines; commit only when the owner has OK'd committing for this work; never push or open the PR without the owner's OK.
- Checks before each commit: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`, `PYTHONPATH=src uv run --no-project --python 3.9 python -m unittest discover -s tests -t .`, `uvx ruff check .`, `python scripts/check_private_words.py`.

## Review Focus

- **Unassigned issue with no description, no parent and no comments** (`assignee`, `description`, `parent` are `null`, `comment.comments` is `[]`): `issue` prints `null`/`[]`, no traceback → test in Task 4.
- **ADF with node types the converter does not know** (`panel`, `expand`, `mediaSingle`, `status`, `emoji`, `inlineCard`): their text content is kept, nothing raises → test in Task 4.
- **Classic token rejected and `tenant_info` unusable** (404, not JSON, no `cloudId`, network error): login says the token was rejected and how to pass a cloudId; nothing is saved → test in Task 2.
- **A cloudId that is not a UUID** (from `tenant_info`, `JIRA_CLOUD_ID` or a hand-edited file, e.g. `../x`): refused before any request, so it can never steer the token to another path → test in Task 2.
- **`--all` on `search/jql` when Jira repeats a `nextPageToken` or omits `isLast`**: stops instead of looping until `MAX_PAGES` → test in Task 3.

---

### Task 1: Core: body-aware pagination, wrapped lists, `set_query`

**Files:**
- Modify: `src/cli_tools/core/api.py`, `src/cli_tools/tools/github/client.py` (`next_page`)
- Test: `tests/test_core.py`, `tests/test_agents.py`

**Interfaces:**
- Produces:
  - `api.set_query(path: str, params: dict) -> str` — sets or replaces query parameters of a relative path, keeps the others in order, encodes with `urllib.parse.urlencode`; a value `None` removes the parameter.
  - `api.next_page_path(headers, body=None, path=None, api_root=None) -> str | None` — same result as today; `body` and `path` ignored.
  - `api.run_api(args, call, ctx=None, next_page=next_page_path, items_key=None, join=None) -> int`; `next_page` is now called as `next_page(headers, raw_body, current_path)`.
    - `items_key(data) -> str | None`: when it returns a key whose value is a list, `--max-items` and `--fields` act on `data[key]` and the envelope is printed around it.
    - `join(pages: list) -> object`: with `--all` and more than one page, builds the result from the parsed pages; default (`None`) keeps today's flattening.
  - `github.client.next_page(headers, body=None, path=None)`.

- [ ] **Step 1: Write the failing tests**
  - `tests/test_core.py::SetQueryTests.test_adds_replaces_and_removes`: `set_query("search/jql?jql=a%3D1&fields=key", {"nextPageToken": "t 1"}) == "search/jql?jql=a%3D1&fields=key&nextPageToken=t+1"`; `set_query("x?startAt=0&a=b", {"startAt": 50}) == "x?startAt=50&a=b"`; `set_query("x?a=b&c=d", {"a": None}) == "x?c=d"`; `set_query("x", {"a": 1}) == "x?a=1"`.
  - `tests/test_agents.py::OutputSizeTests.test_fields_and_max_items_reach_a_wrapped_list`: body `{"total": 3, "issues": [{"key": "A-1", "x": 1}, {"key": "A-2", "x": 2}, {"key": "A-3", "x": 3}]}`, `items_key=lambda d: "issues"`, `fields="key"`, `max_items=2` → stdout JSON `{"total": 3, "issues": [{"key": "A-1"}, {"key": "A-2"}]}`, stderr contains `2 of 3`.
  - `tests/test_agents.py::OutputSizeTests.test_all_passes_the_body_and_path_to_next_page_and_uses_join`: two pages `{"issues": [{"n": 1}], "nextPageToken": "t"}` and `{"issues": [{"n": 2}], "isLast": true}`; `next_page` records its `(body, path)` arguments and returns `"items?nextPageToken=t"` then `None`; `join=lambda pages: {"issues": [i for p in pages for i in p["issues"]]}` → output `{"issues": [{"n": 1}, {"n": 2}]}`, and the recorded paths are `["items", "items?nextPageToken=t"]`.
  - The existing `test_all_follows_gitea_and_taiga_pagination_on_the_same_server` and `NextPageTests` stay unchanged and must still pass.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_core tests.test_agents -v` → the three new tests FAIL (`set_query` missing; unexpected keyword `items_key`).
- [ ] **Step 3: Implement** in `core/api.py`: `set_query`; `next_page_path` with the two new optional parameters; `run_api` keeps the current path in a variable, calls `next_page(headers, raw, path)`, joins with `join(parsed_pages)` when given, then applies `--max-items` and `--fields` to `data[items_key(data)]` when `items_key` is given and returns a key holding a list, else to `data` as today. Update `github.client.next_page` to the new signature (it still delegates with `api_root`).
- [ ] **Step 4: Run** the full suite → PASS (all existing tests untouched).
- [ ] **Step 5: Commit** `refactor(api): let tools page through the body and filter lists wrapped in an object`.

### Task 2: Jira client: credentials, both token kinds, requests

**Files:**
- Create: `src/cli_tools/tools/jira/__init__.py` (empty), `src/cli_tools/tools/jira/client.py`
- Modify: `tests/helpers.py` (`TempConf`: strip `JIRA_` variables, set `JIRA_CONF_DIR`)
- Test: `tests/test_jira.py`

**Interfaces:**
- Consumes: `http.resolve_url`, `http.validate_base_url`, `http.api_url`, `http.json_request(method, url, body, None, cacert, headers)`, `http.send`, `http.parse_json`, `credentials.read_json_file`, `credentials.write_private_json`.
- Produces (module `cli_tools.tools.jira.client`):
  - Constants: `PROG = "clt jira"`, `URL_VAR = "JIRA_URL"`, `API_PREFIX = "/rest/api/3/"`, `GATEWAY = "https://api.atlassian.com"`, `TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"`, `SCOPES = ("read:jira-work", "write:jira-work", "read:jira-user")`.
  - `basic_header(email: str, token: str) -> str` → `"Basic " + base64(email:token)`.
  - `check_cloud_id(value) -> str` — lowercase UUID (`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`, case-insensitive) or `ToolError("... is not a Jira cloud id ...")`.
  - `class Jira(url=None, require_url=True)`: attributes `conf_dir` (`JIRA_CONF_DIR` or `~/.config/jira`), `credentials_file`, `cacert` (`JIRA_CACERT` or `ca.pem`), `url` (the site, via `resolve_url(url, JIRA_URL, ...)`).
    - `base(creds) -> (root, prefix)`: `("https://<site>", API_PREFIX)` for `mode == "site"`, `(GATEWAY, f"/ex/jira/{cloud_id}/rest/api/3/")` for `"scoped"` (cloud id checked).
    - `request(method, path, body=None, creds=None) -> (status, raw, headers)`: builds the URL with `http.api_url(root, path, prefix)` and sends `Authorization: basic_header(...)` through `headers`, `token=None`.
    - `cloud_id() -> str`: `GET <site>/_edge/tenant_info` with `http.send` and no `Authorization`; returns `check_cloud_id(body["cloudId"])`; any failure → `ToolError` that says to pass `--cloud-id`.
    - `detect(email, token, cloud_id=None, scoped=False) -> (creds: dict, me: dict)`: unless `scoped`, `GET myself` on the site; on 401, or when `scoped`, `GET myself` through the gateway with `cloud_id or self.cloud_id()`; returns `creds = {"url", "email", "token", "mode", "cloud_id"}` (`cloud_id` `None` in site mode) and the `myself` body. Both rejected → `ToolError("Jira rejected the token ...")`.
    - `save_credentials(creds)`; `load_credentials() -> dict`: `JIRA_EMAIL` + `JIRA_TOKEN` win (mode `"scoped"` when `JIRA_CLOUD_ID` is set, else `"site"`); no file → `NotLoggedIn` naming `clt jira login` and `JIRA_TOKEN`; incomplete file → `ToolError`; session for another site → `ToolError` as Taiga does.
    - `call(method, path, body) -> (status, raw, headers)`: `request` with `load_credentials()`; a 401 in site mode from the environment raises `ToolError` that mentions `JIRA_CLOUD_ID`; other errors propagate. No renewal.

- [ ] **Step 1: Write the failing tests** in `tests/test_jira.py` (a `FakeJira` like `FakeGitHub` in `tests/test_github.py`, keyed by `(method, full_url)`), `class JiraClientTests(TempConf)`:
  - `test_basic_header`: `basic_header("me@example.com", "tok") == "Basic bWVAZXhhbXBsZS5jb206dG9r"`.
  - `test_classic_token_stays_on_the_site`: `myself` on `https://example.atlassian.net/rest/api/3/myself` answers 200 → `creds["mode"] == "site"`, `creds["cloud_id"] is None`, one request only.
  - `test_scoped_token_is_detected_through_tenant_info`: site `myself` 401, `https://example.atlassian.net/_edge/tenant_info` → `{"cloudId": "11111111-2222-3333-4444-555555555555"}`, gateway `https://api.atlassian.com/ex/jira/11111111-2222-3333-4444-555555555555/rest/api/3/myself` 200 → mode `"scoped"`; the `tenant_info` request has no `Authorization` header.
  - `test_scoped_flag_skips_the_site`: `detect(..., scoped=True, cloud_id=<uuid>)` sends only the gateway request.
  - `test_rejected_everywhere_saves_nothing`: both `myself` 401 → `ToolError` matching `rejected`; credentials file does not exist.
  - `test_tenant_info_unusable_says_how_to_pass_the_cloud_id`: for each of 404, `"not json"`, `{}`, `ToolError("Cannot connect")` → `ToolError` matching `--cloud-id`.
  - `test_cloud_id_must_be_a_uuid`: `check_cloud_id("../x")`, `"abc"`, `""` raise; `JIRA_CLOUD_ID="../x"` with `JIRA_EMAIL`/`JIRA_TOKEN` → `call` raises before `http.send` is called.
  - `test_environment_wins_and_picks_the_mode`: saved site session plus `JIRA_EMAIL`/`JIRA_TOKEN` → env creds used; adding `JIRA_CLOUD_ID` → request goes to the gateway.
  - `test_not_logged_in`: `JIRA_URL` set, no file → `NotLoggedIn` matching `JIRA_TOKEN`.
  - `test_session_for_another_site_is_refused`: saved `url` `https://other.atlassian.net`, `JIRA_URL=https://example.atlassian.net` → `ToolError` matching `other.atlassian.net`.
  - `test_env_site_401_mentions_jira_cloud_id`: env creds, site 401 → `ToolError` matching `JIRA_CLOUD_ID`.
  - `test_paths_cannot_leave_the_api`: `call("GET", "https://evil.example/x", None)` raises `ToolError` and nothing is sent.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_jira -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `client.py` and the `TempConf` change.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(jira): client with classic and scoped API tokens`.

### Task 3: `clt jira` commands: login, logout, me, token, api

**Files:**
- Create: `src/cli_tools/tools/jira/cli.py`
- Modify: `src/cli_tools/tools/jira/client.py` (add `next_page`, `items_key`, `join`), `src/cli_tools/registry.py`
- Modify tests: `tests/test_contracts.py`, `tests/test_registry.py`, `tests/test_clt.py` (the unknown-tool example `jira` becomes `nope`; `CLT_HELP` gains the jira line; command list; not-logged-in case)
- Test: `tests/test_jira.py`

**Interfaces:**
- Consumes: Task 1 (`run_api` hooks, `set_query`), Task 2 (`Jira`, `TOKEN_PAGE`, `SCOPES`).
- Produces:
  - `registry.TOOLS` entry, after `github`: `("jira", "cli_tools.tools.jira.cli", "client for the Jira Cloud API, with an API token")`.
  - `cli.main(argv, ctx=None, prog="clt jira") -> int`, `cli.build_parser(prog)`; commands `login`, `logout`, `me`, `token`, `api` (and `issue` in Task 4).
  - `login` options: `--url URL`, `--email EMAIL`, `--create-token`, `--token-stdin`, `--scoped`, `--cloud-id ID`, `--no-browser`. Without `--token-stdin` it needs a terminal (`context.require_terminal("clt jira login")`) and prompts for the email (if no `--email`) and the token with `getpass`. `--create-token` prints which button to press ("Create API token", or "Create API token with scopes" plus `SCOPES`) and opens `TOKEN_PAGE` unless `--no-browser`. Success message: `✓ Logged in to Jira as <displayName> (<mode> token)`.
  - `me --json` → exactly the `ME_KEYS` of `tests/test_contracts.py`: `id` = `accountId`, `username` = the session email, `full_name` = `displayName`, `email` = `emailAddress` or the session email, `url` = site, `credentials_file` (`None` with env creds).
  - `client.next_page(headers, body, path) -> str | None`: `nextPageToken` in the body and `isLast` not true and the token differs from the one already in `path` → `set_query(path, {"nextPageToken": token})`; else `startAt` + list from `items_key` + not `isLast` + (`total` absent or `startAt + len < total`) + the list is not empty → `set_query(path, {"startAt": startAt + len})`; else `None`.
  - `client.items_key(data) -> str | None`: first of `issues`, `values`, `comments`, `worklogs` present in a dict with a list value.
  - `client.join(pages) -> object`: if the first page is a dict with an `items_key`, a copy of it with that key holding all pages' items and `nextPageToken`, `isLast`, `startAt`, `maxResults` removed (and `total` kept); otherwise today's flattening.

- [ ] **Step 1: Write the failing tests**
  - `tests/test_jira.py::JiraCliTests`: `test_login_token_stdin_saves_the_detected_mode` (`login --url https://example.atlassian.net --email me@example.com --token-stdin`, stdin `tok\n`, scoped path as in Task 2 → file has `mode: "scoped"` and the cloud id; stdout empty); `test_login_without_a_terminal_needs_token_stdin`; `test_me_json_shape_and_no_token` (output keys == `ME_KEYS`, `tok` not in output); `test_logout_deletes_the_session`; `test_read_only_blocks_login_and_token`.
  - `tests/test_jira.py::JiraPagingTests`: `test_search_jql_follows_next_page_token` (`GET search/jql?jql=project%3DA&fields=key --all`, pages with tokens `t1` then `isLast: true` → output `{"issues": [3 items]}` with no `nextPageToken`/`isLast`); `test_repeated_token_stops` (page 2 returns `nextPageToken` equal to the one just sent and no `isLast` → exactly 2 requests); `test_start_at_paging_for_values` (`{"startAt": 0, "maxResults": 2, "total": 3, "values": [2]}` then `{"startAt": 2, ..., "values": [1]}` → 3 values, 2 requests); `test_fields_reach_issues` (`--fields key,fields.status.name` on a search body → each issue reduced).
  - `tests/test_contracts.py`: `CLT_HELP` gains `  jira     client for the Jira Cloud API, with an API token` after the github line; `test_unknown_tool_is_an_error` and `test_unknown_tool_ends_with_the_json_line` use `nope`; `test_each_command_names_itself_in_its_usage` gains `("clt", ["jira"], "usage: clt jira ")`; `test_the_api_clients_list_their_commands` gains `("clt", ["jira"], "login logout me token api")` (Task 4 adds `issue` to that string); `test_not_logged_in` gains `("clt", ["jira", "me"], {"JIRA_URL": "https://example.atlassian.net"})`; `test_default_credentials_folders_are_under_the_home_config` covers `~/.config/jira` the way it covers the others.
  - `tests/test_registry.py` and `tests/test_clt.py`: the unknown-tool example becomes `nope`.
- [ ] **Step 2: Run** the full suite → the new and changed tests FAIL (no `clt jira`).
- [ ] **Step 3: Implement** `cli.py` following `tools/github/cli.py` (same epilog sections: examples, token kinds and scopes, environment variables `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`, `JIRA_CLOUD_ID`, `JIRA_CONF_DIR`, `JIRA_CACERT`, `NO_COLOR`, the agents block), the paging hooks in `client.py`, and the registry line. The `api` command: `api.add_api_parser(sub, prog, "issue/<KEY>", lambda j, args: api.run_api(args, j.call, args.ctx, next_page, items_key, join), api_prefix=API_PREFIX)`.
- [ ] **Step 4: Run** the full suite → PASS; `PYTHONPATH=src python3 -m cli_tools jira --help` shows the commands.
- [ ] **Step 5: Commit** `feat(jira): login, me, token and api commands`.

### Task 4: ADF to text, and `clt jira issue`

**Files:**
- Create: `src/cli_tools/tools/jira/adf.py`
- Modify: `src/cli_tools/tools/jira/cli.py`
- Test: `tests/test_jira.py`

**Interfaces:**
- Consumes: Task 3 (`Jira.call`, parser).
- Produces:
  - `adf.to_text(node) -> str`: `None` → `""`; a plain string → itself. Paragraphs separated by a blank line; `heading` level n → `#`×n + space; `bulletList` items `- `, `orderedList` items `1. `, `2. `… (nested lists indented two spaces per level); `codeBlock` fenced with ``` and its `language`; `blockquote` lines prefixed `> `; `hardBreak` → newline; `mention` → `@` + `attrs.text` without a leading `@`; `text` with a `link` mark → `[text](href)`; `inlineCard` → its `attrs.url`; `emoji` → `attrs.text` or `attrs.shortName`; `table` → one line per row, cells joined with ` | `; `rule` → `---`; anything else → its children's text. Output stripped of trailing whitespace.
  - `issue KEY [--comments N]` (default 10, `0` none): `GET issue/<KEY>?fields=summary,status,issuetype,priority,assignee,reporter,labels,parent,description,subtasks,issuelinks,comment`; when `comment.total` exceeds the comments returned and N > 0, also `GET issue/<KEY>/comment?startAt=<max(total-N,0)>&maxResults=<N>`. Keeps the last N comments, oldest first. `--json` → the object in the spec with exactly these keys: `key, summary, type, status, priority, assignee, reporter, labels, parent, description, subtasks, links, comments, url` (`url` = `<site>/browse/<KEY>`, always the site, also in scoped mode). `links` items: `{"type", "key", "summary", "status"}` with `type` = `outward` or `inward` text of the link type as seen from this issue. Without `--json`: readable text (layout left to the builder) with the same information.

- [ ] **Step 1: Write the failing tests**
  - `AdfTests`: one test per rule above with a small ADF fixture and the exact expected string, plus `test_unknown_nodes_keep_their_text` (a `panel` containing a paragraph `"Note"`, a `status` node with `attrs.text` `"DONE"` → output contains `Note`; no exception), and `test_none_and_empty_doc` (`to_text(None) == ""`, `to_text({"type": "doc", "content": []}) == ""`).
  - `tests/test_contracts.py`: the jira entry of `test_the_api_clients_list_their_commands` becomes `"login logout me token issue api"`.
  - `IssueTests`: `test_issue_json_shape` (full fixture → exact dict), `test_issue_with_nothing_set` (all optional fields `null`, `comment: {"comments": [], "total": 0}` → `null`s and `[]`, no second request), `test_more_comments_are_fetched` (`total: 12`, 5 inline, `--comments 3` → second request `issue/A-1/comment?startAt=9&maxResults=3`, 3 comments in output), `test_comments_zero` (no comments key content, one request), `test_url_uses_the_site_in_scoped_mode`, `test_issue_text_mode_mentions_summary_status_and_description`.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_jira -v` → new tests FAIL.
- [ ] **Step 3: Implement** `adf.py` and `cmd_issue`.
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** `feat(jira): issue command with ADF turned into text`.

### Task 5: Docs and the recipe dictionary

**Files:**
- Create: `skills/clt/references/jira.md`
- Modify: `skills/clt/SKILL.md`, `README.md`, `docs/architecture.md`, `pyproject.toml` (`description` mentions Jira)
- Test: `tests/test_agents.py` (or a new `tests/test_skill.py`)

**Interfaces:**
- Consumes: the commands of Tasks 3–4 exactly as their `--help` shows them.

- [ ] **Step 1: Write the failing tests** in `tests/test_skill.py`:
  - `test_skill_mentions_jira_and_points_to_its_reference`: `SKILL.md` front-matter `description` contains `Jira`; the body contains `references/jira.md`.
  - `test_reference_is_small`: `references/jira.md` is under 8,000 characters (about 2,000 tokens).
  - `test_every_recipe_uses_json_and_a_known_command`: every line starting with `clt jira` in a code block contains `--json`, and its first word after `clt jira` is one of `login`, `me`, `issue`, `GET`, `POST`, `PUT`, `api`; lines with `POST`/`PUT` that are not `--dry-run` follow a `--dry-run` line for the same path.
- [ ] **Step 2: Run** → FAIL (no reference file).
- [ ] **Step 3: Write**
  - `references/jira.md`: login (both token kinds, env vars), read an issue (`clt jira --json --read-only issue KEY`), my open issues and a JQL search (`GET "search/jql?jql=...&fields=key,summary,status"`), list transitions (`GET issue/KEY/transitions --fields transitions` → `id`, `name`, `to.name`), move an issue (`POST issue/KEY/transitions -d '{"transition": {"id": "<id>"}}'`, `--dry-run` first), add a comment (ADF body: `{"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "..."}]}]}}`), assign (`PUT issue/KEY/assignee -d '{"accountId": "..."}'`, account id from `GET "user/search?query=<email>"`), and a closing "What agents get wrong" list: the status is changed through transitions, never by editing `status`; `search` is gone, use `search/jql` and always pass `fields`; bodies of descriptions and comments are ADF, not plain text.
  - `SKILL.md`: description and title mention Jira; one section "Jira" of at most five lines: what `clt jira` is, "read `references/jira.md` before any Jira call", `issue` as the way to read a ticket.
  - `README.md`: Jira in the intro, the login section, the env var table (`JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`, `JIRA_CLOUD_ID`, `JIRA_CONF_DIR`, `JIRA_CACERT`), the agents section, the paths table.
  - `docs/architecture.md`: `jira/` in the tree (`client.py` with the two token modes, `adf.py`, `cli.py`); the `run_api` hooks in the core table; the "Adding a tool" example now says "Say the new service is Linear" so it does not describe a tool that exists.
- [ ] **Step 4: Run** the full suite, ruff and the private-words check → PASS / `OK`.
- [ ] **Step 5: Commit** `docs: Jira in the README, the architecture and the skill`.

### Task 6: Hand-off for the owner's real test

- [ ] **Step 1:** Run all four checks from Global Constraints and paste their last lines into the hand-off message.
- [ ] **Step 2:** Write, in the hand-off message (not in the repo), the commands for the owner's Jira machine, all with `--json --read-only` except the last one: install from the branch (once pushed with the owner's OK), `clt jira login --url https://<site>.atlassian.net --create-token`, `clt jira --json --read-only me`, `clt jira --json --read-only issue <KEY>`, a `search/jql` with `--all --max-items 20`, `GET issue/<KEY>/transitions`, and the transition with `--dry-run` and then for real only on a ticket the owner picks. Ask them to report the `mode` that login chose and anything `issue` shows wrongly.
- [ ] **Step 3:** With the owner's OK: push `feature/jira-client`, open the PR with the template, `Refs #47` (not `Closes`: two more PRs follow), and move the card to *In Progress*.
