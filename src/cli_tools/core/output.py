"""What the tools print: colors (only on a terminal, never with NO_COLOR), messages and API responses."""
import json
import os
import sys


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


# Replaced by setup_streams(); use them as output.out and output.err, not imported by name
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


def cell(value):
    """One table cell: nothing for null, compact JSON for objects and lists, tabs and line breaks as spaces."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, bool)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return " ".join(str(value).replace("\t", " ").splitlines())


def table(rows):
    """Objects as tab-separated lines under a header with every key, in the order they first appear."""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    return "\n".join(["\t".join(columns)] + ["\t".join(cell(row.get(c)) for c in columns) for row in rows])


def print_table(rows, columns=None):
    """Rows as a table; with none, the header alone when the columns are known, and a note on stderr."""
    if rows:
        print(table(rows))
        return
    if columns:
        print("\t".join(columns))
    info("No items.")


def print_rows(rows, as_table, json_mode, columns=None):
    """A list of flat objects: a table, unless --json without --table asks for JSON."""
    if json_mode and not as_table:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        print_table(rows, columns)
