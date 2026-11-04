"""The GitHub API client: a token from the environment or the saved session, and renewing expiring ones."""
import os
import time
from pathlib import Path

from cli_tools.core import api, credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError
from cli_tools.core.output import info
from cli_tools.tools.github import auth

API_ROOT = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
# Renew a little before the token expires, so a call is not wasted on a 401
EXPIRY_MARGIN = 60


def _expires_at(seconds):
    return int(time.time()) + int(seconds) if isinstance(seconds, (int, float)) else None


class GitHub:
    def __init__(self):
        self.conf_dir = Path(os.environ.get("GITHUB_CONF_DIR") or Path.home() / ".config" / "github")
        self.credentials_file = self.conf_dir / "credentials.json"
        # Optional extra CA (e.g. behind a TLS-inspecting proxy); without it the system store is used
        self.cacert = Path(os.environ.get("GITHUB_CACERT") or self.conf_dir / "ca.pem")
        self.url = API_ROOT

    def request(self, method, path, body=None, token=None):
        return http.json_request(method, http.api_url(self.url, path, prefix="/"), body, token, self.cacert, HEADERS)

    # --- Credentials ---

    def save_session(self, tokens, client_id, previous_refresh=None):
        """Saves a device flow token response. With expiring tokens it carries a refresh token and expiry times;
        GitHub replaces the refresh token on every renewal, so the last one it returned is kept."""
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("The authorization response has no access_token")
        credentials.write_private_json(self.credentials_file, {
            "token": token,
            "refresh_token": tokens.get("refresh_token") or previous_refresh,
            "expires_at": _expires_at(tokens.get("expires_in")),
            "refresh_expires_at": _expires_at(tokens.get("refresh_token_expires_in")),
            "client_id": client_id,
        })

    def load_credentials(self):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:  # a token from the environment is used as is and never renewed
            return {"token": token}
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold("clt github login") + " or set GITHUB_TOKEN")
        if not isinstance(creds, dict) or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run clt github login")
        return creds

    def refresh(self, creds):
        """True if it renewed the token; False if it cannot be renewed (a token from the environment, a pasted one
        or a session without expiring tokens). Raises ToolError if GitHub refuses the renewal."""
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            return False
        client_id = creds.get("client_id") or auth.client_id()
        refresh_expires_at = creds.get("refresh_expires_at")
        tokens = None
        if not (isinstance(refresh_expires_at, int) and refresh_expires_at < time.time()):
            tokens = auth.refresh_tokens(client_id, refresh_token, self.cacert)
        if tokens is None:
            # Another clt may have renewed it first: GitHub then refuses the refresh token this one read
            saved = credentials.read_json_file(self.credentials_file)
            if isinstance(saved, dict) and saved.get("token") and saved.get("refresh_token") != refresh_token:
                return True
            raise ToolError("GitHub did not renew the session (it expired or was revoked); run "
                            + output.err.bold("clt github login") + " again")
        self.save_session(tokens, client_id, refresh_token)
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
            return self.request(method, path, body, creds["token"])
        except HttpError as e:
            # Tokens without a refresh token (personal access tokens, from the environment) do not renew
            if e.status != 401 or not self.refresh(creds):
                raise
            info(output.err.dim("GitHub answered 401; token renewed and request retried."))
            return self.request(method, path, body, self.load_credentials()["token"])


def next_page(headers, body=None, path=None):
    return api.next_page_path(headers, api_root=API_ROOT + "/")
