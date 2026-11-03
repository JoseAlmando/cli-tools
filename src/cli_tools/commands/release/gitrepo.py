"""Small helpers around the git command line, for releases."""
import re
import subprocess

from cli_tools.core.errors import ToolError

RELEASE_COMMIT = re.compile(r"Release \d+\.\d+\.\d+$")


def _run(root, *args):
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except FileNotFoundError:
        raise ToolError("git is not installed or not on the PATH") from None


def git(root, *args):
    """Runs git in root and returns its output; a failure raises ToolError with git's own message."""
    done = _run(root, *args)
    if done.returncode != 0:
        raise ToolError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout.rstrip("\n")


def parse_remote(url):
    """(host, owner, repo) from an https://, ssh:// or git@host:owner/repo remote URL."""
    found = (re.fullmatch(r"https://([^/@]+@)?([^/:]+)(:\d+)?/([^/]+)/([^/]+?)(\.git)?/?", url)
             or re.fullmatch(r"ssh://([^/@]+@)?([^/:]+)(:\d+)?/([^/]+)/([^/]+?)(\.git)?/?", url))
    if found:
        return found.group(2), found.group(4), found.group(5)
    found = re.fullmatch(r"[^/@]+@([^/:]+):([^/]+)/([^/]+?)(\.git)?/?", url)
    if found:
        return found.group(1), found.group(2), found.group(3)
    shown = re.sub(r"//[^/@]*@", "//", url)  # never echo credentials embedded in the URL
    raise ToolError(f"Cannot tell the host, owner and repo from the remote URL {shown!r}")


def default_branch(root):
    done = _run(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if done.returncode == 0 and done.stdout.strip():
        return done.stdout.strip().rsplit("/", 1)[-1]
    if _run(root, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode == 0:
        return "main"
    return "master"


def last_tag(root):
    """The newest vX.Y.Z tag reachable from HEAD, or None."""
    done = _run(root, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    return done.stdout.strip() if done.returncode == 0 else None


def messages_since(root, tag):
    """Full commit messages after tag (or all of HEAD), oldest first, without earlier "Release X.Y.Z" commits."""
    raw = git(root, "log", "--reverse", "--format=%B%x00", f"{tag}..HEAD" if tag else "HEAD")
    messages = [m.strip() for m in raw.split("\x00") if m.strip()]
    return [m for m in messages if not RELEASE_COMMIT.match(m.splitlines()[0])]


def subjects_since(root, tag):
    return [m.splitlines()[0] for m in messages_since(root, tag)]


def remote_tag_exists(root, tag):
    return bool(git(root, "ls-remote", "--tags", "origin", f"refs/tags/{tag}"))
