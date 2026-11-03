#!/usr/bin/env python3
"""Checks commit messages (Conventional Commits 1.0) and branch names (Conventional Branch).

    python scripts/check_conventions.py commit-msg .git/COMMIT_EDITMSG   # what the commit-msg hook runs
    python scripts/check_conventions.py branch feature/add-login         # the current branch without a name
    python scripts/check_conventions.py range origin/main..HEAD          # every commit in a range
    python scripts/check_conventions.py install-hook [--force]           # the commit-msg hook for this repo

Exit codes: 0 everything follows the conventions, 1 something does not, 2 wrong usage or a git error.
Standard library only, Python 3.9 or later; see CONTRIBUTING.md.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
# Messages git writes itself: merges, reverts, and the fixups that git rebase --autosquash folds away
GIT_GENERATED = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")
# type, an optional (scope), an optional !, then the colon; the rest is checked piece by piece
HEADER = re.compile(r"(?P<type>[^\s():!]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?:(?P<rest>.*)")
SCISSORS = re.compile(r"# -+ >8 -+$")  # git commit --verbose: everything below is the diff
COMMIT_FORMAT = f"""\
expected: <type>(<scope>)!: <description>
  type: one of {", ".join(COMMIT_TYPES)}
  (<scope>) and ! (a breaking change) are optional; a blank line goes between the header and a body
  examples: feat(release): group the changelog by commit type
            fix: skip earlier release commits
            refactor!: rename the client   (or a "BREAKING CHANGE: ..." footer)
  see CONTRIBUTING.md and https://www.conventionalcommits.org/en/v1.0.0/"""

PLAIN_BRANCHES = ("main", "master", "develop")
BRANCH_TYPES = ("feature", "feat", "bugfix", "fix", "hotfix", "release", "chore")
BRANCH_FORMAT = f"""\
expected: <type>/<description>, or {", ".join(PLAIN_BRANCHES)}
  type: one of {", ".join(BRANCH_TYPES)}
  description: lowercase letters, digits and single hyphens; dots only in release/ branches
  examples: feature/add-login, fix/issue-123-header, release/v1.2.0
  see CONTRIBUTING.md and https://conventional-branch.github.io/"""

HOOK_MARKER = "# Installed by scripts/check_conventions.py install-hook"
HOOK = f"""#!/bin/sh
{HOOK_MARKER}: checks each commit message against Conventional Commits.
script="$(git rev-parse --show-toplevel)/scripts/check_conventions.py"
if [ ! -f "$script" ]; then
    exit 0  # a branch, or another repo sharing core.hooksPath, without the checker
fi
# python3 first; on Windows "python3" can be the Microsoft Store stub, which does not run anything
if command -v python3 >/dev/null 2>&1 && python3 -c "" >/dev/null 2>&1; then
    exec python3 "$script" commit-msg "$1"
elif command -v python >/dev/null 2>&1; then
    exec python "$script" commit-msg "$1"
fi
echo "commit-msg: neither python3 nor python was found; the commit message was not checked" >&2
"""


class GitError(Exception):
    pass


# --- Commit messages -------------------------------------------------------------------

