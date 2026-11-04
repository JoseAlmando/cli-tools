"""Command-line client for the GitHub API: log in from the browser or with a token, then call the API."""
import argparse
import getpass
import json
import os
import sys

from cli_tools.core import api, context, credentials, http, output
from cli_tools.core.errors import HttpError, ToolError, error_detail
from cli_tools.core.output import info
from cli_tools.tools.github import auth
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

def choose_login():
    ready = bool(auth.client_id())
    info("How do you want to log in to GitHub?")
    info("  [1] Authorize in the browser" + (" (recommended)" if ready else
                                             " (not set up here: no OAuth app; see clt github login --help)"))
    info("  [2] Create a token in the browser and paste it here")
    info("  [3] Paste a token you already have")
    default = "1" if ready else "2"
    while True:
        sys.stderr.write(f"Choice [{default}]: ")
        sys.stderr.flush()
        answer = sys.stdin.readline().strip() or default
        if answer in ("1", "2", "3"):
            return {"1": "browser", "2": "create", "3": "paste"}[answer]
        info(output.err.yellow("Answer 1, 2 or 3."))


def cmd_login(gh, args):
    method = ("stdin" if args.token_stdin else "browser" if args.browser else "create" if args.create_token
              else None)
    # Only a link printed for someone else to open (--browser --no-browser) can go without a terminal
    if method != "stdin" and not (method == "browser" and args.no_browser):
        context.require_terminal("clt github login")
    method = method or choose_login()
    tokens = None
    if method == "browser":
        tokens = auth.device_login(gh.cacert, open_browser=not args.no_browser)
        token = tokens["access_token"]
    elif method == "stdin":
        token = sys.stdin.readline().strip()
    else:
        if method == "create":
            info(output.err.cyan("Create the token on this page (its name and permissions are filled in), "
                                 "then paste it here:"))
            auth.show(auth.token_page_url(), not args.no_browser)
        token = getpass.getpass(output.err.cyan("GitHub token: ")).strip()
    if not token:
        raise ToolError("No token given")
    try:
        _, raw, _ = gh.request("GET", "user", token=token)
    except HttpError as e:
        raise ToolError(f"GitHub rejected the token: {error_detail(e.body)}") from None
    me = http.parse_json(raw, "Checking the token")
    if tokens:  # the device flow's token, which may expire and renew itself
        gh.save_session(tokens, auth.client_id())
    else:  # a personal access token is saved as it is
        credentials.write_private_json(gh.credentials_file, {"token": token})
    info(output.err.green("✓ ") + f"Logged in to GitHub as {output.err.bold(me.get('login', '?'))}")


def cmd_refresh(gh, args):
    if not gh.refresh(gh.load_credentials()):
        raise ToolError("This token cannot be renewed: only a session from authorizing in the browser renews; "
                        "for a new token, run " + output.err.bold("clt github login"))
    info(output.err.green("✓ ") + "Token renewed.")


def cmd_logout(gh, args):
    gh.credentials_file.unlink(missing_ok=True)
    info(output.err.green("✓ ") + "Logged out.")


def cmd_token(gh, args):
    print(gh.fresh_credentials()["token"])


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
  {prog} login                                   # asks: authorize in the browser, create a token, or paste one
  {prog} login --browser                         # enter a code at github.com/login/device
  {prog} login --create-token                    # opens the token page with clt's permissions filled in
  {prog} me
  {prog} GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title --all
  {prog} GET "repos/<owner>/<repo>/releases" --fields tag_name,html_url --max-items 5
  {prog} protect <owner>/<repo> --dry-run        # only the owner may update main and v* tags

token permissions (fine-grained personal access token):
  Contents: read and write         releases
  Administration: read and write   protect
authorizing in the browser asks for the "repo" scope (releases and rulesets; OAuth apps cannot ask
for less) and the "project" scope (Projects boards); its token renews itself before it expires

environment variables:
  GITHUB_TOKEN, GH_TOKEN   use this token instead of the saved session
  GITHUB_CLIENT_ID         OAuth app (with device flow enabled) for login --browser
  GITHUB_CONF_DIR          credentials folder (default ~/.config/github)
  GITHUB_CACERT            extra CA (default ca.pem in the credentials folder)
  NO_COLOR                 turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, refresh, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the GitHub API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", aliases=context.command_aliases("login"),
                       help="log in from the browser or with a token, and save the session")
    how = p.add_mutually_exclusive_group()
    how.add_argument("--browser", action="store_true", help="authorize in the browser with a one-time code")
    how.add_argument("--create-token", action="store_true",
                     help="open GitHub's token page with clt's permissions filled in, then paste the token")
    how.add_argument("--token-stdin", action="store_true", help="read an existing token from stdin")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", aliases=context.command_aliases("refresh"),
                   help="renew the token of a browser login now").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", aliases=context.command_aliases("logout"), help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", aliases=context.command_aliases("token"),
                   help="print the access token (renewed if it expired)").set_defaults(func=cmd_token)
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
    args.command = context.canonical_command(args.command)
    ctx.check_session_command(args.command)
    args.ctx = ctx
    return args.func(GitHub(), args)
