import base64
import os
import urllib.parse
from functools import cached_property
from pathlib import Path

from cli_tools.core import api, context, credentials, http
from cli_tools.core.errors import HttpError, ToolError

PROG = "clt jenkins"
WHO_AM_I = "whoAmI/api/json"


def safe_path(path):
    decoded = urllib.parse.unquote(path.partition("?")[0])
    if "\\" in decoded or any(part.split(";", 1)[0] in (".", "..") for part in decoded.split("/")):
        raise ToolError("Jenkins paths cannot contain backslashes or . or .. segments")
    return http.safe_path(path)


def controller_url(value, *, api_page=True):
    invalid = "JENKINS_URL must be a Jenkins controller URL without credentials, query or fragment"
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "\\?#"):
        raise ToolError(invalid)
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname or "@" in parts.netloc or parts.port == 0:
            raise ToolError(invalid)
    except ValueError:
        raise ToolError(invalid) from None
    if parts.scheme == "http" and not context.env_flag(os.environ, "JENKINS_ALLOW_HTTP"):
        raise ToolError("Jenkins requires HTTPS unless JENKINS_ALLOW_HTTP=1 is set")
    route = safe_path(parts.path).rstrip("/")
    if api_page:
        route = route.removesuffix("/api").rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, route, "", ""))


def job_path(name):
    parts = name.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise ToolError("Use the full job name with folders separated by /, e.g. Folder/Job")
    return "/".join(f"job/{urllib.parse.quote(part, safe='')}" for part in parts)


def authorization(user, token):
    if not user or not token or ":" in user:
        raise ToolError("A Jenkins username (without a colon) and an API token are needed")
    return "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode("ascii")


class Jenkins:
    def __init__(self, url=None):
        self.explicit_url = url
        conf_dir = Path(os.environ.get("JENKINS_CONF_DIR") or Path.home() / ".config" / "jenkins")
        self.credentials_file = conf_dir / "credentials.json"
        self.cacert = Path(os.environ.get("JENKINS_CACERT") or conf_dir / "ca.pem")

    @cached_property
    def saved(self):
        session = credentials.read_json_file(self.credentials_file)
        if session is None and not self.credentials_file.exists():
            return {}
        if not isinstance(session, dict) or not all(isinstance(session.get(key), str) and session[key]
                                                   for key in ("url", "user", "token")):
            raise ToolError(f"The Jenkins saved session is incomplete; run {PROG} login again")
        return session

    @cached_property
    def url(self):
        configured = self.explicit_url or os.environ.get("JENKINS_URL")
        if configured:
            return controller_url(configured)
        saved_url = self.saved.get("url")
        if not saved_url:
            raise ToolError(f"No Jenkins server configured; set JENKINS_URL or run {PROG} login --url https://server")
        return controller_url(saved_url, api_page=False)

    def auth_header(self):
        user, token = os.environ.get("JENKINS_USER"), os.environ.get("JENKINS_TOKEN")
        if user or token:
            env_url = os.environ.get("JENKINS_URL")
            if not (user and token and env_url):
                raise ToolError("Set JENKINS_URL, JENKINS_USER and JENKINS_TOKEN together")
            if controller_url(env_url) != self.url:
                raise ToolError("The Jenkins environment credentials are for another server")
            return authorization(user, token)
        if self.saved:
            if self.saved["url"] != self.url:
                raise ToolError(f"The Jenkins saved session is for another server; run {PROG} login again")
            return authorization(self.saved["user"], self.saved["token"])
        return None

    def request(self, method, path, body, auth):
        url = http.api_url(self.url, safe_path(path), prefix="/")
        headers = {"Authorization": auth} if auth else None
        return http.json_request(method, url, body, None, self.cacert, headers)

    def call(self, method, path, body):
        return self.request(method, path, body, self.auth_header())

    def get_object(self, path):
        _, raw, _ = self.call("GET", path, None)
        return read_object(raw, path)

    def login(self, user, token):
        try:
            _, raw, _ = self.request("GET", WHO_AM_I, None, authorization(user, token))
        except HttpError as exc:
            if exc.status not in (401, 403):
                raise
            raise ToolError("Jenkins rejected the username or API token") from None
        identity = read_object(raw, WHO_AM_I)
        if identity.get("authenticated") is not True or not identity.get("name"):
            raise ToolError("Jenkins did not authenticate the username and API token")
        credentials.write_private_json(self.credentials_file, {"url": self.url, "user": user, "token": token})
        return identity["name"]

    def next_page(self, headers, body, path):
        return api.next_page_path(headers, body, path, api_root=self.url + "/")


def read_object(raw, path):
    data = http.parse_json(raw, f"GET {path.partition('?')[0]}")
    if not isinstance(data, dict):
        raise ToolError(f"Expected a JSON object from {path.partition('?')[0]}")
    return data
