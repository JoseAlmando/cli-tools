# cli-tools

Command-line tools for the Taiga, Gitea, GitHub, Jira Cloud, Azure DevOps Services and Jenkins APIs, gathered under a single command, `clt`. They only use the Python standard library, with no dependencies.

| Command | What it does |
|---|---|
| `clt` | Groups the tools: `clt taiga …`, `clt gitea …`, `clt github …`, `clt jira …`, `clt azdo …`, `clt jenkins …`. |
| `clt taiga` | Taiga API client: username and password login, automatic token renewal, and `GET/POST/PUT/PATCH/DELETE` calls to `/api/v1/`. |
| `clt gitea` | Gitea API client: OAuth2 login with PKCE in the browser (including through a Gitea external login source, such as an OIDC provider), automatic token renewal, and calls to `/api/v1/`. |
| `clt github` | GitHub API client: login in the browser or with a token, and calls to `https://api.github.com/`. |
| `clt jira` | Jira Cloud API client: login with an Atlassian API token (classic or with scopes), calls to `/rest/api/3/`, and `clt jira issue KEY` to read a ticket as plain text. |
| `clt azdo` | Azure DevOps Services API client: login with a personal access token, calls to `https://dev.azure.com/<org>/` with `api-version` added for you. |
| `clt jenkins` | Jenkins Remote Access API client: anonymous or API token access, job and queue lists, build details and console logs. |

## Installation

Requires Python 3.9 or later. It installs one command, `clt`, which groups the tools (on Windows, `clt.exe`).

Short names save typing: `clt t` is `clt taiga`, `clt gt` is `clt gitea`, `clt gh` is `clt github`, `clt j` is `clt jira` and `clt ad` is `clt azdo`; and `lg` is `login`, `rfh` is `refresh`, `lo` is `logout`, `tk` is `token` and `i` is Jira's `issue`. `clt --help` lists them.

From GitHub, the latest release (or a given one with `@vX.Y.Z`):

```bash
uv tool install git+https://github.com/JoseAlmando/cli-tools          # or: pipx install git+https://…
clt skill install                                                     # teach your AI agents to use clt (optional)
```

`clt --help` and `clt update` remind you of the second step when they find Claude Code, Codex or Gemini CLI without
clt's skill (see [For scripts and AI agents](#for-scripts-and-ai-agents)).

To move to another version later:

```bash
clt update --check          # installed and latest version, and what the newer ones bring
clt update                  # the latest release
clt update 0.2.0            # a given one, older ones included
```

Before installing a newer version, `clt update` lists what each release in between brings (breaking changes, features
and fixes, from the release notes). It reinstalls with the tool that installed clt, `uv tool` or `pipx`, so there is
never a second copy; for an install made with plain `pip` it prints the command to run instead.

On Windows the update runs in the background once `clt update` has exited (Windows cannot replace a running
`clt.exe`), and writes its output to `clt-update.log` in the temporary folder (the message gives the path). Wait until
that file ends with `Installed …` before running `clt` again: running it meanwhile can interrupt the update. To uninstall: `uv tool uninstall cli-tools`
(or `pipx uninstall cli-tools`).

For development, from a clone (changes take effect right away; `clt update` then only reminds you to `git pull`):

```bash
uv tool install -e .        # or: pipx install -e .
```

Without installing, from the repo:

```bash
PYTHONPATH=src python3 -m cli_tools taiga me   # python on Windows
```

## Usage

