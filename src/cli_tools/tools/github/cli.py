"""Command-line client for the GitHub API, with a personal access token."""
import argparse
import getpass
import json
import os
import sys

from cli_tools.core import api, context, credentials, http, output
from cli_tools.core.errors import HttpError, ToolError, error_detail
from cli_tools.core.output import info
from cli_tools.tools.github.client import GitHub, next_page

# Only the repository admin role (the owner, on a personal repo) may update main or create, move or delete v* tags
_ADMIN_ONLY = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
RULESETS = [
    {"name": "main", "target": "branch", "enforcement": "active", "bypass_actors": _ADMIN_ONLY,
     "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
     "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}]},
    {"name": "v* tags", "target": "tag", "enforcement": "active", "bypass_actors": _ADMIN_ONLY,
     "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
     "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]},
]


# --- Commands -----------------------------------------------------------------

def cmd_login(gh, args):
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        context.require_terminal("clt github login")
        token = getpass.getpass(output.err.cyan("GitHub token: ")).strip()
    if not token:
        raise ToolError("No token given")
    try:
        _, raw, _ = gh.request("GET", "user", token=token)
    except HttpError as e:
        raise ToolError(f"GitHub rejected the token: {error_detail(e.body)}") from None
    me = http.parse_json(raw, "Checking the token")
    credentials.write_private_json(gh.credentials_file, {"token": token})
    info(output.err.green("✓ ") + f"Logged in to GitHub as {output.err.bold(me.get('login', '?'))}")


def cmd_logout(gh, args):
    gh.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


def cmd_token(gh, args):
    print(gh.load_credentials()["token"])


def cmd_me(gh, args):
    _, raw, _ = gh.call("GET", "user", None)
    me = http.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from user")
    from_env = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    if args.ctx.json_mode:
        print(json.dumps({"id": me.get("id"), "username": me.get("login"), "full_name": me.get("name"),
                          "email": me.get("email"), "url": "https://github.com",
                          "credentials_file": None if from_env else str(gh.credentials_file)}, ensure_ascii=False))
        return
    print(f"{output.out.bold(me.get('name') or me.get('login', ''))} ({output.out.cyan(me.get('login', '?'))}) on GitHub")
    print(output.out.dim("Token from the environment" if from_env else f"Credentials: {gh.credentials_file}"))


def cmd_protect(gh, args):
    if args.repo.count("/") != 1 or not all(args.repo.split("/")):
        raise ToolError(f"Expected OWNER/REPO, got {args.repo!r}")
    if not args.dry_run:
        args.ctx.require_writes("protect")
    base = f"repos/{args.repo}/rulesets"
    _, raw, _ = gh.call("GET", base, None)
    existing = http.parse_json(raw, "Listing the rulesets")
    ids = {r.get("name"): r.get("id") for r in existing} if isinstance(existing, list) else {}
    planned = [("PUT", f"{base}/{ids[r['name']]}", r) if r["name"] in ids else ("POST", base, r) for r in RULESETS]
    if args.dry_run:
        print(json.dumps([{"method": m, "path": p, "body": b} for m, p, b in planned], ensure_ascii=False, indent=2))
        return
    for method, path, body in planned:
        gh.call(method, path, json.dumps(body))
        info(output.err.green("✓ ") + f"Ruleset {body['name']!r} {'updated' if method == 'PUT' else 'created'} in {args.repo}")


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login                                   # paste a personal access token
  {prog} me
  {prog} GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title --all
  {prog} GET "repos/<owner>/<repo>/releases" --fields tag_name,html_url --max-items 5
  {prog} protect <owner>/<repo> --dry-run        # only the owner may update main and v* tags

token permissions (fine-grained personal access token):
  Contents: read and write         releases
  Administration: read and write   protect

environment variables:
  GITHUB_TOKEN, GH_TOKEN   use this token instead of the saved session
  GITHUB_CONF_DIR          credentials folder (default ~/.config/github)
  GITHUB_CACERT            extra CA (default ca.pem in the credentials folder)
  NO_COLOR                 turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the GitHub API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="save a personal access token after checking it")
    p.add_argument("--token-stdin", action="store_true", help="read the token from stdin")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", help="print the access token").set_defaults(func=cmd_token)
    p = sub.add_parser("protect", help="rulesets so only the repo admin can update main and create, move or delete v* tags")
    p.add_argument("repo", metavar="OWNER/REPO")
    p.add_argument("--dry-run", action="store_true", help="print the requests that would be sent and send nothing")
    p.set_defaults(func=cmd_protect)
    api.add_api_parser(sub, prog, "repos/<owner>/<repo>/pulls",
                        lambda gh, args: api.run_api(args, gh.call, args.ctx, next_page), api_prefix="/")
    return parser


def main(argv, ctx=None, prog="clt github"):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(api.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    ctx.check_session_command(args.command)
    args.ctx = ctx
    return args.func(GitHub(), args)
