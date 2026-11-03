import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools.commands import update
from cli_tools.core import errors, http
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf

RELEASES = json.dumps([{"tag_name": "v0.2.0"}, {"tag_name": "v0.10.0"}, {"tag_name": "v0.1.0"}, {"tag_name": "nightly"}])
INSTALLED = {"installed": True, "editable": False, "path": None}


class UpdateTests(TempConf):
    def setUp(self):
        super().setUp()
        self.sent = []

        def send(req, cacert):
            self.sent.append(req)
            return 200, RELEASES, {}

        self.addCleanup(mock.patch.stopall)
        mock.patch.object(http, "send", send).start()
        mock.patch.object(update, "installed_version", lambda: "0.2.0").start()
        mock.patch.object(update, "install_info", lambda: INSTALLED).start()
        mock.patch.object(update.shutil, "which", lambda name: "/usr/bin/uv").start()
        self.run_ = mock.patch.object(update.subprocess, "run", return_value=mock.Mock(returncode=0)).start()
        self.popen = mock.patch.object(update.subprocess, "Popen").start()

    def update(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = update.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_versions_are_sorted_as_numbers(self):
        self.assertEqual(update.released_versions(), ["0.10.0", "0.2.0", "0.1.0"])
        self.assertIsNone(self.sent[0].get_header("Authorization"))
        self.assertEqual(self.sent[0].full_url, "https://api.github.com/repos/JoseAlmando/cli-tools/releases?per_page=100")

    def test_check_reports_and_installs_nothing_even_read_only(self):
        _, out, _ = self.update("--check", env={"CLT_JSON": "1", "CLT_READONLY": "1"})
        self.assertEqual(json.loads(out), {"installed": "0.2.0", "latest": "0.10.0", "target": None, "action": "check"})
        self.run_.assert_not_called()

    def test_a_development_install_is_left_alone(self):
        with mock.patch.object(update, "install_info",
                               lambda: {"installed": True, "editable": True, "path": "/src/cli-tools"}):
            _, _, err = self.update()
        self.assertIn("development install", err)
        self.assertIn("/src/cli-tools", err)
        self.assertIn("git pull", err)
        self.run_.assert_not_called()

    def test_installs_the_latest_or_a_given_older_version(self):
        self.update()
        self.assertEqual(self.run_.call_args[0][0], update.install_command("0.10.0"))
        self.update("v0.1.0")
        self.assertEqual(self.run_.call_args[0][0],
                         ["uv", "tool", "install", "--force", "git+https://github.com/JoseAlmando/cli-tools@v0.1.0"])

    def test_an_unknown_version_lists_the_available_ones(self):
        with self.assertRaisesRegex(ToolError, "0.10.0, 0.2.0, 0.1.0"):
            self.update("9.9.9")

    def test_nothing_to_do_on_the_same_version(self):
        _, _, err = self.update("0.2.0")
        self.assertIn("already on 0.2.0", err)
        self.run_.assert_not_called()

    def test_without_uv_it_prints_the_commands(self):
        with mock.patch.object(update.shutil, "which", lambda name: None):
            with self.assertRaises(ToolError) as caught:
                self.update()
        self.assertIn("uv tool install --force git+https://github.com/JoseAlmando/cli-tools@v0.10.0", str(caught.exception))
        self.assertIn("pipx install --force git+https://github.com/JoseAlmando/cli-tools@v0.10.0", str(caught.exception))

    def test_on_windows_it_continues_in_the_background(self):
        with mock.patch.object(update.os, "name", "nt"):
            code, _, err = self.update()
        self.assertEqual(code, 0)
        flags = self.popen.call_args[1]["creationflags"]
        self.assertEqual(flags, update.DETACHED_PROCESS | update.CREATE_NEW_PROCESS_GROUP)
        self.assertIn("background", err)
        self.run_.assert_not_called()

    def test_elsewhere_it_returns_the_installer_exit_code(self):
        self.run_.return_value = mock.Mock(returncode=7)
        self.assertEqual(self.update()[0], 7)

    def test_read_only_blocks_an_update(self):
        with self.assertRaises(errors.ReadOnlyError):
            self.update(env={"CLT_READONLY": "1"})
        self.run_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
