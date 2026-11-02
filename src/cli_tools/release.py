"""clt release: bump the version of the project in the current folder, update its changelog, commit and tag it."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

from . import core, gitea, github, gitrepo, versions
from .core import ToolError, info

CONFIG_FILE = ".clt.json"
CHANGELOG = "CHANGELOG.md"
CHOICES = {"1": "fix", "2": "feat", "3": "major"}


# --- Settings and tests -------------------------------------------------------------

def load_config(root):
    """The "release" object of .clt.json at the repo root, or {} without one."""
    path = Path(root) / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ToolError(f"{CONFIG_FILE} is not valid JSON: {e}") from None
    section = data.get("release", {}) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise ToolError(f'{CONFIG_FILE}: "release" must be an object')
    return section


def _interpreter(root):
    venv = Path(root) / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    return str(venv) if venv.is_file() else sys.executable


def _uses_pytest(root):
    root = Path(root)
    pyproject = root / "pyproject.toml"
    return ((root / "pytest.ini").is_file() or (root / "conftest.py").is_file()
            or (pyproject.is_file() and "[tool.pytest" in pyproject.read_text(encoding="utf-8")))


def test_command(root, config, kind):
    """(argv, extra environment) that runs the project's tests."""
    env = {str(k): str(v) for k, v in (config.get("test_env") or {}).items()}
    command = config.get("test_command")
    if command:
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
            raise ToolError(f"{CONFIG_FILE}: release.test_command must be a list of strings")
        # "python" means the project's interpreter, so one .clt.json works where only python3 or py exists
        return ([_interpreter(root)] + command[1:] if command[0] == "python" else list(command)), env
    if kind == "python":
        module = ["-m", "pytest"] if _uses_pytest(root) else ["-m", "unittest", "discover"]
        return [_interpreter(root)] + module, env
    if kind == "dotnet":
        return ["dotnet", "test"], env
    raise ToolError(f"No default test command for this project; set release.test_command in {CONFIG_FILE} "
                    "or use --skip-tests")


