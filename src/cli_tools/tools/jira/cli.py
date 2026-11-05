import argparse
import getpass
import json
import sys
import urllib.parse
import webbrowser

from cli_tools.core import api, context, http, mcp, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info
from cli_tools.tools.jira import adf, client
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


ISSUE_FIELDS = ("summary,status,issuetype,priority,assignee,reporter,labels,parent,description,subtasks,"
                "issuelinks,comment")


def _name(value, key="name"):
    return value.get(key) if isinstance(value, dict) else None


def _brief(issue):
    fields = issue.get("fields") or {}
    return {"key": issue.get("key"), "summary": fields.get("summary"), "status": _name(fields.get("status"))}


def _link(link):
    if link.get("outwardIssue"):
        return {"type": _name(link.get("type"), "outward"), **_brief(link["outwardIssue"])}
    return {"type": _name(link.get("type"), "inward"), **_brief(link.get("inwardIssue") or {})}


def _comment(comment):
    return {"author": _name(comment.get("author"), "displayName"), "created": comment.get("created"),
            "body": adf.to_text(comment.get("body"))}


def _comments(jira, key, field, wanted):
    if wanted <= 0:
        return []
    field = field if isinstance(field, dict) else {}
    found = field.get("comments") or []
    total = field.get("total") if isinstance(field.get("total"), int) else len(found)
    if total > len(found):
        _, raw, _ = jira.call("GET", f"issue/{key}/comment?startAt={max(total - wanted, 0)}&maxResults={wanted}", None)
        found = http.parse_json(raw, "Getting the comments").get("comments") or []
    return [_comment(c) for c in found[-wanted:] if isinstance(c, dict)]


def issue_summary(jira, key, wanted):
    path_key = urllib.parse.quote(key, safe="")
    _, raw, _ = jira.call("GET", f"issue/{path_key}?fields={ISSUE_FIELDS}", None)
    issue = http.parse_json(raw, "Getting the issue")
    if not isinstance(issue, dict):
        raise ToolError("Unexpected response from issue")
    fields = issue.get("fields") or {}
    description = fields.get("description")
    return {"key": issue.get("key"), "summary": fields.get("summary"), "type": _name(fields.get("issuetype")),
            "status": _name(fields.get("status")), "priority": _name(fields.get("priority")),
            "assignee": _name(fields.get("assignee"), "displayName"),
            "reporter": _name(fields.get("reporter"), "displayName"), "labels": fields.get("labels") or [],
            "parent": _name(fields.get("parent"), "key"),
            "description": None if description is None else adf.to_text(description),
            "subtasks": [_brief(s) for s in fields.get("subtasks") or [] if isinstance(s, dict)],
            "links": [_link(link) for link in fields.get("issuelinks") or [] if isinstance(link, dict)],
            "comments": _comments(jira, path_key, fields.get("comment"), wanted),
            "url": f"{jira.url}/browse/{issue.get('key')}"}


def print_issue(summary):
    out = output.out
    print(f"{out.bold(summary['key'])}  {summary['summary']}")
    print(" · ".join(str(v) for v in (summary["type"], summary["status"], summary["priority"]) if v))
    for label, value in (("Assignee", summary["assignee"]), ("Reporter", summary["reporter"]),
                         ("Parent", summary["parent"]), ("Labels", ", ".join(summary["labels"]))):
        if value:
            print(f"{out.dim(label + ':')} {value}")
    print(out.dim(summary["url"]))
    if summary["description"]:
        print()
        print(summary["description"])
    if summary["subtasks"]:
        print("\n" + out.bold("Subtasks"))
        for item in summary["subtasks"]:
            print(f"  {item['key']}  {item['summary']}  [{item['status']}]")
    if summary["links"]:
        print("\n" + out.bold("Links"))
        for item in summary["links"]:
            print(f"  {item['type']} {item['key']}  {item['summary']}  [{item['status']}]")
    for item in summary["comments"]:
        print("\n" + out.bold(f"{item['author']}, {item['created']}"))
        print(item["body"])


def cmd_issue(jira, args):
    summary = issue_summary(jira, args.key, args.comments)
    if args.ctx.json_mode:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print_issue(summary)


MCP_URL = "https://mcp.atlassian.com/v1/mcp"
MCP_EPILOG = (f"Atlassian's remote server, {MCP_URL}. You sign in with OAuth in the browser, and your Atlassian "
              "organization must allow the app; Codex starts the sign-in as soon as the server is added.")
MCP_THEN = {"claude": "/mcp in Claude Code to sign in",
            "codex": "sign in when the browser opens (later: codex mcp login atlassian)",
            "gemini": "/mcp auth atlassian in Gemini CLI"}


def cmd_mcp(jira, args):
    return mcp.run_mcp(args, args.ctx, mcp.http_command(args.agent, "atlassian", MCP_URL), MCP_THEN[args.agent])


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login --url https://<site>.atlassian.net --create-token
  {prog} me
  {prog} issue <KEY>                             # summary, description and comments as Markdown
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

    p = sub.add_parser("login", aliases=context.command_aliases("login"), help="check an Atlassian API token and save the session")
    p.add_argument("--url", help="the site, https://<site>.atlassian.net")
    p.add_argument("--email", help="the Atlassian account email")
    how = p.add_mutually_exclusive_group()
    how.add_argument("--create-token", action="store_true", help="open Atlassian's token page first")
    how.add_argument("--token-stdin", action="store_true", help="read the token from stdin (needs --email)")
    p.add_argument("--scoped", action="store_true", help="the token has scopes: go through api.atlassian.com")
    p.add_argument("--cloud-id", help="the site's cloud id, when it cannot be found on its own")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", aliases=context.command_aliases("logout"), help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", aliases=context.command_aliases("token"), help="print the API token").set_defaults(func=cmd_token)
    p = sub.add_parser("issue", aliases=context.command_aliases("issue"),
                       help="one issue as a short summary, with its description and comments as Markdown")
    p.add_argument("key", metavar="KEY", help="the issue key, e.g. PROJ-123")
    p.add_argument("--comments", type=int, default=10, metavar="N", help="keep the last N comments (default 10)")
    p.set_defaults(func=cmd_issue)
    mcp.add_mcp_parser(sub, cmd_mcp, "print (or --install) the command that connects Atlassian's MCP server to an agent",
                       MCP_EPILOG)
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
    args.command = context.canonical_command(args.command)
    ctx.check_session_command(args.command)
    args.ctx = ctx
    if args.command in ("logout", "mcp"):
        return args.func(Jira(require_url=False), args)
    return args.func(Jira(getattr(args, "url", None)), args)
