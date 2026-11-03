"""clt release: bump the version of the project in the current folder, update its changelog, commit and tag it."""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from cli_tools.commands.release import gitrepo, versions
from cli_tools.core import context, http, output
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.core.output import info
from cli_tools.tools.gitea.client import Gitea
from cli_tools.tools.github.client import GitHub

CONFIG_FILE = ".clt.json"
CHANGELOG = "CHANGELOG.md"
CHOICES = {"1": "fix", "2": "feat", "3": "major"}
CHANGELOG_GROUPS = (("breaking", "Breaking changes"), ("feat", "Features"), ("fix", "Fixes"), ("other", "Other"))


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
    if venv.is_file():
        return str(venv)
    # The project's Python, not clt's own (an isolated uv/pipx venv without the project's packages)
    return shutil.which("python") or shutil.which("python3") or sys.executable


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
    info(output.err.cyan("Running the tests: ") + " ".join(argv))
    try:
        proc = subprocess.Popen(argv, cwd=root, env={**os.environ, **env}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ToolError(f"Cannot run the tests: {argv[0]} was not found") from None
    with proc:
        for line in proc.stdout:  # shown as it runs, on stderr so --json output stays clean
            sys.stderr.write(line)
    if proc.returncode != 0:
        raise ToolError(f"The tests failed (exit code {proc.returncode}); nothing was changed")


# --- Changelog ----------------------------------------------------------------------

def format_section(messages):
    """One bullet per commit subject; grouped under ### headings when any commit has a Conventional prefix."""
    subjects = [m.splitlines()[0] for m in messages]
    if not any(versions.is_conventional(m) for m in messages):
        return "\n".join(f"- {s}" for s in subjects)
    kinds = [versions.change_kind(m) for m in messages]
    groups = []
    for kind, title in CHANGELOG_GROUPS:
        bullets = [f"- {s}" for s, k in zip(subjects, kinds) if k == kind]
        if bullets:
            groups.append(f"### {title}\n\n" + "\n".join(bullets))
    return "\n\n".join(groups)


def prepend_changelog(path, version, date, messages):
    """Adds "## version - date" with the commits' section on top of the changelog; returns the section."""
    path = Path(path)
    body = format_section(messages)
    block = f"## {version} - {date}\n\n{body}\n"
    text = path.read_bytes().decode("utf-8") if path.is_file() else "# Changelog\n"
    first = text.find("\n## ")
    if first == -1:
        text = text.rstrip("\n") + "\n\n" + block
    else:
        text = text[:first + 1] + block + "\n" + text[first + 1:]
    path.write_bytes(text.encode("utf-8"))
    return body


def changelog_section(path, version):
    """The bullets under "## version" in the changelog, or None."""
    path = Path(path)
    if not path.is_file():
        return None
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    start = text.find(f"\n## {version} ")
    if start == -1:
        return None
    body = text[text.index("\n", start + 1) + 1:]
    end = body.find("\n## ")
    return (body if end == -1 else body[:end]).strip()


# --- Hosts ---------------------------------------------------------------------------

class Publisher:
    """Releases through a clt client; GitHub and Gitea share these paths."""

    def __init__(self, client):
        self.client = client

    def release_url(self, owner, repo, tag):
        try:
            _, raw, _ = self.client.call("GET", f"repos/{owner}/{repo}/releases/tags/{tag}", None)
        except HttpError as e:
            if e.status == 404:
                return None
            raise
        return http.parse_json(raw, "Reading the release").get("html_url")

    def create_release(self, owner, repo, tag, body):
        _, raw, _ = self.client.call("POST", f"repos/{owner}/{repo}/releases",
                                     json.dumps({"tag_name": tag, "name": tag, "body": body}))
        return http.parse_json(raw, "Creating the release").get("html_url")

def remote_url(root):
    """origin's URL as configured (not rewritten by url.*.insteadOf)."""
    return gitrepo.git(root, "config", "--get", "remote.origin.url")


def publisher_for(host):
    """The Publisher for the host the release goes to; fails early when there is no session for it."""
    if host == "github.com":
        client = GitHub()
        client.load_credentials()
        return Publisher(client)
    try:
        g = Gitea()
    except ToolError:
        g = None
    if g and urllib.parse.urlsplit(g.url).hostname == host:
        g.load_credentials()
        return Publisher(g)
    raise ToolError(f"{host} is neither github.com nor the server of the clt gitea session; cannot publish there")


# --- The release ---------------------------------------------------------------------

def find_root():
    gitrepo.git(Path.cwd(), "--version")  # says so when git is missing
    try:
        return Path(gitrepo.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    except ToolError:
        raise ToolError("Not inside a git repository") from None


def plan_release(root, args):
    """Every check that can stop a release, and what it would do; changes nothing."""
    if gitrepo.git(root, "status", "--porcelain"):
        raise ToolError("The working tree has uncommitted or untracked changes; commit or stash them first")
    branch = gitrepo.default_branch(root)
    current = gitrepo.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        raise ToolError(f"Releases are made from {branch}, and this is {current}")
    for key in ("user.name", "user.email"):
        try:
            gitrepo.git(root, "config", "--get", key)
        except ToolError:
            raise ToolError(f"git has no {key} to sign the release commit; set it with "
                            f'git config --global {key} "..."') from None
    host, owner, repo = gitrepo.parse_remote(remote_url(root))
    publisher = publisher_for(host)
    # One look at origin for the whole release: its branch and its tags (each contact may ask for a passphrase)
    remote_head, remote_tags = gitrepo.remote_refs(root, branch)
    if remote_head and not gitrepo.is_ancestor(root, remote_head):
        raise ToolError(f"{branch} is behind origin/{branch}; pull first")
    config = load_config(root)
    version_file = versions.find_version_file(root, args.version_file or config.get("version_file"))
    old = version_file.read()
    previous = gitrepo.last_tag(root)
    messages = gitrepo.messages_since(root, previous)
    if not messages:
        if previous and previous not in remote_tags:
            raise ToolError(f"There are no commits since {previous}, which is tagged here but not on origin; "
                            "run clt release --publish to finish it")
        raise ToolError(f"There are no commits since {previous}; nothing to release")
    return {"root": root, "branch": branch, "host": host, "owner": owner, "repo": repo, "config": config,
            "publisher": publisher, "remote_head": remote_head, "remote_tags": remote_tags,
            "version_file": version_file, "old": old, "previous": previous,
            "messages": messages, "subjects": [m.splitlines()[0] for m in messages],
            "suggested": versions.suggest_bump(messages)}


def publish(root, plan, tag, body):
    """Pushes what origin lacks, in one atomic push, and creates the release if it is missing; safe to run again."""
    branch = plan["branch"]
    tagged = gitrepo.git(root, "rev-parse", f"{tag}^{{commit}}")
    refspecs, names = [], []
    # Only up to the tagged commit: later local commits are not part of this release
    if not (plan["remote_head"] and gitrepo.is_ancestor(root, tagged, plan["remote_head"])):
        refspecs.append(f"{tagged}:refs/heads/{branch}")
        names.append(branch)
    if tag not in plan["remote_tags"]:
        refspecs.append(f"refs/tags/{tag}")
        names.append(tag)
    if refspecs:
        info(f"Pushing {' and '.join(names)} to origin…")
        gitrepo.push(root, *refspecs)
    publisher = plan["publisher"]
    url = publisher.release_url(plan["owner"], plan["repo"], tag)
    if url:
        info(f"The release {tag} already exists: {url}")
    else:
        url = publisher.create_release(plan["owner"], plan["repo"], tag, body)
        info(output.err.green("✓ ") + f"Published {tag}: {url}")
    return url


def undo(root, originals):
    """Puts the version file and the changelog back as they were, unstaged."""
    gitrepo.git(root, "reset", "--quiet", "--", *originals)
    for name, data in originals.items():
        path = Path(root) / name
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)


def check_tag(root, tag, remote_tags):
    if gitrepo.git(root, "tag", "--list", tag):
        raise ToolError(f"The tag {tag} already exists here")
    if tag in remote_tags:
        raise ToolError(f"The tag {tag} already exists on origin; was this version released from another machine?")


def ask(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def ask_bump(plan):
    context.require_terminal("clt release (without --bump or --yes)")
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
        info(output.err.yellow("Answer 1, 2 or 3."))


def release_message(version):
    """The release commit's and the annotated tag's message, a Conventional Commit."""
    return f"chore(release): {version}"


def relative(plan):
    return plan["version_file"].path.relative_to(plan["root"]).as_posix()


def make_release(args):
    if not args.dry_run:
        args.ctx.require_writes("release")
    root = find_root()
    plan = plan_release(root, args)
    if not args.dry_run:
        if args.skip_tests:
            info(output.err.yellow("Tests skipped (--skip-tests)."))
        else:
            run_tests(root, *test_command(root, plan["config"], plan["version_file"].kind))
    kind = args.bump or (plan["suggested"] if args.yes or args.dry_run else ask_bump(plan))
    new = versions.bump(plan["old"], kind)
    tag = f"v{new}"
    check_tag(root, tag, plan["remote_tags"])
    if args.dry_run:
        print(json.dumps({"version_file": relative(plan), "old": plan["old"], "new": new, "tag": tag,
                          "changelog": format_section(plan["messages"]), "host": plan["host"],
                          "repo": f"{plan['owner']}/{plan['repo']}"}, ensure_ascii=False, indent=2))
        return 0
    files = [relative(plan), CHANGELOG]
    originals = {name: (root / name).read_bytes() if (root / name).is_file() else None for name in files}
    try:
        plan["version_file"].write(new)
        prepend_changelog(root / CHANGELOG, new, datetime.date.today().isoformat(), plan["messages"])
        gitrepo.git(root, "add", "--", *files)
        gitrepo.git(root, "commit", "--quiet", "-m", release_message(new))
    except ToolError as e:
        undo(root, originals)
        raise ToolError(f"{e}\nThe version and changelog changes were undone; nothing was committed.") from None
    try:
        gitrepo.git(root, "tag", "-a", tag, "-m", release_message(new))
    except ToolError as e:
        gitrepo.git(root, "reset", "--quiet", "--soft", "HEAD~1")
        undo(root, originals)
        raise ToolError(f"{e}\nThe release commit and the file changes were undone.") from None
    info(output.err.green("✓ ") + f"{plan['old']} → {new}: committed and tagged {tag}")
    result = {"version": new, "tag": tag, "version_file": relative(plan), "pushed": False, "release_url": None}
    where = f"{plan['host']}/{plan['owner']}/{plan['repo']}"
    # Pushing needs an explicit yes: never with --yes or --no-push, nor without a person to answer
    if not (args.yes or args.no_push) and sys.stdin.isatty() and \
            ask(f"Push and publish {tag} to {where}? [y/N] ").lower() in ("y", "yes"):
        body = changelog_section(root / CHANGELOG, new) or f"Release {new}"
        try:
            url = publish(root, plan, tag, body)
        except ToolError as e:
            e.args = (f"{e}\n{tag} is committed and tagged locally; run clt release --publish to finish.",)
            raise
        result.update(pushed=True, release_url=url)
    else:
        info(f"Run {output.err.bold('clt release --publish')} to push and publish {tag}.")
    if args.ctx.json_mode:
        print(json.dumps(result, ensure_ascii=False))
    return 0


def resume_publish(args):
    """--publish: push and publish the newest release tagged here, skipping whatever is already done."""
    args.ctx.require_writes("release --publish")
    root = find_root()
    at_head = gitrepo.git(root, "tag", "--points-at", "HEAD", "--list", "v[0-9]*").splitlines()
    tags = at_head or gitrepo.git(root, "tag", "--list", "v[0-9]*", "--sort=-v:refname").splitlines()
    if not tags:
        raise ToolError("There is no vX.Y.Z tag to publish; run clt release first")
    tag = tags[0]
    branch = gitrepo.default_branch(root)
    host, owner, repo = gitrepo.parse_remote(remote_url(root))
    remote_head, remote_tags = gitrepo.remote_refs(root, branch)
    plan = {"branch": branch, "owner": owner, "repo": repo, "publisher": publisher_for(host),
            "remote_head": remote_head, "remote_tags": remote_tags}
    version = tag[1:]
    url = publish(root, plan, tag, changelog_section(root / CHANGELOG, version) or f"Release {version}")
    if args.ctx.json_mode:
        version_file = versions.find_version_file(root, args.version_file or load_config(root).get("version_file"))
        print(json.dumps({"version": version, "tag": tag, "version_file": version_file.path.relative_to(root).as_posix(),
                          "pushed": True, "release_url": url}, ensure_ascii=False))
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


def main(argv, ctx=None, prog="clt release"):
    args = build_parser(prog).parse_args(argv)
    args.ctx = ctx or context.CLIContext.from_env()
    return resume_publish(args) if args.publish else make_release(args)
