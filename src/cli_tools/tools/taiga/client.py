"""The Taiga API client: the server, the saved session and its renewal."""
import json
import os
from pathlib import Path

from cli_tools.core import credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError
from cli_tools.core.output import info

PROG = "clt taiga"
URL_VAR = "TAIGA_URL"


class Taiga:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("TAIGA_CONF_DIR") or Path.home() / ".config" / "taiga")
        self.credentials_file = self.conf_dir / "credentials.json"
        # Optional extra CA (e.g. an internal one); without it the system store is used
        self.cacert = Path(os.environ.get("TAIGA_CACERT") or self.conf_dir / "ca.pem")
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = http.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return http.json_request(method, http.api_url(self.url, path), body, token, self.cacert)

    # --- Credentials ---

    def save_credentials(self, auth, previous_refresh=""):
        token = auth.get("auth_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("The authentication response has no auth_token")
        credentials.write_private_json(self.credentials_file,
                                {"url": self.url, "auth_token": token, "refresh": auth.get("refresh") or previous_refresh})

    def load_credentials(self):
        token = os.environ.get("TAIGA_TOKEN")
        if token:  # a token from the environment is used as is; it cannot be renewed
            return {"url": self.url, "auth_token": token}
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold(f"{PROG} login") + " or set TAIGA_TOKEN")
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
        self.save_credentials(http.parse_json(raw, "Renewing the token"), creds["refresh"])
        return True

    def call(self, method, path, body):
        creds = self.load_credentials()
        try:
            return self.request(method, path, body, creds["auth_token"])
        except HttpError as e:
            if e.status != 401 or not self.refresh(creds):
                raise
            info(output.err.dim("The server answered 401; token renewed and request retried."))
            return self.request(method, path, body, self.load_credentials()["auth_token"])
