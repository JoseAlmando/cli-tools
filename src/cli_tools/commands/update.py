"""clt update: move the installed clt to the latest release, or to any released version."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from importlib import metadata
from pathlib import Path

from cli_tools import __version__
from cli_tools.core import agents, context, http, output
from cli_tools.core.errors import ToolError
from cli_tools.core.output import info
from cli_tools.tools.github.client import API_ROOT, HEADERS

REPO = "JoseAlmando/cli-tools"
# Windows process flags (not defined by subprocess elsewhere)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


# Windows: run by the base interpreter, outside clt's environment, as "WAIT_THEN_RUN <clt's pid> <installer...>".
# It waits for clt to exit, because the installer cannot remove a folder whose python.exe is still running.
WAIT_THEN_RUN = """\
import ctypes, subprocess, sys, time
kernel32 = ctypes.windll.kernel32
handle = kernel32.OpenProcess(0x00100000, False, int(sys.argv[1]))  # SYNCHRONIZE
if handle:
    kernel32.WaitForSingleObject(handle, 120000)
    kernel32.CloseHandle(handle)
time.sleep(1)  # the clt.exe launcher exits just after its python
# Explicit handles: without them Windows does not pass this process's log file on to the installer
sys.exit(subprocess.run(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr).returncode)
"""


def on_windows():
    return os.name == "nt"


def base_python():
    """The interpreter clt's environment was made from; it lives outside that environment."""
    return getattr(sys, "_base_executable", None) or sys.executable


def installed_version():
    return __version__


def _key(version):
    return tuple(int(n) for n in version.split("."))


def releases():
    """Released X.Y.Z versions with their notes, newest first, from GitHub's public API (no token is sent)."""
    _, raw, _ = http.json_request("GET", f"{API_ROOT}/repos/{REPO}/releases?per_page=100", None, None, None,
                                  HEADERS)
    listed = http.parse_json(raw, "Listing the releases")
    listed = [r for r in listed if isinstance(r, dict)] if isinstance(listed, list) else []
    found = {r.get("tag_name", "")[1:]: r.get("body") or "" for r in listed}
    found = [(v, b) for v, b in found.items() if v.count(".") == 2 and all(n.isdigit() for n in v.split("."))]
    if not found:
        raise ToolError(f"No releases of {REPO} yet")
    return sorted(found, key=lambda release: _key(release[0]), reverse=True)


def released_versions():
    return [v for v, _ in releases()]


def _notes(body):
    return "".join(s for s in re.split(r"(?m)^(?=### )", body) if not s.startswith("### Other")).strip()


def changes(found, installed, target):
    """The notes of the releases after the installed one up to the target, newest first (none on a downgrade)."""
    try:
        low, high = _key(installed.split("+")[0]), _key(target)
    except ValueError:
        return []
    return [{"version": v, "notes": _notes(b)} for v, b in found if low < _key(v) <= high and _notes(b)]


def show(found):
    for change in found:
        info(f"\n{output.err.bold(change['version'])}\n{change['notes'].replace('### ', '')}")


def installer(prefix=None):
    """What installed clt's environment: uv tool and pipx leave a file at its root; anything else is pip."""
    prefix = Path(prefix or sys.prefix)
    if (prefix / "uv-receipt.toml").is_file():
        return "uv"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    return "pip"


def install_info():
    """How clt is installed: editable (development) installs record it in direct_url.json (PEP 610)."""
    try:
        dist = metadata.distribution("cli-tools")
    except metadata.PackageNotFoundError:
        return {"installed": False, "editable": False, "path": None, "installer": None}
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    editable = bool(direct.get("dir_info", {}).get("editable"))
    url = direct.get("url", "")
    path = urllib.request.url2pathname(urllib.parse.urlsplit(url).path) if url.startswith("file:") else None
    return {"installed": True, "editable": editable, "path": path, "installer": installer()}


