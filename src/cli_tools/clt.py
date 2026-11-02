"""clt: one command for every tool (clt taiga ..., clt gitea ...)."""
import sys

from . import __version__, core, gitea, taiga

TOOLS = {
    "taiga": (taiga.main, "client for the Taiga API"),
    "gitea": (gitea.main, "client for the Gitea API, with OAuth2 login in the browser"),
}


def usage():
    lines = ["usage: clt TOOL [arguments]", "", "tools:"]
    lines += [f"  {name:<8} {description}" for name, (_, description) in TOOLS.items()]
    lines += ["", "Each tool has its own help: clt taiga --help, clt gitea --help.",
              "They are also installed on their own: taiga ..., gitea ...",
              "",
              "for scripts and AI agents (any position on the command line):",
              "  --json        errors as one JSON line on stderr (also CLT_JSON=1)",
              "  --read-only   send only GET requests (also CLT_READONLY=1);",
              "                login, refresh, logout and token are blocked too",
              "  exit codes    0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode"]
    return "\n".join(lines)


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        print(f"clt {__version__}")
        return 0
    tool = TOOLS.get(argv[0])
    if tool is None:
        print(usage(), file=sys.stderr)
        raise core.ToolError(f"Unknown tool {argv[0]!r}")
    return tool[0](argv[1:], prog=f"clt {argv[0]}")


def run():
    return core.run(main)
