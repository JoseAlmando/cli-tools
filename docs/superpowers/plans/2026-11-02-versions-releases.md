# Versions, Releases and Self-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `clt release` cuts versioned releases of Python and .NET projects on GitHub or Gitea, `clt github` talks to the GitHub API (including `protect`), and `clt update` moves an installed `clt` to any released version.

**Architecture:** Three new tools registered in `clt.TOOLS` (`github`, `release`, `update`), built on `core` (generalised for a per-tool API prefix) plus two helper modules: `versions.py` (version files, bumps) and `gitrepo.py` (git subprocess helpers, remotes). `release.py` orchestrates and publishes through `github.GitHub` or `gitea.Gitea`.

**Tech Stack:** Python 3.9+ standard library only (`urllib`, `subprocess`, `importlib.metadata`, `re`, `json`), `git` on the PATH, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-02-versions-releases-design.md`

## Global Constraints

- Python 3.9+, standard library only; no new runtime dependency.
- Works on Windows: no bash, no `chmod` there, subprocesses with argument lists (never `shell=True`).
- English in code, help, messages, docs and tests.
- Tokens are never printed, logged, put in a URL, or sent to a host other than their own API.
- Nothing about the owner's employer in the repo; test hosts are `gitea.example.com`, `taiga.example.com`.
- Credentials via `core.write_private_json` (`0600`, `0700` folder, atomic).
- `--read-only` blocks every write; `--json` errors are one JSON line on stderr; exit codes 0/1/2/3/4 as today.
- Every task: tests first, full suite green (`PYTHONPATH=src python3 -m unittest discover -s tests -t .`), sensitive-word scan (`git grep -n -i --untracked` with the owner's list), then commit with no attribution lines. No push without the owner's OK.
- Existing behaviour of `taiga`, `gitea` and `clt` does not change (their tests keep passing untouched).

## Review Focus

- **CRLF version files** (a `.csproj` edited on Windows): the edit changes only the version text and keeps every `\r\n` → test in Task 5.
- **`git` not installed**: any `clt release` path says "git is not installed or not on the PATH" (exit 1), no traceback → test in Task 6.
- **Non-ASCII commit subjects** (accents, emoji) on a console with a non-UTF-8 locale: `CHANGELOG.md` is written as UTF-8 and the release body matches it → test in Task 7.
- **Tag already on the remote but not local** (released from another machine): the checks stop before any file changes → test in Task 7.
- **Version in the file is not `X.Y.Z`** (`1.2`, `v1.2.3`, `1.2.3-beta`): clear error naming the file and the value → test in Task 5.

---

### Task 1: One version source

**Files:**
- Modify: `src/cli_tools/__init__.py`
- Test: `tests/test_clt.py`

**Interfaces:**
- Produces: `cli_tools.__version__: str` (unchanged name); `cli_tools._version_from_pyproject(path) -> str or None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_clt.py`:
  - `test_version_matches_pyproject`: `__version__ == re.search(r'^version = "([^"]+)"', Path("pyproject.toml").read_text(), re.M).group(1)`.
  - `test_version_from_pyproject_reads_the_project_table`: a temp `pyproject.toml` with `[tool.x]\nversion = "9.9.9"\n[project]\nname = "a"\nversion = "1.2.3"\n` gives `"1.2.3"`; a missing file gives `None`.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_clt -v` → FAIL (`_version_from_pyproject` missing).
- [ ] **Step 3: Implement.** `__version__` = `importlib.metadata.version("cli-tools")`; on `PackageNotFoundError`, `_version_from_pyproject(Path(__file__).resolve().parents[2] / "pyproject.toml")`; if that is `None`, `"0+unknown"`. The helper finds the `[project]` table and its `version = "…"` line with `re` (no `tomllib`: Python 3.9).
- [ ] **Step 4: Run** the full suite → PASS, and `clt --version` (editable install) prints `clt 0.1.0`.
- [ ] **Step 5: Commit** "Read the version from pyproject.toml only".

### Task 2: Per-tool API prefix in core, and a write guard

**Files:**
- Modify: `src/cli_tools/core.py` (`api_url`, `json_request`, `next_page_path`, `run_api`, `add_api_parser`, `check_read_only_command`)
- Test: `tests/test_core.py`, `tests/test_agents.py`

