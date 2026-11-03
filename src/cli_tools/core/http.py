"""The configured server and HTTPS requests to it: the token never leaves that server and redirects are not
followed."""
import http.client
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cli_tools.core.credentials import read_json_file
from cli_tools.core.errors import HttpError, ToolError, error_detail


def validate_base_url(url, var):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ToolError(f"{var} must be https://server (it is {url!r})")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ToolError(f"{var} cannot carry a user, query or fragment ({url!r})")
    return url.rstrip("/")


def resolve_url(cli_url, env_url, credentials_file, prog, var):
    """The server: login's --url, otherwise the environment variable, otherwise the last session's."""
    if cli_url:
        return validate_base_url(cli_url, var)
    if env_url:
        return validate_base_url(env_url, var)
    saved = read_json_file(credentials_file)
    if not isinstance(saved, dict) or not saved.get("url"):
        raise ToolError(f"No server configured; use {prog} login --url https://server or set {var}")
    return validate_base_url(saved["url"], var)


def api_url(base, path, prefix="/api/v1/"):
    """Only paths relative to the API: the token never leaves the configured server."""
    if "://" in path or path.startswith("//") or "\\" in path:
        raise ToolError(f"Only paths relative to {base}{prefix} are accepted (got {path!r})")
    return f"{base}{prefix}{path.lstrip('/')}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib forwards Authorization when it follows a redirect, so redirects are not followed
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ssl_context(cacert):
    ctx = ssl.create_default_context()
    if cacert and Path(cacert).is_file():
        try:
            ctx.load_verify_locations(str(cacert))
        except ssl.SSLError as e:
            raise ToolError(f"The certificate {cacert} is not valid: {e}") from None
    return ctx


def send(req, cacert):
    """Returns (status, body, headers). Raises HttpError or ToolError; never exits the program."""
    server = urllib.parse.urlsplit(req.full_url).hostname
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context(cacert)), NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), dict(e.headers)) from None
    except urllib.error.URLError as e:
        raise ToolError(f"Cannot connect to {server}: {e.reason}") from None
    except OSError as e:  # includes TimeoutError
        raise ToolError(f"Network error with {server}: {e}") from None
    except http.client.InvalidURL as e:  # e.g. a space in the path
        raise ToolError(str(e)) from None
    except ValueError:
        # http.client rejects a header with a line break, and its message would show the value: the token
        raise ToolError(f"The request to {server} was not sent: a header has a character HTTP does not allow "
                        "(a line break in the token?)") from None


def json_request(method, url, body, token, cacert, headers=None):
    req = urllib.request.Request(url, data=body.encode("utf-8") if isinstance(body, str) else body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return send(req, cacert)


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError:
        raise ToolError(f"{what}: the server did not return JSON ({error_detail(raw)})") from None
