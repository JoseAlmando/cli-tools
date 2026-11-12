import argparse
import getpass
import json
import sys
import webbrowser

from cli_tools.core import api, context, mcp, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info
from cli_tools.tools.azdo import client
from cli_tools.tools.azdo.client import TOKEN_SCOPES, AzureDevOps


def cmd_login(azdo, args):
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        context.require_terminal("clt azdo login")
        if args.create_token:
            page = f"{azdo.url}/_usersSettings/tokens"
            info(output.err.cyan("Create a personal access token on this page and paste it here:"))
            info("  \"New Token\", organization " + azdo.url.rsplit("/", 1)[1] + ", scopes:")
            for scope in TOKEN_SCOPES:
                info("    " + scope)
            info(page)
            if not args.no_browser:
                webbrowser.open(page)
        token = getpass.getpass(output.err.cyan("Azure DevOps personal access token: ")).strip()
    if not token:
        raise ToolError("A personal access token is needed")
    user = azdo.check_token(token)
    azdo.save_credentials(token)
    variable = client.token_variable()
    if variable:
        info(output.err.yellow(f"{variable} is set and wins over the token saved now; unset it to use this one."))
    name = user.get("providerDisplayName") or user.get("id")
    info(output.err.green("✓ ") + f"Logged in to {azdo.url} as {output.err.bold(str(name))}")


def cmd_logout(azdo, args):
    azdo.forget_token()
    info(output.err.green("✓ ") + "Logged out; the organization is kept. The token stays valid until it expires or "
         "you revoke it in Azure DevOps.")


def cmd_token(azdo, args):
    print(azdo.load_credentials()["token"])


def _account(user):
    properties = user.get("properties")
    account = properties.get("Account") if isinstance(properties, dict) else None
    value = account.get("$value") if isinstance(account, dict) else None
    return value if isinstance(value, str) and value else None


def cmd_me(azdo, args):
    creds = azdo.load_credentials()
    user = azdo.check_token(creds["token"])
    email = _account(user)
    credentials_file = None if creds.get("env") else str(azdo.credentials_file)
    if args.ctx.json_mode:
        print(json.dumps({"id": user.get("id"), "username": email, "full_name": user.get("providerDisplayName"),
                          "email": email, "url": azdo.url, "credentials_file": credentials_file,
                          "token_source": creds.get("env") or "credentials_file"}, ensure_ascii=False))
        return
    print(f"{output.out.bold(str(user.get('providerDisplayName') or user.get('id')))}"
          + (f" ({output.out.cyan(email)})" if email else "") + f" on {azdo.url}")
    print(output.out.dim(f"Token from {creds['env']}" if credentials_file is None else f"Credentials: {credentials_file}"))


def query_items(azdo, query, project, top, flag):
    """The query's work items, saying on stderr when it stopped at `top` (there may be more)."""
    items = client.query_items(azdo, query, project, top)
    if top > 0 and len(items) >= top:
        info(output.err.yellow(f"Stopped at {top} work items; there may be more (raise {flag})."))
    return items


def cmd_wiql(azdo, args):
    output.print_rows(query_items(azdo, args.query, args.project, args.top, "--top"), args.table, args.ctx.json_mode)


def cmd_mine(azdo, args):
    items = query_items(azdo, client.mine_query(args.type, args.project), args.project, args.limit, "--limit")
    rows = [{"kind": item.get("System.WorkItemType"), "id": item["id"], "state": item.get("System.State"),
             "title": item.get("System.Title"), "where": item.get("System.TeamProject")} for item in items]
    output.print_rows(rows, args.table, args.ctx.json_mode, api.MINE_COLUMNS)


MCP_THEN = "sign in in the browser the first time the agent uses it (needs Node.js 20 or later)"
MCP_EPILOG = ("Runs Microsoft's local server (npx @azure-devops/mcp <organization>). If your tenant offers the remote "
              "one, https://mcp.dev.azure.com/<organization>, it needs an Entra ID app registration for Claude Code "
              "and does not work with Codex.")