**Interfaces:**
- Produces:
  - `api_url(base, path, prefix="/api/v1/") -> str` — `base + prefix + path.lstrip("/")`; the error text names `base + prefix`.
  - `json_request(method, url, body, token, cacert, headers=None)` — extra headers added after the defaults (so they can replace `Accept`).
  - `next_page_path(headers, api_root=None) -> str or None` — with `api_root` (e.g. `"https://api.github.com/"`), the next URL must start with it and the remainder is returned; without it, today's `/api/v1/` behaviour.
  - `run_api(args, call, next_page=next_page_path)`.
  - `add_api_parser(sub, prog, example_path, func, api_prefix="/api/v1/")` — help and description use `api_prefix`.
  - `require_writes(what)` — raises `ReadOnlyError(f"{what} was not run: read-only mode (--read-only or CLT_READONLY)")` when `read_only()`; `check_read_only_command` calls it.

- [ ] **Step 1: Write the failing tests:**
  - `test_core`: `api_url("https://api.github.com", "user", prefix="/") == "https://api.github.com/user"`; `api_url(..., "https://evil/x", prefix="/")` raises `ToolError`.
  - `test_core`: `next_page_path({"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}, api_root="https://api.github.com/") == "user/repos?page=2"`; the same header with `https://evil.example/user/repos?page=2` gives `None`.
  - `test_core`: `json_request` with `headers={"Accept": "application/vnd.github+json"}` sends that `Accept` (patch `core.send`, inspect the `Request`).
  - `test_agents`: `require_writes("protect")` raises `ReadOnlyError` under `CLT_READONLY=1` and returns `None` otherwise.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement** the signatures above; defaults keep every current call site identical.
- [ ] **Step 4: Run** the full suite → PASS (all 54 existing tests unchanged).
- [ ] **Step 5: Commit** "Let each tool choose its API prefix".

### Task 3: `clt github`

**Files:**
- Create: `src/cli_tools/github.py`
- Modify: `src/cli_tools/clt.py` (`TOOLS["github"]`), `README.md`, `skills/clt/SKILL.md`
- Test: `tests/test_github.py`, `tests/test_clt.py`

**Interfaces:**
- Consumes: Task 2 (`api_url(..., prefix="/")`, `json_request(..., headers=)`, `next_page_path(..., api_root=)`, `run_api(..., next_page=)`, `add_api_parser(..., api_prefix="/")`).
- Produces:
  - `PROG = "github"`, `API_ROOT = "https://api.github.com"`, `HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}`.
  - `class GitHub`: `conf_dir` (`GITHUB_CONF_DIR` or `~/.config/github`), `credentials_file`, `cacert` (`GITHUB_CACERT` or `ca.pem` there), `url = API_ROOT`; `request(method, path, body=None, token=None)`; `load_credentials() -> {"token": str}` (`GITHUB_TOKEN`, then `GH_TOKEN`, then the file; `NotLoggedIn` text mentions `clt github login` and `GITHUB_TOKEN`); `call(method, path, body)` (no renewal; `HttpError` propagates).
  - `main(argv, prog="clt github")` with `login [--token-stdin]`, `logout`, `me`, `token`, `api` / `METHOD path`.
- Not added to `[project.scripts]`: a standalone `github` command collides with GitHub Desktop's CLI. Only `clt github`.

- [ ] **Step 1: Write the failing tests** (`tests/test_github.py`, `TempConf`, `core.send` patched — no network):
  - login with `--token-stdin` reads one line, calls `GET user` with `Authorization: Bearer <t>` and the two `HEADERS`, saves `{"token": t}`; a 401 there raises `ToolError` and saves nothing.
  - login without a terminal and without `--token-stdin` fails with "needs a person".
  - `GITHUB_TOKEN` wins over the file, `GH_TOKEN` is used when `GITHUB_TOKEN` is unset.
  - `api GET "repos/o/r/pulls" --all` follows a same-host `Link` and ignores a foreign one (Task 2 behaviour through the tool).
  - `me` with `CLT_JSON=1` prints `{"id", "username", "full_name", "email", "url": "https://github.com", "credentials_file"}` and never the token.
  - under `CLT_READONLY=1`: `login`, `logout`, `token`, and `POST` are blocked (exit 4), `GET` passes.
  - `test_clt`: `clt --help` lists `github`.
- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** `github.py` following `taiga.py`'s shape (login prompt with `getpass` after `core.require_terminal`); epilog lists `GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_CONF_DIR` and the token permissions: "Contents: read and write (releases); Administration: read and write (protect)".
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Docs** — README table row and a "GitHub" usage block (login, token permissions); SKILL.md: `clt github` recipes (`GET repos/<o>/<r>/pulls`, releases list).
- [ ] **Step 6: Commit** "Add clt github, a GitHub API client".

### Task 4: `clt github protect`

**Files:**
- Modify: `src/cli_tools/github.py`
- Test: `tests/test_github.py`

**Interfaces:**
- Consumes: `GitHub.call`, `core.require_writes`.
- Produces: `RULESETS` (exact payloads below); `cmd_protect(gh, args)` for `clt github protect OWNER/REPO [--dry-run]`.

`RULESETS` (both `"enforcement": "active"`, `"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]`):
- `{"name": "main", "target": "branch", "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}, "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}]}`
- `{"name": "v* tags", "target": "tag", "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}}, "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]}`

- [ ] **Step 1: Write the failing tests** (fake `GitHub.call` recording `(method, path, body)`):
  - no rulesets (`GET repos/o/r/rulesets` → `[]`): two `POST repos/o/r/rulesets` with exactly `RULESETS[0]` and `RULESETS[1]`.
  - existing `[{"id": 11, "name": "main"}, {"id": 12, "name": "v* tags"}]`: `PUT repos/o/r/rulesets/11` and `/12`, no `POST`.
  - `--dry-run`: prints a JSON list of the would-be requests, sends only the `GET`.
  - `CLT_READONLY=1` without `--dry-run`: `ReadOnlyError`, only the `GET` or nothing is sent.
  - `"not-a-repo"` → `ToolError` mentioning `OWNER/REPO`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `cmd_protect`; prints one line per ruleset (`created`/`updated`).
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** "Add clt github protect for main and v* tags".

### Task 5: Version files and bumps

**Files:**
- Create: `src/cli_tools/versions.py`
- Test: `tests/test_versions.py`

**Interfaces:**
- Produces:
  - `BUMPS = ("fix", "feat", "major")`; `bump(version, kind) -> str` (`fix` → patch, `feat` → minor + patch 0, `major` → major + minor/patch 0).
  - `suggest_bump(subjects) -> str` — `"major"` if any subject contains `BREAKING CHANGE` or matches `^\w+(\(.+\))?!:`; else `"feat"` if any matches `^feat(\(.+\))?:`; else `"fix"`.
  - `class VersionFile`: attributes `path` (`Path`), `kind` (`"python"`, `"dotnet"`, `"plain"`); `read() -> str` (validated `X.Y.Z`, else `ToolError` naming file and value); `write(new_version)` (bytes in, bytes out; only the version text changes).
  - `find_version_file(root, override=None) -> VersionFile` — order and errors as in the spec (`--version-file` / override; `pyproject.toml` `[project] version`, dynamic → error suggesting `--version-file`; `Directory.Build.props` `<Version>`; the single `*.csproj` with `<Version>` or `<VersionPrefix>`, skipping `bin/` and `obj/`; several → error naming them and `Directory.Build.props`; nothing → error listing what was looked for).

- [ ] **Step 1: Write the failing tests** (temp dirs):
  - `bump`: `("1.2.3","fix")→"1.2.4"`, `("1.2.3","feat")→"1.3.0"`, `("1.2.3","major")→"2.0.0"`.
  - `suggest_bump`: `["Fix x"]→"fix"`, `["feat: x","fix: y"]→"feat"`, `["feat(api)!: x"]→"major"`, `["x\n\nBREAKING CHANGE: y"]→"major"`.
  - each detection case of the spec, including precedence of `Directory.Build.props` over a `.csproj`, and `bin/`/`obj/` skipped.
  - **Review Focus:** a `.csproj` with `\r\n` line endings: after `write("1.3.0")` the bytes equal the original with only `1.2.3` → `1.3.0` replaced.
  - **Review Focus:** `version = "1.2"`, `"v1.2.3"` and `"1.2.3-beta"` each raise `ToolError` containing the file name and the value.
- [ ] **Step 2: Run** `PYTHONPATH=src python3 -m unittest tests.test_versions -v` → FAIL.
- [ ] **Step 3: Implement** with `re` on the decoded text; `plain` kind = whole file is the version (trailing newline kept).
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** "Find and bump the version of Python and .NET projects".

### Task 6: Git helpers

