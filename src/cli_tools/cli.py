"""clt: one command for every tool (clt taiga ..., clt gitea ...)."""
import importlib
import sys

from cli_tools import __version__, registry
from cli_tools.core import context, runner
from cli_tools.core.errors import ToolError


def usage():
    lines = ["usage: clt TOOL [arguments]", "", "tools:"]
    short = {tool: alias for alias, tool in registry.ALIASES.items()}
    labels = [(f"{name}, {short[name]}" if name in short else name, description)
              for name, _, description in registry.TOOLS]
    lines += [f"  {label:<12} {description}" for label, description in labels]
    commands = ", ".join(f"{name} {alias}" for name, alias in context.COMMAND_ALIASES.items())
    lines += ["", "Each tool has its own help: clt taiga --help, clt github --help.",
              f"Short names for commands: {commands}.",
              "",
              "for scripts and AI agents (any position on the command line):",
              "  --json        errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)",
              "  --read-only   send only GET requests (also CLT_READONLY=1);",
              "                login, refresh, logout and token are blocked too",
              "  exit codes    0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode"]
    return "\n".join(lines)


def main(argv, ctx=None):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        print(f"clt {__version__}")
        return 0
    module = registry.find(argv[0])
    if module is None:
        print(usage(), file=sys.stderr)
        raise ToolError(f"Unknown tool {argv[0]!r}")
    # Only the tool that runs is imported
    ctx = ctx or context.CLIContext.from_env()
    return importlib.import_module(module).main(argv[1:], ctx=ctx, prog=f"clt {registry.canonical(argv[0])}")


def run():
    return runner.run(main)
