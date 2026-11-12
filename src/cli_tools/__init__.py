"""Command-line tools for the APIs of development services."""
import json
import re
from importlib import metadata
from pathlib import Path

NAME = "cli-tools"


def _project_table(path):
    """The [project] table of a pyproject.toml, read without tomllib (Python 3.9 has none), or None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    table = re.search(r"^\[project\][ \t]*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    return table.group(1) if table else None


def _version_from_pyproject(path):
    table = _project_table(path)
    found = table and re.search(r'^version\s*=\s*"([^"]+)"', table, re.M)
    return found.group(1) if found else None


def _find_version(source_pyproject, installed_version):
    """pyproject.toml is the only place the version is written. Running from a source tree (an editable install, or
    src/ on the path), it is read there: the installed metadata keeps the version of the day it was installed."""
    table = _project_table(source_pyproject)
    if table and re.search(rf'^name\s*=\s*"{NAME}"', table, re.M):
        version = _version_from_pyproject(source_pyproject)
        if version:
            return version
    try:
        return installed_version()
    except metadata.PackageNotFoundError:
        return "0+unknown"


def _with_commit(version, direct_url):
    """An install from a branch or a commit (PEP 610's direct_url.json) shows it, such as 0.5.0+d1db646, so it is
    not taken for the release; a release installed by its tag, as clt update does, is the release itself."""
    vcs = direct_url.get("vcs_info") or {}
    commit = vcs.get("commit_id")
    if not commit or "+" in version or vcs.get("requested_revision") == f"v{version}":
        return version
    return f"{version}+{commit[:7]}"


def _installed_version():
    dist = metadata.distribution(NAME)
    try:
        direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        direct_url = {}
    return _with_commit(dist.version, direct_url if isinstance(direct_url, dict) else {})


__version__ = _find_version(Path(__file__).resolve().parents[2] / "pyproject.toml", _installed_version)


def skill_dir():
    return Path(__file__).resolve().parent / "skill"