```bash
clt taiga login --url https://taiga.example.com # the first time; the server is saved
clt taiga me                                    # same as: clt t me
clt taiga GET "userstories?project=<id>"
clt taiga mine                                  # your open stories, tasks and issues, one line each
clt taiga issue 12 --project web                # one of them by its ref (or set TAIGA_PROJECT)

clt gitea login --url https://gitea.example.com # opens the browser
clt gitea login --source <source>               # go straight to a Gitea external login source
clt gitea GET "repos/<owner>/<repo>/pulls?state=open"
clt gitea pr create --repo <owner>/<repo> --head <branch> --title "<title>" --body-file pr.md --dry-run
clt gitea mine                                  # assigned to you, waiting for your review, or opened by you

export TAIGA_TOKEN="$(clt taiga token)"         # for other scripts

clt github login                                # asks: authorize in the browser, create a token, or paste one
clt github GET "repos/<owner>/<repo>/releases" --fields tag_name,html_url
clt github protect <owner>/<repo> --dry-run      # rulesets: only the repo admin updates main and v* tags
clt github mine                                 # issues and pull requests assigned to you

clt jira login --url https://<site>.atlassian.net --create-token
clt jira issue PROJ-123                          # summary, description and comments as Markdown
clt jira GET "search/jql?jql=assignee=currentUser()&fields=key,summary,status" --all
clt jira mine                                   # your open tickets

clt azdo login --org <organization> --create-token
clt azdo GET _apis/projects --fields id,name --table
clt azdo mine                                   # your open work items in every project
clt azdo wiql "SELECT [System.Id], [System.Title] FROM WorkItems WHERE [System.AssignedTo] = @Me"
clt azdo GET '<project>/_apis/git/repositories/<repo>/pullrequests?$top=50' --all --fields pullRequestId,title

clt jenkins login --url https://jenkins.example.com --user <username>
clt jenkins --json --read-only jobs "Folder/Subfolder"
clt jenkins --json --read-only build "Folder/Job" lastSuccessfulBuild
clt jenkins --json --read-only log "Folder/Job" 42 --tail 200
clt jenkins --json --read-only queue --table
clt jenkins --json --read-only GET "api/json?tree=jobs[name,color]"
```

`clt github login` offers three ways in:

1. **Authorize in the browser** (`--browser`): it shows a code, opens `github.com/login/device`, and saves the token once you approve. It asks for the `repo` scope, the narrowest an OAuth app can ask for that covers releases and rulesets, and the `project` scope, to manage GitHub Projects boards. GitHub gives this login a token that expires after about 8 hours and a refresh token; `clt` renews the token by itself a little before it expires (or after a 401), and `clt github refresh` renews it now. When GitHub no longer accepts the refresh token (after about 6 months, or if you revoke `clt`), run `clt github login` again.
2. **Create a token in the browser** (`--create-token`): it opens GitHub's page for a fine-grained token with the name `clt`, a 90-day expiry and the permissions already filled in (*Contents: read and write* to publish releases, *Administration: read and write* for `clt github protect`); you generate it and paste it.
3. **Paste a token you already have** (`--token-stdin` for scripts).

`--no-browser` only prints the link. Authorizing in the browser needs a GitHub OAuth app with device flow enabled; `clt` ships the ID of its own, and `GITHUB_CLIENT_ID` sets another.

`clt jira login` asks for your Atlassian email and an API token (`--create-token` opens id.atlassian.com first). Both kinds of token work: a classic one talks to the site, and one with scopes (`read:jira-work`, `write:jira-work`, `read:jira-user`) goes through `api.atlassian.com` with the site's cloud id, which login finds on its own (`--cloud-id` gives it, `--scoped` skips the check). `clt jira issue KEY` prints a ticket's summary, people, links, subtasks, description and last comments, with Jira's rich text turned into plain text.

