import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools import core
from cli_tools.core import ToolError


class BaseUrlTests(unittest.TestCase):
    def test_accepts_https_and_strips_the_trailing_slash(self):
        self.assertEqual(core.validate_base_url("https://example.com/", "X_URL"), "https://example.com")

    def test_rejects_anything_but_a_plain_https_server(self):
        for url in ("http://example.com", "https://user:pw@example.com", "https://example.com/?a=1", "example.com"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                core.validate_base_url(url, "X_URL")


class ApiUrlTests(unittest.TestCase):
    def test_joins_relative_paths_under_api_v1(self):
        self.assertEqual(core.api_url("https://example.com", "/users/me"), "https://example.com/api/v1/users/me")

    def test_never_sends_the_token_to_another_server(self):
        for path in ("https://evil.example/x", "//evil.example/x", "..\\x"):
            with self.subTest(path=path), self.assertRaises(ToolError):
                core.api_url("https://example.com", path)

    def test_each_tool_can_choose_its_prefix(self):
        self.assertEqual(core.api_url("https://api.github.com", "user", prefix="/"), "https://api.github.com/user")
        with self.assertRaisesRegex(ToolError, "https://api.github.com/"):
            core.api_url("https://api.github.com", "https://evil.example/x", prefix="/")


class NextPageTests(unittest.TestCase):
    def test_with_an_api_root_keeps_only_same_host_pages(self):
        same = {"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}
        other = {"Link": '<https://evil.example/user/repos?page=2>; rel="next"'}
        self.assertEqual(core.next_page_path(same, api_root="https://api.github.com/"), "user/repos?page=2")
        self.assertIsNone(core.next_page_path(other, api_root="https://api.github.com/"))


class JsonRequestTests(unittest.TestCase):
    def test_extra_headers_replace_the_defaults(self):
        with mock.patch.object(core, "send", return_value=(200, "{}", {})) as send:
            core.json_request("GET", "https://api.github.com/user", None, "t", None,
                              headers={"Accept": "application/vnd.github+json"})
        req = send.call_args[0][0]
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")


class ResolveUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.creds = Path(self.tmp.name) / "credentials.json"

    def test_login_option_wins_over_the_environment_and_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(core.resolve_url("https://cli.example", "https://env.example", self.creds, "t", "T_URL"),
                         "https://cli.example")

    def test_environment_wins_over_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(core.resolve_url(None, "https://env.example", self.creds, "t", "T_URL"), "https://env.example")

    def test_falls_back_to_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(core.resolve_url(None, "", self.creds, "t", "T_URL"), "https://saved.example")

    def test_without_any_server_explains_how_to_set_one(self):
        with self.assertRaisesRegex(ToolError, "login --url"):
            core.resolve_url(None, "", self.creds, "t", "T_URL")


class PrivateJsonTests(unittest.TestCase):
    def test_writes_atomically_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conf" / "credentials.json"
            core.write_private_json(path, {"a": 1})
            self.assertEqual(core.read_json_file(path), {"a": 1})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["credentials.json"])

    @unittest.skipIf(os.name == "nt", "on Windows the user profile's ACL protects it")
    def test_only_the_owner_can_read_the_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conf" / "credentials.json"
            core.write_private_json(path, {"a": 1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_a_missing_or_broken_file_reads_as_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json")
            self.assertIsNone(core.read_json_file(Path(tmp) / "missing.json"))
            self.assertIsNone(core.read_json_file(broken))


class BodyTests(unittest.TestCase):
    def test_accepts_inline_json_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.json"
            body.write_text('{"title": "x"}', encoding="utf-8")
            self.assertEqual(core.read_body('{"a": 1}'), '{"a": 1}')
            self.assertEqual(core.read_body(f"@{body}"), '{"title": "x"}')
            self.assertIsNone(core.read_body(None))

    def test_rejects_a_body_that_is_not_json(self):
        with self.assertRaisesRegex(ToolError, "not valid JSON"):
            core.read_body("{nope")


class ErrorDetailTests(unittest.TestCase):
    def test_uses_the_message_each_server_returns(self):
        self.assertEqual(core.error_detail('{"_error_message": "Taiga says no"}'), "Taiga says no")
        self.assertEqual(core.error_detail('{"message": "Gitea says no"}'), "Gitea says no")
        self.assertEqual(core.error_detail('{"error": "invalid_grant", "error_description": "expired"}'), "expired")
        self.assertEqual(core.error_detail("<html>  boom  </html>"), "<html> boom </html>")


class ShortcutTests(unittest.TestCase):
    def test_a_method_first_means_the_api_command(self):
        self.assertEqual(core.with_api_shortcut(["get", "users/me"]), ["api", "get", "users/me"])
        self.assertEqual(core.with_api_shortcut(["me"]), ["me"])
        self.assertEqual(core.with_api_shortcut([]), [])


if __name__ == "__main__":
    unittest.main()
