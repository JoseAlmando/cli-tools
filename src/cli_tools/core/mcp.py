import json
import os
import shlex
import shutil
import subprocess
import sys

from cli_tools.core import output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info

AGENTS = ("claude", "codex", "gemini")


def on_windows():
    return os.name == "nt"


def http_command(agent, name, url):
    if agent == "codex":
        return ["codex", "mcp", "add", name, "--url", url]
    return [agent, "mcp", "add", "--scope", "user", "--transport", "http", name, url]


def stdio_command(agent, name, command):
    if agent == "claude":
        return ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", name, "--", *command]
    if agent == "codex":
        return ["codex", "mcp", "add", name, "--", *command]
    return ["gemini", "mcp", "add", "--scope", "user", name, *command]


def npx(*args):
    return (["cmd", "/c"] if on_windows() else []) + ["npx", *args]


def shown(command):
    return subprocess.list2cmdline(command) if on_windows() else shlex.join(command)


def add_mcp_parser(sub, func, help_text, epilog=None):
    p = sub.add_parser("mcp", help=help_text, description=help_text, epilog=epilog)
    p.add_argument("--agent", required=True, choices=AGENTS, help="the agent to connect the server to")
    p.add_argument("--install", action="store_true", help="run the command instead of printing it")
    p.set_defaults(func=func)
    return p


def run_mcp(args, ctx, command, then):
    if args.install:
        ctx.require_writes("mcp --install")
        program = shutil.which(command[0])
        if not program:
            raise ToolError(f"{command[0]} is not on the PATH; run this where it is:\n  {shown(command)}")
        info(output.err.dim(f"{command[0]} may open the browser to sign in; this waits until you finish there."))
        code = subprocess.run([program, *command[1:]], stdout=sys.stderr if ctx.json_mode else None).returncode
        if code:
            raise ToolError(f"{command[0]} exited {code}; the command was:\n  {shown(command)}")
    if ctx.json_mode:
        print(json.dumps({"agent": args.agent, "command": command, "then": then, "installed": args.install}))
    elif not args.install:
        print(shown(command), flush=True)
        info(output.err.dim(f"then: {then}"))
    else:
        info(output.err.green("✓ ") + f"Added to {args.agent}; then: {then}")
    return 0
