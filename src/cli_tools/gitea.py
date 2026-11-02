"""Command-line client for the Gitea API, with OAuth2 (PKCE) login in the browser."""
import argparse
import base64
import hashlib
import http.server
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import core
from .core import ToolError, HttpError, NotLoggedIn, info

PROG = "gitea"
URL_VAR = "GITEA_URL"
# Public OAuth2 app built into Gitea for tea (redirects to 127.0.0.1 on any port)
DEFAULT_CLIENT_ID = "d57cb8c4-630c-4168-8324-ec79935e18d4"
DEFAULT_CALLBACK_PORT = 38291
# Renew a little before the token expires, so a call is not wasted on a 401
EXPIRY_MARGIN = 60


class Gitea:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("GITEA_CONF_DIR") or Path.home() / ".config" / "gitea")
        self.credentials_file = self.conf_dir / "credentials.json"
        # Optional extra CA (e.g. an internal one); without it the system store is used
        self.cacert = Path(os.environ.get("GITEA_CACERT") or self.conf_dir / "ca.pem")
        # Gitea external login source (e.g. an OIDC provider); empty = Gitea's own login page
        self.auth_source = os.environ.get("GITEA_AUTH_SOURCE", "")
        self.client_id = os.environ.get("GITEA_CLIENT_ID", DEFAULT_CLIENT_ID)
        self.callback_port = int(os.environ.get("GITEA_CALLBACK_PORT", DEFAULT_CALLBACK_PORT))
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = core.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return core.json_request(method, core.api_url(self.url, path), body, token, self.cacert)

    def token_request(self, fields):
        """POST to Gitea's OAuth2 token endpoint (outside /api/v1/)."""
        req = urllib.request.Request(f"{self.url}/login/oauth/access_token",
                                     data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        return core.send(req, self.cacert)

    # --- Credentials ---

    def save_credentials(self, tokens, previous_refresh="", client_id=None):
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("The authentication response has no access_token")
        expires_in = tokens.get("expires_in")
        core.write_private_json(self.credentials_file, {
            "url": self.url,
            "client_id": client_id or self.client_id,
            "access_token": token,
            # Gitea may invalidate the refresh token once used; always keep the last one it returned
            "refresh_token": tokens.get("refresh_token") or previous_refresh,
            "expires_at": int(time.time()) + int(expires_in) if isinstance(expires_in, (int, float)) else None,
        })

    def load_credentials(self):
        token = os.environ.get("GITEA_TOKEN")
        if token:  # e.g. a personal access token; it is used as is and cannot be renewed
            return {"url": self.url, "access_token": token}
        creds = core.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + core.err.bold(f"{PROG} login") + " or set GITEA_TOKEN")
        if not isinstance(creds, dict) or not creds.get("access_token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"The saved session is for {creds.get('url')}, not {self.url}; run {PROG} login")
        return creds

    def refresh(self, creds):
        """True if it renewed the token; False if the server rejected the refresh. Other failures propagate."""
        if not creds.get("refresh_token"):
            return False
        client_id = creds.get("client_id") or self.client_id
        try:
            _, raw, _ = self.token_request({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
                                            "client_id": client_id})
        except HttpError as e:
            if e.status in (400, 401, 403):
                return False
            raise
        self.save_credentials(core.parse_json(raw, "Renewing the token"), creds["refresh_token"], client_id)
        return True

    def fresh_credentials(self):
        creds = self.load_credentials()
        expires_at = creds.get("expires_at")
        if isinstance(expires_at, int) and expires_at - EXPIRY_MARGIN < time.time() and self.refresh(creds):
            creds = self.load_credentials()
        return creds

    def call(self, method, path, body):
        creds = self.fresh_credentials()
        try:
            return self.request(method, path, body, creds["access_token"])
        except HttpError as e:
            if e.status != 401 or not self.refresh(creds):
                raise
            info(core.err.dim("The server answered 401; token renewed and request retried."))
            return self.request(method, path, body, self.load_credentials()["access_token"])


# --- Browser login -------------------------------------------------------------

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        CallbackHandler.result = {k: v[0] for k, v in query.items()}
        page = ("Done: you can close this tab and go back to the terminal." if "code" in query
                else "Login failed: " + query.get("error_description", query["error"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8><title>{PROG} login</title><p>{page}</p>".encode())

    def log_message(self, *args):
        pass  # keep the terminal quiet


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login_url(g, source, redirect_uri, state, challenge):
    authorize = "/login/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": g.client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    # With a login source, go straight to it; Gitea keeps redirect_to and comes back to the OAuth2 authorize page
    if source:
        return f"{g.url}/user/oauth2/{urllib.parse.quote(source)}?redirect_to={urllib.parse.quote(authorize)}"
    return g.url + authorize


def wait_for_callback(port, timeout, url, open_browser):
    CallbackHandler.result = None
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError as e:
        raise ToolError(f"Could not listen on 127.0.0.1:{port} ({e}); set GITEA_CALLBACK_PORT") from None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    info(core.err.cyan("Open this link to log in:"))
    info(url)
    if open_browser:
        webbrowser.open(url)
    try:
        deadline = time.time() + timeout
        while CallbackHandler.result is None and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
    if CallbackHandler.result is None:
        raise ToolError(f"No answer from the browser within {timeout} s")
    return CallbackHandler.result


def cmd_login(g, args):
    if not args.no_browser:
        core.require_terminal(f"{PROG} login")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://127.0.0.1:{g.callback_port}"
    source = args.source if args.source is not None else g.auth_source
    result = wait_for_callback(g.callback_port, args.timeout, login_url(g, source, redirect_uri, state, challenge),
                               not args.no_browser)
    if result.get("state") != state:
        raise ToolError("The browser's answer does not belong to this login (state mismatch)")
    if "code" not in result:
        raise ToolError("Gitea rejected the login: " + result.get("error_description", result.get("error", "?")))
    try:
        _, raw, _ = g.token_request({"grant_type": "authorization_code", "code": result["code"],
                                     "redirect_uri": redirect_uri, "client_id": g.client_id, "code_verifier": verifier})
    except HttpError as e:
        raise ToolError(f"Could not get the token: {core.error_detail(e.body)}") from None
    g.save_credentials(core.parse_json(raw, "Logging in"))
    _, raw, _ = g.request("GET", "user", token=g.load_credentials()["access_token"])
    me = core.parse_json(raw, "Getting the user")
    info(core.err.green("✓ ") + f"Logged in to {g.url} as {core.err.bold(me.get('login', '?'))}")


def cmd_refresh(g, args):
    if not g.refresh(g.load_credentials()):
        raise ToolError("The server rejected the renewal; run " + core.err.bold(f"{PROG} login"))
    info(core.err.green("✓ ") + "Token renewed.")


def cmd_logout(g, args):
    g.credentials_file.unlink(missing_ok=True)
    info(core.err.green("✓ ") + "Logged out.")


def cmd_token(g, args):
    print(g.fresh_credentials()["access_token"])


def cmd_me(g, args):
    _, raw, _ = g.call("GET", "user", None)
    me = core.parse_json(raw, "Getting the user")
    if not isinstance(me, dict):
        raise ToolError("Unexpected response from user")
    print(f"{core.out.bold(me.get('full_name') or me.get('login', ''))} ({core.out.cyan(me.get('login', '?'))}) on {g.url}")
    expires_at = g.load_credentials().get("expires_at")
    if isinstance(expires_at, int):
        print(core.out.dim(f"Token valid until {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires_at))}"))
    print(core.out.dim(f"Credentials: {g.credentials_file}"))


def build_parser(prog=PROG):
    epilog = f"""\
examples:
  {prog} login --url https://gitea.example.com       # the first time; opens the browser
  {prog} login --source <source>                      # go straight to a Gitea external login source (OIDC)
  {prog} me
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
  --json            errors as one JSON line on stderr (also CLT_JSON=1)
  --read-only       send only GET requests (also CLT_READONLY=1);
                    login, refresh, logout and token are blocked too
  exit codes        0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
    parser = argparse.ArgumentParser(prog=prog, description="Client for the Gitea API.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="log in through the browser (OAuth2 + PKCE) and save the credentials")
    p.add_argument("--url", help="Gitea server, e.g. https://gitea.example.com")
    p.add_argument("--source", help="Gitea external login source to go straight to (GITEA_AUTH_SOURCE)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser; only print the link")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser (300)")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", help="renew the token without opening the browser").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", help="delete the saved credentials").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="show the logged-in user").set_defaults(func=cmd_me)
    sub.add_parser("token", help="print the access token (renewed if it expired)").set_defaults(func=cmd_token)
    core.add_api_parser(sub, prog, "repos/<owner>/<repo>/pulls", lambda g, args: core.run_api(args, g.call))
    return parser


def main(argv, prog=PROG):
    parser = build_parser(prog)
    args = parser.parse_args(core.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    core.check_read_only_command(args.command)
    # logout only deletes the file, so it does not need to know the server
    return args.func(Gitea(getattr(args, "url", None), require_url=args.command != "logout"), args)


def run():
    return core.run(main)