def _message_lines(text):
    """The message as git keeps it: without # comment lines, the diff under the scissors, or blank edges."""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if SCISSORS.match(line):
            break
        if not line.startswith("#"):
            lines.append(line.rstrip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def check_commit_message(text):
    """The problems with a commit message under Conventional Commits 1.0; [] when it follows them."""
    lines = _message_lines(text)
    if not lines:
        return ["the commit message is empty"]
    header = lines[0]
    if header.startswith(GIT_GENERATED):
        return []
    problems = []
    found = HEADER.fullmatch(header)
    if not found:
        problems.append('the header does not start with a type and a colon, as in "fix: ..."')
    else:
        kind, scope, rest = found.group("type"), found.group("scope"), found.group("rest")
        if kind not in COMMIT_TYPES:
            if kind.lower() in COMMIT_TYPES:
                problems.append(f'write the type "{kind}" in lowercase: "{kind.lower()}"')
            else:
                problems.append(f'"{kind}" is not a commit type; use one of {", ".join(COMMIT_TYPES)}')
        if scope is not None and not scope.strip():
            problems.append("the scope in () is empty; name it, as in fix(release): ..., or leave the () out")
        if not rest.strip():
            problems.append('the description after ": " is empty')
        elif not rest.startswith(" ") or rest.startswith("  "):
            problems.append("put exactly one space after the colon")
    if len(lines) > 1 and lines[1]:
        problems.append("leave a blank line between the header and the body")
    for line in lines[1:]:
        if re.match(r"breaking[ -]change\s*:", line, re.I) and not line.startswith(("BREAKING CHANGE:",
                                                                                       "BREAKING-CHANGE:")):
            problems.append(f'write the footer "{line.split(":")[0]}" as "BREAKING CHANGE:" (uppercase)')
    return problems


# --- Branch names ----------------------------------------------------------------------

def check_branch_name(name):
    """The problems with a branch name under Conventional Branch; [] when it follows it."""
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/"):]
    if name in PLAIN_BRANCHES:
        return []
    if not name:
        return ["the branch name is empty"]
    if "/" not in name:
        return [f'"{name}" has no type: name it <type>/<description>']
    kind, description = name.split("/", 1)
    problems = []
    if kind not in BRANCH_TYPES:
        if kind.lower() in BRANCH_TYPES:
            problems.append(f'write the type "{kind}" in lowercase: "{kind.lower()}"')
        else:
            problems.append(f'"{kind}" is not a branch type; use one of {", ".join(BRANCH_TYPES)}')
    if not description:
        return problems + ['the description after "/" is empty']
    release = kind == "release"
    if any(c.isupper() for c in description):
        problems.append("use lowercase letters only in the description")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-" + ("." if release else ""))
    bad = sorted({c for c in description if c not in allowed and not c.isupper()})
    if "." in bad:
        bad.remove(".")
        problems.append("dots are only allowed in release/ branches, as in release/v1.2.0")
    if bad:
        problems.append("the description may only have lowercase letters, digits and hyphens; found "
                        + ", ".join(repr(c) for c in bad))
    if description.startswith("-") or description.endswith("-") or "--" in description:
        problems.append("no leading, trailing or double hyphens in the description")
    if release and (description.startswith(".") or description.endswith(".") or ".." in description):
        problems.append("no leading, trailing or double dots in the description")
    return problems


# --- git -------------------------------------------------------------------------------

def git(*args, cwd=None):
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except FileNotFoundError:
        raise GitError("git is not installed or not on the PATH") from None
    if done.returncode != 0:
        raise GitError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout.rstrip("\n")


def commits_in(revision_range):
    """[(sha, message)] for every commit in base..head, oldest first."""
    raw = git("log", "--reverse", "--format=%x1e%H%x00%B", revision_range)
    commits = []
    for entry in raw.split("\x1e"):
        if entry.strip():
            sha, _, message = entry.partition("\x00")
            commits.append((sha.strip(), message))
    return commits


def hooks_dir():
    """(folder, whether it is core.hooksPath): where git looks for hooks in the repo of the current folder."""
    top = Path(git("rev-parse", "--show-toplevel"))
    try:
        configured = git("config", "--path", "--get", "core.hooksPath")
    except GitError:
        configured = ""  # not set: git config exits 1
    if configured:
        path = Path(configured)  # a relative one is relative to the top of the working tree
        return (path if path.is_absolute() else top / path), True
    # In a worktree .git is a file; hooks live in the git dir that every worktree shares
    common = Path(git("rev-parse", "--git-common-dir"))
    return (common if common.is_absolute() else Path.cwd() / common) / "hooks", False


# --- Commands --------------------------------------------------------------------------

def report(what, problems, expected):
    print(f"error: {what}:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(expected, file=sys.stderr)


def cmd_commit_msg(args):
    text = Path(args.file).read_bytes().decode("utf-8", "replace")
    problems = check_commit_message(text)
    if problems:
        report("the commit message does not follow Conventional Commits", problems, COMMIT_FORMAT)
        print(f"(your message is saved in {args.file})", file=sys.stderr)
        return 1
    return 0


def cmd_branch(args):
    name = args.name if args.name is not None else git("rev-parse", "--abbrev-ref", "HEAD")
    problems = check_branch_name(name)
    if problems:
        report(f'the branch name "{name}" does not follow Conventional Branch', problems, BRANCH_FORMAT)
        return 1
    print(f"{name}: ok")
    return 0


def cmd_range(args):
    if ".." not in args.range:
        print(f"error: expected a range such as origin/main..HEAD, not {args.range!r}", file=sys.stderr)
        return 2
    commits = commits_in(args.range)
    failed = 0
    for sha, message in commits:
        problems = check_commit_message(message)
        if problems:
            failed += 1
            subject = (_message_lines(message) or [""])[0]
            print(f"{sha[:10]} {subject}", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
    if failed:
        print(f"error: {failed} of {len(commits)} commits in {args.range} do not follow Conventional Commits",
              file=sys.stderr)
        print(COMMIT_FORMAT, file=sys.stderr)
        print("To fix them: git rebase -i, and reword the commits.", file=sys.stderr)
        return 1
    print(f"{len(commits)} commits in {args.range}: ok")
    return 0


def cmd_install_hook(args):
    directory, from_config = hooks_dir()
    hook = directory / "commit-msg"
    content = HOOK.encode("utf-8")  # LF line endings, even on Windows: git runs hooks with sh
    if hook.is_file():
        current = hook.read_bytes()
        if current == content:
            print(f"The commit-msg hook is already installed: {hook}")
            return 0
        if not args.force:
            print(f"error: {hook} already exists and is a different hook; it was not changed.\n"
                  "Run install-hook --force to replace it (it is kept as commit-msg.backup), or call this from it:\n"
                  '  python scripts/check_conventions.py commit-msg "$1"', file=sys.stderr)
            return 1
        backup = directory / "commit-msg.backup"
        backup.write_bytes(current)
        print(f"Replaced the existing commit-msg hook; it is kept as {backup}")
    directory.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(content)
    hook.chmod(0o755)
    print(f"Installed the commit-msg hook: {hook}")
    if from_config:
        print("That folder is core.hooksPath: any repo that shares it runs the hook too "
              "(it does nothing where scripts/check_conventions.py is missing).")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="check_conventions.py", description="Check commit messages (Conventional "
                                     "Commits) and branch names (Conventional Branch).")
    sub = parser.add_subparsers(dest="command", metavar="command")
    p = sub.add_parser("commit-msg", help="check the message in FILE (what the commit-msg hook runs)")
    p.add_argument("file")
    p.set_defaults(func=cmd_commit_msg)
    p = sub.add_parser("branch", help="check a branch name (by default the current branch)")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_branch)
    p = sub.add_parser("range", help="check every commit in BASE..HEAD")
    p.add_argument("range", metavar="BASE..HEAD")
    p.set_defaults(func=cmd_range)
    p = sub.add_parser("install-hook", help="install the commit-msg hook in this repo (respects core.hooksPath)")
    p.add_argument("--force", action="store_true", help="replace a different commit-msg hook, keeping a backup")
    p.set_defaults(func=cmd_install_hook)
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # subjects can hold any character; a Windows console may not
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (GitError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
