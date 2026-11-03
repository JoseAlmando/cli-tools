"""How this invocation runs: the global flags (--json, --read-only) and whether a person is at the terminal."""
import dataclasses
import os
import sys

from cli_tools.core.errors import ReadOnlyError, ToolError

# Global flags, accepted anywhere on the command line (see parse_global_flags)
GLOBAL_FLAGS = ("--json", "--read-only")
# Commands that change the saved session or print the token; read-only mode blocks them
SESSION_COMMANDS = ("login", "refresh", "logout", "token")


def env_flag(environ, name):
    return environ.get(name, "").strip().lower() in ("1", "true", "yes")


@dataclasses.dataclass(frozen=True)
class CLIContext:
    """The global flags of one run, built once and handed to the commands; never written to os.environ, so the
    processes clt starts (a project's tests, uv) do not inherit them."""
    json_mode: bool = False  # --json or CLT_JSON=1: errors as one JSON line on stderr, and me as JSON
    read_only: bool = False  # --read-only or CLT_READONLY=1: only GET requests are sent

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(json_mode=env_flag(environ, "CLT_JSON"), read_only=env_flag(environ, "CLT_READONLY"))

    def require_writes(self, what):
        """Stops anything that would change something while read-only mode is on."""
        if self.read_only:
            raise ReadOnlyError(f"{what} was not run: read-only mode (--read-only or CLT_READONLY)")

    def check_session_command(self, command):
        """Read-only mode also blocks the commands that change the saved session or print the token."""
        if command in SESSION_COMMANDS:
            self.require_writes(command)


def parse_global_flags(argv, environ=None):
    """(CLIContext, the other arguments): the flags work in any position, and the environment can turn them on."""
    ctx = CLIContext.from_env(environ)
    ctx = CLIContext(json_mode=ctx.json_mode or "--json" in argv, read_only=ctx.read_only or "--read-only" in argv)
    return ctx, [arg for arg in argv if arg not in GLOBAL_FLAGS]


def require_terminal(what):
    """Prompts and browser logins need a person; without a terminal, fail instead of waiting."""
    if not sys.stdin.isatty():
        raise ToolError(f"{what} needs a person at a terminal; run it in one, or use a token "
                        "from the environment (see --help)")
