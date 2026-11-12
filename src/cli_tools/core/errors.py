"""Expected errors, and the exit code each one ends the program with."""
import json

# Exit codes: 0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode, 130 interrupted
EXIT_ERROR, EXIT_USAGE, EXIT_NOT_LOGGED_IN, EXIT_READ_ONLY, EXIT_INTERRUPTED = 1, 2, 3, 4, 130


class ToolError(Exception):
    """An expected error; run() prints it without a traceback."""
    exit_code = EXIT_ERROR


class UsageError(ToolError):
    """Wrong arguments: an unknown tool, or what argparse rejects when --json asks for a JSON line."""
    exit_code = EXIT_USAGE


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
