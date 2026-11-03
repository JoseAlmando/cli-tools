"""Getting a GitHub token in the browser: OAuth device flow, or a pre-filled page to create one; and renewing
the device flow's expiring tokens."""
import os
import time
import urllib.parse
import urllib.request
import webbrowser

from cli_tools.core import http, output
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.core.output import info

# Public client ID of the "clt" OAuth app (device flow enabled); GITHUB_CLIENT_ID sets another
DEFAULT_CLIENT_ID = "Ov23liksTJsgDjqbhwaS"
# repo: releases and rulesets on the user's repos (OAuth apps cannot ask for anything narrower);
# project: managing GitHub Projects boards
SCOPE = "repo project"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new"
TOKEN_TEMPLATE = {"name": "clt", "description": "clt: releases and repository protection", "expires_in": "90",
                  "contents": "write", "administration": "write"}


def client_id():
    return os.environ.get("GITHUB_CLIENT_ID") or DEFAULT_CLIENT_ID


def token_page_url():
    """GitHub's page to create a fine-grained token, with clt's name and permissions filled in."""
    return f"{TOKEN_PAGE}?{urllib.parse.urlencode(TOKEN_TEMPLATE)}"


def _form_post(url, fields, cacert):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    _, raw, _ = http.send(req, cacert)
    data = http.parse_json(raw, "Talking to GitHub")
    if not isinstance(data, dict):
        raise ToolError("Unexpected answer from GitHub")
    return data


def show(url, open_browser):
    info(url)
    if open_browser:
        webbrowser.open(url)


def device_login(cacert, open_browser=True):
    """The token response for the user who enters the code shown here at github.com/login/device: access_token and,
    when the OAuth app issues expiring tokens, expires_in, refresh_token and refresh_token_expires_in."""
    cid = client_id()
    if not cid:
        raise ToolError("Authorizing in the browser needs a GitHub OAuth app: set GITHUB_CLIENT_ID, "
                        "or log in with --create-token")
    try:
        codes = _form_post(DEVICE_CODE_URL, {"client_id": cid, "scope": SCOPE}, cacert)
    except HttpError as e:
        if e.status != 404:
            raise
        raise ToolError(f"GitHub does not know the OAuth app {cid}; check GITHUB_CLIENT_ID") from None
    if "device_code" not in codes:
        raise ToolError("GitHub did not start the authorization: "
                        + codes.get("error_description", codes.get("error", "?")))
    info(output.err.cyan("Enter this code in the browser: ") + output.err.bold(codes["user_code"]))
    # Device codes can be phished ("go to github.com/login/device and type this"): say whose code to trust
    info(output.err.yellow("Only enter this code if it appeared in your own terminal just now; "
                           "never type a code someone else gave you."))
    show(codes.get("verification_uri", "https://github.com/login/device"), open_browser)
    interval = int(codes.get("interval", 5))
    deadline = time.monotonic() + int(codes.get("expires_in", 900))
    while time.monotonic() < deadline:
        time.sleep(interval)
        answer = _form_post(TOKEN_URL, {"client_id": cid, "device_code": codes["device_code"],
                                        "grant_type": DEVICE_GRANT}, cacert)
        if answer.get("access_token"):
            return answer
        error = answer.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(answer.get("interval", interval + 5))
            continue
        if error == "access_denied":
            raise ToolError("The authorization was cancelled in the browser")
        if error == "expired_token":
            break
        raise ToolError("GitHub refused the authorization: " + answer.get("error_description", error or "?"))
    raise ToolError("The code expired before it was entered; run clt github login again")


def refresh_tokens(cid, refresh_token, cacert):
    """A new token response for a device flow session, or None if GitHub refuses the refresh token.

    Device flow tokens belong to a public client, so no client secret is sent."""
    try:
        answer = _form_post(TOKEN_URL, {"client_id": cid, "grant_type": "refresh_token",
                                        "refresh_token": refresh_token}, cacert)
    except HttpError as e:
        if e.status in (400, 401, 403):
            return None
        raise
    # GitHub answers a refused refresh token with 200 and an error field
    return answer if answer.get("access_token") else None
