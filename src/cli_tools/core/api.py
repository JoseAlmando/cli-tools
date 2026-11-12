"""The api command every client shares: METHOD path [-d JSON], with pagination, filters and --dry-run."""
import json
import sys
import urllib.parse
from pathlib import Path

from cli_tools.core import output
from cli_tools.core.context import CLIContext
from cli_tools.core.errors import EXIT_ERROR, HttpError, ReadOnlyError, ToolError, error_detail
from cli_tools.core.output import info

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_PAGES = 100


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


def add_api_parser(sub, prog, example_path, func, api_prefix="/api/v1/", description=None, renews=True):
    """renews: whether the tool renews an expired token and retries; Jira and Azure DevOps tokens do not renew."""
    p = sub.add_parser("api", help=f"call the API: {prog} [api] METHOD path [-d JSON]",
                       description=description or (f"Calls {api_prefix}<path> on the configured server."
                                                   + (" If the token has expired, it renews it and retries once."
                                                      if renews else "")))
    p.add_argument("method", metavar="METHOD", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="path", help=f"relative to {api_prefix}, e.g. {example_path!r}")
    p.add_argument("-d", "--data", metavar="JSON", help="JSON body, @file, or @- for stdin")
    p.add_argument("-r", "--raw", action="store_true", help="print the body exactly as received")
    p.add_argument("-i", "--headers", action="store_true", help="show the status and headers (on stderr)")
    p.add_argument("-v", "--verbose", action="store_true", help="show the HTTP status code of each request (each page)")
    p.add_argument("--fields", metavar="LIST",
                   help="keep only these comma-separated fields of each object; dots reach nested ones "
                        "(e.g. ref,subject,status_extra_info.name)")
    p.add_argument("--all", action="store_true", help=f"follow the pagination and join the pages (GET, up to {MAX_PAGES} pages)")
    p.add_argument("--max-items", type=int, metavar="N",
                   help="keep only the first N items of a list; with --all, stop fetching pages once there are N")
    p.add_argument("--table", action="store_true",
                   help="print a list of objects as tab-separated lines under a header (fewer tokens than JSON)")
    p.add_argument("--dry-run", action="store_true", help="print the request that would be sent and send nothing")
    p.set_defaults(func=func)


def options_help(prog):
    """The end of a tool's --help: the api command's options work without typing "api" too."""
    return (f"\napi options, also with the short form {prog} METHOD path:\n"
            "  --fields LIST, --all, --max-items N, --table, --dry-run, -d JSON, -v, -i, -r\n"
            f"  details: {prog} api --help\n")


MINE_COLUMNS = ("kind", "id", "state", "title", "where")


def add_mine_parser(sub, func, what):
    """The mine command: the user's open items in one call, as rows of kind, id, state, title, where."""
    p = sub.add_parser("mine", help=f"your open {what}, one row each: {', '.join(MINE_COLUMNS)}",
                       description=f"Lists your open {what} with one command. Without --json it prints a "
                                   "tab-separated table; with --json, a JSON list, unless --table is given too.")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="at most N items (default 50)")
    p.add_argument("--table", action="store_true", help="a tab-separated table even with --json")
    p.set_defaults(func=func)
    return p


def fetch_list(call, path, limit, next_page=None, items_key=None):
    """The items of a list, following the pages until there are `limit` of them (GET only)."""
    items = []
    pages = 0
    while path and len(items) < limit and pages < MAX_PAGES:
        _, raw, headers = call("GET", path, None)
        pages += 1
        data = parse_json_or_text(raw)
        key = items_key(data) if items_key and isinstance(data, dict) else None
        page = data.get(key) if key else data
        if not isinstance(page, list):
            raise ToolError(f"Expected a list from {path.partition('?')[0]}")
        items += page
        path = (next_page or next_page_path)(headers, raw, path) if page else None
    return items[:limit]


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


def query_pairs(query):
    """The (name, value) pairs of a query, split on & only: parse_qsl in Python 3.9.0 and 3.9.1 splits on ; too."""
    pairs = []
    for part in query.split("&"):
        if part:
            name, _, value = part.partition("=")
            pairs.append((urllib.parse.unquote_plus(name), urllib.parse.unquote_plus(value)))
    return pairs


def set_query(path, params):
    base, _, query = path.partition("?")
    done = set()
    result = []
    for name, value in query_pairs(query):
        if name in params:
            if name in done:
                continue
            done.add(name)
            value = params[name]
        if value is not None:
            result.append((name, value))
    result += [(name, value) for name, value in params.items() if name not in done and value is not None]
    return f"{base}?{urllib.parse.urlencode(result)}" if result else base


def next_page_path(headers, body=None, path=None, api_root=None):
    """The next page as a path relative to the API: a Link rel="next" or Taiga's x-pagination-next.

    With api_root (e.g. "https://api.github.com/"), the next URL must start with it; otherwise it must be under /api/v1/.
    """
    headers = {k.lower(): v for k, v in headers.items()}
    url = headers.get("x-pagination-next")
    if not url and "link" in headers:
        for part in headers["link"].split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    if api_root:
        return url[len(api_root):] if url and url.startswith(api_root) else None
    if not url or "/api/v1/" not in url:
        return None
    # Only the path is kept, so the next request still goes to the configured server
    return url.split("/api/v1/", 1)[1]


