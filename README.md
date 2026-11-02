# cli-tools

Command-line tools for the Taiga and Gitea APIs, gathered under a single command, `clt`. They only use the Python standard library, with no dependencies.

| Command | What it does |
|---|---|
| `clt` | Groups the tools: `clt taiga …`, `clt gitea …`. |
| `taiga` | Taiga API client: username and password login, automatic token renewal, and `GET/POST/PUT/PATCH/DELETE` calls to `/api/v1/`. |
| `gitea` | Gitea API client: OAuth2 login with PKCE in the browser (including through a Gitea external login source, such as an OIDC provider), automatic token renewal, and calls to `/api/v1/`. |

## Installation

Requires Python 3.9 or later. It installs three commands: `clt`, which groups the tools, and `taiga` and `gitea` on their own. On Windows they are generated as `.exe` files.

```bash
uv tool install -e .        # or: pipx install -e .
```

With `-e`, changes in the repo take effect right away, without reinstalling. To uninstall: `uv tool uninstall cli-tools`.

Without installing, from the repo:

```bash
PYTHONPATH=src python3 -m cli_tools taiga me   # python on Windows
```

## Usage

```bash
clt taiga me                                    # same as: taiga me

taiga login --url https://taiga.example.com     # the first time; the server is saved
taiga me
taiga GET "userstories?project=<id>"

gitea login --url https://gitea.example.com     # opens the browser
gitea login --source <source>                   # go straight to a Gitea external login source
gitea GET "repos/<owner>/<repo>/pulls?state=open"
gitea POST repos/<owner>/<repo>/pulls -d @pr.json

export TAIGA_TOKEN="$(taiga token)"             # for other scripts
```

`clt --help`, `taiga --help` and `gitea --help` list the commands and the environment variables.

## For scripts and AI agents

Every tool can run unattended:

- **No prompts.** Without a terminal, `login` fails at once instead of waiting. A session can also come from `TAIGA_TOKEN` or `GITEA_TOKEN`, and `taiga login --user <u> --password-stdin` reads the password from stdin.
- **Machine-readable errors.** `--json` (or `CLT_JSON=1`) prints each error as one JSON line on stderr, with the HTTP status and body when there is one.
- **Exit codes:** `0` success, `1` error, `2` wrong usage, `3` not logged in, `4` blocked by read-only mode.
- **Safe exploration.** `--read-only` (or `CLT_READONLY=1`) sends only `GET` requests and blocks `login`, `refresh`, `logout` and `token`, so it cannot change the saved session or print a token; `--dry-run` prints a request without sending it.
- **Small answers.** `--fields ref,subject,status_extra_info.name` keeps only some fields, `--max-items N` cuts a list, and `--all` joins every page.

`--json` and `--read-only` can go anywhere on the command line.

[`skills/clt/SKILL.md`](skills/clt/SKILL.md) is a skill for Claude Code (and other agents that read skills): it explains these rules and has recipes for the common Taiga and Gitea calls. To use it in Claude Code:

```bash
mkdir -p ~/.claude/skills && ln -s "$PWD/skills/clt" ~/.claude/skills/clt
```

## Configuration

No server is configured by default. Each tool takes it, in this order:
1. the `--url` option of `login`;
2. the `TAIGA_URL` or `GITEA_URL` environment variable;
3. the server of the last session.

| Variable | Purpose |
|---|---|
| `TAIGA_URL`, `GITEA_URL` | Server, `https://` only. |
| `TAIGA_CONF_DIR`, `GITEA_CONF_DIR` | Credentials folder; by default `~/.config/taiga` and `~/.config/gitea`. |
| `TAIGA_CACERT`, `GITEA_CACERT` | Optional extra CA, for example an internal one; by default `ca.pem` in the credentials folder. |
| `GITEA_AUTH_SOURCE` | Gitea external login source to go straight to; empty means Gitea's own login page. |
| `GITEA_CLIENT_ID` | Public Gitea OAuth2 app; by default, the one Gitea has built in for `tea`. |
| `GITEA_CALLBACK_PORT` | Local port for the browser's answer; `38291` by default. |
| `NO_COLOR` | Turns colors off. |

## Security

- **Credentials:** they are stored in `credentials.json`, with `0600` permissions, inside a `0700` folder, and written atomically. They never go into the repo.
- **Token:** it is only sent to paths relative to the configured server's API, and redirects are not followed (urllib would forward the `Authorization` header).

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .   # python on Windows
```

| Path | Contents |
|---|---|
| `src/cli_tools/core.py` | The shared parts: HTTP, credentials, output and the `api` subcommand. |
| `src/cli_tools/taiga.py`, `gitea.py` | One tool per file: its login, its token renewal and its commands. |
| `src/cli_tools/clt.py` | The `clt` command. A new tool is added to `TOOLS`. |
| `tests/` | Tests with `unittest`, with no network or servers. |
| `skills/clt/SKILL.md` | The skill that teaches AI agents to use `clt`. |

## License

[MIT](LICENSE) © 2026 José A. Dominique.
