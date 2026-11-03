import contextlib
import importlib
import io
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from cli_tools import cli, registry
from tests.helpers import TempConf

SRC = str(Path(__file__).resolve().parents[1] / "src")


def modules_loaded_by(*argv):
    """The cli_tools modules a new process has imported after clt.main(argv)."""
    code = ("import contextlib, io, sys\nfrom cli_tools import cli\n"
            "with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \\\n"
            "        contextlib.suppress(Exception):\n"
            f"    cli.main({list(argv)!r})\n"
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('cli_tools'))))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": SRC})
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return set(done.stdout.split())


class RegistryTests(unittest.TestCase):
    def test_names_are_unique_and_every_module_has_a_main(self):
        names = [name for name, _, _ in registry.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for name, module, description in registry.TOOLS:
            with self.subTest(name=name):
                self.assertTrue(description)
                self.assertTrue(callable(importlib.import_module(module).main))

    def test_find(self):
        self.assertEqual(registry.find("taiga"), "cli_tools.tools.taiga.cli")
        self.assertIsNone(registry.find("jira"))

    def test_help_lists_every_entry_in_order(self):
        lines = cli.usage().splitlines()
        start = lines.index("tools:") + 1
        self.assertEqual(lines[start:start + len(registry.TOOLS)],
                         [f"  {name:<8} {description}" for name, _, description in registry.TOOLS])


class LazyImportTests(TempConf):
    def test_help_and_version_import_no_tool(self):
        for argv in (["--help"], ["--version"], ["jira"]):
            with self.subTest(argv=argv):
                loaded = modules_loaded_by(*argv)
                self.assertFalse({m for m in loaded if m.startswith(("cli_tools.tools", "cli_tools.commands"))})

    def test_a_tool_imports_only_itself(self):
        loaded = modules_loaded_by("gitea", "logout")
        self.assertIn("cli_tools.tools.gitea.cli", loaded)
        self.assertFalse({m for m in loaded if m.startswith(("cli_tools.tools.taiga", "cli_tools.tools.github",
                                                             "cli_tools.commands"))})


class DelegationTests(TempConf):
    def test_a_registered_module_gets_the_rest_of_the_arguments_and_its_prog(self):
        calls = []
        fake = types.ModuleType("fake_tool")
        fake.main = lambda argv, **kwargs: calls.append((argv, kwargs)) or 7
        tools = registry.TOOLS + (("fake", "fake_tool", "a tool added by one registry line"),)
        with mock.patch.dict(sys.modules, {"fake_tool": fake}), mock.patch.object(registry, "TOOLS", tools):
            self.assertEqual(cli.main(["fake", "a", "--b"]), 7)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cli.main(["--help"])
        self.assertEqual(calls[0][0], ["a", "--b"])
        self.assertEqual(calls[0][1]["prog"], "clt fake")
        self.assertIn("  fake     a tool added by one registry line", out.getvalue())


if __name__ == "__main__":
    unittest.main()
