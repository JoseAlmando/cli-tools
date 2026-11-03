"""The Gitea API client: the server, the saved session and its renewal."""
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from cli_tools.core import credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError
from cli_tools.core.output import info

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
        try:
            self.callback_port = int(os.environ.get("GITEA_CALLBACK_PORT", DEFAULT_CALLBACK_PORT))
        except ValueError:
            raise ToolError("GITEA_CALLBACK_PORT must be a port number") from None
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = http.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return http.json_request(method, http.api_url(self.url, path), body, token, self.cacert)

    def token_request(self, fields):
        """POST to Gitea's OAuth2 token endpoint (outside /api/v1/)."""
        req = urllib.request.Request(f"{self.url}/login/oauth/access_token",
                                     data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        return http.send(req, self.cacert)

    # --- Credentials ---

    def save_credentials(self, tokens, previous_refresh="", client_id=None):
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("The authentication response has no access_token")
        expires_in = tokens.get("expires_in")
        credentials.write_private_json(self.credentials_file, {
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
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold(f"{PROG} login") + " or set GITEA_TOKEN")
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
        self.save_credentials(http.parse_json(raw, "Renewing the token"), creds["refresh_token"], client_id)
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
            info(output.err.dim("The server answered 401; token renewed and request retried."))
            return self.request(method, path, body, self.load_credentials()["access_token"])
