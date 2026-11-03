import os
import tempfile
import unittest
from unittest import mock

from cli_tools.commands.release import gitrepo
from cli_tools.core.errors import ToolError
from tests.helpers import GitRepo


class ParseRemoteTests(unittest.TestCase):
    def test_accepts_https_ssh_and_scp_like_urls(self):
        for url in ("https://github.com/o/r.git", "https://github.com/o/r", "ssh://git@github.com/o/r.git",
                    "ssh://git@github.com:2222/o/r", "git@github.com:o/r.git"):
            with self.subTest(url=url):
                self.assertEqual(gitrepo.parse_remote(url), ("github.com", "o", "r"))

    def test_an_unparseable_url_is_shown_without_credentials(self):
        with self.assertRaises(ToolError) as caught:
            gitrepo.parse_remote("http://user:s3cret-token@host.example/x")
        self.assertNotIn("s3cret-token", str(caught.exception))
        self.assertIn("host.example", str(caught.exception))

    def test_rejects_anything_else(self):
        for url in ("/srv/git/r.git", "https://github.com/only-owner", "file:///tmp/x"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                gitrepo.parse_remote(url)


class GitHelpersTests(GitRepo):
    def test_default_branch_comes_from_origin(self):
        self.assertEqual(gitrepo.default_branch(self.root), "main")

    def test_last_tag_and_subjects_since(self):
        self.assertIsNone(gitrepo.last_tag(self.root))
        self.git("tag", "-a", "v0.1.0", "-m", "v0.1.0")
        self.commit("feat: add x")
        self.commit("Release 0.1.1")
        self.commit("Fix y\n\nBREAKING CHANGE: z")
        self.assertEqual(gitrepo.last_tag(self.root), "v0.1.0")
        self.assertEqual(gitrepo.subjects_since(self.root, "v0.1.0"), ["feat: add x", "Fix y"])
        self.assertEqual(gitrepo.messages_since(self.root, "v0.1.0"), ["feat: add x", "Fix y\n\nBREAKING CHANGE: z"])
        self.assertEqual(gitrepo.subjects_since(self.root, None), ["Initial commit", "feat: add x", "Fix y"])

    def test_remote_tag_exists(self):
        self.git("tag", "v1.0.0")
        self.assertFalse(gitrepo.remote_tag_exists(self.root, "v1.0.0"))
        self.git("push", "--quiet", "origin", "v1.0.0")
        self.assertTrue(gitrepo.remote_tag_exists(self.root, "v1.0.0"))

    def test_git_errors_carry_git_s_message(self):
        with self.assertRaisesRegex(ToolError, "nope"):
            gitrepo.git(self.root, "checkout", "nope")

    def test_missing_git_says_so(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            with self.assertRaises(ToolError) as caught:
                gitrepo.git(self.root, "status")
        self.assertEqual(str(caught.exception), "git is not installed or not on the PATH")


if __name__ == "__main__":
    unittest.main()
