"""Command-line client for the Taiga API."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import core
from .core import ToolError, HttpError, NotLoggedIn, info

PROG = "taiga"
URL_VAR = "TAIGA_URL"


class Taiga:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("TAIGA_CONF_DIR") or Path.home() / ".config" / "taiga")
        self.credentials_file = self.conf_dir / "credentials.json"
        # Optional extra CA (e.g. an internal one); without it the system store is used
        self.cacert = Path(os.environ.get("TAIGA_CACERT") or self.conf_dir / "ca.pem")
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = core.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return core.json_request(method, core.api_url(self.url, path), body, token, self.cacert)

    # --- Credentials ---

    def save_credentials(self, auth, previous_refresh=""):
        token = auth.get("auth_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("The authentication response has no auth_token")
        core.write_private_json(self.credentials_file,
                                {"url": self.url, "auth_token": token, "refresh": auth.get("refresh") or previous_refresh})

    def load_credentials(self):
        token = os.environ.get("TAIGA_TOKEN")
        if token:  # a token from the environment is used as is; it cannot be renewed
            return {"url": self.url, "auth_token": token}
        creds = core.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + core.err.bold(f"{PROG} login") + " or set TAIGA_TOKEN")
        if not isinstance(creds, dict) or not creds.get("auth_token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"The saved session is for {creds.get('url')}, not {self.url}; run {PROG} login")
        return creds

    def refresh(self, creds):
        """True if it renewed the token; False if the server rejected the refresh. Other failures propagate."""
        if not creds.get("refresh"):
            return False
        try:
            _, raw, _ = self.request("POST", "auth/refresh", json.dumps({"refresh": creds["refresh"]}))
        except HttpError as e:
            if e.status in (400, 401, 403):
                return False
            raise
        self.save_credentials(core.parse_json(raw, "Renewing the token"), creds["refresh"])
        return True

    def call(self, method, path, body):
        creds = self.load_credentials()
        try:
            return self.request(method, path, body, creds["auth_token"])
        except HttpError as e:
            if e.status != 401 or not self.refresh(creds):
                raise
            info(core.err.dim("The server answered 401; token renewed and request retried."))
            return self.request(method, path, body, self.load_credentials()["auth_token"])


# --- Commands -----------------------------------------------------------------

def cmd_login(t, args):
    if args.password_stdin:
        if not args.user:
            raise ToolError("--password-stdin needs --user")
        user, password = args.user, sys.stdin.readline().rstrip("\r\n")
    else:
        core.require_terminal(f"{PROG} login")
        user = args.user or input(core.err.cyan("Username or email: "))
        password = getpass.getpass(core.err.cyan("Password: "))
    body = json.dumps({"type": "normal", "username": user, "password": password})
    try:
        _, raw, _ = t.request("POST", "auth", body)
    except HttpError as e:
        raise ToolError(f"Login failed: {core.error_detail(e.body)}") from None
    data = core.parse_json(raw, "Logging in")
    t.save_credentials(data)
    info(core.err.green("✓ ") + f"Logged in to {t.url} as {core.err.bold(data.get('username', user))}")


def cmd_refresh(t, args):
    if not t.refresh(t.load_credentials()):
        raise ToolError("The server rejected the renewal; run " + core.err.bold(f"{PROG} login"))
    info(core.err.green("✓ ") + "Token renewed.")


def cmd_logout(t, args):
    t.credentials_file.unlink(missing_ok=True)
    info(core.err.green("✓ ") + "Logged out.")


def cmd_token(t, args):
    print(t.load_credentials()["auth_token"])


def cmd_me(t, args):
    _, raw, _ = t.call("GET", "users/me", None)
    me = core.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from users/me")
    if core.json_mode():
        from_env = bool(os.environ.get("TAIGA_TOKEN"))
        print(json.dumps({"id": me.get("id"), "username": me.get("username"), "full_name": me.get("full_name_display"),
                          "email": me.get("email"), "url": t.url,
                          "credentials_file": None if from_env else str(t.credentials_file)}, ensure_ascii=False))
        return
    print(f"{core.out.bold(me.get('full_name_display', ''))} ({core.out.cyan(me.get('username', '?'))}) on {t.url}")
    print(core.out.dim(f"Credentials: {t.credentials_file}"))


def build_parser(prog=PROG):
    epilog = f"""\
examples:
  {prog} login --url https://taiga.example.com      # the first time; after that, {prog} login is enough
  {prog} me
  {prog} GET "userstories?project=<id>"
  {prog} GET "userstories?project=<id>&page=2" -i   # see the x-pagination-* headers
  {prog} PATCH userstories/123 -d '{{"version": 4, "subject": "New title"}}'
  {prog} POST tasks -d @task.json
  export TAIGA_TOKEN="$({prog} token)"               # for other scripts

environment variables:
  TAIGA_URL         server, https only; if unset, the last session's
  TAIGA_TOKEN       use this token instead of the saved session (not renewed)
  TAIGA_CONF_DIR    credentials folder (default ~/.config/taiga)
  TAIGA_CACERT      extra CA (default ca.pem in the credentials folder)
  NO_COLOR          turns colors off

for scripts and AI agents (any position on the command line):
  --json            errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, refresh, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Taiga API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="log in and save the credentials")
    p.add_argument("-u", "--user", help="username or email (asked for if missing)")
    p.add_argument("--url", help="Taiga server, e.g. https://taiga.example.com")
    p.add_argument("--password-stdin", action="store_true", help="read the password from stdin (needs --user)")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", help="renew the token without asking for the password").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", help="print the access token").set_defaults(func=cmd_token)
    core.add_api_parser(sub, prog, "userstories?project=<id>", lambda t, args: core.run_api(args, t.call))
    return parser


def main(argv, prog=PROG):
    parser = build_parser(prog)
    args = parser.parse_args(core.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    core.check_read_only_command(args.command)
    # logout only deletes the file, so it does not need to know the server
    return args.func(Taiga(getattr(args, "url", None), require_url=args.command != "logout"), args)


def run():
    return core.run(main)
