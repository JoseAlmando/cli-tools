"""The version of a project: where it is written, how to read and change it, and the next number."""
import re
from pathlib import Path

from cli_tools.core.errors import ToolError

BUMPS = ("fix", "feat", "major")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
SKIPPED_DIRS = {"bin", "obj", ".git", "node_modules"}
# Conventional Commits: "type(scope)!: description", and a "BREAKING CHANGE: ..." footer
COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
CONVENTIONAL_HEADER = re.compile(r"(?P<type>[A-Za-z]+)(\([^()]*\))?(?P<breaking>!)?: ")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.M)


def bump(version, kind):
    major, minor, patch = (int(n) for n in version.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "feat":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def suggest_bump(subjects):
    """major for breaking changes, feat for new features, fix otherwise (Conventional Commits prefixes)."""
    if any("BREAKING CHANGE" in s or re.match(r"\w+(\(.+\))?!:", s) for s in subjects):
        return "major"
    if any(re.match(r"feat(\(.+\))?:", s) for s in subjects):
        return "feat"
    return "fix"


def _conventional_header(message):
    found = CONVENTIONAL_HEADER.match(message.partition("\n")[0])
    return found if found and found.group("type").lower() in COMMIT_TYPES else None


def is_conventional(message):
    """Whether the commit's subject starts with a Conventional Commits prefix, such as "feat(api): "."""
    return bool(_conventional_header(message))


def change_kind(message):
    """breaking (a "!" or a BREAKING CHANGE footer), feat, fix or other, for the changelog's groups."""
    found = _conventional_header(message)
    if (found and found.group("breaking")) or BREAKING_FOOTER.search(message.partition("\n")[2]):
        return "breaking"
    kind = found.group("type").lower() if found else "other"
    return kind if kind in ("feat", "fix") else "other"


class VersionFile:
    """One file holding the version; only the version text is ever changed, byte for byte."""

    def __init__(self, path, kind):
        self.path = Path(path)
        self.kind = kind

    def _text(self):
        return _decode(self.path)

    def _span(self, text):
        if self.kind == "python":
            return _python_span(text)
        if self.kind == "dotnet":
            return _dotnet_span(text)
        stripped = text.strip().lstrip("﻿").strip()
        if not stripped:
            return None
        start = text.index(stripped)
        return start, start + len(stripped)

    def read(self):
        text = self._text()
        span = self._span(text)
        if span is None:
            raise ToolError(f"No version found in {self.path.name}")
        value = text[span[0]:span[1]]
        if not SEMVER.fullmatch(value):
            raise ToolError(f"The version in {self.path.name} is {value!r}; expected MAJOR.MINOR.PATCH, e.g. 1.2.3")
        return value

    def write(self, new_version):
        text = self._text()
        start, end = self._span(text)
        self.path.write_bytes((text[:start] + new_version + text[end:]).encode("utf-8"))


def _decode(path):
    try:
        return Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{Path(path).name} is not UTF-8 text; save it as UTF-8 to release it") from None


def _has_dotnet_version(path):
    try:
        return bool(_dotnet_span(_decode(path)))
    except ToolError:  # a file in another encoding cannot be the one we edit
        return False


def _project_table(text):
    return re.search(r"^﻿?\[project\][ \t]*\r?$(.*?)(?=^\[|\Z)", text, re.M | re.S)


def _python_span(text):
    table = _project_table(text)
    found = table and re.search(r"""^version\s*=\s*(["'])([^"'\r\n]*)\1""", table.group(1), re.M)
    if not found:
        return None
    return table.start(1) + found.start(2), table.start(1) + found.end(2)


def _dotnet_span(text):
    found = re.search(r"<Version>([^<]*)</Version>", text) or re.search(r"<VersionPrefix>([^<]*)</VersionPrefix>", text)
    return found.span(1) if found else None


def _kind_of(path):
    if path.name == "pyproject.toml":
        return "python"
    if path.suffix in (".props", ".csproj"):
        return "dotnet"
    return "plain"


def find_version_file(root, override=None):
    root = Path(root)
    if override:
        path = Path(override) if Path(override).is_absolute() else root / override
        if not path.is_file():
            raise ToolError(f"The version file {override} does not exist")
        return VersionFile(path, _kind_of(path))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = _decode(pyproject)
        table = _project_table(text)
        if table:
            if _python_span(text):
                return VersionFile(pyproject, "python")
            raise ToolError("pyproject.toml has no static version in [project] (is it dynamic?); "
                            "point to the file that holds it with --version-file")

    props = root / "Directory.Build.props"
    if props.is_file() and _dotnet_span(_decode(props)):
        return VersionFile(props, "dotnet")

    csprojs = sorted(p for p in root.rglob("*.csproj")
                     if not SKIPPED_DIRS.intersection(p.relative_to(root).parts[:-1])
                     and _has_dotnet_version(p))
    if len(csprojs) == 1:
        return VersionFile(csprojs[0], "dotnet")
    if len(csprojs) > 1:
        names = ", ".join(str(p.relative_to(root)) for p in csprojs)
        raise ToolError(f"Several projects have their own version ({names}); move it to <Version> in "
                        "Directory.Build.props at the repo root so it lives in one place")

    raise ToolError("No version file found: looked for pyproject.toml ([project] version), "
                    "Directory.Build.props (<Version>) and *.csproj (<Version>); use --version-file")
