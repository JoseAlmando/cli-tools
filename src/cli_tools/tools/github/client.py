"""The GitHub API client: a personal access token, from the environment or saved."""
import os
from pathlib import Path

from cli_tools.core import api, credentials, http, output
from cli_tools.core.errors import NotLoggedIn, ToolError

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
        return http.json_request(method, http.api_url(self.url, path, prefix="/"), body, token, self.cacert, HEADERS)

    def load_credentials(self):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:  # a token from the environment is used as is
            return {"token": token}
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold("clt github login") + " or set GITHUB_TOKEN")
        if not isinstance(creds, dict) or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run clt github login")
        return creds

    def call(self, method, path, body):
        # Personal access tokens do not renew: a 401 means a new token is needed
        return self.request(method, path, body, self.load_credentials()["token"])


def next_page(headers):
    return api.next_page_path(headers, api_root=API_ROOT + "/")
