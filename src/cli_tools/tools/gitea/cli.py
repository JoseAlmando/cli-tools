"""Command-line client for the Gitea API, with OAuth2 (PKCE) login in the browser."""
import argparse
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

from cli_tools.core import api, context, http, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info
from cli_tools.tools.gitea import auth
from cli_tools.tools.gitea.client import DEFAULT_CALLBACK_PORT, PROG, Gitea


def cmd_login(g, args):
    if not args.no_browser:
        context.require_terminal(f"{PROG} login")
    source = args.source if args.source is not None else g.auth_source
    tokens = auth.browser_login(g, source, args.timeout, open_browser=not args.no_browser)
    g.save_credentials(tokens)
    _, raw, _ = g.request("GET", "user", token=g.load_credentials()["access_token"])
    me = http.parse_json(raw, "Getting the user")
    info(output.err.green("✓ ") + f"Logged in to {g.url} as {output.err.bold(me.get('login', '?'))}")


def cmd_refresh(g, args):
    if os.environ.get("GITEA_TOKEN"):
        raise ToolError("A token from GITEA_TOKEN is never renewed; unset it to renew the saved session")
    if not g.refresh(g.load_credentials()):
        raise ToolError("The server rejected the renewal; run " + output.err.bold(f"{PROG} login"))
    info(output.err.green("✓ ") + "Token renewed.")


def cmd_logout(g, args):
    g.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


def cmd_token(g, args):
    print(g.fresh_credentials()["access_token"])


def cmd_me(g, args):
    _, raw, _ = g.call("GET", "user", None)
    me = http.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from user")
    expires_at = g.load_credentials().get("expires_at")
    if args.ctx.json_mode:
        from_env = bool(os.environ.get("GITEA_TOKEN"))
        print(json.dumps({"id": me.get("id"), "username": me.get("login"), "full_name": me.get("full_name"),
                          "email": me.get("email"), "url": g.url, "expires_at": expires_at,
                          "credentials_file": None if from_env else str(g.credentials_file),
                          "token_source": "GITEA_TOKEN" if from_env else "credentials_file"}, ensure_ascii=False))
        return
    print(f"{output.out.bold(me.get('full_name') or me.get('login', ''))} ({output.out.cyan(me.get('login', '?'))}) on {g.url}")
    if isinstance(expires_at, int):
        print(output.out.dim(f"Token valid until {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires_at))}"))
    print(output.out.dim(f"Credentials: {g.credentials_file}"))


MINE_SEARCHES = ("assigned=true", "type=pulls&review_requested=true", "type=pulls&created=true")


def cmd_mine(g, args):
    limit = max(args.limit, 1)
    rows, seen = [], set()
    for search in MINE_SEARCHES:
        wanted = limit - len(rows)
        if wanted <= 0:
            break
        path = f"repos/issues/search?state=open&{search}&limit={min(wanted, 50)}"
        for item in api.fetch_list(g.call, path, wanted):
            if not isinstance(item, dict):
                continue
            where = (item.get("repository") or {}).get("full_name")
            if (where, item.get("number")) in seen:
                continue
            seen.add((where, item.get("number")))
            rows.append({"kind": "pr" if item.get("pull_request") else "issue", "id": item.get("number"),
                         "state": item.get("state"), "title": item.get("title"), "where": where})
    output.print_rows(rows[:limit], args.table, args.ctx.json_mode, api.MINE_COLUMNS)


def repository(value):
    owner, _, name = value.partition("/")
    if not owner or not name or "/" in name:
        raise argparse.ArgumentTypeError("expected OWNER/REPO, e.g. team/service")
    return "/".join(urllib.parse.quote(part, safe="") for part in (owner, name))


def read_text(path):
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ToolError(f"Could not read {path}: {e}") from None