def run_api(args, call, ctx=None, next_page=next_page_path, items_key=None, join=None):
    """call(method, path, body) -> (status, body, headers), with each tool's token renewal."""
    ctx = ctx or CLIContext.from_env()
    body = read_body(args.data)
    if args.method != "GET" and ctx.read_only:
        raise ReadOnlyError(f"{args.method} {args.path} was not sent: read-only mode (--read-only or CLT_READONLY)")
    if args.dry_run:
        print(json.dumps({"method": args.method, "path": args.path,
                          "body": json.loads(body) if body else None}, ensure_ascii=False, indent=2))
        return 0
    def sent(path, status):  # -v: one line per request, so --all shows how many pages it read
        if args.verbose and not args.headers:
            info(output.err.green(f"✓ {args.method} {path} -> HTTP {status}"))

    try:
        status, raw, headers = call(args.method, args.path, body)
        sent(args.path, status)
        pages = [raw]
        path = args.path
        seen = {path}
        count = count_items(raw, items_key)
        more = False
        while args.all and args.method == "GET" and len(pages) < MAX_PAGES and (path := next_page(headers, raw, path)):
            if path in seen:  # page tokens or links that go round: everything after this was read already
                break
            seen.add(path)
            if args.max_items is not None and count >= args.max_items:
                more = True
                break
            status, raw, headers = call("GET", path, None)
            sent(path, status)
            pages.append(raw)
            if page_items(raw, items_key) == []:  # an empty page ends the list, whatever token it carries
                break
            count += count_items(raw, items_key)
    except HttpError as e:
        if ctx.json_mode:
            detail = e.body if e.body.lstrip().startswith(("{", "[")) else error_detail(e.body)
            print(json.dumps({"error": error_detail(e.body), "status": e.status, "method": args.method,
                              "path": args.path, "body": parse_json_or_text(detail)}, ensure_ascii=False),
                  file=sys.stderr)
            return EXIT_ERROR
        if args.headers:
            output.print_headers(e.status, e.headers)
        info(output.err.red(f"✗ {args.method} {args.path} -> HTTP {e.status}"))
        if e.body.lstrip().startswith(("{", "[")):
            output.print_response(e.body, args.raw, sys.stderr)
        else:
            info(output.err.dim(error_detail(e.body)))
        return EXIT_ERROR
    if args.headers:
        output.print_headers(status, headers)
    for number, page in enumerate(pages[1:], 2):
        if not is_json(page):
            raise ToolError(f"page {number} of {args.path} is not JSON: {error_detail(page)}")
    first = parse_json_or_text(pages[0])
    # With --all, one page of a list in an object gets join() too, so it has the same shape as several
    joined = args.all and join and items_key and isinstance(first, dict) and items_key(first) is not None
    if len(pages) == 1 and not (joined or args.fields or args.max_items or getattr(args, "table", False)):
        output.print_response(raw, args.raw)
        return 0
    data = [first] + [parse_json_or_text(page) for page in pages[1:]]
    if len(pages) > 1 or joined:
        if join:
            data = join(data)
        else:
            data = [item for page in data for item in (page if isinstance(page, list) else [page])]
        if len(pages) == MAX_PAGES:
            info(output.err.yellow(f"Stopped after {MAX_PAGES} pages; there may be more."))
    else:
        data = data[0]
    key = items_key(data) if items_key and isinstance(data, dict) else None
    if key is None or not isinstance(data.get(key), list):
        data = trim(data, args, more)
        rows = data
    else:
        data = {**data, key: trim(data[key], args, more)}
        rows = data[key]
    if getattr(args, "table", False) and isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
        output.print_table(rows, [f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None)
        return 0
    output.print_response(json.dumps(data, ensure_ascii=False), args.raw)
    return 0


def page_items(raw, items_key=None):
    """The list a page holds (itself, or the one items_key names inside it), or None."""
    data = parse_json_or_text(raw)
    if isinstance(data, dict) and items_key:
        data = data.get(items_key(data) or "")
    return data if isinstance(data, list) else None


def count_items(raw, items_key=None):
    return len(page_items(raw, items_key) or [])


def is_json(raw):
    try:
        json.loads(raw)
    except (ValueError, TypeError):
        return False
    return True


def trim(data, args, more=False):
    if args.max_items is not None and isinstance(data, list) and more:
        data = data[:args.max_items]
        info(output.err.yellow(f"Showing {len(data)} items; there may be more."))
    elif args.max_items is not None and isinstance(data, list) and len(data) > args.max_items:
        info(output.err.yellow(f"Showing {args.max_items} of {len(data)} items."))
        data = data[:args.max_items]
    if args.fields:
        data = pick_fields(data, [f.strip() for f in args.fields.split(",") if f.strip()])
    return data


def parse_json_or_text(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def with_api_shortcut(argv):
    """Shortcut: "<tool> GET path" is the same as "<tool> api GET path"."""
    return ["api"] + argv if argv and argv[0].upper() in METHODS else argv
