import contextlib
import io
import json
import os
import shutil
from pathlib import Path
from unittest import mock

from cli_tools import __version__, skill_dir
from cli_tools.core import context, errors
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf


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
                    own.unlink()
                    own.symlink_to(other, target_is_directory=True)
                for action in (("install", "--agent", "claude"), ("uninstall", "--agent", "claude")):
                    with self.assertRaisesRegex(ToolError, str(own).replace("\\", "\\\\")):
                        self.run_skill(*action)
                self.assertTrue(own.exists() or own.is_symlink())
        self.assertTrue(other.is_dir())

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

    def test_read_only_blocks_every_action(self):
        ctx = context.CLIContext(read_only=True)
        for argv in (["install", "--agent", "claude"], ["uninstall", "--agent", "claude"], ["refresh"]):
            with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                self.run_skill(*argv, ctx=ctx)
        self.assertFalse(self.claude().exists())