def cmd_mcp(azdo, args):
    org = azdo.url.rsplit("/", 1)[1]
    command = mcp.stdio_command(args.agent, "azure-devops", mcp.npx("-y", "@azure-devops/mcp", org))
    return mcp.run_mcp(args, args.ctx, command, MCP_THEN)


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login --org <organization> --create-token
  {prog} me
  {prog} mine                                    # your open work items, one line each
  {prog} wiql "SELECT [System.Id], [System.Title] FROM WorkItems WHERE [System.AssignedTo] = @Me"
  {prog} GET _apis/projects --fields id,name --table
  {prog} GET '<project>/_apis/git/repositories/<repo>/pullrequests?$top=50' --all --fields pullRequestId,title
  {prog} PATCH _apis/wit/workitems/<id> -d '[{{"op": "add", "path": "/fields/System.State", "value": "Active"}}]' --dry-run

personal access token (https://dev.azure.com/<organization>/_usersSettings/tokens), scopes:
  {", ".join(TOKEN_SCOPES)}
paths are relative to https://dev.azure.com/<organization>/; api-version=7.1 is added when the path has none
(set AZDO_API_VERSION to change it; a path with its own api-version, such as 7.1-preview.4, is left alone)

environment variables:
  AZDO_ORG                 the organization, by name or as https://dev.azure.com/<organization>
  AZDO_TOKEN               use this token instead of the saved session (AZURE_DEVOPS_EXT_PAT works too)
  AZDO_API_VERSION         the api-version added to paths without one (default 7.1)
  AZDO_CONF_DIR            credentials folder (default ~/.config/azdo)
  AZDO_CACERT              extra CA (default ca.pem in the credentials folder)
  NO_COLOR                 turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode,
                    130 interrupted
""" + api.options_help(prog)
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Azure DevOps Services API.",
                                     epilog=epilog, formatter_class=output.HelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", aliases=context.command_aliases("login"), help="check a personal access token and save the session")
    p.add_argument("--org", help="the organization, by name or as https://dev.azure.com/<organization>")
    how = p.add_mutually_exclusive_group()
    how.add_argument("--create-token", action="store_true", help="open the token page first")
    how.add_argument("--token-stdin", action="store_true", help="read the token from stdin")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", aliases=context.command_aliases("logout"),
                   help="delete the saved token, keep the organization (the token stays valid until revoked)"
                   ).set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", aliases=context.command_aliases("token"), help="print the personal access token").set_defaults(func=cmd_token)
    p = mcp.add_mcp_parser(sub, cmd_mcp, "print (or --install) the command that connects Microsoft's Azure DevOps "
                           "MCP server to an agent", MCP_EPILOG)
    p.add_argument("--org", help="the organization (default: AZDO_ORG or the saved session)")
    p = sub.add_parser("wiql", help="run a WIQL query and read the columns it selects, in one command",
                       description="Runs the query, then reads the work items' fields named in its SELECT "
                                   "(title and state when it selects only [System.Id]). A read: works with "
                                   "--read-only.")
    p.add_argument("query", metavar="WIQL", help="e.g. \"SELECT [System.Id], [System.Title] FROM WorkItems "
                                                 "WHERE [System.AssignedTo] = @Me\"")
    p.add_argument("--project", help="the project the query runs in (needed for @project)")
    p.add_argument("--top", type=int, default=200, metavar="N", help="at most N work items (default 200)")
    p.add_argument("--table", action="store_true", help="a tab-separated table even with --json")
    p.set_defaults(func=cmd_wiql)
    p = api.add_mine_parser(sub, cmd_mine, "work items (not Closed, Done or Removed) in every project")
    p.add_argument("--project", help="only this project")
    p.add_argument("--type", help="only this work item type, e.g. Task or Bug")
    api.add_api_parser(sub, prog, "<project>/_apis/wit/workitems/<id>",
                       lambda a, args: api.run_api(args, a.call, args.ctx, client.pager(), client.items_key,
                                                   client.join), api_prefix="/", renews=False)
    return parser


def main(argv, ctx=None, prog="clt azdo"):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(api.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    args.command = context.canonical_command(args.command)
    ctx.check_session_command(args.command)
    args.ctx = ctx
    if args.command == "logout":
        return args.func(AzureDevOps(require_url=False), args)
    return args.func(AzureDevOps(getattr(args, "org", None)), args)
