"""Command-line client for the Taiga API."""
import argparse
import getpass
import json
import os
import sys

from cli_tools.core import api, context, http, output
from cli_tools.core.errors import HttpError, ToolError, error_detail
from cli_tools.core.output import info
from cli_tools.tools.taiga.client import PROG, Taiga


def cmd_login(t, args):
    if args.password_stdin:
        if not args.user:
            raise ToolError("--password-stdin needs --user")
        user, password = args.user, sys.stdin.readline().rstrip("\r\n")
    else:
        context.require_terminal(f"{PROG} login")
        user = args.user or input(output.err.cyan("Username or email: "))
        password = getpass.getpass(output.err.cyan("Password: "))
    body = json.dumps({"type": "normal", "username": user, "password": password})
    try:
        _, raw, _ = t.request("POST", "auth", body)
    except HttpError as e:
        raise ToolError(f"Login failed: {error_detail(e.body)}") from None
    data = http.parse_json(raw, "Logging in")
    t.save_credentials(data)
    info(output.err.green("✓ ") + f"Logged in to {t.url} as {output.err.bold(data.get('username', user))}")


def cmd_refresh(t, args):
    if os.environ.get("TAIGA_TOKEN"):
        raise ToolError("A token from TAIGA_TOKEN is never renewed; unset it to renew the saved session")
    if not t.refresh(t.load_credentials()):
        raise ToolError("The server rejected the renewal; run " + output.err.bold(f"{PROG} login"))
    info(output.err.green("✓ ") + "Token renewed.")


def cmd_logout(t, args):
    t.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


def cmd_token(t, args):
    print(t.load_credentials()["auth_token"])


def cmd_me(t, args):
    _, raw, _ = t.call("GET", "users/me", None)
    me = http.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from users/me")
    if args.ctx.json_mode:
        from_env = bool(os.environ.get("TAIGA_TOKEN"))
        print(json.dumps({"id": me.get("id"), "username": me.get("username"), "full_name": me.get("full_name_display"),
                          "email": me.get("email"), "url": t.url,
                          "credentials_file": None if from_env else str(t.credentials_file),
                          "token_source": "TAIGA_TOKEN" if from_env else "credentials_file"}, ensure_ascii=False))
        return
    print(f"{output.out.bold(me.get('full_name_display', ''))} ({output.out.cyan(me.get('username', '?'))}) on {t.url}")
    print(output.out.dim(f"Credentials: {t.credentials_file}"))


# A user story can have several people assigned (assigned_users); tasks and issues have one (assigned_to)
MINE_KINDS = (("story", "userstories", "assigned_users"), ("task", "tasks", "assigned_to"),
              ("issue", "issues", "assigned_to"))


# What Taiga's resolver answers for a ref, the collection to read, the kind shown, and the web page's segment
RESOLVED = (("us", "userstories", "story"), ("task", "tasks", "task"), ("issue", "issues", "issue"))


def cmd_issue(t, args):
    project = args.project or os.environ.get("TAIGA_PROJECT")
    if not project:
        raise ToolError("Which project? Pass --project <slug> or set TAIGA_PROJECT (mine shows it as where)")
    _, raw, _ = t.call("GET", api.set_query("resolver", {"project": project, "ref": str(args.ref)}), None)
    found = http.parse_json(raw, "Resolving the ref")
    found = found if isinstance(found, dict) else {}
    match = next(((key, collection, kind) for key, collection, kind in RESOLVED if found.get(key)), None)
    if not match:
        raise ToolError(f"No story, task or issue #{args.ref} in {project}")
    key, collection, kind = match
    _, raw, _ = t.call("GET", f"{collection}/{int(found[key])}", None)
    item = http.parse_json(raw, f"Reading the {kind}")
    if not isinstance(item, dict):
        raise ToolError(f"Unexpected response from {collection}/{found[key]}")
    summary = {"kind": kind, "ref": item.get("ref"), "id": item.get("id"), "version": item.get("version"),
               "subject": item.get("subject"), "status": (item.get("status_extra_info") or {}).get("name"),
               "assigned_to": (item.get("assigned_to_extra_info") or {}).get("full_name_display"),
               "project": (item.get("project_extra_info") or {}).get("slug") or project,
               "url": f"{t.url}/project/{project}/{key}/{args.ref}", "description": item.get("description") or ""}
    if args.ctx.json_mode:
        print(json.dumps(summary, ensure_ascii=False))
        return
    print(f"{kind} #{summary['ref']} {summary['subject'] or ''}")
    print(" · ".join(str(value) for value in (summary["status"], summary["assigned_to"], summary["project"]) if value))
    print(summary["url"])
    if summary["description"]:
        print("\n" + summary["description"])


