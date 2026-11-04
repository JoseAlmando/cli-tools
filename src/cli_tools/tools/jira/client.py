import base64
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from cli_tools.core import api, credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError

PROG = "clt jira"
URL_VAR = "JIRA_URL"
API_PREFIX = "/rest/api/3/"
GATEWAY = "https://api.atlassian.com"
TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"
SCOPES = ("read:jira-work", "write:jira-work", "read:jira-user")
REJECTED = (401, 403)
_CLOUD_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def basic_header(email, token):
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode("ascii")


def check_cloud_id(value):
    if not isinstance(value, str) or not _CLOUD_ID.fullmatch(value.lower()):
        raise ToolError(f"{value!r} is not a Jira cloud id (expected a UUID such as "
                        "11111111-2222-3333-4444-555555555555)")
    return value.lower()


class Jira:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("JIRA_CONF_DIR") or Path.home() / ".config" / "jira")
        self.credentials_file = self.conf_dir / "credentials.json"
        self.cacert = Path(os.environ.get("JIRA_CACERT") or self.conf_dir / "ca.pem")
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = http.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def base(self, creds):
        if creds.get("mode") == "scoped":
            return GATEWAY, f"/ex/jira/{check_cloud_id(creds.get('cloud_id'))}/rest/api/3/"
        return self.url, API_PREFIX

    def request(self, method, path, body=None, creds=None):
        root, prefix = self.base(creds)
        headers = {"Authorization": basic_header(creds["email"], creds["token"])}
        return http.json_request(method, http.api_url(root, path, prefix), body, None, self.cacert, headers)

    def cloud_id(self):
        req = urllib.request.Request(f"{self.url}/_edge/tenant_info", method="GET")
        req.add_header("Accept", "application/json")
        try:
            _, raw, _ = http.send(req, self.cacert)
            return check_cloud_id(json.loads(raw)["cloudId"])
        except (ToolError, ValueError, KeyError, TypeError):
            raise ToolError(f"Could not find the cloud id of {self.url} (it is the cloudId in "
                            f"{self.url}/_edge/tenant_info); pass it with --cloud-id") from None

    def detect(self, email, token, cloud_id=None, scoped=False):
        creds = {"url": self.url, "email": email, "token": token, "mode": "site", "cloud_id": None}
        if not scoped:
            try:
                return creds, self._myself(creds)
            except HttpError as e:
                if e.status not in REJECTED:
                    raise
        creds = {**creds, "mode": "scoped", "cloud_id": check_cloud_id(cloud_id) if cloud_id else self.cloud_id()}
        try:
            return creds, self._myself(creds)
        except HttpError as e:
            if e.status not in REJECTED:
                raise
            raise ToolError("Jira rejected the token" + ("" if scoped else " on the site and through api.atlassian.com")
                            + "; check the email and the token") from None

    def _myself(self, creds):
        _, raw, _ = self.request("GET", "myself", None, creds)
        me = http.parse_json(raw, "Checking the token")
        if not isinstance(me, dict):
            raise ToolError("Unexpected answer from myself")
        return me

    def save_credentials(self, creds):
        credentials.write_private_json(self.credentials_file, {key: creds.get(key) for key in
                                                               ("url", "email", "token", "mode", "cloud_id")})

    def load_credentials(self):
        email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_TOKEN")
        if email and token:
            cloud_id = os.environ.get("JIRA_CLOUD_ID")
            return {"url": self.url, "email": email, "token": token, "mode": "scoped" if cloud_id else "site",
                    "cloud_id": cloud_id, "env": True}
        creds = credentials.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise NotLoggedIn("Not logged in; run " + output.err.bold(f"{PROG} login")
                              + " or set JIRA_EMAIL and JIRA_TOKEN")
        if not isinstance(creds, dict) or not creds.get("email") or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"The saved session is for {creds.get('url')}, not {self.url}; run {PROG} login")
        return creds

    def call(self, method, path, body):
        creds = self.load_credentials()
        try:
            return self.request(method, path, body, creds)
        except HttpError as e:
            if e.status == 401 and creds.get("env") and creds["mode"] == "site":
                raise ToolError("Jira rejected JIRA_EMAIL and JIRA_TOKEN on the site; if the token has scopes, "
                                "set JIRA_CLOUD_ID too") from None
            raise


ITEM_KEYS = ("issues", "values", "comments", "worklogs")
PAGING_KEYS = ("nextPageToken", "isLast", "startAt", "maxResults")


def items_key(data):
    if not isinstance(data, dict):
        return None
    return next((key for key in ITEM_KEYS if isinstance(data.get(key), list)), None)


def next_page(headers, body=None, path=None):
    try:
        data = json.loads(body) if body else None
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("isLast") is True or path is None:
        return None
    token = data.get("nextPageToken")
    if token:
        sent = dict(urllib.parse.parse_qsl(path.partition("?")[2])).get("nextPageToken")
        return api.set_query(path, {"nextPageToken": token}) if token != sent else None
    key = items_key(data)
    start, total = data.get("startAt"), data.get("total")
    if key is None or not isinstance(start, int) or not data[key]:
        return None
    following = start + len(data[key])
    if isinstance(total, int) and following >= total:
        return None
    if isinstance(data.get("maxResults"), int) and len(data[key]) < data["maxResults"]:
        return None
    return api.set_query(path, {"startAt": following})


def join(pages):
    key = items_key(pages[0])
    if key is None:
        return [item for page in pages for item in (page if isinstance(page, list) else [page])]
    joined = {name: value for name, value in pages[0].items() if name not in PAGING_KEYS}
    joined[key] = [item for page in pages if isinstance(page, dict) for item in page.get(key) or []]
    return joined