`clt azdo login` asks for a personal access token (`--create-token` opens the organization's token page and lists the scopes to tick: Work Items, Code, and Project and Team). Paths are relative to `https://dev.azure.com/<org>/`; `clt` adds `api-version=7.1` when a path has none, sends JSON Patch when creating or updating a work item, and follows both kinds of paging (`x-ms-continuationtoken` and `$top`/`$skip`) with `--all`.

`clt jenkins login` checks your username and API token and saves the session for that controller. Set `JENKINS_URL` to use Jenkins' anonymous permissions when you have no saved session or token. Include the controller's context path if it has one, such as `https://jenkins.example.com/ci`; a trailing `/api/` is accepted. Generic calls are relative to the controller, so use `api/json` for the root or `job/Folder/job/Job/api/json` for a nested job. `build` and `log` default to `lastBuild` when you omit the build number.

For a controller that only serves HTTP, opt in for the current PowerShell session:

```powershell
$env:JENKINS_URL = 'http://jenkins.example.com:8080/api/'
$env:JENKINS_ALLOW_HTTP = '1'
clt jenkins --json --read-only jobs --table
```

`clt --help` and each tool's `--help` (`clt taiga --help`, …) list the commands and the environment variables.

## Releasing a project

`clt release` cuts a version of the project in the current folder (a git repo whose `origin` is on github.com or on the server of your `clt gitea` session). Python projects keep their version in `pyproject.toml` (`[project] version`); .NET ones in `Directory.Build.props` or in the single `.csproj` with `<Version>`.

```bash
clt release            # checks, runs the tests, asks fix / feat / new version (Enter takes the suggestion),
                       # updates the version and CHANGELOG.md, commits "chore(release): X.Y.Z", tags vX.Y.Z,
                       # and asks before pushing and publishing the release
clt release --dry-run  # shows what it would do
clt release --publish  # finishes a release that was tagged but not pushed or published (safe to repeat)
clt release --publish --dry-run   # shows what is still missing on origin and on the host, and changes nothing
```

It stops before changing anything if the tree has changes, the branch is not the main one or is behind `origin`, the tag already exists, or the tests fail. The suggestion is `feat` when a commit since the last tag starts with `feat:`, a new major version for a `type!:` header or a `BREAKING CHANGE:` footer, and `fix` otherwise; the changelog uses the same rule. When commits use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) prefixes, the changelog section (also the release's text on GitHub or Gitea) groups them under *Breaking changes*, *Features*, *Fixes* and *Other*; without any, it is a plain list. Merge commits are left out. A changelog keeps its own line endings.

A release contacts `origin` twice: once to check the branch and the tags, and once to push the branch and the tag together (`git push --atomic`), showing git's own messages. With a passphrase-protected SSH key, load it once with `ssh-add` so git does not ask on every push.

| Option | Use |
|---|---|
| `--bump fix\|feat\|major` | Skips the question. |
| `--yes` | Never asks: takes the suggestion and does not push. |
| `--no-push` | Stops after the local commit and tag. |
| `--skip-tests` | Does not run the tests (says so). |
| `--version-file PATH` | The file that holds the version, when it is not found on its own; it must be inside the repo. |

Settings per repo go in an optional `.clt.json`:

```json
{"release": {"test_command": ["python", "-m", "pytest"], "test_env": {"PYTHONPATH": "src"}, "version_file": "VERSION"}}
```

`python` at the start of `test_command` means the project's interpreter (`.venv` when there is one). Without a `test_command`, Python projects run `pytest` (when configured) or `unittest discover`, and .NET ones `dotnet test`.

## For scripts and AI agents

Every tool can run unattended:

- **No prompts.** Without a terminal, `login` fails at once instead of waiting unless you use an option such as `--token-stdin`. See the configuration table for environment variables that let you use an existing token.
- **Machine-readable errors.** `--json` (or `CLT_JSON=1`) prints each error as one JSON line on stderr, with the HTTP status when available, and makes `me` print a JSON object, whose `token_source` names where the token came from: the variable (`TAIGA_TOKEN`, `AZURE_DEVOPS_EXT_PAT`, …) or `credentials_file`. Jenkins reports `username`, `authenticated`, `url` and `token_source` (`null` for anonymous access); `clt jenkins log` always writes plain text. `-v` prints one line per request, so with `--all` it shows every page read. `clt --version` of an install from a branch or a commit adds the commit, such as `0.12.0+d1db646`.
- **Exit codes:** `0` success, `1` error, `2` wrong usage (an unknown tool or wrong arguments; with `--json` it is a JSON line too), `3` not logged in, `4` blocked by read-only mode, `130` interrupted (Ctrl+C, or a question left without an answer).
- **Safe exploration.** `--read-only` (or `CLT_READONLY=1`) sends only `GET` requests and blocks `login`, `refresh`, `logout` and `token`, so it cannot change the saved session or print a token; `--dry-run` prints a request without sending it.
- **Small answers.** `--fields ref,subject,status_extra_info.name` keeps only some fields, `--max-items N` cuts a list, `--all` joins every page (it stops at a repeated page or an empty one, fails if a later page is not JSON, and leaves out the paging fields such as Jira's `isLast` even for one page), and `--table` prints a list as tab-separated lines under a header (an empty list prints the header when the columns are known, and `No items.` on stderr).
- **One command for common reads.** `clt <tool> mine` lists your open work items on the issue tracking services; `clt azdo wiql "<query>"` runs a WIQL query, and `clt jenkins jobs` lists jobs in a folder. These commands work under `--read-only`.

`--json` and `--read-only` can go anywhere on the command line.

clt carries a skill for AI agents ([`src/cli_tools/skill/SKILL.md`](src/cli_tools/skill/SKILL.md)): it explains these rules, opens with a table of common tasks and the exact command for each (so an agent does not search for them), and keeps the recipes in `references/`, which an agent reads only when it works with that service. It also tells agents with persistent memory to keep the commands that worked, so the next session runs them without loading the skill. Install it where your agents read skills:

```bash
clt skill install                    # for every agent CLI on the PATH (claude, codex, gemini)
clt skill install --agent codex      # Codex and Gemini CLI: ~/.agents/skills/clt; Claude Code: ~/.claude/skills/clt
clt skill install --agent claude --link   # a link instead of a copy, when you work on clt itself
```

The copies are marked with `.clt-skill`; `clt update` refreshes them, and `clt skill uninstall --agent …` removes only clt's own.

### MCP servers

Jira and Azure DevOps have official MCP servers. `clt` prints, or with `--install` runs, the command that registers one with your agent, for your user (not just one project); it never edits an agent's configuration or stores a token in it:

```bash
clt jira mcp --agent codex                  # codex mcp add atlassian --url https://mcp.atlassian.com/v1/mcp
clt jira mcp --agent claude --install
clt azdo mcp --agent codex --org <org>      # Microsoft's local server, npx @azure-devops/mcp (Node.js 20+)
```

Atlassian's server signs you in with OAuth in the browser, and your Atlassian organization must allow it: Codex starts the sign-in as soon as the server is added (later, `codex mcp login atlassian`), Claude Code with `/mcp`, Gemini CLI with `/mcp auth atlassian`; `clt jira mcp` prints the step for your agent. Azure DevOps opens the browser the first time the agent uses it. A copy of the skill made by hand is never overwritten: move it out of the skills folder first, and `clt skill refresh` says when it finds one.

## Configuration

For tools with a configurable server URL, clt takes it in this order:
1. the `--url` option of `login`;
2. the service's environment variable, such as `JENKINS_URL`;
3. the server of the last session.

| Variable | Purpose |
|---|---|
| `TAIGA_URL`, `GITEA_URL` | Server, `https://` only. |
| `TAIGA_CONF_DIR`, `GITEA_CONF_DIR` | Credentials folder; by default `~/.config/taiga` and `~/.config/gitea`. |
| `TAIGA_CACERT`, `GITEA_CACERT` | Optional extra CA, for example an internal one; by default `ca.pem` in the credentials folder. |
| `TAIGA_PROJECT` | Project slug for `clt taiga issue` when `--project` is not given. |
| `GITEA_AUTH_SOURCE` | Gitea external login source to go straight to; empty means Gitea's own login page. |
| `GITHUB_TOKEN`, `GH_TOKEN` | GitHub token to use instead of the saved session; it is used as it is and never renewed. |
| `GITHUB_CONF_DIR` | GitHub credentials folder; by default `~/.config/github`. |
| `GITHUB_CLIENT_ID` | GitHub OAuth app (with device flow enabled) for `clt github login --browser`. |
| `JIRA_URL` | Jira Cloud site, `https://<site>.atlassian.net`. |
| `JIRA_EMAIL`, `JIRA_TOKEN` | Atlassian account and API token to use instead of the saved session. |
| `JIRA_CLOUD_ID` | With `JIRA_TOKEN`: the token has scopes, so calls go through `api.atlassian.com` with this cloud id. |
| `JIRA_CONF_DIR`, `JIRA_CACERT` | Jira credentials folder (by default `~/.config/jira`) and optional extra CA. |
| `AZDO_ORG` | Azure DevOps organization, by name or as `https://dev.azure.com/<org>`. |
| `AZDO_TOKEN` | Personal access token to use instead of the saved session (`AZURE_DEVOPS_EXT_PAT`, the Azure CLI's name, works too). |
| `AZDO_API_VERSION` | The `api-version` added to paths without one (by default `7.1`). |
| `AZDO_CONF_DIR`, `AZDO_CACERT` | Azure DevOps credentials folder (by default `~/.config/azdo`) and optional extra CA. |
| `JENKINS_URL` | Jenkins controller URL, including its context path if it has one. |
| `JENKINS_USER`, `JENKINS_TOKEN` | Jenkins username and API token to use with `JENKINS_URL` instead of a saved session. |
| `JENKINS_CONF_DIR`, `JENKINS_CACERT` | Jenkins credentials folder (by default `~/.config/jenkins`) and optional extra CA. |
| `JENKINS_ALLOW_HTTP` | Set to `1` for a Jenkins controller that only serves HTTP. HTTP sends any credentials without TLS; prefer HTTPS. |
| `GITEA_CLIENT_ID` | Public Gitea OAuth2 app; by default, the one Gitea has built in for `tea`. |
| `GITEA_CALLBACK_PORT` | Local port for the browser's answer; `38291` by default. |
| `NO_COLOR` | Turns colors off. |

## Security

- **Credentials:** they are stored in `credentials.json`, with `0600` permissions, inside a `0700` folder, and written atomically. They never go into the repo.
- **Token:** it is only sent to paths relative to the configured server's API, and redirects are not followed (urllib would forward the `Authorization` header).
- **GitHub browser login:** only enter the code that `clt github login` shows in your own terminal. Someone who gets you to type *their* code at `github.com/login/device` receives a token for your account. The OAuth app's client ID in the code is public by design; it has no secret, and you can revoke `clt` under *Settings → Applications* on GitHub.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .   # python on Windows
uvx ruff@0.17.3 check .                                            # the linter, a development tool only
```

**Contributing:** commit messages and branch names follow Conventional Commits and Conventional Branch; see [CONTRIBUTING.md](CONTRIBUTING.md).

| Path | Contents |
|---|---|
| `src/cli_tools/cli.py`, `registry.py` | The `clt` command and the list of what it runs. A new tool is one line in `registry.py`; `clt` imports a tool only when it runs. |
| `src/cli_tools/core/` | Shared parts that are independent of the services: errors and exit codes, the `--json`/`--read-only` context, output, HTTP, credential files, the `api` and `mcp` commands and `runner.run()`. |
| `src/cli_tools/tools/<tool>/` | One package per API client: `client.py` (server, session, token renewal where supported) and `cli.py` (commands); Gitea's browser login is in `auth.py`. |
| `src/cli_tools/commands/` | `clt release` (with its version files and git helpers), `clt update` and `clt skill`. |
| `tests/` | Tests with `unittest`, with no network or servers. `test_contracts.py` pins the commands' public behaviour. |
| `docs/architecture.md` | How the code is organized and how to add a tool. |
| `src/cli_tools/skill/` | The skill that teaches AI agents to use `clt`, shipped in the package (`skills/clt` links to it). |
| `AGENTS.md`, `CLAUDE.md` | Instructions for AI agents that develop `clt`: rules, checks and how work flows. |
| `scripts/check_conventions.py` | Checks commit messages and branch names, and installs the `commit-msg` hook ([CONTRIBUTING.md](CONTRIBUTING.md)). |
| `scripts/check_private_words.py` | Checks that no private word (listed in `.git/info/private-words`, never pushed) is in the repo. |
| `.github/workflows/tests.yml` | CI: Ruff, the commit and branch conventions on pull requests, the tests, installing with pip and with `uv tool install`, and a smoke test of the installed commands on Linux, macOS and Windows, with Python 3.9 (Linux and Windows) and 3.14. |
| `.clt.json` | How `clt release` runs this repo's tests. Releases are cut with `clt release`. |

## License

[MIT](LICENSE) © 2026 José A. Dominique.
