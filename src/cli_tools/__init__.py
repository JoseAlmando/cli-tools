"""Command-line tools for the APIs of development services."""
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


__version__ = _find_version(Path(__file__).resolve().parents[2] / "pyproject.toml", lambda: metadata.version(NAME))
