import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from cli_tools import __version__, skill_dir
from cli_tools.core import context, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info

AGENTS = {"claude": (".claude", "skills", "clt"), "codex": (".agents", "skills", "clt"),
          "gemini": (".agents", "skills", "clt")}
MARKER = ".clt-skill"
SOURCES = (("skills", "clt"), ("cli_tools", "skill"))


def target(agent):
    return Path.home().joinpath(*AGENTS[agent])


def is_clt(path):
    if path.is_symlink():
        return Path(os.path.realpath(path)).parts[-2:] in SOURCES
    return path.is_dir() and (path / MARKER).is_file()


def _check(path):
    if (path.exists() or path.is_symlink()) and not is_clt(path):
        raise ToolError(f"{path} exists and is not clt's skill; move it out of {path.parent} (for example to "
                        f"{path.parent.parent / 'clt.bak'}, where agents do not read it), then run this again")


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _replace(path, make):
    work = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".clt-skill-", dir=path.parent))
        staging = work / path.name
        make(staging)
        aside = None
        if path.exists() or path.is_symlink():
            aside = work / "old"
            path.rename(aside)
        try:
            staging.rename(path)
        except OSError:
            if aside is not None:
                aside.rename(path)
            raise
    except OSError as e:
        raise ToolError(f"Could not write {path}: {e}") from None
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def _copy(staging):
    shutil.copytree(skill_dir(), staging, ignore=shutil.ignore_patterns("__pycache__", MARKER))
    (staging / MARKER).write_text(json.dumps({"version": __version__}) + "\n", encoding="utf-8")


def _link(staging):
    try:
        staging.symlink_to(skill_dir(), target_is_directory=True)
    except OSError as e:
        raise ToolError(f"Could not create a link at {staging.with_name('clt')} ({e}); run it without --link to "
                        "copy the skill instead") from None


def _unique(agents):
    return list(dict.fromkeys(agents))


def install(agents, link):
    done, results = {}, []
    for agent in _unique(agents):
        path = target(agent)
        if path not in done:
            _check(path)
            _replace(path, _link if link else _copy)
            done[path] = "link" if link else "copy"
        results.append({"agent": agent, "path": str(path), "how": done[path]})
    return results


def uninstall(agents):
    removed, results = {}, []
    for agent in _unique(agents):
        path = target(agent)
        if path not in removed:
            if not (path.exists() or path.is_symlink()):
                continue
            _check(path)
            removed[path] = "link" if path.is_symlink() else "copy"
            _remove(path)
            sharing = sorted(name for name in AGENTS if target(name) == path and name not in agents)
            if sharing:
                info(f"{path} is shared with {', '.join(sharing)}; it is removed for {', '.join(sharing)} too.")
        results.append({"agent": agent, "path": str(path), "how": removed[path]})
    return results


def refresh():
    done, warned, results = set(), set(), []
    for agent in AGENTS:
        path = target(agent)
        if path not in done and not path.is_symlink() and (path / MARKER).is_file():
            _replace(path, _copy)
            done.add(path)
        if path in done:
            results.append({"agent": agent, "path": str(path), "how": "copy"})
        elif path not in warned and path.is_dir() and not path.is_symlink():
            warned.add(path)
            info(output.err.yellow(f"{path} has no {MARKER}, so it was not refreshed (a copy made by hand?); move it "
                                   f"out of {path.parent} and run clt skill install --agent {agent}."))
    return results


def build_parser(prog):
    agents = ", ".join(f"{name} ~/{'/'.join(parts)}" for name, parts in AGENTS.items())
    parser = argparse.ArgumentParser(prog=prog, description="Install clt's skill where an AI agent reads it.",
                                     epilog=f"""\
examples:
  {prog} install                     # for every agent whose CLI is on the PATH
  {prog} install --agent codex
  {prog} uninstall --agent claude

folders: {agents}
a copy is marked with {MARKER}; clt update refreshes the copies, links follow their source
""", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    p = sub.add_parser("install", help="copy (or link) the skill into the agents' skills folder")
    p.add_argument("--agent", action="append", choices=list(AGENTS), help="claude, codex or gemini (repeatable)")
    p.add_argument("--link", action="store_true", help="link to clt's folder instead of copying (for a checkout)")
    p = sub.add_parser("uninstall", help="remove the skill clt installed")
    p.add_argument("--agent", action="append", choices=list(AGENTS), required=True,
                   help="claude, codex or gemini (repeatable)")
    sub.add_parser("refresh", help="copy the skill again into every copy clt made (clt update runs it)")
    return parser


def main(argv, ctx=None, prog="clt skill"):
    ctx = ctx or context.CLIContext.from_env()
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    ctx.require_writes(f"skill {args.command}")
    try:
        results = _run(args)
    except OSError as e:
        raise ToolError(f"{e.filename or 'The skill folder'}: {e.strerror or e}") from None
    if ctx.json_mode:
        print(json.dumps({"action": args.command, "targets": results}))
    elif not results:
        info("Nothing to do.")
    for item in [] if ctx.json_mode else results:
        info(output.err.green("✓ ") + f"{item['agent']}: {item['path']} ({item['how']})")
    return 0


def _run(args):
    if args.command == "install":
        agents = args.agent or [name for name in AGENTS if shutil.which(name)]
        if not agents:
            raise ToolError("No agent CLI (claude, codex, gemini) is on the PATH; choose one with --agent")
        return install(agents, args.link)
    if args.command == "uninstall":
        return uninstall(args.agent)
    return refresh()
