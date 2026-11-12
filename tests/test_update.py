import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cli_tools.commands import update
from cli_tools.core import errors, http
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf

NOTES_10 = "### Breaking changes\n\n- feat!: drop a flag\n\n### Features\n\n- feat: ten\n\n### Other\n\n- docs: ten"
NOTES_2 = "### Fixes\n\n- fix: two\n\n### Other\n\n- ci: two"
RELEASES = json.dumps([{"tag_name": "v0.2.0", "body": NOTES_2}, {"tag_name": "v0.10.0", "body": NOTES_10},
                       {"tag_name": "v0.1.0", "body": "### Features\n\n- feat: one"}, {"tag_name": "nightly"}])
INSTALLED = {"installed": True, "editable": False, "path": None, "installer": "uv"}
URL = "git+https://github.com/JoseAlmando/cli-tools@v0.10.0"


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
        # The same outcome on every runner: Linux and macOS unless a test says otherwise
        mock.patch.object(update, "on_windows", lambda: False).start()
        self.run_ = mock.patch.object(update.subprocess, "run",
                                      return_value=mock.Mock(returncode=0, stdout="", stderr="")).start()
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
        self.assertEqual(json.loads(out), {"installed": "0.2.0", "latest": "0.10.0", "target": None, "action": "check",
                                           "changes": [{"version": "0.10.0", "notes": "### Breaking changes\n\n"
                                                        "- feat!: drop a flag\n\n### Features\n\n- feat: ten"}]})
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
        self.assertEqual(self.run_.call_args_list[0][0][0], ["/usr/bin/uv", "tool", "install", "--force", URL])
        self.run_.reset_mock()
        self.update("v0.1.0")
        self.assertEqual(self.run_.call_args_list[0][0][0],
                         ["/usr/bin/uv", "tool", "install", "--force", "git+https://github.com/JoseAlmando/cli-tools@v0.1.0"])

    def test_an_unknown_version_lists_the_available_ones(self):
        with self.assertRaisesRegex(ToolError, "0.10.0, 0.2.0, 0.1.0"):
            self.update("9.9.9")

    def test_nothing_to_do_on_the_same_version(self):
        _, _, err = self.update("0.2.0")
        self.assertIn("already on 0.2.0", err)
        self.run_.assert_not_called()

    def test_without_uv_it_prints_the_command(self):
        with mock.patch.object(update.shutil, "which", lambda name: None):
            with self.assertRaises(ToolError) as caught:
                self.update()
        self.assertIn(f"uv tool install --force {URL}", str(caught.exception))
        self.assertNotIn("pipx", str(caught.exception))  # a second installer would leave two copies

    def test_a_pipx_install_is_updated_with_pipx(self):
        with mock.patch.object(update, "install_info", lambda: {**INSTALLED, "installer": "pipx"}), \
                mock.patch.object(update.shutil, "which", lambda name: f"/usr/bin/{name}"):
            self.update()
        self.assertEqual(self.run_.call_args_list[0][0][0], ["/usr/bin/pipx", "install", "--force", URL])

    def test_a_pip_install_says_what_to_run_and_runs_nothing(self):
        with mock.patch.object(update, "install_info", lambda: {**INSTALLED, "installer": "pip"}), \
                self.assertRaises(ToolError) as caught:
            self.update()
        self.assertIn(f"-m pip install --upgrade {URL}", str(caught.exception))
        self.run_.assert_not_called()
        self.popen.assert_not_called()

    def test_the_installer_is_told_by_the_files_in_the_environment(self):
        for marker, expected in (("uv-receipt.toml", "uv"), ("pipx_metadata.json", "pipx"), (None, "pip")):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as prefix:
                if marker:
                    (Path(prefix) / marker).write_text("")
                self.assertEqual(update.installer(prefix), expected)

    def test_on_windows_it_continues_in_the_background_with_a_log(self):
        with mock.patch.object(update, "on_windows", lambda: True), \
                mock.patch.object(update, "base_python", lambda: "/base/python"), \
                mock.patch.object(update.tempfile, "gettempdir", lambda: self.tmp.name):
            code, _, err = self.update()
        self.assertEqual(code, 0)
        # The base interpreter, outside clt's environment, waits for this clt to exit before the installer runs
        argv = self.popen.call_args[0][0]
        self.assertEqual(argv[:4], ["/base/python", "-I", "-c", update.WAIT_THEN_RUN])
        self.assertEqual(argv[4:], [str(os.getpid()), "/usr/bin/uv", "tool", "install", "--force", URL])
        kwargs = self.popen.call_args[1]
        self.assertEqual(kwargs["creationflags"], update.DETACHED_PROCESS | update.CREATE_NEW_PROCESS_GROUP)
        log = Path(self.tmp.name) / "clt-update.log"
        self.assertEqual((Path(kwargs["stdout"].name), kwargs["stderr"]), (log, update.subprocess.STDOUT))
        self.assertTrue(kwargs["stdout"].closed)  # the child keeps its own handle
        self.assertIn("background", err)
        self.assertIn(str(log), err)
        self.run_.assert_not_called()

    def test_elsewhere_it_returns_the_installer_exit_code(self):
        self.run_.return_value = mock.Mock(returncode=7)
        self.assertEqual(self.update()[0], 7)

    def test_a_successful_update_refreshes_the_skill_copies(self):
        with mock.patch.object(update.shutil, "which", lambda name: f"/x/{name}"):
            self.update(env={"CLT_JSON": "1"})
        self.assertEqual(self.run_.call_args_list[1][0][0], ["/x/clt", "skill", "refresh"])
        kwargs = self.run_.call_args_list[1][1]
        self.assertTrue(kwargs.get("capture_output"))
        self.assertNotIn("CLT_JSON", kwargs["env"])  # its output is shown on stderr, as text, not as a JSON line

    def test_the_refresh_runs_the_clt_that_was_just_updated(self):
        running = Path(self.tmp.name) / "bin" / "clt"
        running.parent.mkdir()
        running.write_text("")
        with mock.patch.object(update.sys, "argv", [str(running)]), \
                mock.patch.object(update.shutil, "which", lambda name: f"/elsewhere/{name}"):
            self.update()
        self.assertEqual(self.run_.call_args_list[1][0][0], [str(running), "skill", "refresh"])

    def test_a_refresh_that_fails_says_so_instead_of_showing_its_output(self):
        self.run_.side_effect = [mock.Mock(returncode=0),
                                 mock.Mock(returncode=2, stdout="", stderr="usage: clt [-h] TOOL\nclt: error: skill")]
        with mock.patch.object(update.shutil, "which", lambda name: f"/x/{name}"):
            _, _, err = self.update()
        self.assertIn("not refreshed", err)
        self.assertNotIn("usage:", err)

    def test_a_successful_update_recommends_the_skill_for_agents_without_it(self):
        with mock.patch.object(update.agents, "recommend_skill") as recommend:
            self.update()
        recommend.assert_called_once()
        self.run_.return_value = mock.Mock(returncode=7)
        with mock.patch.object(update.agents, "recommend_skill") as recommend:
            self.update()
        recommend.assert_not_called()

    def test_a_failed_update_refreshes_nothing(self):
        self.run_.return_value = mock.Mock(returncode=7)
        self.update()
        self.assertEqual(self.run_.call_count, 1)

    def test_without_the_new_clt_on_the_path_it_says_to_refresh(self):
        with mock.patch.object(update.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None):
            _, _, err = self.update()
        self.assertEqual(self.run_.call_count, 1)
        self.assertIn("clt skill refresh", err)

    def test_on_windows_it_says_to_refresh_afterwards(self):
        with mock.patch.object(update, "on_windows", lambda: True):
            _, _, err = self.update()
        self.assertIn("clt skill refresh", err)

    def test_check_shows_what_the_new_versions_bring_newest_first(self):
        with mock.patch.object(update, "installed_version", lambda: "0.1.0"):
            _, _, err = self.update("--check")
        self.assertLess(err.index("0.10.0"), err.index("feat: ten"))
        self.assertLess(err.index("feat: ten"), err.index("fix: two"))
        self.assertIn("Breaking changes", err)
        self.assertNotIn("Other", err)
        self.assertNotIn("docs: ten", err)
        self.assertNotIn("feat: one", err)

    def test_an_update_shows_the_changes_before_installing(self):
        _, _, err = self.update()
        self.assertLess(err.index("feat: ten"), err.index("Installing clt 0.10.0"))
        self.assertNotIn("fix: two", err)

    def test_a_downgrade_or_the_same_version_shows_no_changes(self):
        for argv in (["0.1.0"], ["0.2.0"]):
            _, out, err = self.update(*argv, env={"CLT_JSON": "1"})
            self.assertNotIn("feat:", err)
            self.assertEqual(json.loads(out)["changes"], [])

    def test_a_local_version_suffix_still_finds_the_changes(self):
        with mock.patch.object(update, "installed_version", lambda: "0.2.0+d1db646"):
            _, _, err = self.update("--check")
        self.assertIn("feat: ten", err)
        self.assertNotIn("fix: two", err)

    def test_read_only_blocks_an_update(self):
        with self.assertRaises(errors.ReadOnlyError):
            self.update(env={"CLT_READONLY": "1"})
        self.run_.assert_not_called()


class WaitThenRunTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "the waiting script uses the Windows API")
    def test_the_installer_starts_only_after_clt_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "stamp"
            started = time.time()
            clt = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])  # stands for clt
            write = f"import time; open({str(stamp)!r}, 'w').write(str(time.time()))"  # stands for the installer
            done = subprocess.run([sys.executable, "-I", "-c", update.WAIT_THEN_RUN, str(clt.pid),
                                   sys.executable, "-c", write], timeout=60)
            clt.wait()
            self.assertEqual(done.returncode, 0)
            self.assertGreaterEqual(float(stamp.read_text()) - started, 1.5)

    @unittest.skipUnless(os.name == "nt", "the waiting script uses the Windows API")
    def test_the_installer_output_reaches_the_log_of_a_detached_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "clt-update.log"
            with open(log, "w", encoding="utf-8") as out:
                helper = subprocess.Popen([sys.executable, "-I", "-c", update.WAIT_THEN_RUN, "0", sys.executable, "-c",
                                           "import sys; print('to stdout'); print('to stderr', file=sys.stderr)"],
                                          stdout=out, stderr=subprocess.STDOUT, close_fds=True,
                                          creationflags=update.DETACHED_PROCESS | update.CREATE_NEW_PROCESS_GROUP)
            self.assertEqual(helper.wait(timeout=60), 0)
            self.assertEqual(sorted(log.read_text(encoding="utf-8").split("\n")), ["", "to stderr", "to stdout"])


if __name__ == "__main__":
    unittest.main()
