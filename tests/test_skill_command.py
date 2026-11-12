import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools import __version__, skill_dir
from cli_tools.core import agents, context, errors
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf


def can_link():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            (Path(tmp) / "link").symlink_to(tmp, target_is_directory=True)
        except OSError:
            return False
    return True


# Windows creates links only with Developer Mode or as an administrator
NEEDS_LINKS = unittest.skipUnless(can_link(), "this system does not let this user create symbolic links")


def skill():
    from cli_tools.commands import skill as module
    return module


class SkillCommandTests(TempConf):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home))
        self.on_path = set()
        patcher = mock.patch.object(shutil, "which", lambda name: f"/bin/{name}" if name in self.on_path else None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_skill(self, *argv, ctx=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = skill().main(list(argv), ctx=ctx or context.CLIContext(json_mode=True))
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def claude(self):
        return self.home / ".claude" / "skills" / "clt"

    def agents(self):
        return self.home / ".agents" / "skills" / "clt"

    def test_install_copies_and_marks(self):
        _, result, _ = self.run_skill("install", "--agent", "claude")
        self.assertTrue((self.claude() / "SKILL.md").is_file())
        self.assertTrue((self.claude() / "references" / "jira.md").is_file())
        self.assertFalse(self.claude().is_symlink())
        self.assertEqual(json.loads((self.claude() / ".clt-skill").read_text()), {"version": __version__})
        self.assertEqual(result, {"action": "install",
                                  "targets": [{"agent": "claude", "path": str(self.claude()), "how": "copy"}]})

    def test_install_without_agent_uses_the_clis_on_the_path(self):
        self.on_path = {"codex"}
        _, result, _ = self.run_skill("install")
        self.assertEqual([t["agent"] for t in result["targets"]], ["codex"])
        self.assertTrue((self.agents() / "SKILL.md").is_file())
        self.assertFalse(self.claude().exists())
        self.on_path = set()
        with self.assertRaisesRegex(ToolError, "--agent"):
            self.run_skill("install")

    def test_codex_and_gemini_share_one_folder(self):
        with mock.patch.object(shutil, "copytree", wraps=shutil.copytree) as copies:
            _, result, _ = self.run_skill("install", "--agent", "codex", "--agent", "gemini")
        self.assertEqual(sum(1 for call in copies.call_args_list if Path(call.args[0]) == skill_dir()), 1)
        self.assertEqual([(t["agent"], t["path"]) for t in result["targets"]],
                         [("codex", str(self.agents())), ("gemini", str(self.agents()))])

    def test_reinstall_replaces_a_clt_copy(self):
        self.run_skill("install", "--agent", "claude")
        (self.claude() / "old.md").write_text("old")
        self.run_skill("install", "--agent", "claude")
        self.assertFalse((self.claude() / "old.md").exists())
        self.assertTrue((self.claude() / "SKILL.md").is_file())

    def test_a_foreign_target_is_left_alone(self):
        own = self.claude()
        own.mkdir(parents=True)
        (own / "mine.md").write_text("mine")
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        for setup in ("folder", "file", "link"):
            with self.subTest(setup=setup):
                if setup == "file":
                    shutil.rmtree(own)
                    own.write_text("a file")
                if setup == "link":
                    if not can_link():
                        self.skipTest("this system does not let this user create symbolic links")
                    own.unlink()
                    own.symlink_to(other, target_is_directory=True)
                for action in (("install", "--agent", "claude"), ("uninstall", "--agent", "claude")):
                    with self.assertRaisesRegex(ToolError, str(own).replace("\\", "\\\\")):
                        self.run_skill(*action)
                self.assertTrue(own.exists() or own.is_symlink())
        self.assertTrue(other.is_dir())

    def test_a_foreign_sibling_is_left_alone(self):
        sibling = self.claude().parent / ".clt.new"
        sibling.mkdir(parents=True)
        (sibling / "precious.txt").write_text("keep")
        self.run_skill("install", "--agent", "claude")
        self.assertEqual((sibling / "precious.txt").read_text(), "keep")
        self.assertEqual(sorted(p.name for p in self.claude().parent.iterdir()), [".clt.new", "clt"])

    def test_a_failed_swap_keeps_the_old_copy(self):
        self.run_skill("install", "--agent", "claude")
        (self.claude() / "SKILL.md").write_text("old copy")
        real = Path.rename
        calls = []

        def rename(source, destination):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("the file is in use")
            return real(source, destination)

        with mock.patch.object(Path, "rename", rename), self.assertRaisesRegex(ToolError, "in use"):
            self.run_skill("install", "--agent", "claude")
        self.assertEqual((self.claude() / "SKILL.md").read_text(), "old copy")
        self.assertTrue((self.claude() / ".clt-skill").is_file())
        self.assertEqual(sorted(p.name for p in self.claude().parent.iterdir()), ["clt"])

    def test_a_briefly_locked_folder_is_renamed_on_a_later_try(self):
        real = Path.rename
        denied = []

        def rename(source, destination):
            if Path(destination) == self.claude() and len(denied) < 2:  # e.g. an antivirus scanning the new files
                denied.append(source)
                raise PermissionError(13, "Access is denied")
            return real(source, destination)

        with mock.patch.object(Path, "rename", rename), mock.patch("time.sleep") as sleep:
            code, result, _ = self.run_skill("install", "--agent", "claude")
        self.assertEqual((code, len(denied), sleep.call_count), (0, 2, 2))
        self.assertTrue((self.claude() / ".clt-skill").is_file())

    def test_a_folder_that_stays_locked_fails_and_keeps_the_old_copy(self):
        self.run_skill("install", "--agent", "claude")
        (self.claude() / "SKILL.md").write_text("old copy")
        real = Path.rename

        def rename(source, destination):
            if Path(destination) == self.claude() and Path(source).name == "clt":
                raise PermissionError(13, "Access is denied")
            return real(source, destination)

        with mock.patch.object(Path, "rename", rename), mock.patch("time.sleep"), \
                self.assertRaisesRegex(ToolError, "Access is denied"):
            self.run_skill("install", "--agent", "claude")
        self.assertEqual((self.claude() / "SKILL.md").read_text(), "old copy")

    def test_filesystem_errors_are_tool_errors(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "skills").write_text("not a folder")
        with self.assertRaises(ToolError):
            self.run_skill("install", "--agent", "claude")

    def test_the_refusal_says_where_to_move_it(self):
        self.agents().mkdir(parents=True)
        with self.assertRaises(ToolError) as caught:
            self.run_skill("install", "--agent", "codex")
        self.assertIn("out of", str(caught.exception))
        self.assertIn(str(self.home / ".agents" / "clt.bak"), str(caught.exception))

    def test_refresh_warns_about_unmarked_copies(self):
        self.agents().mkdir(parents=True)
        (self.agents() / "SKILL.md").write_text("by hand")
        _, result, err = self.run_skill("refresh")
        self.assertEqual(result["targets"], [])
        self.assertIn(str(self.agents()), err)
        self.assertIn(".clt-skill", err)
        self.assertEqual((self.agents() / "SKILL.md").read_text(), "by hand")

    @NEEDS_LINKS
    def test_link(self):
        _, result, _ = self.run_skill("install", "--agent", "claude", "--link")
        self.assertTrue(self.claude().is_symlink())
        self.assertEqual(self.claude().resolve(), skill_dir())
        self.assertFalse((skill_dir() / ".clt-skill").exists())
        self.assertEqual(result["targets"][0]["how"], "link")
        self.run_skill("install", "--agent", "claude")
        self.assertFalse(self.claude().is_symlink())

    def test_link_without_privilege(self):
        with mock.patch.object(Path, "symlink_to", side_effect=OSError("privilege not held")), \
                self.assertRaisesRegex(ToolError, "without --link"):
            self.run_skill("install", "--agent", "claude", "--link")

    @NEEDS_LINKS
    def test_uninstall_removes_only_clt_targets(self):
        self.run_skill("install", "--agent", "claude")
        self.run_skill("install", "--agent", "codex", "--link")
        _, result, _ = self.run_skill("uninstall", "--agent", "claude", "--agent", "codex")
        self.assertFalse(self.claude().exists())
        self.assertFalse(self.agents().is_symlink() or self.agents().exists())
        self.assertTrue((skill_dir() / "SKILL.md").is_file())
        self.assertEqual([t["agent"] for t in result["targets"]], ["claude", "codex"])
        code, result, _ = self.run_skill("uninstall", "--agent", "claude")
        self.assertEqual((code, result["targets"]), (0, []))

    def test_uninstall_codex_mentions_gemini(self):
        self.run_skill("install", "--agent", "codex")
        _, _, err = self.run_skill("uninstall", "--agent", "codex")
        self.assertIn("gemini", err.lower())

    @NEEDS_LINKS
    def test_refresh_recopies_marked_copies_only(self):
        _, result, _ = self.run_skill("refresh")
        self.assertEqual(result, {"action": "refresh", "targets": []})
        self.run_skill("install", "--agent", "claude")
        self.run_skill("install", "--agent", "codex", "--link")
        (self.claude() / "SKILL.md").write_text("stale")
        _, result, _ = self.run_skill("refresh")
        self.assertEqual((self.claude() / "SKILL.md").read_text(encoding="utf-8"),
                         (skill_dir() / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue(self.agents().is_symlink())
        self.assertEqual([t["agent"] for t in result["targets"]], ["claude"])

    def help_hint(self, ctx=None, tty=True):
        from cli_tools import cli

        class Err(io.StringIO):
            def isatty(self):
                return tty

        out, err = io.StringIO(), Err()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main(["--help"], ctx=ctx or context.CLIContext())
        return out.getvalue(), err.getvalue()

    def test_missing_lists_agents_on_the_path_without_any_skill_copy(self):
        self.on_path = {"claude", "codex", "gemini"}
        self.assertEqual(agents.without_skill(), ["claude", "codex", "gemini"])
        self.run_skill("install", "--agent", "codex")
        self.assertEqual(agents.without_skill(), ["claude"])
        self.claude().mkdir(parents=True)
        self.assertEqual(agents.without_skill(), [])
        self.on_path = set()
        shutil.rmtree(self.claude())
        self.assertEqual(agents.without_skill(), [])

    def test_help_recommends_installing_the_skill_on_stderr(self):
        self.on_path = {"claude", "codex"}
        out, err = self.help_hint()
        self.assertIn("clt skill install --agent claude --agent codex", err)
        self.assertNotIn("clt skill install", out)

    def test_no_recommendation_without_a_terminal_or_with_json_or_when_installed(self):
        self.on_path = {"claude"}
        self.assertEqual(self.help_hint(tty=False)[1], "")
        self.assertEqual(self.help_hint(ctx=context.CLIContext(json_mode=True))[1], "")
        self.run_skill("install", "--agent", "claude")
        self.assertEqual(self.help_hint()[1], "")

    def test_read_only_blocks_every_action(self):
        ctx = context.CLIContext(read_only=True)
        for argv in (["install", "--agent", "claude"], ["uninstall", "--agent", "claude"], ["refresh"]):
            with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                self.run_skill(*argv, ctx=ctx)
        self.assertFalse(self.claude().exists())