def run_tests(root, argv, env):
    info(core.err.cyan("Running the tests: ") + " ".join(argv))
    try:
        proc = subprocess.Popen(argv, cwd=root, env={**os.environ, **env}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ToolError(f"Cannot run the tests: {argv[0]} was not found") from None
    for line in proc.stdout:  # shown as it runs, on stderr so --json output stays clean
        sys.stderr.write(line)
    if proc.wait() != 0:
        raise ToolError(f"The tests failed (exit code {proc.returncode}); nothing was changed")


# --- Changelog ----------------------------------------------------------------------

def format_section(subjects):
    return "\n".join(f"- {s}" for s in subjects)


def prepend_changelog(path, version, date, subjects):
    """Adds "## version - date" with one bullet per subject on top of the changelog; returns the bullets."""
    path = Path(path)
    body = format_section(subjects)
    block = f"## {version} - {date}\n\n{body}\n"
    text = path.read_bytes().decode("utf-8") if path.is_file() else "# Changelog\n"
    first = text.find("\n## ")
    if first == -1:
        text = text.rstrip("\n") + "\n\n" + block
    else:
        text = text[:first + 1] + block + "\n" + text[first + 1:]
    path.write_bytes(text.encode("utf-8"))
    return body


# --- Hosts ---------------------------------------------------------------------------

def remote_url(root):
    """origin's URL as configured (not rewritten by url.*.insteadOf)."""
    return gitrepo.git(root, "config", "--get", "remote.origin.url")


def publisher_for(host):
    """Checks that there is a session for the host the release goes to."""
    if host == "github.com":
        github.GitHub().load_credentials()
        return "github"
    try:
        g = gitea.Gitea()
    except ToolError:
        g = None
    if g and urllib.parse.urlsplit(g.url).hostname == host:
        g.load_credentials()
        return "gitea"
    raise ToolError(f"{host} is neither github.com nor the server of the clt gitea session; cannot publish there")


# --- The release ---------------------------------------------------------------------

def find_root():
    gitrepo.git(Path.cwd(), "--version")  # says so when git is missing
    try:
        return Path(gitrepo.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    except ToolError:
        raise ToolError("Not inside a git repository") from None


def plan_release(root, args):
    """Every check that can stop a release, and what it would do; changes nothing (besides git fetch)."""
    if gitrepo.git(root, "status", "--porcelain"):
        raise ToolError("The working tree has uncommitted or untracked changes; commit or stash them first")
    branch = gitrepo.default_branch(root)
    current = gitrepo.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise ToolError(f"Releases are made from {branch}, and this is {current}")
    host, owner, repo = gitrepo.parse_remote(remote_url(root))
    publisher_for(host)
    # Only the branch: fetching tags would bring a remote vX.Y.Z here and hide where it came from
    gitrepo.git(root, "fetch", "--quiet", "--no-tags", "origin", branch)
    behind = int(gitrepo.git(root, "rev-list", "--count", f"HEAD..origin/{branch}"))
    if behind:
        raise ToolError(f"{branch} is {behind} commit(s) behind origin/{branch}; pull first")
    config = load_config(root)
    version_file = versions.find_version_file(root, args.version_file or config.get("version_file"))
    old = version_file.read()
    previous = gitrepo.last_tag(root)
    messages = gitrepo.messages_since(root, previous)
    if not messages:
        raise ToolError(f"There are no commits since {previous}; nothing to release")
    return {"root": root, "branch": branch, "host": host, "owner": owner, "repo": repo, "config": config,
            "version_file": version_file, "old": old, "previous": previous,
            "subjects": [m.splitlines()[0] for m in messages], "suggested": versions.suggest_bump(messages)}


def check_tag(root, tag):
    if gitrepo.git(root, "tag", "--list", tag):
        raise ToolError(f"The tag {tag} already exists here")
    if gitrepo.remote_tag_exists(root, tag):
        raise ToolError(f"The tag {tag} already exists on origin; was this version released from another machine?")


def ask(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def ask_bump(plan):
    core.require_terminal("clt release (without --bump or --yes)")
    default = next(n for n, kind in CHOICES.items() if kind == plan["suggested"])
    labels = {"fix": "fix", "feat": "feat", "major": "new version"}
    info(f"Project: {plan['version_file'].kind} ({plan['version_file'].path.name}) · current version {plan['old']} · "
         f"{len(plan['subjects'])} commits since {plan['previous'] or 'the start'}")
    info("What is this release?")
    for n, kind in CHOICES.items():
        info(f"  [{n}] {labels[kind]:<12} {versions.bump(plan['old'], kind)}")
    while True:
        answer = ask(f"Choice [{default}]: ") or default
        if answer in CHOICES:
            return CHOICES[answer]
        info(core.err.yellow("Answer 1, 2 or 3."))


def relative(plan):
    return plan["version_file"].path.relative_to(plan["root"]).as_posix()


def make_release(args):
    if not args.dry_run:
        core.require_writes("release")
    root = find_root()
    plan = plan_release(root, args)
    if not args.dry_run:
        if args.skip_tests:
            info(core.err.yellow("Tests skipped (--skip-tests)."))
        else:
            run_tests(root, *test_command(root, plan["config"], plan["version_file"].kind))
    kind = args.bump or (plan["suggested"] if args.yes or args.dry_run else ask_bump(plan))
    new = versions.bump(plan["old"], kind)
    tag = f"v{new}"
    check_tag(root, tag)
    if args.dry_run:
        print(json.dumps({"version_file": relative(plan), "old": plan["old"], "new": new, "tag": tag,
                          "changelog": format_section(plan["subjects"]), "host": plan["host"],
                          "repo": f"{plan['owner']}/{plan['repo']}"}, ensure_ascii=False, indent=2))
        return 0
    plan["version_file"].write(new)
    prepend_changelog(root / CHANGELOG, new, datetime.date.today().isoformat(), plan["subjects"])
    gitrepo.git(root, "add", "--", relative(plan), CHANGELOG)
    gitrepo.git(root, "commit", "--quiet", "-m", f"Release {new}")
    gitrepo.git(root, "tag", "-a", tag, "-m", f"Release {new}")
    info(core.err.green("✓ ") + f"{plan['old']} → {new}: committed and tagged {tag}")
    info(f"Run {core.err.bold('clt release --publish')} to push and publish {tag}.")
    result = {"version": new, "tag": tag, "version_file": relative(plan), "pushed": False, "release_url": None}
    if core.json_mode():
        print(json.dumps(result, ensure_ascii=False))
    return 0


def build_parser(prog):
    epilog = f"""\
run it inside the project's git repo, on its main branch:
  {prog}                          # asks fix / feat / new version (Enter takes the suggestion)
  {prog} --bump feat --yes --no-push --json   # for scripts and agents: no questions, no push
  {prog} --dry-run                # shows what it would do

projects: Python (pyproject.toml [project] version) and .NET (Directory.Build.props or the one .csproj
with <Version>); other files with --version-file. Settings per repo in {CONFIG_FILE}:
  {{"release": {{"test_command": ["python", "-m", "pytest"], "test_env": {{}}, "version_file": "VERSION"}}}}
"""
    parser = argparse.ArgumentParser(prog=prog, epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="Bump the version, update CHANGELOG.md, commit, tag, "
                                                 "and publish the release on GitHub or Gitea.")
    parser.add_argument("--bump", choices=versions.BUMPS, help="fix, feat or major (new version); skips the question")
    parser.add_argument("--yes", action="store_true", help="never ask: take the suggested bump and do not push")
    parser.add_argument("--no-push", action="store_true", help="stop after the local commit and tag")
    parser.add_argument("--publish", action="store_true", help="push and publish a release already tagged here")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the project's tests first")
    parser.add_argument("--version-file", metavar="PATH", help="the file that holds the version")
    return parser


def main(argv, prog="clt release"):
    args = build_parser(prog).parse_args(argv)
    return make_release(args)