**Files:**
- Create: `src/cli_tools/gitrepo.py`
- Modify: `tests/helpers.py` (add `GitRepo`)
- Test: `tests/test_gitrepo.py`

**Interfaces:**
- Produces:
  - `git(root, *args) -> str` — `subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")`; non-zero → `ToolError` with git's stderr; `FileNotFoundError` → `ToolError("git is not installed or not on the PATH")`.
  - `parse_remote(url) -> (host, owner, repo)` for `https://host/o/r(.git)`, `ssh://git@host(:port)/o/r(.git)`, `git@host:o/r(.git)`; anything else → `ToolError`.
  - `default_branch(root) -> str` — `origin/HEAD` target, else `main` if it exists, else `master`.
  - `last_tag(root) -> str or None` — newest `v*` tag reachable from `HEAD` (`git describe --tags --abbrev=0 --match "v[0-9]*"`).
  - `subjects_since(root, tag) -> list[str]` — commit subjects in `tag..HEAD` (all of `HEAD` when `tag` is `None`), oldest first, without `Release …` commits.
  - `remote_tag_exists(root, tag) -> bool` (`git ls-remote --tags origin refs/tags/<tag>`).
- `tests/helpers.GitRepo(TempConf)`: `setUp` makes a bare `origin.git`, a clone `work/` with local `user.name`/`user.email`, one commit on `main` pushed; `commit(subject, files=None)` writes files, commits; `self.root` is `work/`.

- [ ] **Step 1: Write the failing tests:** each `parse_remote` form (and one bad URL); `default_branch == "main"`; `last_tag` is `None` then `"v0.1.0"` after tagging; `subjects_since` order and the `Release` filter; `remote_tag_exists` false then true after pushing a tag; **Review Focus:** with `PATH` set to an empty temp dir, `git(root, "status")` raises `ToolError` whose text is exactly "git is not installed or not on the PATH".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite → PASS (CI runners have `git`).
- [ ] **Step 5: Commit** "Add git helpers for releases".

### Task 7: `clt release` up to the local commit and tag

**Files:**
- Create: `src/cli_tools/release.py`
- Modify: `src/cli_tools/clt.py` (`TOOLS["release"]`)
- Test: `tests/test_release.py`

**Interfaces:**
- Consumes: Tasks 5 and 6; `core.require_writes`, `core.json_mode`, `core.require_terminal`.
- Produces:
  - `load_config(root) -> dict` — `.clt.json` → its `"release"` object, `{}` when absent; invalid JSON → `ToolError`.
  - `test_command(root, config) -> (list, dict)` — argv and env overrides. Interpreter = `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` when present, else `sys.executable`. Python default: `[interp, "-m", "pytest"]` when pytest is configured, else `[interp, "-m", "unittest", "discover"]`; .NET: `["dotnet", "test"]`. `config["test_command"]` / `config["test_env"]` win, and a leading `"python"` in `test_command` is replaced by the interpreter (so the same `.clt.json` works where only `python3` or `py` exists).
  - The version file is `--version-file`, else `config["version_file"]`, else detection.
  - `prepend_changelog(path, version, date, subjects) -> str` — creates the file with `# Changelog\n\n` when missing; inserts `## {version} - {date}\n\n- s1\n- s2\n\n` after the header; returns the section without its heading line; reads and writes UTF-8 explicitly.
  - `plan_release(root, args) -> dict` — every check of spec step 1 plus the computed `version_file`, `old`, `new`, `tag`, `subjects`, `host`, `owner`, `repo`, `branch`; no side effects.
  - `publisher_for(host)` — placeholder that only checks a token exists (full publishing in Task 8): `github.com` → `github.GitHub().load_credentials()`; the Gitea session host → `gitea.Gitea().load_credentials()`; else `ToolError` naming the host.
  - `main(argv, prog="clt release")` with `--bump {fix,feat,major}`, `--yes`, `--no-push`, `--publish`, `--dry-run`, `--skip-tests`, `--version-file PATH`.
