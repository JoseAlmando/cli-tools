import base64
import json
import os
import re
import urllib.parse
from pathlib import Path

from cli_tools.core import api, credentials, http, output
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError

PROG = "clt azdo"
ORG_VAR = "AZDO_ORG"
HOST = "https://dev.azure.com/"
API_VERSION = "7.1"
CONNECTION_DATA = "_apis/connectionData?api-version=7.1-preview"
TOKEN_SCOPES = ("Work Items (Read & write)", "Code (Read & write)", "Project and Team (Read)")
TOKEN_VARS = ("AZDO_TOKEN", "AZURE_DEVOPS_EXT_PAT")
REJECTED = "Azure DevOps did not accept the token ({}); check it or run " + PROG + " login"
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


def token_variable():
    return next((name for name in TOKEN_VARS if os.environ.get(name)), None)


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
                raise ToolError(REJECTED.format(f"HTTP {e.status}, a redirect to its sign-in page")) from None
            raise
        if status == 203:
            raise ToolError(REJECTED.format("HTTP 203, its sign-in page"))
        return status, raw, answer

    def check_token(self, token):
        try:
            _, raw, _ = self.request("GET", CONNECTION_DATA, None, token)
        except HttpError as e:
            if e.status not in (401, 403):
                raise
            raise ToolError(REJECTED.format(f"HTTP {e.status}")) from None
        data = http.parse_json(raw, "Checking the token")
        user = data.get("authenticatedUser") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            raise ToolError("Unexpected answer from connectionData")
        return user

    def save_credentials(self, token):
        credentials.write_private_json(self.credentials_file, {"url": self.url, "token": token})

    def load_credentials(self):
        name = token_variable()
        if name:
            return {"url": self.url, "token": os.environ[name], "env": name}
        creds = credentials.read_json_file(self.credentials_file)
        only_org = isinstance(creds, dict) and creds.get("url") and not creds.get("token")
        if (creds is None and not self.credentials_file.exists()) or only_org:
            raise NotLoggedIn("Not logged in; run " + output.err.bold(f"{PROG} login") + " or set AZDO_TOKEN")
        if not isinstance(creds, dict) or not creds.get("token"):
            raise ToolError(f"{self.credentials_file} is incomplete; run {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"The saved session is for {creds.get('url')}, not {self.url}; run {PROG} login")
        return creds

    def forget_token(self):
        saved = credentials.read_json_file(self.credentials_file)
        if isinstance(saved, dict) and saved.get("url"):
            credentials.write_private_json(self.credentials_file, {"url": saved["url"]})
        else:
            self.credentials_file.unlink(missing_ok=True)

    def call(self, method, path, body):
        return self.request(method, path, body, self.load_credentials()["token"])


def items_key(data):
    return "value" if isinstance(data, dict) and isinstance(data.get("value"), list) else None


def next_page(headers, body=None, path=None):
    if path is None:
        return None
    params = dict(urllib.parse.parse_qsl(path.partition("?")[2], keep_blank_values=True))
    token = {name.lower(): value for name, value in (headers or {}).items()}.get("x-ms-continuationtoken")
    if token:
        return api.set_query(path, {"continuationToken": token}) if token != params.get("continuationToken") else None
    try:
        data = json.loads(body) if body else None
    except ValueError:
        return None
    top, skip = params.get("$top", ""), params.get("$skip", "0") or "0"
    key = items_key(data)
    if key is None or not top.isdigit() or not skip.isdigit() or int(top) == 0 or len(data[key]) < int(top):
        return None
    return api.set_query(path, {"$skip": int(skip) + int(top)})


def pager():
    seen = set()
    previous = []

    def follow(headers, body=None, path=None):
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        items = data.get("value") if isinstance(data, dict) else None
        if items and previous and items == previous[-1]:
            return None
        previous.append(items)
        following = next_page(headers, body, path)
        if following in seen:
            return None
        seen.add(following)
        return following

    return follow


def join(pages):
    key = items_key(pages[0])
    if key is None:
        return [item for page in pages for item in (page if isinstance(page, list) else [page])]
    joined = dict(pages[0])
    kept = [page for number, page in enumerate(pages) if isinstance(page, dict)
            and not (number and page.get(key) and page.get(key) == pages[number - 1].get(key))]
    joined[key] = [item for page in kept for item in page.get(key) or []]
    if "count" in joined:
        joined["count"] = len(joined[key])
    return joined


BATCH = 200  # the most ids GET _apis/wit/workitems takes at once
DEFAULT_COLUMNS = ("System.Title", "System.State")


def field_value(value):
    """A field as one short value: a person by their name, everything else as it is."""
    if isinstance(value, dict) and "displayName" in value:
        return value["displayName"]
    return value


def query_items(azdo, query, project=None, top=200):
    """Runs a WIQL query and reads the columns it selects: a list of {"id", <column>: value, ...} in query order."""
    path = (f"{urllib.parse.quote(project, safe='')}/" if project else "") + f"_apis/wit/wiql?$top={top}"
    _, raw, _ = azdo.call("POST", path, json.dumps({"query": query}))
    answer = http.parse_json(raw, "Running the query")
    if not isinstance(answer, dict) or not isinstance(answer.get("workItems"), list):
        raise ToolError("The query returned links, not work items; run link queries with "
                        f"{PROG} POST '<project>/_apis/wit/wiql' -d @query.json")
    columns = [c.get("referenceName") for c in answer.get("columns") or [] if isinstance(c, dict)]
    columns = [c for c in columns if c and c != "System.Id"] or list(DEFAULT_COLUMNS)
    ids = [item["id"] for item in answer["workItems"] if isinstance(item, dict) and "id" in item]
    found = {}
    for start in range(0, len(ids), BATCH):
        part = ",".join(str(i) for i in ids[start:start + BATCH])
        _, raw, _ = azdo.call("GET", f"_apis/wit/workitems?ids={urllib.parse.quote(part)}"
                                     f"&fields={urllib.parse.quote(','.join(columns))}&errorPolicy=omit", None)
        data = http.parse_json(raw, "Reading the work items")
        for item in (data.get("value") if isinstance(data, dict) else None) or []:
            if isinstance(item, dict):
                found[item.get("id")] = item.get("fields") or {}
    return [{"id": i, **{c: field_value(found[i].get(c)) for c in columns}} for i in ids if i in found]


def wiql_text(value):
    return "'" + value.replace("'", "''") + "'"


def mine_query(work_item_type=None, project=None):
    conditions = ["[System.AssignedTo] = @Me", "[System.State] NOT IN ('Closed', 'Done', 'Removed')"]
    if work_item_type:
        conditions.append(f"[System.WorkItemType] = {wiql_text(work_item_type)}")
    if project:
        conditions.append("[System.TeamProject] = @project")
    return ("SELECT [System.Id], [System.WorkItemType], [System.State], [System.Title], [System.TeamProject] "
            "FROM WorkItems WHERE " + " AND ".join(conditions) + " ORDER BY [System.ChangedDate] DESC")
