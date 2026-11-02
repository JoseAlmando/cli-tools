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

from cli_tools import core, gitea, github, gitrepo, release
from cli_tools.core import HttpError, ToolError
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
                         f"# Changelog\n\n## 0.2.0 - {today}\n\n- feat: add x\n- Fix y\n")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Release 0.2.0")
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

    def test_dry_run_prints_the_plan_and_changes_nothing_even_read_only(self):
        before = self.state()
        _, out, _ = self.release("--dry-run", "--bump", "feat", env={"CLT_READONLY": "1"})
        plan = json.loads(out)
        self.assertEqual({k: plan[k] for k in ("version_file", "old", "new", "tag", "host")},
                         {"version_file": "pyproject.toml", "old": "0.1.0", "new": "0.2.0", "tag": "v0.2.0",
                          "host": "github.com"})
        self.assertEqual(plan["changelog"], "- feat: add x\n- Fix y")
        self.assertEqual(self.state(), before)

    def test_read_only_blocks_a_real_release(self):
        with self.assertRaises(core.ReadOnlyError):
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
            with self.assertRaisesRegex(ToolError, "Not inside a git repository"):
                self.release("--dry-run", "--bump", "fix")

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
        self.assertEqual(self.publisher.created, [("o", "r", "v0.2.0", "- feat: add x\n- Fix y")])
        result = json.loads(out)
        self.assertEqual((result["pushed"], result["release_url"]),
                         (True, "https://github.com/o/r/releases/tag/v0.2.0"))

    def test_no_or_enter_at_the_question_keeps_it_local(self):
        for answer in ("n\n", "\n"):
            with self.subTest(answer=answer):
                _, _, err = self.release("--skip-tests", stdin="2\n" + answer, tty=True)
                self.assertFalse(gitrepo.remote_tag_exists(self.root, "v0.2.0"))
                self.assertIn("clt release --publish", err)
                self.assertEqual(self.publisher.created, [])
                self.git("tag", "-d", "v0.2.0")
                self.git("reset", "--quiet", "--hard", "origin/main")

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
        self.assertEqual(self.publisher.created[0][2:], ("v0.2.0", "- feat: add x\n- Fix y"))

    def test_publish_when_the_release_exists_creates_nothing(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        self.publisher.existing = "https://github.com/o/r/releases/tag/v0.2.0"
        _, _, err = self.release("--publish")
        self.assertEqual(self.publisher.created, [])
        self.assertIn("https://github.com/o/r/releases/tag/v0.2.0", err)

    def test_publish_is_blocked_in_read_only_mode(self):
        self.release("--bump", "feat", "--yes", "--no-push", "--skip-tests")
        with self.assertRaises(core.ReadOnlyError):
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
    def test_python_defaults_and_overrides(self):
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

    def test_a_broken_config_file_says_so(self):
        (self.root / ".clt.json").write_text("{not json")
        with self.assertRaisesRegex(ToolError, ".clt.json"):
            release.load_config(self.root)


if __name__ == "__main__":
    unittest.main()
