# Contributing

Thanks for helping with `clt`. This page covers setting up a checkout, and the conventions for commit messages and branch names that CI checks on every pull request.

## Setting up

You need Python 3.9 or later, git and [uv](https://docs.astral.sh/uv/). From a clone:

```bash
uv tool install -e .        # clt now runs this checkout; changes take effect right away
python scripts/check_conventions.py install-hook   # checks each commit message as you commit
```

Before you open a pull request:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .   # python on Windows
uvx ruff@0.17.3 check .                                            # the linter
```

The code uses the standard library only and runs on Python 3.9 and later, on Linux, macOS and Windows. The tests never reach the network. [`docs/architecture.md`](docs/architecture.md) explains how the code is organized.

<!-- More sections (issues, the community) go here, above the conventions. -->

## Commit messages

Commits follow [Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>)!: <description>

<body>

<footers>
```

- **type** is one of `feat` (a new feature), `fix` (a bug fix), `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore` or `revert`, in lowercase.
- **scope** is optional: the part of the code, such as `release`, `gitea` or `core`.
- **`!`** marks a breaking change; so does a `BREAKING CHANGE: <what changed>` footer (in uppercase).
- **description** follows `: ` (one space) and says what the commit does.
- A blank line goes between the header and the body. The body and the footers are optional.

```
feat(release): group the changelog by commit type
fix(gitea): renew the token before it expires
docs: explain the commit-msg hook
refactor(core)!: pass the context explicitly

feat(api): add --max-items

BREAKING CHANGE: --old-flag is gone; use --new-flag.
```

Messages that git writes itself are accepted as they are: `Merge ...`, `Revert "..."`, and `fixup! ...` / `squash! ...` / `amend! ...` for `git rebase --autosquash`. Lines starting with `#` (commit templates) are ignored.

The type matters beyond style: `clt release` suggests a new major version for a breaking change, a minor one for `feat`, and a patch otherwise, and it groups the changelog under *Breaking changes*, *Features*, *Fixes* and *Other*. Its own release commits are `chore(release): X.Y.Z`.

## Branch names

Branches follow [Conventional Branch](https://conventional-branch.github.io/): `main`, `master` and `develop` as they are, and everything else `<type>/<description>`.

- **type** is one of `feature` (or `feat`), `bugfix` (or `fix`), `hotfix`, `release` or `chore`.
- **description** has lowercase letters, digits and hyphens, with no hyphen at the start or the end and no two in a row. Dots are allowed only in `release/` branches, for the version.

```
feature/add-login
feat/conventional-commits
fix/issue-123-header
hotfix/token-leak
release/v1.2.0
chore/update-ruff
```

## Checking your work

`scripts/check_conventions.py` checks both, with the standard library only:

```bash
python scripts/check_conventions.py install-hook             # the commit-msg hook (respects core.hooksPath)
python scripts/check_conventions.py branch                   # the current branch, or: branch feature/add-login
python scripts/check_conventions.py range origin/main..HEAD  # every commit on your branch
```

The hook rejects a commit whose message does not follow the rules, says why and shows the format; your message stays in `.git/COMMIT_EDITMSG`. `install-hook` never replaces a different `commit-msg` hook unless you pass `--force` (it keeps the old one as `commit-msg.backup`). To fix messages already committed, reword them with `git rebase -i origin/main`.

Every pull request runs the same checks in CI (the `conventions` job in [`.github/workflows/tests.yml`](.github/workflows/tests.yml)): the branch name, and each commit the pull request adds.
