"""Gitea's browser login: OAuth2 authorization code with PKCE, answered on a local callback server."""
import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from cli_tools.core import http, output
from cli_tools.core.errors import HttpError, ToolError, error_detail
from cli_tools.core.output import info
from cli_tools.tools.gitea.client import PROG


class CallbackHandler(BaseHTTPRequestHandler):
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
        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError as e:
        raise ToolError(f"Could not listen on 127.0.0.1:{port} ({e}); set GITEA_CALLBACK_PORT") from None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    info(output.err.cyan("Open this link to log in:"))
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


def browser_login(g, source, timeout, open_browser):
    """The OAuth2 authorization code flow with PKCE, through the browser; returns the token response."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://127.0.0.1:{g.callback_port}"
    result = wait_for_callback(g.callback_port, timeout, login_url(g, source, redirect_uri, state, challenge),
                               open_browser)
    if result.get("state") != state:
        raise ToolError("The browser's answer does not belong to this login (state mismatch)")
    if "code" not in result:
        raise ToolError("Gitea rejected the login: " + result.get("error_description", result.get("error", "?")))
    try:
        _, raw, _ = g.token_request({"grant_type": "authorization_code", "code": result["code"],
                                     "redirect_uri": redirect_uri, "client_id": g.client_id, "code_verifier": verifier})
    except HttpError as e:
        raise ToolError(f"Could not get the token: {error_detail(e.body)}") from None
    return http.parse_json(raw, "Logging in")
