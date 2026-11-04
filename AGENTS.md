# Instructions for AI coding agents

This file is for agents that **develop** clt. To **use** clt (call Taiga, Gitea, GitHub, Jira or Azure DevOps from a task), read
[`skills/clt/SKILL.md`](skills/clt/SKILL.md) instead.

## The project

clt is a command-line client for the Taiga, Gitea, GitHub, Jira Cloud and Azure DevOps Services APIs, plus `clt release` (version, changelog, tag,
publish) and `clt update` (self-update). It is a Python package with **no runtime dependencies**.

- Layout and responsibilities: [`docs/architecture.md`](docs/architecture.md). Read it before changing code.
  `core/` knows no service; each API client is a package under `tools/`; clt's own commands live in `commands/`;
  `registry.py` lists them and clt imports only the one that runs. No tool imports another tool.
- Designs and implementation plans: `docs/superpowers/specs/` and `docs/superpowers/plans/`.
- Contribution rules (commits, branches, pull requests): [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Rules that are not negotiable

1. **Standard library only** at runtime, **Python 3.9+**. Development tools (Ruff) are dev-only.
2. **Works on Windows.** The `clt` console script is `clt.exe`; no `chmod` there; subprocesses take argument lists, never
   `shell=True`; no bash-only scripts in the product (`scripts/` are Python).
3. **English** in code, help, messages, comments, docs, tests and commit messages.
4. **Never print, log or commit tokens.** Never read, list or write anything under `~/.config` — the owner's real
   sessions live there. Tests use temporary folders (`tests/helpers.TempConf`) and a fake `cli_tools.core.http.send`;
   they never touch the network.
5. **Against real servers, only reads**, with `--json --read-only`. Writes (POST, PUT, PATCH, DELETE, releases,
   rulesets) only when the owner asked for that specific change.
6. **Nothing private in the repo**: no employer, client or project names, internal hosts, project ids, local paths or
   the login provider used at work. Run `python scripts/check_private_words.py` before every commit; it must print
   `OK`. The list it uses is in `.git/info/private-words`, local to each clone and never pushed — never copy its
   contents into the repo, an issue, a PR or a commit message. Use `taiga.example.com`, `gitea.example.com` in tests.
7. **Never push, merge, tag, publish, or change GitHub settings without the owner's explicit OK** for that action.
8. Keep behaviour backward compatible: commands, flags, environment variables, output formats, exit codes
   (0 ok, 1 error, 2 usage, 3 not logged in, 4 read-only) and credential locations. `tests/test_contracts.py` pins them.

## Checks before every commit

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .                              # all tests
PYTHONPATH=src uv run --no-project --python 3.9 python -m unittest discover -s tests -t .  # oldest Python
uvx ruff check .                                                                       # lint
python scripts/check_private_words.py                                                  # private words
```

On Windows use `python` and set `PYTHONPATH` the PowerShell way. New behaviour needs a test that failed first.

## How work flows

1. Every piece of work has an **issue**, and its card is on the public
   [project board](https://github.com/users/JoseAlmando/projects/3) (Todo → In Progress → Done). New ideas or bugs
   found along the way become new issues on the board.
2. Work on a branch named per **Conventional Branch** (`feature/…`, `bugfix/…`, `hotfix/…`, `release/…`, `chore/…`),
   never directly on `main`.
3. Commits follow **Conventional Commits** (`feat(github): …`, `fix(release): …`, `docs: …`, `test: …`, `ci: …`,
   `chore: …`); `scripts/check_conventions.py` checks them, locally as a `commit-msg` hook and in CI. No attribution
   lines.
4. Open a pull request with the template's checklist and `Closes #N`; move the card to *In Progress*. With the owner's
   OK, merge with **rebase** once CI is green (tests on Linux, macOS and Windows, lint, conventions). The issue closes
   and the card moves to *Done*.
5. `main` and `v*` tags are protected: only the repository admin can update them.

## Keeping things in sync

When a user or an agent would see something different, update in the same pull request: `--help` text, `README.md`,
`docs/architecture.md`, `skills/clt/SKILL.md`, and the contract tests if a contract deliberately changes.

## Adding a tool or a command

Follow "Adding a tool" in [`docs/architecture.md`](docs/architecture.md): a package under `tools/<name>/` with
`client.py` and `cli.py` (and `auth.py` only for a real login flow), one line in `registry.py`, tests against the fake
server. `clt` is the only console script: no tool gets a command of its own.

## Releasing

`clt release` cuts a version (tests, bump question, `CHANGELOG.md`, `chore(release): X.Y.Z` commit, `vX.Y.Z` tag). As an
agent: `clt release --json --bump <fix|feat|major> --yes --no-push`, show the result to the owner, and only after their
OK run `clt release --json --publish`. Never `--skip-tests` unless the owner asks.
