# Versions, releases and self-update for clt

Status: draft for approval · 2026-11-02

## Why

`cli-tools` is installed on more than one machine (Linux for development, Windows), and there is no way to
know which version a machine runs or to move it to another one. The owner also wants the same tool to cut
versioned releases of *other* projects (Python and .NET for now), hosted on GitHub or on a Gitea server,
with one command and sensible defaults: answer one question, press Enter, done.

## Goals

1. **`clt release`**: run inside a local clone of any Python or .NET project hosted on GitHub or Gitea. It
   bumps the version in the project's single version file, updates `CHANGELOG.md`, commits, tags
   `vX.Y.Z`, and (after an explicit yes) pushes and creates the release on the host.
2. **`clt github`**: a GitHub API client with the same shape and flags as `clt taiga` and `clt gitea`
   (`--json`, `--read-only`, `api`, `--fields`, `--all`, `--dry-run`, exit codes). `clt release` uses it.
3. **`clt update [VERSION]`**: move an installed `clt` to the latest release or to a given one (downgrades
   included). `clt update --check` only reports.
4. **`clt github protect OWNER/REPO`**: rulesets so that only the owner can update `main` and create or
   move `v*` tags. Applied to this repo once the owner approves.
5. This repo releases itself with `clt release`; the first release is `0.2.0`.

## Non-goals (for now)

- Project types other than Python and .NET (the design must make adding one a self-contained change).
- Pre-releases (`-beta`, `-rc`), build metadata, calendar versions.
- Publishing packages to PyPI or NuGet.
- Hosts other than github.com and the Gitea server of the current `clt gitea` session (no GitHub
  Enterprise, GitLab, Bitbucket).
- Branch protection on Gitea (`clt gitea protect`).
- Automatic update checks: nothing contacts the network unless the user runs a network command.

## Anti-goals (it fails even if it "works")

- Any new runtime dependency, or anything that only works on Linux (bash scripts, `chmod` on Windows).
- A token printed, logged, put in a URL, or sent to a host other than its own API.
- Pushing, tagging on the remote, or publishing without an explicit yes (interactive) or `--publish`.
- A release that leaves the repo half-done with no way to resume (pushed but not published, etc.).
- The version number living in two places in this repo.
- Anything about the owner's employer in the repo (names, hosts, project ids, the login provider).

## Constraints (from the project rules)

- Python 3.9+, standard library only. `git` is required on the PATH for `clt release` (it shells out to it
  with `subprocess`, never through a shell).
- Works on Windows: console scripts are `.exe`, no `chmod` there, ANSI enabled.
- English in code, help, messages, docs and tests.
- `unittest` tests for every change, without network; the full suite passes before each commit; CI runs
  it on Linux, macOS and Windows.
- Credentials files `0600` in a `0700` folder, written atomically (reuse `core.write_private_json`).
- Commits made by the tool use the repo's own git config (author, email); no attribution lines.

## Decided behaviour

### Version source

- **This repo:** `pyproject.toml` `[project] version` is the only place. `cli_tools.__version__` reads the
  installed version with `importlib.metadata.version("cli-tools")`; when the package is not installed
  (running from `src/` with `PYTHONPATH`), it falls back to reading `pyproject.toml` next to the package
  with a regular expression, and to `"0+unknown"` if that fails too.
- **Python projects:** the `version = "X.Y.Z"` line inside `[project]` of `pyproject.toml`. A project
  whose version is dynamic gets a clear error suggesting `--version-file`.
- **.NET projects:** `<Version>` in `Directory.Build.props` at the repo root; otherwise the single
  `*.csproj` (searched recursively, skipping `bin/` and `obj/`) that has `<Version>` (or `<VersionPrefix>`).
  More than one `.csproj` with its own version is an error that explains how to move it to
  `Directory.Build.props`.
- **`--version-file PATH`:** use that file. Supported formats: `pyproject.toml`, `*.props`/`*.csproj`, and a
  plain file whose whole content is the version (e.g. `VERSION`).
- Edits change only the version text and keep the rest of the file byte for byte (line endings included).
- Each project type is a small "detector" (find the file, read the version, write the version); adding a
  type later is adding a detector.

