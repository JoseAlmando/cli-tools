"""Command-line tools for the APIs of development services."""
import re
from importlib import metadata
from pathlib import Path


def _version_from_pyproject(path):
    """The version line of the [project] table, read without tomllib (Python 3.9 has none)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    table = re.search(r"^\[project\][ \t]*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    found = table and re.search(r'^version\s*=\s*"([^"]+)"', table.group(1), re.M)
    return found.group(1) if found else None


# pyproject.toml is the only place the version is written
try:
    __version__ = metadata.version("cli-tools")
except metadata.PackageNotFoundError:  # running from src/ without installing
    __version__ = _version_from_pyproject(Path(__file__).resolve().parents[2] / "pyproject.toml") or "0+unknown"