- Prompts and test output go to **stderr** (the tests' stdout is redirected there), so `--json` leaves stdout with only the result object.

- [ ] **Step 1: Write the failing tests** (`GitRepo` with a Python layout: `pyproject.toml` version `0.1.0`, tag `v0.1.0`, then commits `feat: add x` and `Fix y`; `release.remote_url(root)` — which returns `git remote get-url origin` — patched to `https://github.com/o/r.git` while pushes still go to the bare `origin`; `publisher_for` patched to accept `github.com`):
  - `--bump feat --yes --no-push --skip-tests`: `pyproject.toml` has `0.2.0`; `CHANGELOG.md` has `## 0.2.0 - <today>` with `- feat: add x` then `- Fix y`; last commit subject `Release 0.2.0` touching only those two files; annotated tag `v0.2.0` at `HEAD`; nothing pushed (`remote_tag_exists` false).
  - `--yes` alone picks the suggestion (`feat` here).
  - interactive: stdin `"\n"` with a fake TTY accepts the suggestion; `"1\n"` gives `0.1.1`.
  - no TTY and no `--bump`/`--yes` → `ToolError` "needs a person".
  - each stopping check leaves the tree and tags untouched: dirty file, untracked file, not on `main`, behind `origin`, local tag exists, **Review Focus:** tag `v0.2.0` pushed to `origin` from a second clone but absent locally.
  - tests: a `.clt.json` with `test_command` `[sys.executable, "-c", "import sys; sys.exit(1)"]` stops the release before any change; with `exit(0)` it continues; `--skip-tests` prints a warning on stderr and does not run it.
  - `--dry-run` prints the plan as JSON (`version_file`, `old`, `new`, `tag`, `changelog`, `host`) and changes nothing; allowed under `CLT_READONLY=1`, while a real run is blocked (exit 4).
  - `--json` success prints one object `{"version": "0.2.0", "tag": "v0.2.0", "version_file": "pyproject.toml", "pushed": false, "release_url": null}`.
  - **Review Focus:** a commit subject `"Añade soporte 🚀"` with the locale forced to ASCII (`PYTHONIOENCODING`/`LC_ALL=C` in a subprocess run of `python -m cli_tools release …`): `CHANGELOG.md` decodes as UTF-8 and contains it.
  - .NET layout: `Directory.Build.props` `<Version>1.0.0</Version>` → `1.0.1` with `--bump fix`.
  - `test_command`: a `.clt.json` `test_command` starting with `"python"` comes back starting with `sys.executable`; a `.venv/bin/python` (or `Scripts/python.exe`) in the repo is preferred; `pytest.ini` present → `-m pytest`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `release.py`; the flow is spec steps 1–6 in order; `--no-push` (or a "no" at the push question) prints `Run clt release --publish to push and publish vX.Y.Z.`
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Commit** "Add clt release: bump, changelog, commit and tag".

### Task 8: Push, publish and resume

**Files:**
- Modify: `src/cli_tools/release.py`
- Test: `tests/test_release.py`

**Interfaces:**
- Consumes: `github.GitHub.call`, `gitea.Gitea.call`, Task 7.
- Produces:
  - `publisher_for(host)` now returns an object with `release_url(owner, repo, tag) -> str or None` (`GET repos/{o}/{r}/releases/tags/{tag}`, 404 → `None`, else `html_url`) and `create_release(owner, repo, tag, body) -> str` (`POST repos/{o}/{r}/releases` with `{"tag_name": tag, "name": tag, "body": body}`, returns `html_url`). Same paths on GitHub and Gitea.
  - `publish(root, plan) -> dict` — push branch if `origin/<branch>` lacks `HEAD`, push tag if `remote_tag_exists` is false, create the release if `release_url` is `None`; returns `{"pushed": True, "release_url": url}`.
  - `--publish` finds the tag: `git describe --exact-match --tags HEAD` matching `v*`, else the newest local `v*` tag; body = that version's section from `CHANGELOG.md`.

- [ ] **Step 1: Write the failing tests** (fake publisher recording calls; the bare `origin`):
  - interactive "y" at the push question: `origin` has the commit and `v0.2.0`; one `create_release` with the changelog section as body; `--json` result has `"pushed": true` and the URL.
  - "n" (or Enter) at the push question: nothing pushed, the resume command printed.
  - resume after the push succeeded but `create_release` raised: `--publish` pushes nothing again and creates the release once.
  - resume when the release already exists: no `create_release` call, prints its URL.
  - `--publish` under `CLT_READONLY=1` → exit 4, nothing pushed.
  - Gitea: remote host equal to the session's host (`GITEA_URL=https://gitea.example.com`, `GITEA_TOKEN=t`) selects the Gitea publisher; another host → `ToolError` naming it.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Docs** — README "Releasing a project" (the flow, `.clt.json`, flags, resume); SKILL.md: agents run `clt release --bump <kind> --yes --no-push --json`, show the result, ask, then `clt release --publish --json`; never `--skip-tests` without the user's go-ahead.
- [ ] **Step 6: Commit** "Push and publish releases on GitHub or Gitea, and resume".

### Task 9: `clt update`

**Files:**
- Create: `src/cli_tools/update.py`
- Modify: `src/cli_tools/clt.py` (`TOOLS["update"]`), `README.md`, `skills/clt/SKILL.md`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: `core.json_request` (no token), `github.HEADERS`, `cli_tools.__version__`, `core.require_writes`.
- Produces:
  - `REPO = "JoseAlmando/cli-tools"`; `released_versions() -> list[str]` (`GET https://api.github.com/repos/{REPO}/releases`, `tag_name` without `v`, newest first by numeric tuple); none → `ToolError("No releases of JoseAlmando/cli-tools yet")`.
  - `install_info() -> {"editable": bool, "path": str or None}` from `importlib.metadata.distribution("cli-tools").read_text("direct_url.json")` (PEP 610).
  - `install_command(version) -> list[str]` — `["uv", "tool", "install", "--force", f"git+https://github.com/{REPO}@v{version}"]`.
  - `main(argv, prog="clt update")` with optional `VERSION` (`0.2.0` or `v0.2.0`) and `--check`.

- [ ] **Step 1: Write the failing tests** (`core.send` and `subprocess` patched; never runs `uv`):
  - `--check` prints installed and latest; `--json` gives `{"installed", "latest", "target": null, "action": "check"}`; allowed under `CLT_READONLY=1`.
  - an editable install (fake `direct_url.json` with `"dir_info": {"editable": true}`, `"url": "file:///src/cli-tools"`) prints the development-install message with the path and runs nothing.
  - `update 0.1.0` when `0.2.0` is installed builds `install_command("0.1.0")` (downgrade allowed); unknown `9.9.9` → `ToolError` listing the available versions.
  - already on the target → "already on 0.2.0", runs nothing.
  - `uv` missing (`shutil.which` → `None`) → `ToolError` containing the `uv tool install …` and `pipx install --force git+https://github.com/JoseAlmando/cli-tools@v0.2.0` commands.
  - on Windows (`os.name` patched to `"nt"`): `subprocess.Popen` with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and the message that it continues in the background; elsewhere `subprocess.run(..., check=False)` and its exit code returned.
  - `CLT_READONLY=1` without `--check` → exit 4.
  - no `Authorization` header is ever sent.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite → PASS.
- [ ] **Step 5: Docs** — README "Installing from GitHub" (`uv tool install git+https://github.com/JoseAlmando/cli-tools`) and "Updating"; SKILL.md: `update` only with the user's go-ahead.
- [ ] **Step 6: Commit** "Add clt update to move to any released version".

### Task 10: Wire this repo

**Files:**
- Create: `.clt.json`
- Modify: `.github/workflows/tests.yml`, `README.md` (Development table)
- Test: CI run, `clt release --dry-run` locally

- [ ] **Step 1:** `.clt.json`: `{"release": {"test_command": ["python", "-m", "unittest", "discover", "-s", "tests", "-t", "."], "test_env": {"PYTHONPATH": "src"}}}` (the leading `python` is replaced by the interpreter, Task 7).
- [ ] **Step 2:** CI: add `astral-sh/setup-uv@v6` and a step that runs `uv tool install .` and `clt --version` (the uv-installed `clt.exe` starts on Windows). Keep the `pip install .` smoke test.
- [ ] **Step 3: Run** `clt release --dry-run --bump feat` here → plan shows `0.1.0 → 0.2.0`, `pyproject.toml`, the changelog with every commit since the first one, host `github.com`. Changes nothing (`git status` clean).
- [ ] **Step 4:** Full suite, sensitive scan, commit "Configure clt release for this repo".

### After the plan (each step needs the owner's explicit OK)

1. Push the branch; watch CI.
2. `clt github login` (owner types the token) and `clt github protect JoseAlmando/cli-tools --dry-run`, then for real.
3. First release: `clt release --bump feat --yes --no-push`, show it, then `clt release --publish`.
4. Add to CI, now that `v0.2.0` exists: `clt update --check`; and on Windows, install `git+…@v0.2.0` with `uv`, run `clt update 0.2.0` (reinstall over the running `clt.exe`), wait for the background process, and check `clt --version` prints `clt 0.2.0`.
