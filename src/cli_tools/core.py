"""Shared building blocks for every tool: HTTP, credentials, output and the api subcommand."""
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Global flags, accepted anywhere on the command line (see take_global_flags)
GLOBAL_FLAGS = ("--json", "--read-only")
MAX_PAGES = 100

# Exit codes: 0 success, 1 error, 2 wrong usage (argparse), 3 not logged in, 4 blocked by read-only mode
EXIT_ERROR, EXIT_USAGE, EXIT_NOT_LOGGED_IN, EXIT_READ_ONLY = 1, 2, 3, 4


class ToolError(Exception):
    """An expected error; run() prints it without a traceback."""
    exit_code = EXIT_ERROR


class NotLoggedIn(ToolError):
    exit_code = EXIT_NOT_LOGGED_IN


class ReadOnlyError(ToolError):
    exit_code = EXIT_READ_ONLY


class HttpError(ToolError):
    def __init__(self, status, body, headers=None):
        super().__init__(f"HTTP {status}: {error_detail(body)}")
        self.status = status
        self.body = body
        self.headers = headers or {}


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def json_errors():
    """Errors as one JSON line on stderr (--json or CLT_JSON=1), for scripts and AI agents."""
    return env_flag("CLT_JSON")


def read_only():
    """Only GET requests are sent (--read-only or CLT_READONLY=1)."""
    return env_flag("CLT_READONLY")


def take_global_flags(argv):
    """Removes the global flags from argv and records them in the environment, so they work in any position."""
    rest = []
    for arg in argv:
        if arg == "--json":
            os.environ["CLT_JSON"] = "1"
        elif arg == "--read-only":
            os.environ["CLT_READONLY"] = "1"
        else:
            rest.append(arg)
    return rest


def require_terminal(what):
    """Prompts and browser logins need a person; without a terminal, fail instead of waiting."""
    if not sys.stdin.isatty():
        raise ToolError(f"{what} needs a person at a terminal; run it in one, or use a token "
                        "from the environment (see --help)")


# --- Output -------------------------------------------------------------------

class Color:
    def __init__(self, stream):
        isatty = getattr(stream, "isatty", None)
        self.enabled = bool(isatty and isatty()) and not os.environ.get("NO_COLOR")
        if self.enabled and os.name == "nt":
            os.system("")  # turns on ANSI escape sequences in the Windows console

    def paint(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.enabled else str(text)

    def red(self, t): return self.paint("31", t)
    def green(self, t): return self.paint("32", t)
    def yellow(self, t): return self.paint("33", t)
    def blue(self, t): return self.paint("34", t)
    def cyan(self, t): return self.paint("36", t)
    def dim(self, t): return self.paint("2", t)
    def bold(self, t): return self.paint("1", t)


out = Color(None)
err = Color(None)


def setup_streams():
    global out, err
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if encoding and encoding.lower() != "utf-8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    out = Color(sys.stdout)
    err = Color(sys.stderr)


def info(msg):
    print(msg, file=sys.stderr)


def colorize_json(value, indent=0):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}  {out.blue(json.dumps(k, ensure_ascii=False))}: {colorize_json(v, indent + 1)}"
                 for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        items = [f"{pad}  {colorize_json(v, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    text = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return out.green(text)
    if value is None or isinstance(value, bool):
        return out.yellow(text)
    return out.cyan(text)


def print_response(raw, as_is, stream=None):
    stream = stream or sys.stdout
    if not raw:
        return
    try:
        data = json.loads(raw)
    except ValueError:
        as_is = True
    if as_is:
        print(raw, file=stream)
    elif out.enabled and stream is sys.stdout:
        print(colorize_json(data), file=stream)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2), file=stream)


def print_headers(status, headers):
    info(err.dim(f"HTTP {status}"))
    for name, value in headers.items():
        info(err.dim(f"{name}: {value}"))
    info("")


# --- Server and HTTP ------------------------------------------------------------

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


def api_url(base, path):
    """Only paths relative to the API: the token never leaves the configured server."""
    if "://" in path or path.startswith("//") or "\\" in path:
        raise ToolError(f"Only paths relative to {base}/api/v1/ are accepted (got {path!r})")
    return f"{base}/api/v1/{path.lstrip('/')}"


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


