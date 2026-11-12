import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools import cli as clt
from cli_tools.commands.release import cli as release
from cli_tools.commands.release import gitrepo
from cli_tools.core import errors, runner
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.gitea import client as gitea
from cli_tools.tools.github import client as github
from tests.helpers import GitRepo, TempConf, run_git

PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'
REMOTE = "https://github.com/o/r.git"


class TTY(io.StringIO):
    def isatty(self):
        return True


class ReleaseRepo(GitRepo):
    """A Python project at v0.1.0 with two commits since, whose origin looks like github.com/o/r."""

    def setUp(self):
        super().setUp()
        self.commit("Add the project", {"pyproject.toml": PYPROJECT})
        self.git("tag", "-a", "v0.1.0", "-m", "Release 0.1.0")
        self.commit("feat: add x")
        self.commit("Fix y")
        self.git("push", "--quiet", "origin", "main", "v0.1.0")
        # git talks to the bare repo, while the configured URL (what clt reads) is a GitHub one
        self.git("config", "remote.origin.url", REMOTE)
        self.git("config", f"url.{self.origin}.insteadOf", REMOTE)
        env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)

    def release(self, *argv, stdin="", tty=False, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", (TTY if tty else io.StringIO)(stdin)), mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def state(self):
        return self.git("rev-parse", "HEAD"), self.git("tag"), self.git("status", "--porcelain")


class ReleaseTests(ReleaseRepo):
    def test_bump_writes_version_changelog_commit_and_tag_without_pushing(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.assertIn('version = "0.2.0"', (self.root / "pyproject.toml").read_text())
        today = datetime.date.today().isoformat()
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"),
                         f"# Changelog\n\n## 0.2.0 - {today}\n\n### Features\n\n- feat: add x\n\n### Other\n\n- Fix y\n")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "chore(release): 0.2.0")
        self.assertEqual(self.git("tag", "--list", "--format=%(contents)", "v0.2.0"), "chore(release): 0.2.0")
        self.assertEqual(sorted(self.git("show", "--name-only", "--format=", "HEAD").splitlines()),
                         ["CHANGELOG.md", "pyproject.toml"])
        self.assertEqual(self.git("cat-file", "-t", "v0.2.0"), "tag")
        self.assertEqual(self.git("rev-parse", "v0.2.0^{commit}"), self.git("rev-parse", "HEAD"))
        self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))

    def test_a_new_section_goes_on_top_of_an_existing_changelog(self):
        self.commit("Add a changelog", {"CHANGELOG.md": "# Changelog\n\n## 0.1.0 - 2026-01-01\n\n- First\n"})
        self.git("push", "--quiet", "origin", "main")
        self.release("--bump", "fix", "--yes", "--no-push", "--skip-tests")
        text = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Changelog\n\n## 0.1.1 - "))
        self.assertTrue(text.endswith("- Add a changelog\n\n## 0.1.0 - 2026-01-01\n\n- First\n"))

    def test_without_conventional_prefixes_the_changelog_stays_a_flat_list(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.commit("Fix z")
        self.commit("Update the docs")
        self.git("push", "--quiet", "origin", "main")
        self.release("--bump", "fix", "--yes", "--no-push", "--skip-tests")
        # The earlier "chore(release): 0.2.0" commit is not part of the new section
        self.assertEqual(release.changelog_section(self.root / "CHANGELOG.md", "0.2.1"), "- Fix z\n- Update the docs")

    def test_yes_takes_the_suggestion(self):
        self.release("--yes", "--no-push", "--skip-tests")
        self.assertEqual(self.git("describe", "--tags"), "v0.2.0")

    def test_enter_accepts_the_suggestion_and_a_number_picks_another(self):
        self.release("--no-push", "--skip-tests", stdin="\n", tty=True)
        self.assertEqual(self.git("describe", "--tags"), "v0.2.0")
        self.commit("Fix z")
        self.git("push", "--quiet", "origin", "main")
        self.release("--no-push", "--skip-tests", stdin="1\n", tty=True)
        self.assertEqual(self.git("describe", "--tags"), "v0.2.1")

    def test_without_a_terminal_it_needs_bump_or_yes(self):
        with self.assertRaisesRegex(ToolError, "needs a person"):
            self.release("--no-push", "--skip-tests")

    def test_without_a_terminal_it_fails_before_running_the_tests(self):
        marker = Path(self.tmp.name) / "ran"
        (self.root / ".clt.json").write_text(json.dumps({"release": {
            "test_command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]}}))
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Configure releases")
        self.git("push", "--quiet", "origin", "main")
        with self.assertRaisesRegex(ToolError, "needs a person"):
            self.release("--no-push")
        self.assertFalse(marker.exists())

    def test_a_version_file_outside_the_repo_is_refused(self):
        outside = Path(self.tmp.name) / "VERSION"
        outside.write_text("0.1.0\n")
        for argv in (("--dry-run", "--bump", "fix"), ("--bump", "fix", "--yes", "--no-push", "--skip-tests")):
            with self.subTest(argv=argv):
                before = self.state()
                with self.assertRaisesRegex(ToolError, "inside the repository"):
                    self.release(*argv, "--version-file", str(outside))
                self.assertEqual(self.state(), before)

    def test_yes_never_pushes(self):
        _, _, err = self.release("--yes", "--skip-tests")
        self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))
        self.assertIn("clt release --publish", err)

    def test_checks_stop_before_any_change(self):
        cases = {
            "uncommitted": lambda: (self.root / "README.md").write_text("changed\n"),
            "untracked": lambda: (self.root / "new.txt").write_text("x\n"),
            "branch": lambda: self.git("checkout", "--quiet", "-b", "other"),
            "local tag": lambda: self.git("tag", "v0.2.0"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                change()
                before = self.state()
                with self.assertRaises(ToolError):
                    self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
                self.assertEqual(self.state(), before)
                self.git("checkout", "--quiet", "main")
                self.git("reset", "--quiet", "--hard", "origin/main")
                self.git("clean", "-fdq")
                self.git("branch", "-D", "other") if name == "branch" else None
                self.git("tag", "-d", "v0.2.0") if name == "local tag" else None

    def test_a_failed_commit_undoes_the_version_change(self):
        hooks = Path(self.git("config", "core.hooksPath"))
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        before = self.state()
        with self.assertRaisesRegex(ToolError, "undone"):
            self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.assertEqual(self.state(), before)
        self.assertFalse((self.root / "CHANGELOG.md").exists())

    def test_a_missing_git_identity_stops_before_any_change(self):
        empty = Path(self.tmp.name) / "empty.gitconfig"
        empty.write_text("")
        self.git("config", "--unset", "user.email")
        before = self.state()
        with self.assertRaisesRegex(ToolError, "user.email"):
            self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests",
                         env={"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        self.assertEqual(self.state(), before)

    def test_behind_origin_stops(self):
        other = Path(self.tmp.name) / "other"
        run_git(self.tmp.name, "clone", "--quiet", str(self.origin), str(other))
        for key, value in (("user.name", "T"), ("user.email", "t@example.com")):
            run_git(other, "config", key, value)
        (other / "x.txt").write_text("x\n")
        run_git(other, "add", "-A")
        run_git(other, "commit", "--quiet", "-m", "Elsewhere")
        run_git(other, "push", "--quiet", "origin", "main")
        before = self.state()
        with self.assertRaisesRegex(ToolError, "behind"):
            self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.assertEqual(self.state(), before)

    def test_a_tag_already_on_the_remote_stops(self):
        other = Path(self.tmp.name) / "other"
        run_git(self.tmp.name, "clone", "--quiet", str(self.origin), str(other))
        run_git(other, "tag", "v0.2.0")
        run_git(other, "push", "--quiet", "origin", "v0.2.0")
        before = self.state()
        with self.assertRaisesRegex(ToolError, "v0.2.0"):
            self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.assertEqual(self.state(), before)

    def test_failing_tests_stop_the_release(self):
        (self.root / ".clt.json").write_text(json.dumps(
            {"release": {"test_command": [sys.executable, "-c", "import sys; sys.exit(1)"]}}))
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Configure releases")
        self.git("push", "--quiet", "origin", "main")
        before = self.state()
        with self.assertRaisesRegex(ToolError, "tests failed"):
            self.release("--bump", "feat", "--yes", "--no-push")
        self.assertEqual(self.state(), before)

    def test_passing_tests_let_it_continue_and_skip_tests_warns(self):
        marker = Path(self.tmp.name) / "ran"
        (self.root / ".clt.json").write_text(json.dumps({"release": {
            "test_command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]}}))
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Configure releases")
        self.git("push", "--quiet", "origin", "main")
        _, _, err = self.release("--bump", "fix", "--yes", "--no-push", "--skip-tests")
        self.assertFalse(marker.exists())
        self.assertIn("skipped", err.lower())
        self.git("tag", "-d", "v0.1.1")
        self.git("reset", "--quiet", "--hard", "origin/main")
        self.release("--bump", "fix", "--yes", "--no-push")
        self.assertTrue(marker.exists())

    def test_the_project_tests_do_not_see_clt_global_flags(self):
        seen = Path(self.tmp.name) / "env.json"
        script = f"import json, os; json.dump(sorted(k for k in os.environ if k.startswith('CLT_')), open({str(seen)!r}, 'w'))"
        (self.root / ".clt.json").write_text(json.dumps({"release": {"test_command": [sys.executable, "-c", script]}}))
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Configure releases")
        self.git("push", "--quiet", "origin", "main")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = runner.run(clt.main, ["--json", "release", "--bump", "fix", "--yes", "--no-push"])
        self.assertEqual((code, json.loads(out.getvalue())["tag"]), (0, "v0.1.1"))
        self.assertEqual(json.loads(seen.read_text()), [])
        self.assertEqual({k for k in os.environ if k.startswith("CLT_")}, set())

    def test_dry_run_prints_the_plan_and_changes_nothing_even_read_only(self):
        before = self.state()
        _, out, _ = self.release("--dry-run", "--bump", "feat", env={"CLT_READONLY": "1"})
        plan = json.loads(out)
        self.assertEqual({k: plan[k] for k in ("version_file", "old", "new", "tag", "host")},
                         {"version_file": "pyproject.toml", "old": "0.1.0", "new": "0.2.0", "tag": "v0.2.0",
                          "host": "github.com"})
        self.assertEqual(plan["changelog"], "### Features\n\n- feat: add x\n\n### Other\n\n- Fix y")
        self.assertEqual(self.state(), before)

    def test_read_only_blocks_a_real_release(self):
        with self.assertRaises(errors.ReadOnlyError):
            self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests", env={"CLT_READONLY": "1"})

    def test_json_prints_one_result_object(self):
        _, out, _ = self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests", env={"CLT_JSON": "1"})
        self.assertEqual(json.loads(out), {"version": "0.2.0", "tag": "v0.2.0", "version_file": "pyproject.toml",
                                           "pushed": False, "release_url": None})

    def test_without_git_it_says_so(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            with self.assertRaisesRegex(ToolError, "^git is not installed or not on the PATH$"):
                self.release("--dry-run", "--bump", "fix")

    def test_outside_a_repo_it_says_so(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            os.chdir(elsewhere)
            try:
                with self.assertRaisesRegex(ToolError, "Not inside a git repository"):
                    self.release("--dry-run", "--bump", "fix")
            finally:
                os.chdir(self.root)  # Windows cannot delete the folder a process is in

    def test_an_unknown_host_stops(self):
        self.git("config", "remote.origin.url", "https://gitlab.example.com/o/r.git")
        with self.assertRaisesRegex(ToolError, "gitlab.example.com"):
            self.release("--dry-run", "--bump", "fix")

    def test_non_ascii_subjects_with_an_ascii_console(self):
        self.commit("Añade soporte 🚀")
        self.git("push", "--quiet", "origin", "main")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
               "PYTHONIOENCODING": "ascii", "LC_ALL": "C", "CLT_JSON": "1"}
        done = subprocess.run([sys.executable, "-m", "cli_tools", "release", "--bump", "feat", "--yes", "--no-push",
                               "--skip-tests"], cwd=self.root, env=env, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertIn("- Añade soporte 🚀", (self.root / "CHANGELOG.md").read_bytes().decode("utf-8"))


class ChangelogTests(unittest.TestCase):
    def test_sections_group_conventional_commits_by_type(self):
        cases = [
            (["feat: a", "fix: b", "Update c"], "### Features\n\n- feat: a\n\n### Fixes\n\n- fix: b\n\n### Other\n\n- Update c"),
            (["fix(api): b", "docs: d", "feat(cli)!: e", "refactor: f\n\nBREAKING CHANGE: g", "feat: a"],
             "### Breaking changes\n\n- feat(cli)!: e\n- refactor: f\n\n### Features\n\n- feat: a\n\n"
             "### Fixes\n\n- fix(api): b\n\n### Other\n\n- docs: d"),
            (["fix: b", "fix: c"], "### Fixes\n\n- fix: b\n- fix: c"),
            (["Fix y\n\nBREAKING-CHANGE: z", "chore: x"], "### Breaking changes\n\n- Fix y\n\n### Other\n\n- chore: x"),
            # No Conventional prefix at all: today's flat list, even with a BREAKING CHANGE footer
            (["Fix y", "Add x\n\nBREAKING CHANGE: z", "WIP: w"], "- Fix y\n- Add x\n- WIP: w"),
            ([], ""),
        ]
        for messages, expected in cases:
            with self.subTest(messages=messages):
                self.assertEqual(release.format_section(messages), expected)

    def test_prepend_changelog_writes_the_grouped_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            body = release.prepend_changelog(path, "1.0.0", "2026-01-01", ["feat!: a", "fix: b"])
            self.assertEqual(path.read_text(encoding="utf-8"), "# Changelog\n\n## 1.0.0 - 2026-01-01\n\n"
                             "### Breaking changes\n\n- feat!: a\n\n### Fixes\n\n- fix: b\n")
            self.assertEqual(release.changelog_section(path, "1.0.0"), body)

    def test_a_changelog_without_a_title_gets_the_section_on_top(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_bytes(b"## 0.2.0 - 2026-01-02\n\n- Second\n\n## 0.1.0 - 2026-01-01\n\n- First\n")
            release.prepend_changelog(path, "0.3.0", "2026-01-03", ["Third"])
            self.assertEqual(path.read_bytes(), b"## 0.3.0 - 2026-01-03\n\n- Third\n\n## 0.2.0 - 2026-01-02\n\n"
                                                b"- Second\n\n## 0.1.0 - 2026-01-01\n\n- First\n")

    def test_a_changelog_with_windows_line_endings_keeps_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_bytes(b"# Changelog\r\n\r\n## 0.1.0 - 2026-01-01\r\n\r\n- First\r\n")
            release.prepend_changelog(path, "0.2.0", "2026-01-02", ["feat: x", "fix: y"])
            self.assertEqual(path.read_bytes(), b"# Changelog\r\n\r\n## 0.2.0 - 2026-01-02\r\n\r\n### Features\r\n\r\n"
                                                b"- feat: x\r\n\r\n### Fixes\r\n\r\n- fix: y\r\n\r\n"
                                                b"## 0.1.0 - 2026-01-01\r\n\r\n- First\r\n")
            self.assertEqual(release.changelog_section(path, "0.2.0"), "### Features\n\n- feat: x\n\n### Fixes\n\n- fix: y")


class FakePublisher:
    def __init__(self, existing=None, fail_create=False):
        self.existing = existing
        self.fail_create = fail_create
        self.created = []

    def release_url(self, owner, repo, tag):
        return self.existing

    def create_release(self, owner, repo, tag, body):
        if self.fail_create:
            raise ToolError("GitHub is down")
        self.created.append((owner, repo, tag, body))
        self.existing = f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
        return self.existing


class PublishTests(ReleaseRepo):
    def setUp(self):
        super().setUp()
        self.publisher = FakePublisher()
        patcher = mock.patch.object(release, "publisher_for", lambda host: self.publisher)
        patcher.start()
        self.addCleanup(patcher.stop)

    def remote_has(self, tag):
        return gitrepo.remote_tag_exists(self.root, tag) and \
            run_git(self.origin, "rev-parse", "main") == self.git("rev-parse", "HEAD")

    def test_yes_at_the_question_pushes_and_publishes(self):
        _, out, _ = self.release("--skip-tests", stdin="2\ny\n", tty=True, env={"CLT_JSON": "1"})
        self.assertTrue(self.remote_has("v0.2.0"))
        self.assertEqual(self.publisher.created, [("o", "r", "v0.2.0", "### Features\n\n- feat: add x\n\n### Other\n\n- Fix y")])
        result = json.loads(out)
        self.assertEqual((result["pushed"], result["release_url"]),
                         (True, "https://github.com/o/r/releases/tag/v0.2.0"))

    def talks(self):
        """Records every git command that contacts origin (one SSH connection each)."""
        calls = []
        real_run, real_push = gitrepo._run, gitrepo.push

        def run(root, *args):
            if args[0] in ("fetch", "ls-remote", "push", "pull"):
                calls.append(args[0])
            return real_run(root, *args)

        def push(root, *refspecs):
            calls.append("push " + " ".join(refspecs))
            return real_push(root, *refspecs)

        mock.patch.object(gitrepo, "_run", run).start()
        mock.patch.object(gitrepo, "push", push).start()
        self.addCleanup(mock.patch.stopall)
        return calls

    def test_a_release_contacts_origin_twice_and_says_what_it_pushes(self):
        calls = self.talks()
        _, _, err = self.release("--skip-tests", stdin="2\ny\n", tty=True)
        talked = list(calls)
        self.assertTrue(self.remote_has("v0.2.0"))
        self.assertEqual([c.split()[0] for c in talked], ["ls-remote", "push"])
        self.assertIn("refs/tags/v0.2.0", talked[1])
        self.assertIn("Pushing main and v0.2.0 to origin", err)

    def test_publish_resumes_with_one_look_and_one_push(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        calls = self.talks()
        self.release("--publish")
        talked = list(calls)
        self.assertTrue(self.remote_has("v0.2.0"))
        self.assertEqual([c.split()[0] for c in talked], ["ls-remote", "push"])

    def test_publish_pushes_nothing_that_origin_already_has(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.git("push", "--quiet", "origin", "main", "v0.2.0")
        calls = self.talks()
        _, _, err = self.release("--publish")
        self.assertEqual(calls, ["ls-remote"])
        self.assertEqual(len(self.publisher.created), 1)

    def test_no_or_enter_at_the_question_keeps_it_local(self):
        for answer in ("n\n", "\n"):
            with self.subTest(answer=answer):
                _, _, err = self.release("--skip-tests", stdin="2\n" + answer, tty=True)
                self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))
                self.assertIn("clt release --publish", err)
                self.assertEqual(self.publisher.created, [])
                self.git("tag", "-d", "v0.2.0")
                self.git("reset", "--quiet", "--hard", "origin/main")

    def test_publish_pushes_only_the_tag_when_the_branch_already_went(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.git("push", "--quiet", "origin", "main")  # as if the tag push had failed after the branch push
        calls = self.talks()
        _, _, err = self.release("--publish")
        self.assertEqual([c.split()[0] for c in calls], ["ls-remote", "push"])
        self.assertEqual(calls[1].split()[1:], ["refs/tags/v0.2.0"])
        self.assertIn("Pushing v0.2.0 to origin", err)
        self.assertTrue(self.remote_has("v0.2.0"))
        self.assertEqual(len(self.publisher.created), 1)

    def test_publish_resumes_after_a_failed_release_creation(self):
        self.publisher.fail_create = True
        with self.assertRaisesRegex(ToolError, "down"):
            self.release("--skip-tests", stdin="2\ny\n", tty=True)
        self.assertTrue(self.remote_has("v0.2.0"))
        self.publisher.fail_create = False
        _, _, err = self.release("--publish")
        self.assertEqual(len(self.publisher.created), 1)
        self.assertIn("releases/tag/v0.2.0", err)

    def test_publish_after_no_push_pushes_and_publishes(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.release("--publish")
        self.assertTrue(self.remote_has("v0.2.0"))
        self.assertEqual(self.publisher.created[0][2:], ("v0.2.0", "### Features\n\n- feat: add x\n\n### Other\n\n- Fix y"))

    def test_publish_when_the_release_exists_creates_nothing(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.publisher.existing = "https://github.com/o/r/releases/tag/v0.2.0"
        _, _, err = self.release("--publish")
        self.assertEqual(self.publisher.created, [])
        self.assertIn("https://github.com/o/r/releases/tag/v0.2.0", err)

    def test_publish_pushes_only_up_to_the_tag(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        tagged = self.git("rev-parse", "v0.2.0^{commit}")
        self.commit("WIP: not for release")
        self.release("--publish")
        self.assertEqual(run_git(self.origin, "rev-parse", "main"), tagged)
        self.assertTrue(gitrepo.remote_tag_exists(self.root, "v0.2.0"))

    def test_a_failed_publish_says_how_to_finish(self):
        self.publisher.fail_create = True
        with self.assertRaisesRegex(ToolError, "clt release --publish"):
            self.release("--skip-tests", stdin="2\ny\n", tty=True)

    def test_nothing_to_release_points_to_an_unpublished_tag(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        with self.assertRaisesRegex(ToolError, "v0.2.0.*clt release --publish"):
            self.release("--yes", "--no-push", "--skip-tests")

    def test_publish_dry_run_says_what_is_missing_and_changes_nothing(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        calls = self.talks()
        for env in ({}, {"CLT_READONLY": "1"}):  # a dry run only reads, so read-only mode allows it
            with self.subTest(env=env):
                code, out, _ = self.release("--publish", "--dry-run", env=env)
                self.assertEqual((code, json.loads(out)), (0, {"tag": "v0.2.0", "host": "github.com", "repo": "o/r",
                                                               "push": ["main", "v0.2.0"], "create_release": True}))
        self.assertEqual(calls, ["ls-remote", "ls-remote"])
        self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))
        self.assertEqual(self.publisher.created, [])

    def test_publish_dry_run_with_everything_published_has_nothing_to_do(self):
        self.release("--skip-tests", stdin="2\ny\n", tty=True)
        code, out, _ = self.release("--publish", "--dry-run")
        self.assertEqual((json.loads(out)["push"], json.loads(out)["create_release"]), ([], False))
        self.assertEqual(len(self.publisher.created), 1)

    def test_publish_is_blocked_in_read_only_mode(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        with self.assertRaises(errors.ReadOnlyError):
            self.release("--publish", env={"CLT_READONLY": "1"})
        self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))


class PublisherTests(TempConf):
    def test_github_and_the_gitea_session_host_get_their_client(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITEA_URL": "https://gitea.example.com",
                                          "GITEA_TOKEN": "t"}):
            self.assertIsInstance(release.publisher_for("github.com").client, github.GitHub)
            self.assertIsInstance(release.publisher_for("gitea.example.com").client, gitea.Gitea)
            with self.assertRaisesRegex(ToolError, "other.example.com"):
                release.publisher_for("other.example.com")

    def test_a_host_without_a_gitea_session_is_not_logged_in(self):
        with self.assertRaises(errors.NotLoggedIn) as caught:
            release.publisher_for("gitea.example.com")
        self.assertIn("clt gitea login --url https://gitea.example.com", str(caught.exception))

    def test_release_url_and_create_release(self):
        calls = []

        class Client:
            def call(self, method, path, body):
                calls.append((method, path, json.loads(body) if body else None))
                if method == "GET":
                    raise HttpError(404, '{"message": "Not Found"}')
                return 201, '{"html_url": "https://h/o/r/releases/tag/v1.0.0"}', {}

        pub = release.Publisher(Client())
        self.assertIsNone(pub.release_url("o", "r", "v1.0.0"))
        self.assertEqual(pub.create_release("o", "r", "v1.0.0", "- x"), "https://h/o/r/releases/tag/v1.0.0")
        self.assertEqual(calls, [("GET", "repos/o/r/releases/tags/v1.0.0", None),
                                 ("POST", "repos/o/r/releases", {"tag_name": "v1.0.0", "name": "v1.0.0", "body": "- x"})])


class DotnetReleaseTests(GitRepo):
    def test_directory_build_props_is_bumped(self):
        self.commit("Add the solution", {"Directory.Build.props": "<Project>\r\n  <PropertyGroup>\r\n"
                                         "    <Version>1.0.0</Version>\r\n  </PropertyGroup>\r\n</Project>\r\n"})
        self.git("push", "--quiet", "origin", "main")
        self.git("config", "remote.origin.url", REMOTE)
        self.git("config", f"url.{self.origin}.insteadOf", REMOTE)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), contextlib.redirect_stderr(io.StringIO()):
            release.main(["--bump", "fix", "--yes", "--no-push", "--skip-tests"])
        self.assertIn(b"<Version>1.0.1</Version>\r\n", (self.root / "Directory.Build.props").read_bytes())


class TestCommandTests(GitRepo):
    def test_the_python_on_the_path_runs_the_tests(self):
        with mock.patch.object(release.shutil, "which", lambda name: "/opt/py/python" if name == "python" else None):
            self.assertEqual(release.test_command(self.root, {}, "python")[0][0], "/opt/py/python")
            self.assertEqual(release.test_command(self.root, {"test_command": ["python", "-m", "x"]}, "python")[0],
                             ["/opt/py/python", "-m", "x"])
        with mock.patch.object(release.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None):
            self.assertEqual(release.test_command(self.root, {}, "python")[0][0], "/usr/bin/python3")

    def test_python_defaults_and_overrides(self):
        which = mock.patch.object(release.shutil, "which", lambda name: None)
        which.start()
        self.addCleanup(which.stop)
        self.assertEqual(release.test_command(self.root, {}, "python"),
                         ([sys.executable, "-m", "unittest", "discover"], {}))
        (self.root / "pytest.ini").write_text("[pytest]\n")
        self.assertEqual(release.test_command(self.root, {}, "python")[0], [sys.executable, "-m", "pytest"])
        venv = self.root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        venv.parent.mkdir(parents=True)
        venv.write_text("")
        self.assertEqual(release.test_command(self.root, {}, "python")[0][0], str(venv))
        config = {"test_command": ["python", "-m", "unittest"], "test_env": {"PYTHONPATH": "src"}}
        self.assertEqual(release.test_command(self.root, config, "python"),
                         ([str(venv), "-m", "unittest"], {"PYTHONPATH": "src"}))

    def test_dotnet_default(self):
        self.assertEqual(release.test_command(self.root, {}, "dotnet"), (["dotnet", "test"], {}))

    def test_test_env_must_be_an_object(self):
        for value in (["PYTHONPATH=src"], "PYTHONPATH=src", 1):
            with self.subTest(value=value), self.assertRaisesRegex(ToolError, "test_env must be an object"):
                release.test_command(self.root, {"test_env": value}, "python")

    def test_a_broken_config_file_says_so(self):
        (self.root / ".clt.json").write_text("{not json")
        with self.assertRaisesRegex(ToolError, ".clt.json"):
            release.load_config(self.root)


if __name__ == "__main__":
    unittest.main()
