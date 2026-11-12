import argparse
import contextlib
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools.core import api, credentials, errors, http
from cli_tools.core.context import CLIContext
from cli_tools.core.errors import ToolError
from cli_tools.tools.jira import client as jira


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


class SafePathTests(unittest.TestCase):
    def test_encodes_once_and_refuses_dot_segments(self):
        self.assertEqual(http.safe_path("Demo Project/_apis/wit/workitems/$Task?q=a b&x=%20"),
                         "Demo%20Project/_apis/wit/workitems/$Task?q=a%20b&x=%20")
        for bad in ("a/../b", "./a", "a/%2e%2e/b"):
            with self.subTest(bad=bad), self.assertRaises(http.ToolError):
                http.safe_path(bad)

    def test_every_tool_refuses_dot_segments_through_api_url(self):
        for bad in ("../users/me", "repos/./o", "repos/%2e%2e/o", "a/..?x=1"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ToolError, ". or .. segments"):
                http.api_url("https://example.com", bad)
        self.assertEqual(http.api_url("https://example.com", "a/b?q=../x"), "https://example.com/api/v1/a/b?q=../x")


def api_args(path, **flags):
    defaults = {"method": "GET", "path": path, "data": None, "dry_run": False, "all": True, "max_items": None,
                "fields": None, "table": False, "raw": False, "headers": False, "verbose": False}
    return argparse.Namespace(**{**defaults, **flags})


class PagingTests(unittest.TestCase):
    """run_api's --all loop, with a fake call(method, path, body) that answers each path."""

    def fetch(self, first, pages, next_page=api.next_page_path, items_key=None, join=None):
        self.sent = []

        def call(method, path, body):
            self.sent.append(path)
            raw, headers = pages[path]
            return 200, raw, headers

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = api.run_api(api_args(first), call, CLIContext(), next_page, items_key, join)
        return code, json.loads(out.getvalue())

    def test_a_later_page_that_is_not_json_is_an_error(self):
        link = {"Link": '<https://h.example/api/v1/x?page=2>; rel="next"'}
        pages = {"x": ('[{"id": 1}]', link), "x?page=2": ("<html>Bad gateway</html>", {})}
        with self.assertRaisesRegex(ToolError, "page 2 of x is not JSON"):
            self.fetch("x", pages)

    def test_all_gives_the_same_shape_for_one_page_or_several(self):
        one = {"search": (json.dumps({"issues": [{"key": "A-1"}], "isLast": True}), {})}
        several = {"search": (json.dumps({"issues": [{"key": "A-1"}], "nextPageToken": "t"}), {}),
                   "search?nextPageToken=t": (json.dumps({"issues": [{"key": "A-2"}], "isLast": True}), {})}
        hooks = {"next_page": jira.next_page, "items_key": jira.items_key, "join": jira.join}
        self.assertEqual(self.fetch("search", one, **hooks), (0, {"issues": [{"key": "A-1"}]}))
        self.assertEqual(self.fetch("search", several, **hooks), (0, {"issues": [{"key": "A-1"}, {"key": "A-2"}]}))

    def test_a_token_cycle_or_an_empty_page_stops_the_paging(self):
        hooks = {"next_page": jira.next_page, "items_key": jira.items_key, "join": jira.join}
        def page(key, token):
            return json.dumps({"issues": [{"key": key}] if key else [], "nextPageToken": token}), {}

        cycle = {"s": page("A-1", "A"), "s?nextPageToken=A": page("A-2", "B"), "s?nextPageToken=B": page("A-3", "A")}
        self.assertEqual(self.fetch("s", cycle, **hooks)[1], {"issues": [{"key": "A-1"}, {"key": "A-2"}, {"key": "A-3"}]})
        self.assertEqual(self.sent, ["s", "s?nextPageToken=A", "s?nextPageToken=B"])
        empty = {"s": page("A-1", "A"), "s?nextPageToken=A": page(None, "B"), "s?nextPageToken=B": page(None, "C")}
        self.fetch("s", empty, **hooks)
        self.assertEqual(self.sent, ["s", "s?nextPageToken=A"])

    def test_set_query_splits_only_on_ampersands(self):
        # Python 3.9.0 and 3.9.1 also split on ";", as if this were the old parse_qsl
        def old_parse_qsl(query, keep_blank_values=False, **kwargs):
            return [tuple(pair.split("=", 1)) for part in query.split("&") for pair in part.split(";") if pair]

        with mock.patch.object(api.urllib.parse, "parse_qsl", old_parse_qsl):
            self.assertEqual(api.set_query("search?jql=a%3Bb;c&x=1", {"x": "2"}), "search?jql=a%3Bb%3Bc&x=2")


if __name__ == "__main__":
    unittest.main()
