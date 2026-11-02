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
PYTHONPATH=src python -m cli_tools taiga me
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
PYTHONPATH=src python -m unittest discover -s tests -t .
```

| Path | Contents |
|---|---|
| `src/cli_tools/core.py` | The shared parts: HTTP, credentials, output and the `api` subcommand. |
| `src/cli_tools/taiga.py`, `gitea.py` | One tool per file: its login, its token renewal and its commands. |
| `src/cli_tools/clt.py` | The `clt` command. A new tool is added to `TOOLS`. |
| `tests/` | Tests with `unittest`, with no network or servers. |

## License

[MIT](LICENSE) © 2026 José A. Dominique.
