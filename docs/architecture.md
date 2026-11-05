# Architecture

`clt` is a small modular monolith: one installable package, `cli_tools`, with the standard library only at runtime. Each tool lives in its own package; what they share lives in `core/`, which knows nothing about any particular service.

## Organization

```
src/cli_tools/
├── __init__.py          the version (from the installed metadata, or pyproject.toml in a checkout)
├── __main__.py          python -m cli_tools
├── cli.py               the clt command: help, --version, and handing the arguments to a tool
├── registry.py          every command clt runs: (name, module, one-line description), and the short names
├── skill/               the skill for AI agents (SKILL.md, references/), shipped as package data
├── core/                shared building blocks, no service-specific code
│   ├── errors.py        ToolError, NotLoggedIn, ReadOnlyError, HttpError, exit codes, error_detail
│   ├── context.py       CLIContext (--json, --read-only), the global flags, require_terminal
│   ├── output.py        colors, info(), printing JSON responses and headers
│   ├── http.py          the configured server, api_url, safe_path, HTTPS without redirects, json_request, parse_json
│   ├── credentials.py   credential files: 0600 in a 0700 folder, written atomically
│   ├── api.py           the api command: METHOD path [-d JSON], --all, --fields, --max-items, --dry-run
│   ├── mcp.py           the mcp command: an agent's "mcp add" command for an HTTP or stdio server, printed or run
│   ├── agents.py        where each agent CLI reads skills, and the hint to install clt's (used by clt --help)
│   └── runner.py        run(main): global flags, then errors turned into messages and exit codes
├── tools/               the API clients, one package each
│   ├── taiga/           client.py (server, session, token renewal) and cli.py (commands, main, run)
│   ├── gitea/           client.py, cli.py, and auth.py (OAuth2 + PKCE login in the browser)
│   ├── github/          client.py (session, renewal of expiring tokens), cli.py, and auth.py (device flow, token refresh
│   │                    and the pre-filled token page; only as clt github)
│   ├── jira/            client.py (site or api.atlassian.com by token kind, Basic auth, paging hooks), adf.py (rich text
│   │                    to plain text), cli.py (login, me, issue, mcp, api)
│   └── azdo/            client.py (organization, PAT as Basic auth, api-version, JSON Patch, paging hooks), cli.py
│                        (login, me, mcp, api)
└── commands/            clt's own commands that are not API clients
    ├── release/         cli.py (clt release), versions.py (version files), gitrepo.py (git helpers)
    ├── skill.py         clt skill: install, uninstall and refresh the skill in the agents' folders
    └── update.py        clt update (then clt skill refresh)
```

Dependencies go one way:

- `core` imports nothing outside `core`.
- A tool imports `core` and its own package, never another tool or `commands`.
- `commands` may import `core` and a tool's client (`clt release` publishes through the GitHub or Gitea client; `clt update` reads GitHub's public API with the GitHub client's `API_ROOT` and `HEADERS`).
- `cli.py` imports `registry` and `core`, and a tool's module only when that tool runs (`importlib.import_module`), so `clt --help` and `clt --version` import no tool.

## How a command runs

