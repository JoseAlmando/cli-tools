import argparse
import getpass
import json
import sys
import webbrowser

from cli_tools.core import api, context, output
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
    name = user.get("providerDisplayName") or user.get("id")
    info(output.err.green("✓ ") + f"Logged in to {azdo.url} as {output.err.bold(str(name))}")


def cmd_logout(azdo, args):
    azdo.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


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
                          "email": email, "url": azdo.url, "credentials_file": credentials_file},
                         ensure_ascii=False))
        return
    print(f"{output.out.bold(str(user.get('providerDisplayName') or user.get('id')))}"
          + (f" ({output.out.cyan(email)})" if email else "") + f" on {azdo.url}")
    print(output.out.dim("Token from the environment" if credentials_file is None else f"Credentials: {credentials_file}"))


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login --org <organization> --create-token
  {prog} me
  {prog} GET _apis/projects --fields id,name
  {prog} GET "<project>/_apis/git/repositories/<repo>/pullrequests?$top=50" --all --fields pullRequestId,title
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
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Azure DevOps Services API.",
                                     epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="check a personal access token and save the session")
    p.add_argument("--org", help="the organization, by name or as https://dev.azure.com/<organization>")
    how = p.add_mutually_exclusive_group()
    how.add_argument("--create-token", action="store_true", help="open the token page first")
    how.add_argument("--token-stdin", action="store_true", help="read the token from stdin")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", help="print the personal access token").set_defaults(func=cmd_token)
    api.add_api_parser(sub, prog, "<project>/_apis/wit/workitems/<id>",
                       lambda a, args: api.run_api(args, a.call, args.ctx, client.next_page, client.items_key,
                                                   client.join), api_prefix="/")
    return parser


def main(argv, ctx=None, prog="clt azdo"):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(api.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    ctx.check_session_command(args.command)
    args.ctx = ctx
    if args.command == "logout":
        return args.func(AzureDevOps(require_url=False), args)
    return args.func(AzureDevOps(getattr(args, "org", None)), args)
