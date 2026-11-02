"""Command-line client for the GitHub API, with a personal access token."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import core
from .core import ToolError, HttpError, NotLoggedIn, info

PROG = "github"
API_ROOT = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHub:
    def __init__(self):
        self.conf_dir = Path(os.environ.get("GITHUB_CONF_DIR") or Path.home() / ".config" / "github")
        self.credentials_file = self.conf_dir / "credentials.json"
        # Optional extra CA (e.g. behind a TLS-inspecting proxy); without it the system store is used
        self.cacert = Path(os.environ.get("GITHUB_CACERT") or self.conf_dir / "ca.pem")
        self.url = API_ROOT

    def request(self, method, path, body=None, token=None):
        return core.json_request(method, core.api_url(self.url, path, prefix="/"), body, token, self.cacert, HEADERS)

    def load_credentials(self):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:  # a token from the environment is used as is
            return {"token": token}
        creds = core.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + core.err.bold("clt github login") + " or set GITHUB_TOKEN")
        if not isinstance(creds, dict) or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run clt github login")
        return creds

    def call(self, method, path, body):
        # Personal access tokens do not renew: a 401 means a new token is needed
        return self.request(method, path, body, self.load_credentials()["token"])


def next_page(headers):
    return core.next_page_path(headers, api_root=API_ROOT + "/")


# --- Commands -----------------------------------------------------------------

def cmd_login(gh, args):
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        core.require_terminal("clt github login")
        token = getpass.getpass(core.err.cyan("GitHub token: ")).strip()
    if not token:
        raise ToolError("No token given")
    try:
        _, raw, _ = gh.request("GET", "user", token=token)
    except HttpError as e:
        raise ToolError(f"GitHub rejected the token: {core.error_detail(e.body)}") from None
    me = core.parse_json(raw, "Checking the token")
    core.write_private_json(gh.credentials_file, {"token": token})
    info(core.err.green("✓ ") + f"Logged in to GitHub as {core.err.bold(me.get('login', '?'))}")


def cmd_logout(gh, args):
    gh.credentials_file.unlink(missing_ok=True)
    info(core.err.green("✓ ") + "Logged out.")


def cmd_token(gh, args):
    print(gh.load_credentials()["token"])


def cmd_me(gh, args):
    _, raw, _ = gh.call("GET", "user", None)
    me = core.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from user")
    from_env = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    if core.json_mode():
        print(json.dumps({"id": me.get("id"), "username": me.get("login"), "full_name": me.get("name"),
                          "email": me.get("email"), "url": "https://github.com",
                          "credentials_file": None if from_env else str(gh.credentials_file)}, ensure_ascii=False))
        return
    print(f"{core.out.bold(me.get('name') or me.get('login', ''))} ({core.out.cyan(me.get('login', '?'))}) on GitHub")
    print(core.out.dim("Token from the environment" if from_env else f"Credentials: {gh.credentials_file}"))


def build_parser(prog):
    epilog = f"""\
examples:
  {prog} login                                   # paste a personal access token
  {prog} me
  {prog} GET "repos/<owner>/<repo>/pulls?state=open" --fields number,title --all
  {prog} GET "repos/<owner>/<repo>/releases" --fields tag_name,html_url --max-items 5

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
    core.add_api_parser(sub, prog, "repos/<owner>/<repo>/pulls",
                        lambda gh, args: core.run_api(args, gh.call, next_page), api_prefix="/")
    return parser


def main(argv, prog="clt github"):
    parser = build_parser(prog)
    args = parser.parse_args(core.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    core.check_read_only_command(args.command)
    return args.func(GitHub(), args)
