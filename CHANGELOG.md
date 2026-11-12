# Changelog

## 0.14.1 - 2026-11-12

### Fixes

- fix(api): refuse dot segments in every tool, and make --all stop and fail where it should

## 0.14.0 - 2026-11-12

### Features

- feat: clt taiga issue reads any ref, and clt gitea pr create opens a pull request

### Other

- test: build the release tests' repository once per class

## 0.13.0 - 2026-11-12

### Features

- feat: clearer api output, token_source in me, and the commit in clt --version

## 0.12.0 - 2026-11-12

### Features

- feat(jenkins): log --tail N prints only the last lines of a build console

## 0.11.1 - 2026-11-12

### Fixes

- fix(update): reinstall with the installer that installed clt, and log the Windows update
- fix(update): on Windows, start the installer only after clt has exited
- fix(update): pass the log file on to the Windows installer

### Other

- ci: run a real clt update on the installed clt
- ci: show the update log and the version the clt update step ends with

## 0.11.0 - 2026-11-12

### Features

- feat(core): usage errors exit with 2 and are a JSON line with --json

### Fixes

- fix: refresh says a token from the environment is never renewed

### Other

- refactor(github): drop the login menu's branch for a missing OAuth app

## 0.10.1 - 2026-11-12

### Fixes

- fix(release): check what can stop a release before the tests, and agree on breaking changes
- fix(skill): retry the rename when Windows holds the new folder for a moment

## 0.10.0 - 2026-11-12

### Features

- feat(jenkins): client for the Jenkins API with jobs, builds, queue and console logs

### Fixes

- fix(jenkins): print the header of an empty table, and pin the me output
- fix(release): --publish --dry-run prints what is missing and changes nothing

### Other

- ci: check the Jenkins reference in the installed skill
- docs: clt is the only command a checkout installs

## 0.9.1 - 2026-11-10

### Fixes

- fix(output): an empty table prints its header and says "No items." on stderr
- fix(gitea): mine also lists the pull requests waiting for your review and the ones you opened

### Other

- docs(skill): Gitea recipes for pull request files, reviews, replies and merges

## 0.9.0 - 2026-11-06

### Features

- feat(azdo): wiql and mine commands, --table, and a cheaper skill
- feat(skill): cut the skill to half its tokens

### Other

- Merge pull request #63 from JoseAlmando/feature/azdo-wiql-and-cheaper-skill

## 0.8.0 - 2026-11-05

### Features

- feat(skill): recommend installing the skill for the agents found without it

## 0.7.0 - 2026-11-04

### Features

- feat(skill): a task table with ready commands, and GitHub recipes

## 0.6.1 - 2026-11-04

### Fixes

- fix(release): read single-quoted and BOM-prefixed versions

### Other

- docs(release): drop the py launcher claim from a comment
- docs(github): describe the browser login next to the token
- ci: pin Ruff to 0.17.3

## 0.6.0 - 2026-11-04

### Features

- feat(skill): install clt's skill for Claude Code, Codex and Gemini CLI
- feat(update): refresh the installed skill copies after an update
- feat(mcp): connect the official Jira and Azure DevOps MCP servers to an agent
- feat(update): show what each newer release brings

### Fixes

- fix(skill,mcp): safe swap of the skill copy, clear agent failures
- fix(skill,mcp): findings from the first real run

### Other

- docs: plan for the agent setup commands
- refactor: ship the skill inside the package
- docs: MCP setup and skill install
- ci: check the packaged skill installs
- docs: Gemini's MCP sign-in step, and the skill's new home in the PR checklist
- test(skill): skip the link tests where links cannot be made

## 0.5.0 - 2026-11-04

### Breaking changes

- feat!: clt as the only command, with short aliases

### Features

- feat(azdo): Azure DevOps client with PAT, api-version and JSON Patch
- feat(azdo): follow continuation tokens and $top/$skip pages
- feat(azdo): login, logout, me, token and api commands
- feat(azdo): clt ad and the short command names

### Fixes

- fix(azdo): stop on repeated pages, quote $ in help, clear bad-token message
- fix(azdo): logout keeps the organization, and say which token wins

### Other

- docs: clt as the only command, and its short names
- ci: run the smoke test through clt and its short names
- docs: uv tool install -e installs only clt
- docs: plan for the Azure DevOps client
- refactor(http): share the typed-path encoding
- docs: Azure DevOps in the README, architecture and the skill's recipes
- docs(azdo): recipes from the first real run
- docs(azdo): keep the recipes under the size limit

## 0.4.0 - 2026-11-04

### Features

- feat(jira): client with classic and scoped API tokens
- feat(jira): login, me, token and api commands
- feat(jira): issue command with ADF turned into text

### Fixes

- fix(jira): encode the path typed by the user and refuse dot segments
- fix(jira): keep ADF formatting in issue text
- fix(api): stop paging once --max-items has enough

### Other

- refactor(api): let tools page through the body and filter lists wrapped in an object
- docs: Jira in the README, the architecture and the skill

## 0.3.0 - 2026-11-03

### Fixes

- fix(release): talk to origin once to check and once to push
- fix: read the version from pyproject.toml when running from the source tree

### Other

- chore: add instructions for AI coding agents and a private-word check
- Merge branch 'main' into chore/agent-instructions
- test: accept Windows path separators in the private-word list message
- Merge pull request #31 from chore/agent-instructions

## 0.2.0 - 2026-11-03

### Features

- feat(release): commit and tag as chore(release): X.Y.Z, and group the changelog by type
- feat(conventions): add a checker for commit messages and branch names
- feat(github): renew expiring tokens from the browser login

### Other

- Initial commit
- Add taiga and gitea API command-line clients
- Package taiga and gitea with a shared core and the clt command
- License under MIT
- Translate messages, help and docs to English
- Make the tools safe to drive from scripts and AI agents: --json errors, exit codes, --read-only, --dry-run, --fields, --all, env tokens, and a skill
- Block login, refresh, logout and token in read-only mode
- Run the tests on Linux, macOS and Windows in GitHub Actions
- Print me as one JSON object with --json
- Run Python 3.9 in CI only where setup-python has it
- Add the design for versions, releases and self-update
- Run the project's tests before a release, in the design
- Add the implementation plan for versions, releases and self-update
- Read the version from pyproject.toml only
- Let each tool choose its API prefix
- Add clt github, a GitHub API client
- Add clt github protect for main and v* tags
- Find and bump the version of Python and .NET projects
- Add git helpers for releases
- Add clt release: bump, changelog, commit and tag
- Push and publish releases on GitHub or Gitea, and resume
- Add clt update to move to any released version
- Configure clt release for this repo
- Close the test runner's output pipe, and document .clt.json
- Fix release edge cases found in review
- Split clt into a shared core, tool packages and a lazy registry
- Log in to GitHub from the browser
- Make two tests pass on Windows runners
- ci: check the branch name and commit messages of pull requests
- docs: add CONTRIBUTING.md with the commit and branch conventions
- ci: ask uv for the matrix Python version instead of a path
- chore: add issue forms, a pull request template, a security policy and a code of conduct
- docs(github): describe the project scope and token renewal
- Merge pull request #22 from feature/conventional-commits