1. The console script `clt` (the only one declared in `pyproject.toml`) calls `cli.run()`, which calls `core.runner.run(main)`.
2. `run` sets up the output streams and takes `--json` and `--read-only` out of the arguments, wherever they are, into a `CLIContext` (the environment variables `CLT_JSON` and `CLT_READONLY` can turn them on too). It never writes them to `os.environ`, so the processes `clt` starts (a project's tests in `clt release`, `uv` in `clt update`) do not inherit them.
3. `run` calls `main(argv, ctx=ctx)`. For `clt`, `cli.main` looks the first argument up in `registry.TOOLS`, imports that module and calls its `main(rest, ctx=ctx, prog="clt <name>")`.
4. A tool's `main` parses its arguments with argparse, turns a short command name into the full one (`context.canonical_command`), checks read-only mode for the session commands (`ctx.check_session_command`), stores the context in `args.ctx`, and runs the command function with its client.
5. Expected errors are `ToolError` subclasses; `run` prints them (one JSON line on stderr with `--json`) and returns their exit code: `0` success, `1` error, `2` wrong usage (argparse), `3` not logged in, `4` blocked by read-only mode.

### CLIContext

`core.context.CLIContext` is a frozen dataclass with `json_mode` and `read_only`. It is built once per run and passed explicitly: to `main(argv, ctx=...)`, to the command functions as `args.ctx`, and to `api.run_api(args, call, ctx)`. Use `ctx.require_writes("what")` before anything that changes something, and `ctx.json_mode` to choose machine-readable output. When a function gets no context (a direct call in a test, for example) it builds one from the environment with `CLIContext.from_env()`. There is no module-level "current context".

## The core modules

| Module | Use it for |
|---|---|
| `errors` | Raise `ToolError` (exit 1), `NotLoggedIn` (exit 3) or `ReadOnlyError` (exit 4); `HttpError` carries the status, body and headers of a failed request. `error_detail(body)` gets the message out of any error body. |
| `context` | `CLIContext`; `require_terminal("what")` before a prompt or a browser login, so they fail at once without a person. |
| `output` | `info()` for messages (stderr); `output.out` and `output.err` for colors. Use them as attributes of the module: `setup_streams()` replaces them when a run starts. |
| `http` | `resolve_url(cli_url, env_url, credentials_file, prog, var)` picks the server (login's `--url`, then the environment, then the last session) and accepts only `https://server`. `api_url(base, path, prefix)` accepts only paths relative to the API, so the token never leaves that server. `json_request(method, url, body, token, cacert, headers)` sends it without following redirects. `parse_json(raw, what)` fails with a clear message. |
| `credentials` | `write_private_json(path, data)` and `read_json_file(path)` for the saved session. |
| `api` | `add_api_parser(sub, prog, example_path, func, api_prefix)` adds the `api` command; `run_api(args, call, ctx, next_page, items_key, join)` sends it, follows the pagination (only to the same server), and applies `--fields`, `--max-items` and `--dry-run`. A tool passes `next_page(headers, body, path)` when the next page is in the body, `items_key(data)` when lists come inside an object (`{"issues": [...]}`) so `--fields` and `--max-items` reach them, and `join(pages)` to merge pages into that object. `set_query(path, params)` sets or removes query parameters of a relative path. `with_api_shortcut(argv)` makes `<tool> GET path` the same as `<tool> api GET path`. |
| `runner` | `run(main)`, for the `clt` console script. |
| `agents` | `skill_target(agent)`, where an agent CLI reads clt's skill; `recommend_skill(ctx)` prints how to install it for the agents on the PATH that have none (only for a person: not with `--json`, not without a terminal). |

## Adding a tool

Say the new service is Linear.

1. **The client**, `src/cli_tools/tools/linear/client.py`: a class that knows the server (`http.resolve_url`), where its credentials live (an environment variable such as `LINEAR_CONF_DIR`, by default `~/.config/linear`), and how to send a request (`http.json_request(method, http.api_url(self.url, path, prefix=...), body, token, self.cacert)`). It loads and saves the session with `credentials`, raises `NotLoggedIn` when there is none, refuses a saved session for another server, and renews the token if the service allows it. Give it a `call(method, path, body)` that returns `(status, body, headers)`, renewing and retrying once on a 401 when it can.
2. **The commands**, `src/cli_tools/tools/linear/cli.py`: one function per command, `cmd_x(client, args)`, reading the context from `args.ctx`; `build_parser(prog)`, with the `api` command from `api.add_api_parser(sub, prog, "<example path>", lambda c, args: api.run_api(args, c.call, args.ctx))`; and

    ```python
    def main(argv, ctx=None, prog=PROG):
        ctx = ctx or context.CLIContext.from_env()
        parser = build_parser(prog)
        args = parser.parse_args(api.with_api_shortcut(argv))
        if not args.command:
            parser.print_help()
            return 2
        args.command = context.canonical_command(args.command)
        ctx.check_session_command(args.command)
        args.ctx = ctx
        return args.func(Linear(), args)
    ```

3. **Tool-specific authentication** (a browser flow, a callback server, a signing scheme) goes in the tool's own package, as `auth.py` does for Gitea. Keep `core` free of it.
4. **Register it**: one line in `src/cli_tools/registry.py`, `("linear", "cli_tools.tools.linear.cli", "client for the Linear API")`. `clt linear ...` and `clt --help` pick it up; no other module changes.
5. **A short name, if it helps**: one entry in `registry.ALIASES` (`"ln": "linear"`). No tool gets a console script of its own; `clt` is the only one. Session commands get their short names (`lg`, `rfh`, `lo`, `tk`) by passing `aliases=context.command_aliases(name)` to `add_parser`; `tests/test_registry.py` checks that no alias clashes with a tool, a command or an HTTP method.
6. **Tests** in `tests/test_linear.py`, with no network: patch `cli_tools.core.http.send`, or a client's `request`/`call`, and use `tests.helpers.TempConf` for a private credentials folder. `tests/test_contracts.py` shows how to drive the real entry points against a fake server.
7. **Docs**: the README tables, and `src/cli_tools/skill/` (`SKILL.md`, or a file under `references/` read only for that tool) if agents should use it.

## Adding a clt command that is not an API client

Put it in `src/cli_tools/commands/` (a module, or a package when it needs helpers, as `release/` does), give it the same `main(argv, ctx=None, prog="clt <name>")`, and add its line to `registry.py`. It may use a tool's client; a tool never imports a command.

## Development

```bash
uv tool install -e .                                        # clt, taiga and gitea run this checkout
PYTHONPATH=src python3 -m unittest discover -s tests -t .   # python on Windows
uvx ruff@0.17.3 check .                                            # lint (Ruff is a development dependency only)
```

The tests use `unittest` and never reach the network or a real server; `test_contracts.py` pins the public behaviour (help, exit codes, `--json` shapes, flags, credential locations) through the entry points declared in `pyproject.toml`. Ruff's settings are in `pyproject.toml` (`[tool.ruff]`): the `E`, `F`, `W`, `I`, `B` and `UP` rules for Python 3.9, with lines up to about 120 columns. The code is not run through `ruff format`.
