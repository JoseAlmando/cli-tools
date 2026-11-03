"""scripts/check_conventions.py: Conventional Commits, Conventional Branch, and the commit-msg hook."""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import GitRepo, run_git

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_conventions.py"
_spec = importlib.util.spec_from_file_location("check_conventions", SCRIPT)
conventions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conventions)


def run_script(cwd, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class CommitMessageTests(unittest.TestCase):
    VALID = [
        "feat: add the range mode",
        "fix(release): skip earlier release commits",
        "feat(api)!: drop the v1 paths",
        "refactor!: rename the client",
        "docs: explain the hook\n\nWith an example.\n",
        "chore(release): 1.2.0",
        "fix: x\n\nThe body.\n\nBREAKING CHANGE: the y option is gone",
        "fix: x\n\nBREAKING-CHANGE: the y option is gone",
        "perf: x\n\nRefs: #123\nReviewed-by: Someone",
        "style: x", "test: x", "build: x", "ci: x", "revert: x",
        "Merge branch 'main' into feature/x",
        "Merge pull request #12 from someone/feature/x",
        'Revert "feat: add x"\n\nThis reverts commit 0123456.',
        "fixup! feat: add x",
        "squash! feat: add x",
        "amend! feat: add x",
        # git commit templates: comment lines are ignored, and so is everything under the scissors line
        "# Please enter the commit message\nfeat: add x\n# Lines starting with '#' are ignored\n",
        "\n\nfeat: add x\n\n\n",
        "feat: add x\n# ------------------------ >8 ------------------------\ndiff --git a/x b/x\n",
        "feat: añade soporte 🚀",
    ]
    INVALID = [
        ("", "empty"),
        ("# only a comment\n", "empty"),
        ("Add the range mode", "type"),
        ("feature: add x", "feature"),
        ("Feat: add x", "lowercase"),
        ("feat:add x", "space"),
        ("feat:  add x", "space"),
        ("feat: ", "description"),
        ("feat:", "description"),
        ("feat(): add x", "scope"),
        ("feat(api: add x", "type"),
        ("feat: add x\nno blank line", "blank line"),
        ("fix: x\n\nbreaking change: y", "BREAKING CHANGE"),
        ("WIP: not for release", "WIP"),
    ]

    def test_valid_messages(self):
        for text in self.VALID:
            with self.subTest(text=text):
                self.assertEqual(conventions.check_commit_message(text), [])

    def test_invalid_messages(self):
        for text, hint in self.INVALID:
            with self.subTest(text=text):
                problems = conventions.check_commit_message(text)
                self.assertTrue(problems)
                self.assertIn(hint.lower(), " ".join(problems).lower())

    def test_commit_msg_mode_exits_1_with_the_problems_and_the_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = Path(tmp) / "COMMIT_EDITMSG"
            message.write_bytes(b"Added stuff\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(conventions.main(["commit-msg", str(message)]), 1)
            self.assertIn("<type>(<scope>)!: <description>", err.getvalue())
            self.assertIn("feat(", err.getvalue())  # an example
            message.write_bytes("feat: añade x\n".encode())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(conventions.main(["commit-msg", str(message)]), 0)


class BranchNameTests(unittest.TestCase):
    VALID = ["main", "master", "develop", "feature/add-login", "feat/range-mode", "bugfix/fix-header",
             "fix/issue-123", "hotfix/security-patch", "release/v1.2.0", "release/1.2.0", "chore/update-deps",
             "feature/conventional-commits", "refs/heads/fix/x"]
    INVALID = [
        ("", "empty"),
        ("my-branch", "<type>/<description>"),
        ("feature", "<type>/<description>"),
        ("features/add-x", "features"),
        ("docs/add-x", "docs"),
        ("Feature/add-x", "lowercase"),
        ("feature/Add-Login", "lowercase"),
        ("feature/add_login", "_"),
        ("feature/add login", "' '"),
        ("feature/a/b", "/"),
        ("feature/", "description"),
        ("feature/-add-x", "hyphen"),
        ("feature/add-x-", "hyphen"),
        ("feature/add--x", "hyphen"),
        ("feature/v1.2", "release/"),
        ("release/v1..2", "dot"),
        ("release/.v1", "dot"),
        ("release/v1.", "dot"),
    ]

    def test_valid_names(self):
        for name in self.VALID:
            with self.subTest(name=name):
                self.assertEqual(conventions.check_branch_name(name), [])

    def test_invalid_names(self):
        for name, hint in self.INVALID:
            with self.subTest(name=name):
                problems = conventions.check_branch_name(name)
                self.assertTrue(problems)
                self.assertIn(hint.lower(), " ".join(problems).lower())

    def test_branch_mode_shows_the_format_with_an_example(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(conventions.main(["branch", "My_Branch"]), 1)
        self.assertIn("<type>/<description>", err.getvalue())
        self.assertIn("feature/", err.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(conventions.main(["branch", "fix/header"]), 0)
        self.assertEqual(out.getvalue(), "fix/header: ok\n")


class IsolatedGitRepo(GitRepo):
    """A GitRepo that ignores the user's global and system git settings (a global core.hooksPath, say)."""

    def setUp(self):
        super().setUp()
        empty = Path(self.tmp.name) / "empty.gitconfig"
        empty.write_text("")
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)


class RangeTests(IsolatedGitRepo):
    def test_every_commit_in_the_range_is_checked(self):
        self.git("checkout", "--quiet", "-b", "feature/x")
        self.commit("feat: add x")
        good = self.git("rev-parse", "HEAD")
        self.commit("Fix y")
        self.commit("docs: z\nno blank line")
        done = run_script(self.root, "range", "main..HEAD")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("Fix y", done.stderr)
        self.assertIn("docs: z", done.stderr)
        self.assertNotIn("feat: add x", done.stderr)
        self.assertIn("2 of 3", done.stderr)
        done = run_script(self.root, "range", f"main..{good}")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_an_empty_range_passes_and_a_bad_one_is_an_error(self):
        self.assertEqual(run_script(self.root, "range", "main..main").returncode, 0)
        done = run_script(self.root, "range", "main..nope")
        self.assertEqual(done.returncode, 2)
        self.assertIn("nope", done.stderr)
        self.assertEqual(run_script(self.root, "range", "main").returncode, 2)


class InstallHookTests(IsolatedGitRepo):
    def setUp(self):
        super().setUp()
        self.git("config", "--unset", "core.hooksPath")  # GitRepo points it to an empty folder

    def install(self, *args, cwd=None):
        return run_script(cwd or self.root, "install-hook", *args)

    def test_writes_the_hook_into_the_git_folder_and_is_idempotent(self):
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        hook = self.root / ".git" / "hooks" / "commit-msg"
        text = hook.read_bytes().decode("utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertNotIn("\r\n", text)
        self.assertIn("python3", text)
        self.assertIn("commit-msg", text)
        if os.name != "nt":
            self.assertTrue(os.access(hook, os.X_OK))
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("already", done.stdout + done.stderr)

    def test_never_overwrites_a_different_hook_without_force(self):
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "commit-msg").write_text("#!/bin/sh\necho mine\n")
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertIn("--force", done.stderr)
        self.assertEqual((hooks / "commit-msg").read_text(), "#!/bin/sh\necho mine\n")
        done = self.install("--force")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("check_conventions.py", (hooks / "commit-msg").read_text())
        self.assertEqual((hooks / "commit-msg.backup").read_text(), "#!/bin/sh\necho mine\n")
        self.assertIn("commit-msg.backup", done.stdout + done.stderr)

    def test_respects_core_hooks_path(self):
        self.git("config", "core.hooksPath", "githooks")  # relative: to the top of the working tree
        (self.root / "sub").mkdir()
        done = self.install(cwd=self.root / "sub")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.root / "githooks" / "commit-msg").is_file())
        self.assertFalse((self.root / ".git" / "hooks" / "commit-msg").exists())
        elsewhere = Path(self.tmp.name) / "shared-hooks"
        self.git("config", "core.hooksPath", str(elsewhere))
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((elsewhere / "commit-msg").is_file())

    def test_works_from_a_worktree_where_git_is_a_file(self):
        worktree = Path(self.tmp.name) / "wt"
        self.git("worktree", "add", "--quiet", "-b", "feature/x", str(worktree))
        self.assertTrue((worktree / ".git").is_file())
        done = self.install(cwd=worktree)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.root / ".git" / "hooks" / "commit-msg").is_file())  # hooks are shared by worktrees

    def test_outside_a_repo_it_says_so(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            done = self.install(cwd=elsewhere)
        self.assertEqual(done.returncode, 2)
        self.assertIn("git", done.stderr)

    @unittest.skipUnless(shutil.which("sh") or os.name == "nt", "git runs hooks with sh")
    def test_the_installed_hook_rejects_a_bad_message(self):
        (self.root / "scripts").mkdir()
        shutil.copy(SCRIPT, self.root / "scripts" / "check_conventions.py")
        self.commit("chore: add the checker")
        self.assertEqual(self.install().returncode, 0)
        (self.root / "x.txt").write_text("x\n")
        self.git("add", "-A")
        done = subprocess.run(["git", "commit", "--quiet", "-m", "Added x"], cwd=self.root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("<type>(<scope>)!: <description>", done.stderr)
        run_git(self.root, "commit", "--quiet", "-m", "feat: add x")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "feat: add x")


if __name__ == "__main__":
    unittest.main()
