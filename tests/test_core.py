import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools.core import api, credentials, errors, http
from cli_tools.core.errors import ToolError


class BaseUrlTests(unittest.TestCase):
    def test_accepts_https_and_strips_the_trailing_slash(self):
        self.assertEqual(http.validate_base_url("https://example.com/", "X_URL"), "https://example.com")

    def test_rejects_anything_but_a_plain_https_server(self):
        for url in ("http://example.com", "https://user:pw@example.com", "https://example.com/?a=1", "example.com"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                http.validate_base_url(url, "X_URL")


class ApiUrlTests(unittest.TestCase):
    def test_joins_relative_paths_under_api_v1(self):
        self.assertEqual(http.api_url("https://example.com", "/users/me"), "https://example.com/api/v1/users/me")

    def test_never_sends_the_token_to_another_server(self):
        for path in ("https://evil.example/x", "//evil.example/x", "..\\x"):
            with self.subTest(path=path), self.assertRaises(ToolError):
                http.api_url("https://example.com", path)

    def test_each_tool_can_choose_its_prefix(self):
        self.assertEqual(http.api_url("https://api.github.com", "user", prefix="/"), "https://api.github.com/user")
        with self.assertRaisesRegex(ToolError, "https://api.github.com/"):
            http.api_url("https://api.github.com", "https://evil.example/x", prefix="/")


class NextPageTests(unittest.TestCase):
    def test_with_an_api_root_keeps_only_same_host_pages(self):
        same = {"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}
        other = {"Link": '<https://evil.example/user/repos?page=2>; rel="next"'}
        self.assertEqual(api.next_page_path(same, api_root="https://api.github.com/"), "user/repos?page=2")
        self.assertIsNone(api.next_page_path(other, api_root="https://api.github.com/"))


class SetQueryTests(unittest.TestCase):
    def test_adds_replaces_and_removes(self):
        self.assertEqual(api.set_query("search/jql?jql=a%3D1&fields=key", {"nextPageToken": "t 1"}),
                         "search/jql?jql=a%3D1&fields=key&nextPageToken=t+1")
        self.assertEqual(api.set_query("x?startAt=0&a=b", {"startAt": 50}), "x?startAt=50&a=b")
        self.assertEqual(api.set_query("x?a=b&c=d", {"a": None}), "x?c=d")
        self.assertEqual(api.set_query("x", {"a": 1}), "x?a=1")


class JsonRequestTests(unittest.TestCase):
    def test_extra_headers_replace_the_defaults(self):
        with mock.patch.object(http, "send", return_value=(200, "{}", {})) as send:
            http.json_request("GET", "https://api.github.com/user", None, "t", None,
                              headers={"Accept": "application/vnd.github+json"})
        req = send.call_args[0][0]
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("Authorization"), "Bearer t")


class SendErrorTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(socket, "create_connection", side_effect=AssertionError("no network in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_token_http_cannot_carry_is_never_shown(self):
        with self.assertRaises(ToolError) as caught:
            http.json_request("GET", "https://taiga.example.com/api/v1/x", None, "s3cret-token\r", None)
        self.assertNotIn("s3cret-token", str(caught.exception))
        self.assertIn("taiga.example.com", str(caught.exception))

    def test_a_path_http_cannot_carry_is_a_clear_error(self):
        with self.assertRaisesRegex(ToolError, "a b"):
            http.json_request("GET", "https://taiga.example.com/api/v1/a b", None, "t", None)


class ResolveUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.creds = Path(self.tmp.name) / "credentials.json"

    def test_login_option_wins_over_the_environment_and_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(http.resolve_url("https://cli.example", "https://env.example", self.creds, "t", "T_URL"),
                         "https://cli.example")

    def test_environment_wins_over_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(http.resolve_url(None, "https://env.example", self.creds, "t", "T_URL"), "https://env.example")

    def test_falls_back_to_the_saved_session(self):
        self.creds.write_text(json.dumps({"url": "https://saved.example"}))
        self.assertEqual(http.resolve_url(None, "", self.creds, "t", "T_URL"), "https://saved.example")

    def test_without_any_server_explains_how_to_set_one(self):
        with self.assertRaisesRegex(ToolError, "login --url"):
            http.resolve_url(None, "", self.creds, "t", "T_URL")


class PrivateJsonTests(unittest.TestCase):
    def test_writes_atomically_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conf" / "credentials.json"
            credentials.write_private_json(path, {"a": 1})
            self.assertEqual(credentials.read_json_file(path), {"a": 1})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["credentials.json"])

    @unittest.skipIf(os.name == "nt", "on Windows the user profile's ACL protects it")
    def test_only_the_owner_can_read_the_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conf" / "credentials.json"
            credentials.write_private_json(path, {"a": 1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_a_missing_or_broken_file_reads_as_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json")
            self.assertIsNone(credentials.read_json_file(Path(tmp) / "missing.json"))
            self.assertIsNone(credentials.read_json_file(broken))


class BodyTests(unittest.TestCase):
    def test_accepts_inline_json_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.json"
            body.write_text('{"title": "x"}', encoding="utf-8")
            self.assertEqual(api.read_body('{"a": 1}'), '{"a": 1}')
            self.assertEqual(api.read_body(f"@{body}"), '{"title": "x"}')
            self.assertIsNone(api.read_body(None))

    def test_rejects_a_body_that_is_not_json(self):
        with self.assertRaisesRegex(ToolError, "not valid JSON"):
            api.read_body("{nope")


class ErrorDetailTests(unittest.TestCase):
    def test_uses_the_message_each_server_returns(self):
        self.assertEqual(errors.error_detail('{"_error_message": "Taiga says no"}'), "Taiga says no")
        self.assertEqual(errors.error_detail('{"message": "Gitea says no"}'), "Gitea says no")
        self.assertEqual(errors.error_detail('{"error": "invalid_grant", "error_description": "expired"}'), "expired")
        self.assertEqual(errors.error_detail("<html>  boom  </html>"), "<html> boom </html>")


class ShortcutTests(unittest.TestCase):
    def test_a_method_first_means_the_api_command(self):
        self.assertEqual(api.with_api_shortcut(["get", "users/me"]), ["api", "get", "users/me"])
        self.assertEqual(api.with_api_shortcut(["me"]), ["me"])
        self.assertEqual(api.with_api_shortcut([]), [])


if __name__ == "__main__":
    unittest.main()


class SafePathTests(unittest.TestCase):
    def test_encodes_once_and_refuses_dot_segments(self):
        from cli_tools.core import http
        self.assertEqual(http.safe_path("Demo Project/_apis/wit/workitems/$Task?q=a b&x=%20"),
                         "Demo%20Project/_apis/wit/workitems/$Task?q=a%20b&x=%20")
        for bad in ("a/../b", "./a", "a/%2e%2e/b"):
            with self.subTest(bad=bad), self.assertRaises(http.ToolError):
                http.safe_path(bad)
