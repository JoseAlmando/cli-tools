"""Checks that no private word (employer, clients, internal hosts...) is in the repo's files.

The list lives in .git/info/private-words, inside the clone and never pushed: one regular expression per line, matched
ignoring case; blank lines and lines starting with # are skipped. Tracked files and new, not ignored ones are checked.

    python scripts/check_private_words.py          # exit 0 clean, 1 words found, 2 no list or not a git repo
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

LIST_NAME = Path("info") / "private-words"


def git(repo, *args):
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout


def list_path(repo):
    # The common git dir, so every worktree of a clone shares one list
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / LIST_NAME


def load_patterns(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [re.compile(line.strip(), re.IGNORECASE) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def find_matches(repo, patterns):
    """(path, line number) of every line that matches a pattern, in tracked and new files."""
    found = []
    names = git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    for name in filter(None, names):
        path = Path(repo) / name
        try:
            data = path.read_bytes()
        except OSError:  # deleted but still in the index
            continue
        if b"\0" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if any(p.search(line) for p in patterns):
                found.append((name, number))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the repo for private words listed in .git/info/private-words.")
    parser.add_argument("--repo", default=".", help="the repository to check (default: the current folder)")
    args = parser.parse_args(argv)
    try:
        path = list_path(args.repo)
        if not path.is_file():
            print(f"No list of private words at {path}; create it with one regular expression per line "
                  "(it stays in this clone and is never pushed).", file=sys.stderr)
            return 2
        matches = find_matches(args.repo, load_patterns(path))
    except (RuntimeError, re.error) as e:
        print(f"Cannot check: {e}", file=sys.stderr)
        return 2
    if not matches:
        print("OK: no private words found.")
        return 0
    # Only where: the words themselves are what must not spread (logs, CI output, chats)
    for name, number in matches:
        print(f"{name}:{number}: contains a private word", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