def install_command(version, tool="uv"):
    """The command that reinstalls clt with the installer that installed it, so there is never a second copy."""
    url = f"git+https://github.com/{REPO}@v{version}"
    if tool == "pipx":
        return ["pipx", "install", "--force", url]
    if tool == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", url]
    return ["uv", "tool", "install", "--force", url]


def report(ctx, installed, latest, target, action, notes=()):
    if ctx.json_mode:
        print(json.dumps({"installed": installed, "latest": latest, "target": target, "action": action,
                          "changes": list(notes)}))


def main(argv, ctx=None, prog="clt update"):
    ctx = ctx or context.CLIContext.from_env()
    parser = argparse.ArgumentParser(prog=prog, description="Install the latest release of clt, or a given one.",
                                     epilog=f"examples:\n  {prog} --check\n  {prog}\n  {prog} 0.2.0      # also older ones",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", metavar="VERSION", help="e.g. 0.2.0 or v0.2.0 (default: the latest)")
    parser.add_argument("--check", action="store_true",
                        help="only show the installed and the latest version, and what the newer ones bring")
    args = parser.parse_args(argv)
    installed = installed_version()

    if args.check:
        found = releases()
        latest = found[0][0]
        notes = changes(found, installed, latest)
        info(f"Installed: {installed}\nLatest:    {latest}" + ("  (up to date)" if latest == installed else
                                                              f"  (run {prog} to install it)"))
        show(notes)
        report(ctx, installed, latest, None, "check", notes)
        return 0

    ctx.require_writes("update")
    how = install_info()
    if how["editable"]:
        info(f"This is a development install from {how['path']}; it follows that checkout, so update it with git pull.")
        report(ctx, installed, None, None, "none")
        return 0
    if not how["installed"]:
        raise ToolError("clt is not installed as a package here (running from the source tree?); nothing to update")

    found = releases()
    available = [v for v, _ in found]
    target = (args.version or available[0]).lstrip("v")
    if target not in available:
        raise ToolError(f"There is no release {target}; available: {', '.join(available)}")
    if target == installed:
        info(f"clt is already on {target}.")
        report(ctx, installed, available[0], target, "none")
        return 0

    command = install_command(target, how["installer"])
    if how["installer"] == "pip":  # pip has no "tool" to reinstall from, and on Windows it cannot replace clt.exe
        raise ToolError("clt was installed with pip; update it with:\n  " + " ".join(command))
    program = shutil.which(command[0])
    if not program:
        raise ToolError(f"{command[0]} installed clt but is not on the PATH now; install the version with:\n  "
                        + " ".join(command))
    command[0] = program
    notes = changes(found, installed, target)
    show(notes)
    info(f"Installing clt {target} (now {installed})…")
    if on_windows():
        # A running clt cannot be replaced: the installer starts once this process has exited, and writes a log
        log = Path(tempfile.gettempdir()) / "clt-update.log"
        with open(log, "w", encoding="utf-8") as out:
            subprocess.Popen([base_python(), "-I", "-c", WAIT_THEN_RUN, str(os.getpid()), *command], stdout=out,
                             stderr=subprocess.STDOUT, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                             close_fds=True)
        info(f"The update continues in the background once this command ends; its output goes to {log}. When that "
             f"file ends with \"Installed\" (about half a minute), run {output.err.bold('clt --version')}, then "
             f"{output.err.bold('clt skill refresh')} if you installed the skill. Running clt before then can "
             "interrupt the update.")
        report(ctx, installed, available[0], target, "started", notes)
        return 0
    code = subprocess.run(command).returncode
    if code == 0:
        refresh_skill()
        agents.recommend_skill(ctx)
    report(ctx, installed, available[0], target, "installed" if code == 0 else "failed", notes)
    return code


def refresh_skill():
    new = shutil.which("clt")
    if not new:
        info(f"Run {output.err.bold('clt skill refresh')} to update the copies of the skill.")
        return
    if subprocess.run([new, "skill", "refresh"], stdout=sys.stderr).returncode != 0:
        info(f"The skill copies were not refreshed; run {output.err.bold('clt skill refresh')}.")