def cmd_mine(t, args):
    _, raw, _ = t.call("GET", "users/me", None)
    me = http.parse_json(raw, "Getting the user")
    if not isinstance(me, dict) or me.get("id") is None:
        raise ToolError("Unexpected response from users/me")
    rows = []
    for kind, collection, assigned in MINE_KINDS:
        left = args.limit - len(rows)
        if left <= 0:
            break
        items = api.fetch_list(t.call, f"{collection}?{assigned}={me['id']}&status__is_closed=false", left)
        rows += [{"kind": kind, "id": item.get("ref"), "state": (item.get("status_extra_info") or {}).get("name"),
                  "title": item.get("subject"), "where": (item.get("project_extra_info") or {}).get("slug")}
                 for item in items if isinstance(item, dict)]
    output.print_rows(rows, args.table, args.ctx.json_mode, api.MINE_COLUMNS)


def build_parser(prog=PROG):
    epilog = f"""\
examples:
  {prog} login --url https://taiga.example.com      # the first time; after that, {prog} login is enough
  {prog} me
  {prog} mine                                    # open user stories, tasks and issues assigned to you
  {prog} issue 12 --project web                  # one of them by its ref (mine's id and where)
  {prog} GET "userstories?project=<id>"
  {prog} GET "userstories?project=<id>&page=2" -i   # see the x-pagination-* headers
  {prog} PATCH userstories/123 -d '{{"version": 4, "subject": "New title"}}'
  {prog} POST tasks -d @task.json
  export TAIGA_TOKEN="$({prog} token)"               # for other scripts

environment variables:
  TAIGA_URL         server, https only; if unset, the last session's
  TAIGA_TOKEN       use this token instead of the saved session (not renewed)
  TAIGA_PROJECT     project slug for issue, when --project is not given
  TAIGA_CONF_DIR    credentials folder (default ~/.config/taiga)
  TAIGA_CACERT      extra CA (default ca.pem in the credentials folder)
  NO_COLOR          turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, refresh, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode,
                    130 interrupted
""" + api.options_help(prog)
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Taiga API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", aliases=context.command_aliases("login"), help="log in and save the credentials")
    p.add_argument("-u", "--user", help="username or email (asked for if missing)")
    p.add_argument("--url", help="Taiga server, e.g. https://taiga.example.com")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin (needs --user)")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", aliases=context.command_aliases("refresh"),
                   help="renew the token without asking for the password").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", aliases=context.command_aliases("logout"), help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", aliases=context.command_aliases("token"), help="print the access token").set_defaults(func=cmd_token)
    p = sub.add_parser("issue", aliases=context.command_aliases("issue"),
                       help="one user story, task or issue by its ref, with its description")
    p.add_argument("ref", type=int, metavar="REF", help="the ref, as mine shows it in its id column")
    p.add_argument("--project", help="project slug (mine's where column); default TAIGA_PROJECT")
    p.set_defaults(func=cmd_issue)
    api.add_mine_parser(sub, cmd_mine, "user stories, tasks and issues assigned to you, in every project")
    api.add_api_parser(sub, prog, "userstories?project=<id>", lambda t, args: api.run_api(args, t.call, args.ctx))
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
    return args.func(Taiga(getattr(args, "url", None), require_url=args.command != "logout"), args)