### The bump question

```
Project: Python (pyproject.toml) · current version 0.1.0 · 7 commits since v0.1.0
What is this release?
  [1] fix          0.1.1
  [2] feat         0.2.0
  [3] new version  1.0.0
Choice [2]:
```

- Default suggestion: `3` if any commit since the last tag contains `BREAKING CHANGE` or has a `type!:`
  prefix; else `2` if any starts with `feat`; else `1`. Enter accepts it.
- Without a terminal and without `--bump`, it fails at once (same rule as the logins).

### `clt release` flow

1. **Checks, before touching anything:** inside a git repo; clean working tree (untracked files count as
   changes); on the remote's default
   branch (from `origin/HEAD`, else `main`, else `master`); `git fetch` shows the branch is not behind
   `origin`; `origin` (HTTPS, `ssh://` or `git@host:owner/repo` form) is github.com or the current Gitea session's server; the tag does not exist locally
   or on the remote; a token for that host is available. Any failure stops with a message saying what to
   do.
2. Show the summary and ask the bump question.
3. Write the new version to the version file.
4. Update `CHANGELOG.md` at the repo root (created if missing, with a one-line header): a new section at
   the top, `## X.Y.Z - YYYY-MM-DD`, with one bullet per commit subject since the previous tag (oldest
   first, skipping earlier `Release …` commits). With no previous tag, all commits.
5. Commit both files as `Release X.Y.Z` and create an annotated tag `vX.Y.Z`.
6. Ask `Push and publish vX.Y.Z to <host>/<owner>/<repo>? [y/N]`. On yes: push the branch and the tag,
   then create the release on the host, named `vX.Y.Z`, with the new changelog section as its notes.
   On no: stop and print the command that finishes later (`clt release --publish`).

Flags:

| Flag | Effect |
|---|---|
| `--bump fix\|feat\|major` | Skip the question. |
| `--yes` | Accept the default bump if `--bump` is not given; never asks. |
| `--no-push` | Stop after the local commit and tag (what an agent runs before asking the user). |
| `--publish` | Resume: push what is not pushed yet and create the release if missing. Idempotent. |
| `--dry-run` | Print the plan (version file, old → new version, changelog section, commands) and change nothing. |
| `--version-file PATH` | See above. |

- `--read-only` blocks `clt release` (exit 4), except `--dry-run`.
- `--json`: errors as one JSON line (as everywhere); on success, one JSON object on stdout
  (`version`, `tag`, `version_file`, `pushed`, `release_url`).
- **Resuming:** `--publish` works out the state from git and the host: tag pushed or not, release exists
  or not. Each step is skipped when already done, so a failure halfway is fixed by running it again.

### `clt github`

- Same structure as `taiga.py`/`gitea.py`: `login`, `logout`, `me`, `token`, `api` (and the
  `METHOD path` shortcut), with every global flag and exit code.
- Base URL is fixed: `https://api.github.com`. Paths are relative to it (no `/api/v1/` prefix), so `core`
  needs the API prefix to be per tool (`api_url`, the error text, and `next_page_path`, which must keep
  only the path of a `Link: rel="next"` URL on the same host).
- **Login:** a fine-grained personal access token, read with `getpass` or `--token-stdin`, checked with
  `GET user`, saved to `~/.config/github/credentials.json` (`GITHUB_CONF_DIR` to change it). `GITHUB_TOKEN`
  (then `GH_TOKEN`) overrides the saved session, as `TAIGA_TOKEN`/`GITEA_TOKEN` do. No renewal (PATs do not
  refresh); a 401 says to log in again.
- Sends `Accept: application/vnd.github+json` and `X-GitHub-Api-Version`.
- `--read-only` blocks `login`, `logout`, `token` and every non-`GET`, as for the other tools.
- **`clt github protect OWNER/REPO`:** creates or updates two rulesets, both with the repository admin
  role as the only bypass actor:
  - `main`: blocks deletion, force pushes and updates (`deletion`, `non_fast_forward`, `update`).
  - `v* tags`: blocks creation, update and deletion of `refs/tags/v*`.
  `--dry-run` prints the JSON it would send. Running it twice changes nothing the second time.