def cmd_pr_create(g, args):
    args.ctx.require_writes("pr create")
    base = args.base
    if not base:
        _, raw, _ = g.call("GET", f"repos/{args.repo}", None)
        base = (http.parse_json(raw, "Reading the repository") or {}).get("default_branch")
        if not base:
            raise ToolError(f"repos/{args.repo} has no default branch; pass --base")
    body = read_text(args.body_file) if args.body_file else args.body
    path, request = f"repos/{args.repo}/pulls", {"head": args.head, "base": base, "title": args.title, "body": body}
    if args.dry_run:
        print(json.dumps({"method": "POST", "path": path, "body": request}, ensure_ascii=False, indent=2))
        return
    _, raw, _ = g.call("POST", path, json.dumps(request))
    created = http.parse_json(raw, "Opening the pull request")
    result = {"number": created.get("number"), "url": created.get("html_url")}
    if args.ctx.json_mode:
        print(json.dumps(result, ensure_ascii=False))
    else:
        info(output.err.green("✓ ") + f"Opened pull request #{result['number']}")
        print(result["url"])


def build_parser(prog=PROG):
    epilog = f"""\
examples:
  {prog} login --url https://gitea.example.com       # the first time; opens the browser
  {prog} login --source <source>                      # go straight to a Gitea external login source (OIDC)
  {prog} me
  {prog} mine                                    # assigned to you, waiting for your review, or opened by you
  {prog} GET "repos/<owner>/<repo>/pulls?state=open"
  {prog} POST repos/<owner>/<repo>/pulls -d @pr.json
  {prog} PATCH repos/<owner>/<repo>/pulls/<n> -d '{{"title": "New title"}}'
  export GITEA_TOKEN="$({prog} token)"               # for other scripts

environment variables:
  GITEA_URL            server, https only; if unset, the last session's
  GITEA_TOKEN          use this token (e.g. a personal access token) instead of the saved session
  GITEA_AUTH_SOURCE    Gitea external login source; empty = Gitea's own login page
  GITEA_CLIENT_ID      public Gitea OAuth2 app (default: the one built in for tea)
  GITEA_CALLBACK_PORT  local port for the browser's answer (default {DEFAULT_CALLBACK_PORT})
  GITEA_CONF_DIR       credentials folder (default ~/.config/gitea)
  GITEA_CACERT         extra CA (default ca.pem in the credentials folder)
  NO_COLOR             turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, refresh, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode,
                    130 interrupted
""" + api.options_help(prog)
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Gitea API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", aliases=context.command_aliases("login"),
                       help="log in through the browser (OAuth2 + PKCE) and save the credentials")
    p.add_argument("--url", help="Gitea server, e.g. https://gitea.example.com")
    p.add_argument("--source", help="Gitea external login source to go straight to (GITEA_AUTH_SOURCE)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser (300)")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", aliases=context.command_aliases("refresh"),
                   help="renew the token without opening the browser").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", aliases=context.command_aliases("logout"), help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", aliases=context.command_aliases("token"),
                   help="print the access token (renewed if it expired)").set_defaults(func=cmd_token)
    api.add_mine_parser(sub, cmd_mine, "issues and pull requests assigned to you, then the pull requests waiting for your "
                                        "review and the ones you opened, in every repository")
    pr = sub.add_parser("pr", help="pull requests: pr create").add_subparsers(dest="pr_command", metavar="COMMAND",
                                                                               required=True)
    p = pr.add_parser("create", help="open a pull request (try it with --dry-run first)")
    p.add_argument("--repo", required=True, type=repository, metavar="OWNER/REPO")
    p.add_argument("--head", required=True, help="the branch with the changes")
    p.add_argument("--base", help="the branch to merge into (default: the repository's default branch)")
    p.add_argument("--title", required=True)
    body = p.add_mutually_exclusive_group()
    body.add_argument("--body", default="", help="the description, as Markdown")
    body.add_argument("--body-file", metavar="PATH", help="read the description from a file, or - for stdin")
    p.add_argument("--dry-run", action="store_true", help="print the request and open nothing")
    p.set_defaults(func=cmd_pr_create)
    api.add_api_parser(sub, prog, "repos/<owner>/<repo>/pulls", lambda g, args: api.run_api(args, g.call, args.ctx))
    return parser


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
    # logout only deletes the file, so it does not need to know the server
    return args.func(Gitea(getattr(args, "url", None), require_url=args.command != "logout"), args)

