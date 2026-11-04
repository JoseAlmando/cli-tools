import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

import cli_tools
from cli_tools import __version__
from cli_tools import cli as clt
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf


class CltTests(TempConf):
    def test_lists_the_tools(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(clt.main(["--help"]), 0)
        self.assertIn("taiga", buffer.getvalue())
        self.assertIn("gitea", buffer.getvalue())
        self.assertIn("github", buffer.getvalue())

    def test_shows_the_version(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            clt.main(["--version"])
        self.assertEqual(buffer.getvalue().strip(), f"clt {__version__}")

    def test_passes_the_arguments_to_the_tool(self):
        self.assertIsNone(clt.main(["gitea", "logout"]))

    def test_names_the_tool_in_its_help(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(SystemExit):
            clt.main(["taiga", "--help"])
        self.assertIn("usage: clt taiga", buffer.getvalue())

    def test_rejects_an_unknown_tool(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(ToolError, "nope"):
            clt.main(["nope"])


class VersionTests(unittest.TestCase):
    def test_the_source_tree_wins_over_what_was_installed(self):
        def installed():  # an editable install keeps the version it was installed with
            return "0.1.0"

        with tempfile.TemporaryDirectory() as tmp:
            ours = Path(tmp) / "pyproject.toml"
            ours.write_text('[project]\nname = "cli-tools"\nversion = "9.8.7"\n')
            self.assertEqual(cli_tools._find_version(ours, installed), "9.8.7")
            ours.write_text('[project]\nname = "other-project"\nversion = "5.5.5"\n')
            self.assertEqual(cli_tools._find_version(ours, installed), "0.1.0")
            self.assertEqual(cli_tools._find_version(Path(tmp) / "missing.toml", installed), "0.1.0")

            def not_installed():
                raise cli_tools.metadata.PackageNotFoundError("cli-tools")

            self.assertEqual(cli_tools._find_version(Path(tmp) / "missing.toml", not_installed), "0+unknown")

    def test_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        self.assertEqual(__version__, re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.M).group(1))

    def test_version_from_pyproject_reads_the_project_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            path.write_text('[tool.x]\nversion = "9.9.9"\n[project]\nname = "a"\nversion = "1.2.3"\n')
            self.assertEqual(cli_tools._version_from_pyproject(path), "1.2.3")
            self.assertIsNone(cli_tools._version_from_pyproject(Path(tmp) / "missing.toml"))


if __name__ == "__main__":
    unittest.main()
