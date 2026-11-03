# Changelog

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
