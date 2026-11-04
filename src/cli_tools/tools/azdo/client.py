import base64
import os
import re
import urllib.parse
from pathlib import Path

from cli_tools.core import credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError

PROG = "clt azdo"
ORG_VAR = "AZDO_ORG"
HOST = "https://dev.azure.com/"
API_VERSION = "7.1"
CONNECTION_DATA = "_apis/connectionData?api-version=7.1-preview"
TOKEN_SCOPES = ("Work Items (Read & write)", "Code (Read & write)", "Project and Team (Read)")
TOKEN_VARS = ("AZDO_TOKEN", "AZURE_DEVOPS_EXT_PAT")
REJECTED = "Azure DevOps did not accept the token (it answered with its sign-in page); check it or run "
_ORG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,49}")
_PATCHED = (("POST", re.compile(r"(^|/)_apis/wit/workitems/\$[^/]+$", re.I)),
            ("PATCH", re.compile(r"(^|/)_apis/wit/workitems/\d+$", re.I)))


def org_url(value):
    name = value.strip() if isinstance(value, str) else ""
    if name.lower().startswith(HOST):
        name = name[len(HOST):].rstrip("/")
    if not _ORG.fullmatch(name):
        raise ToolError(f"{value!r} is not an Azure DevOps organization; give its name, as in "
                        f"{HOST}<organization>")
    return HOST + name


def basic_header(token):
    return "Basic " + base64.b64encode(f":{token}".encode()).decode("ascii")


def with_api_version(path, version):
    query = path.partition("?")[2]
    if any(name.lower() == "api-version" for name, _ in urllib.parse.parse_qsl(query, keep_blank_values=True)):
        return path
    return path + ("&" if "?" in path else "?") + "api-version=" + urllib.parse.quote(version, safe="")


def content_type(method, path):
    route = path.partition("?")[0]
    if any(method == wanted and pattern.search(route) for wanted, pattern in _PATCHED):
        return "application/json-patch+json"
    return "application/json"


class AzureDevOps:
    def __init__(self, org=None, require_url=True):
        self.conf_dir = Path(os.environ.get("AZDO_CONF_DIR") or Path.home() / ".config" / "azdo")
        self.credentials_file = self.conf_dir / "credentials.json"
        self.cacert = Path(os.environ.get("AZDO_CACERT") or self.conf_dir / "ca.pem")
        env = os.environ.get(ORG_VAR, "")
        self.url = None
        if require_url:
            saved = credentials.read_json_file(self.credentials_file)
            if not (org or env or (isinstance(saved, dict) and saved.get("url"))):
                raise ToolError(f"No organization configured; use {PROG} login --org <organization> or set {ORG_VAR}")
            self.url = org_url(http.resolve_url(org_url(org) if org else None, org_url(env) if env else "",
                                                self.credentials_file, PROG, ORG_VAR))

    def request(self, method, path, body=None, token=None):
        version = os.environ.get("AZDO_API_VERSION") or API_VERSION
        url = http.api_url(self.url, http.safe_path(with_api_version(path, version)), "/")
        headers = {"Authorization": basic_header(token), "X-TFS-FedAuthRedirect": "Suppress",
                   "Content-Type": content_type(method, path)}
        try:
            status, raw, answer = http.json_request(method, url, body, None, self.cacert, headers)
        except HttpError as e:
            if 300 <= e.status < 400:
                raise ToolError(REJECTED + f"{PROG} login") from None
            raise
        if status == 203:
            raise ToolError(REJECTED + f"{PROG} login")
        return status, raw, answer

    def check_token(self, token):
        _, raw, _ = self.request("GET", CONNECTION_DATA, None, token)
        data = http.parse_json(raw, "Checking the token")
        user = data.get("authenticatedUser") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            raise ToolError("Unexpected answer from connectionData")
        return user

    def save_credentials(self, token):
        credentials.write_private_json(self.credentials_file, {"url": self.url, "token": token})

    def load_credentials(self):
        token = next((os.environ[name] for name in TOKEN_VARS if os.environ.get(name)), None)
        if token:
            return {"url": self.url, "token": token, "env": True}
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold(f"{PROG} login") + " or set AZDO_TOKEN")
        if not isinstance(creds, dict) or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"The saved session is for {creds.get('url')}, not {self.url}; run {PROG} login")
        return creds

    def call(self, method, path, body):
        return self.request(method, path, body, self.load_credentials()["token"])
