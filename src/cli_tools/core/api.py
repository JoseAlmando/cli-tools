"""The api command every client shares: METHOD path [-d JSON], with pagination, filters and --dry-run."""
import json
import sys
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


def add_api_parser(sub, prog, example_path, func, api_prefix="/api/v1/"):
    p = sub.add_parser("api", help=f"call the API: {prog} [api] METHOD path [-d JSON]",
                       description=f"Calls {api_prefix}<path> on the configured server. "
                                   "If the token has expired, it renews it and retries once.")
    p.add_argument("method", metavar="METHOD", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="path", help=f"relative to {api_prefix}, e.g. {example_path!r}")
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


def next_page_path(headers, api_root=None):
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


def run_api(args, call, ctx=None, next_page=next_page_path):
    """call(method, path, body) -> (status, body, headers), with each tool's token renewal."""
    ctx = ctx or CLIContext.from_env()
    body = read_body(args.data)
    if args.method != "GET" and ctx.read_only:
        raise ReadOnlyError(f"{args.method} {args.path} was not sent: read-only mode (--read-only or CLT_READONLY)")
    if args.dry_run:
        print(json.dumps({"method": args.method, "path": args.path,
                          "body": json.loads(body) if body else None}, ensure_ascii=False, indent=2))
        return 0
    try:
        status, raw, headers = call(args.method, args.path, body)
        pages = [raw]
        path = args.path
        while args.all and args.method == "GET" and len(pages) < MAX_PAGES and (path := next_page(headers)):
            status, raw, headers = call("GET", path, None)
            pages.append(raw)
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
    elif args.verbose:
        info(output.err.green(f"✓ {args.method} {args.path} -> HTTP {status}"))
    if len(pages) == 1 and not (args.fields or args.max_items):
        output.print_response(raw, args.raw)
        return 0
    data = [parse_json_or_text(page) for page in pages]
    if len(pages) > 1:
        data = [item for page in data for item in (page if isinstance(page, list) else [page])]
        if len(pages) == MAX_PAGES:
            info(output.err.yellow(f"Stopped after {MAX_PAGES} pages; there may be more."))
    else:
        data = data[0]
    if args.max_items is not None and isinstance(data, list) and len(data) > args.max_items:
        info(output.err.yellow(f"Showing {args.max_items} of {len(data)} items."))
        data = data[:args.max_items]
    if args.fields:
        data = pick_fields(data, [f.strip() for f in args.fields.split(",") if f.strip()])
    output.print_response(json.dumps(data, ensure_ascii=False), args.raw)
    return 0


def parse_json_or_text(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def with_api_shortcut(argv):
    """Shortcut: "<tool> GET path" is the same as "<tool> api GET path"."""
    return ["api"] + argv if argv and argv[0].upper() in METHODS else argv