- The help says which token permissions are needed: *Contents: read and write* for releases, and
  *Administration: read and write* for `protect`.

### Publishing on Gitea

- Uses the current `clt gitea` session (its token renewal included); the remote must be on that server.
- Release created with `POST repos/{owner}/{repo}/releases` (`tag_name`, `name`, `body`).
- Nothing Gitea-specific beyond that; no server names in code, tests or docs (use `gitea.example.com`).

### `clt update`

- `clt update`: the latest release of `JoseAlmando/cli-tools` (GitHub's public API, no token).
  `clt update 0.2.0` (or `v0.2.0`): that release, older ones included; an unknown version lists the
  available ones. `clt update --check`: prints installed and latest, installs nothing.
- Installs with `uv tool install --force "git+https://github.com/JoseAlmando/cli-tools@vX.Y.Z"`. If `uv` is
  not on the PATH, it prints that command (and the `pipx` equivalent) and exits with an error.
- **Development installs:** if the installed distribution is editable (PEP 610 `direct_url.json` with
  `dir_info.editable`), it prints that this is a development install from `<path>` that updates with
  `git pull`, and changes nothing.
- **Windows:** a running `clt.exe` may not be replaceable. `update` starts the `uv` command as a separate
  process and exits right away, telling the user it continues in the background; CI checks that the new
  version is installed afterwards.
- `--read-only` blocks `update` (exit 4), except `--check`. `--json` gives
  `{"installed", "latest", "target", "action"}`.

### This repo

- Released with `clt release`; the first release is `0.2.0`. No release workflow in GitHub Actions.
- README: installing from GitHub (`uv tool install git+https://github.com/JoseAlmando/cli-tools`),
  updating, cutting a release, and the GitHub token permissions.
- `skills/clt/SKILL.md`: how an agent cuts a release (`--bump … --no-push`, show the result, ask, then
  `--publish`) and that `update`/`protect` need the user's go-ahead.
- Once approved by the owner: `clt github protect JoseAlmando/cli-tools`.

## Testing

- `clt release`: tests build a temporary git repo with a bare repo as `origin` (no network), for Python and
  .NET layouts: detection, the bump suggestion, file edits keeping line endings, the changelog section,
  commit and tag, every check that stops the flow, and `--publish` resuming after each possible failure
  point (the host API is faked at the HTTP-call boundary).
- `clt github`: request building, pagination limited to the same host, token never in output, env tokens,
  read-only blocks, and the `protect` payloads.
- `clt update`: version listing and choice, the editable-install message, the `uv` command built, without
  running `uv` or touching the network.
- CI smoke test: install the package from the checkout with `uv tool install`, run `clt update --check`
  against the real GitHub API (read-only, no token), and on Windows check that an update replaces
  `clt.exe`. The update steps can only run end to end once `v0.2.0` exists; until then they are skipped.

## Left to the builder

- Module layout (e.g. `github.py`, `release.py`, `update.py`) and how `core` is generalised for a per-tool
  API prefix, as long as the existing tools behave exactly as before.
- Exact wording of messages and help, within the style of the existing ones.
- How the Windows background update is started and how CI checks it.
- Order of the work, with one commit (and suite run) per coherent step.

## Calls made by the designer, not by the owner

- Logic in Python inside `clt`, not shell scripts (Windows has no bash by default).
- GitHub login by personal access token, not OAuth device flow (would need a registered OAuth app).
- `--yes` accepts the suggested bump; the suggestion follows Conventional Commits prefixes when present.
- `CHANGELOG.md` format: plain `## X.Y.Z - date` sections with commit subjects; no categories.
- The first release of this repo is `0.2.0`.
- Untracked files block a release; remote URLs in HTTPS, `ssh://` and scp-like forms are accepted.
- CI may use the `astral-sh/setup-uv` action for the `uv tool install` smoke test (CI only, not a runtime dependency).
- `protect` only for GitHub; Gitea protection is out of scope.
- `clt update` installs from git tags with `uv`; no PyPI.
