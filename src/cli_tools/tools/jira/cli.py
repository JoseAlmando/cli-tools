import argparse
import getpass
import json
import sys
import webbrowser

from cli_tools.core import api, context, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info
from cli_tools.tools.jira import client
from cli_tools.tools.jira.client import API_PREFIX, SCOPES, TOKEN_PAGE, Jira


def prompt(text):
    sys.stderr.write(output.err.cyan(text))
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def cmd_login(jira, args):
    if args.token_stdin:
        if not args.email:
            raise ToolError("--token-stdin needs --email")
        email, token = args.email, sys.stdin.readline().strip()
    else:
        context.require_terminal("clt jira login")
        email = args.email or prompt("Atlassian account email: ")
        if args.create_token:
            info(output.err.cyan("Create the token on this page and paste it here:"))
            info("  a classic token: \"Create API token\"")
            info("  a token with scopes: \"Create API token with scopes\", app Jira, scopes " + ", ".join(SCOPES))
            info(TOKEN_PAGE)
            if not args.no_browser:
                webbrowser.open(TOKEN_PAGE)
        token = getpass.getpass(output.err.cyan("Jira API token: ")).strip()
    if not email or not token:
        raise ToolError("An email and a token are needed")
    creds, me = jira.detect(email, token, cloud_id=args.cloud_id, scoped=args.scoped)
    jira.save_credentials(creds)
    name = me.get("displayName") or email
    info(output.err.green("✓ ") + f"Logged in to Jira as {output.err.bold(name)} ({creds['mode']} token)")


def cmd_logout(jira, args):
    jira.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


def cmd_token(jira, args):
    print(jira.load_credentials()["token"])


def cmd_me(jira, args):
    creds = jira.load_credentials()
    _, raw, _ = jira.call("GET", "myself", None)
    me = json.loads(raw) if raw else {}
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from myself")
    credentials_file = None if creds.get("env") else str(jira.credentials_file)
    if args.ctx.json_mode:
        print(json.dumps({"id": me.get("accountId"), "username": creds["email"], "full_name": me.get("displayName"),
                          "email": me.get("emailAddress") or creds["email"], "url": jira.url,
                          "credentials_file": credentials_file}, ensure_ascii=False))
        return
    print(f"{output.out.bold(me.get('displayName') or creds['email'])} ({output.out.cyan(creds['email'])}) "
          f"on {jira.url}, {creds['mode']} token")
    print(output.out.dim("Token from the environment" if credentials_file is None else f"Credentials: {credentials_file}"))


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login --url https://<site>.atlassian.net --create-token
  {prog} me
  {prog} GET "search/jql?jql=assignee=currentUser() AND statusCategory!=Done&fields=key,summary,status" --all
  {prog} GET issue/<KEY>/transitions --fields transitions
  {prog} POST issue/<KEY>/transitions -d '{{"transition": {{"id": "31"}}}}' --dry-run

API tokens (id.atlassian.com, Security, API tokens), both kinds work:
  classic       "Create API token"; clt calls the site
  with scopes   "Create API token with scopes", scopes {", ".join(SCOPES)};
                clt calls api.atlassian.com with the site's cloud id
login finds out which kind it got; --scoped skips the check, --cloud-id gives the cloud id

environment variables:
  JIRA_URL                 the site, https://<site>.atlassian.net
  JIRA_EMAIL, JIRA_TOKEN   use this account and token instead of the saved session
  JIRA_CLOUD_ID            with JIRA_TOKEN: the token has scopes; go through api.atlassian.com
  JIRA_CONF_DIR            credentials folder (default ~/.config/jira)
  JIRA_CACERT              extra CA (default ca.pem in the credentials folder)
  NO_COLOR                 turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Jira Cloud API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="check an Atlassian API token and save the session")
    p.add_argument("--url", help="the site, https://<site>.atlassian.net")
    p.add_argument("--email", help="the Atlassian account email")
    how = p.add_mutually_exclusive_group()
    how.add_argument("--create-token", action="store_true", help="open Atlassian's token page first")
    how.add_argument("--token-stdin", action="store_true", help="read the token from stdin (needs --email)")
    p.add_argument("--scoped", action="store_true", help="the token has scopes: go through api.atlassian.com")
    p.add_argument("--cloud-id", help="the site's cloud id, when it cannot be found on its own")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", help="print the API token").set_defaults(func=cmd_token)
    api.add_api_parser(sub, prog, "issue/<KEY>",
                       lambda j, args: api.run_api(args, j.call, args.ctx, client.next_page, client.items_key,
                                                   client.join), api_prefix=API_PREFIX)
    return parser


def main(argv, ctx=None, prog="clt jira"):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(api.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    ctx.check_session_command(args.command)
    args.ctx = ctx
    if args.command == "logout":
        return args.func(Jira(require_url=False), args)
    return args.func(Jira(getattr(args, "url", None)), args)