def json_request(method, url, body, token, cacert):
    req = urllib.request.Request(url, data=body.encode("utf-8") if isinstance(body, str) else body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return send(req, cacert)


def error_detail(body):
    """Always returns text, whatever shape the response has."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return " ".join(str(body).split())[:200]
    if isinstance(data, dict):
        for key in ("_error_message", "detail", "message", "error_description", "error"):
            if data.get(key):
                data = data[key]
                break
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError:
        raise ToolError(f"{what}: the server did not return JSON ({error_detail(raw)})") from None


# --- Credentials -------------------------------------------------------------

def read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return None


def write_private_json(path, data):
    """Writes with 0600 permissions in a 0700 folder, atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":  # on Windows the user profile's ACL protects it
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- The api subcommand ------------------------------------------------------------

def read_body(data):
    if data is None:
        return None
    try:
        if data == "@-":
            body = sys.stdin.read()
        elif data.startswith("@"):
            body = Path(data[1:]).read_text(encoding="utf-8")
        else:
            body = data
    except OSError as e:
        raise ToolError(f"Could not read the body {data}: {e}") from None
    try:
        json.loads(body)
    except ValueError as e:
        raise ToolError(f"The -d body is not valid JSON: {e}") from None
    return body


def add_api_parser(sub, prog, example_path, func):
    p = sub.add_parser("api", help=f"call the API: {prog} [api] METHOD path [-d JSON]",
                       description="Calls /api/v1/<path> on the configured server. "
                                   "If the token has expired, it renews it and retries once.")
    p.add_argument("method", metavar="METHOD", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="path", help=f"relative to /api/v1/, e.g. {example_path!r}")
    p.add_argument("-d", "--data", metavar="JSON", help="JSON body, @file, or @- for stdin")
    p.add_argument("-r", "--raw", action="store_true", help="print the body exactly as received")
    p.add_argument("-i", "--headers", action="store_true", help="show the status and headers (on stderr)")
    p.add_argument("-v", "--verbose", action="store_true", help="show the HTTP status code")
    p.add_argument("--fields", metavar="LIST",
                   help="keep only these comma-separated fields of each object; dots reach nested ones "
                        "(e.g. ref,subject,status_extra_info.name)")
    p.add_argument("--all", action="store_true", help=f"follow the pagination and join the pages (GET, up to {MAX_PAGES})")
    p.add_argument("--max-items", type=int, metavar="N", help="keep only the first N items of a list")
    p.add_argument("--dry-run", action="store_true", help="print the request that would be sent and send nothing")
    p.set_defaults(func=func)


def pick_fields(value, fields):
    """Keeps the given fields of an object, or of each object in a list; "a.b" reads a nested field."""
    if isinstance(value, list):
        return [pick_fields(item, fields) for item in value]
    if not isinstance(value, dict):
        return value
    picked = {}
    for field in fields:
        current = value
        for part in field.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        picked[field] = current
    return picked


def next_page_path(headers):
    """The next page as a path relative to /api/v1/: Gitea's Link rel="next" or Taiga's x-pagination-next."""
    headers = {k.lower(): v for k, v in headers.items()}
    url = headers.get("x-pagination-next")
    if not url and "link" in headers:
        for part in headers["link"].split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    if not url or "/api/v1/" not in url:
        return None
    # Only the path is kept, so the next request still goes to the configured server
    return url.split("/api/v1/", 1)[1]


def run_api(args, call):
    """call(method, path, body) -> (status, body, headers), with each tool's token renewal."""
    body = read_body(args.data)
    if args.method != "GET" and read_only():
        raise ReadOnlyError(f"{args.method} {args.path} was not sent: read-only mode (--read-only or CLT_READONLY)")
    if args.dry_run:
        print(json.dumps({"method": args.method, "path": args.path,
                          "body": json.loads(body) if body else None}, ensure_ascii=False, indent=2))
        return 0
    try:
        status, raw, headers = call(args.method, args.path, body)
        pages = [raw]
        path = args.path
        while args.all and args.method == "GET" and len(pages) < MAX_PAGES and (path := next_page_path(headers)):
            status, raw, headers = call("GET", path, None)
            pages.append(raw)
    except HttpError as e:
        if json_errors():
            detail = e.body if e.body.lstrip().startswith(("{", "[")) else error_detail(e.body)
            print(json.dumps({"error": error_detail(e.body), "status": e.status, "method": args.method,
                              "path": args.path, "body": parse_json_or_text(detail)}, ensure_ascii=False),
                  file=sys.stderr)
            return EXIT_ERROR
        if args.headers:
            print_headers(e.status, e.headers)
        info(err.red(f"✗ {args.method} {args.path} -> HTTP {e.status}"))
        if e.body.lstrip().startswith(("{", "[")):
            print_response(e.body, args.raw, sys.stderr)
        else:
            info(err.dim(error_detail(e.body)))
        return EXIT_ERROR
    if args.headers:
        print_headers(status, headers)
    elif args.verbose:
        info(err.green(f"✓ {args.method} {args.path} -> HTTP {status}"))
    if len(pages) == 1 and not (args.fields or args.max_items):
        print_response(raw, args.raw)
        return 0
    data = [parse_json_or_text(page) for page in pages]
    if len(pages) > 1:
        data = [item for page in data for item in (page if isinstance(page, list) else [page])]
        if len(pages) == MAX_PAGES:
            info(err.yellow(f"Stopped after {MAX_PAGES} pages; there may be more."))
    else:
        data = data[0]
    if args.max_items is not None and isinstance(data, list) and len(data) > args.max_items:
        info(err.yellow(f"Showing {args.max_items} of {len(data)} items."))
        data = data[:args.max_items]
    if args.fields:
        data = pick_fields(data, [f.strip() for f in args.fields.split(",") if f.strip()])
    print_response(json.dumps(data, ensure_ascii=False), args.raw)
    return 0


def parse_json_or_text(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# --- Startup ---------------------------------------------------------------------

def with_api_shortcut(argv):
    """Shortcut: "<tool> GET path" is the same as "<tool> api GET path"."""
    return ["api"] + argv if argv and argv[0].upper() in METHODS else argv


def run(main, argv=None):
    """Runs main(argv) and turns expected errors into exit codes."""
    setup_streams()
    try:
        return main(take_global_flags(sys.argv[1:] if argv is None else argv)) or 0
    except ToolError as e:
        if json_errors():
            print(json.dumps({"error": str(e), "type": type(e).__name__, "exit_code": e.exit_code,
                              "status": getattr(e, "status", None)}, ensure_ascii=False), file=sys.stderr)
        else:
            info(err.red("✗ ") + str(e))
        return e.exit_code
    except BrokenPipeError:
        # e.g. "taiga GET ... | head": not an error
        sys.stderr.close()
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
