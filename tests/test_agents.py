"""What makes the tools safe to drive from scripts and AI agents."""
import argparse
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools import core, gitea, taiga
from cli_tools.core import HttpError, ToolError
from tests.helpers import TempConf


def api_args(method="GET", path="items", **overrides):
    values = dict(method=method, path=path, data=None, raw=False, headers=False, verbose=False,
                  fields=None, all=False, max_items=None, dry_run=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def run_api(args, call):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = core.run_api(args, call)
    return code, out.getvalue(), err.getvalue()


def no_call(method, path, body):
    raise AssertionError("no request should have been sent")


class GlobalFlagsTests(TempConf):
    def test_are_taken_from_any_position(self):
        rest = core.take_global_flags(["GET", "--json", "items", "--read-only", "-r"])
        self.assertEqual(rest, ["GET", "items", "-r"])
        self.assertTrue(core.json_errors())
        self.assertTrue(core.read_only())

    def test_can_also_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CLT_JSON": "1", "CLT_READONLY": "true"}):
            self.assertTrue(core.json_errors())
            self.assertTrue(core.read_only())
        self.assertFalse(core.json_errors())
        self.assertFalse(core.read_only())


class ExitCodeTests(TempConf):
    def run_main(self, error, *flags):
        def main(argv):
            raise error

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = core.run(main, list(flags))
        return code, err.getvalue()

    def test_each_kind_of_error_has_its_own_code(self):
        self.assertEqual(self.run_main(ToolError("boom"))[0], core.EXIT_ERROR)
        self.assertEqual(self.run_main(core.NotLoggedIn("no session"))[0], core.EXIT_NOT_LOGGED_IN)
        self.assertEqual(self.run_main(core.ReadOnlyError("blocked"))[0], core.EXIT_READ_ONLY)

    def test_with_json_the_error_is_one_json_line(self):
        code, err = self.run_main(core.NotLoggedIn("Not logged in"), "--json")
        self.assertEqual(code, core.EXIT_NOT_LOGGED_IN)
        line = json.loads(err.strip())
        self.assertEqual(line["type"], "NotLoggedIn")
        self.assertEqual(line["exit_code"], core.EXIT_NOT_LOGGED_IN)
        self.assertEqual(line["error"], "Not logged in")

    def test_with_json_an_http_error_keeps_its_status_and_body(self):
        def call(method, path, body):
            raise HttpError(404, '{"message": "Not found"}')

        with mock.patch.dict(os.environ, {"CLT_JSON": "1"}):
            code, out, err = run_api(api_args(), call)
        self.assertEqual(code, core.EXIT_ERROR)
        self.assertEqual(out, "")
        line = json.loads(err.strip())
        self.assertEqual((line["status"], line["error"], line["body"]), (404, "Not found", {"message": "Not found"}))


class ReadOnlyAndDryRunTests(TempConf):
    def test_read_only_blocks_every_method_but_get(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method), self.assertRaises(core.ReadOnlyError):
                    run_api(api_args(method, data='{"a": 1}'), no_call)
            code, out, _ = run_api(api_args("GET"), lambda m, p, b: (200, '{"ok": true}', {}))
        self.assertEqual((code, json.loads(out)), (0, {"ok": True}))

    def test_read_only_blocks_the_session_commands_of_every_tool(self):
        env = {"CLT_READONLY": "1", "TAIGA_TOKEN": "secret-taiga", "TAIGA_URL": "https://taiga.example.com",
               "GITEA_TOKEN": "secret-gitea", "GITEA_URL": "https://gitea.example.com"}
        for tool in (taiga, gitea):
            for command in (["login", "--password-stdin", "--user", "bot"] if tool is taiga else ["login", "--no-browser"],
                            ["refresh"], ["logout"], ["token"]):
                out = io.StringIO()
                with self.subTest(tool=tool.PROG, command=command[0]), mock.patch.dict(os.environ, env), \
                        mock.patch("sys.stdin", io.StringIO("s3cret\n")), contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        mock.patch.object(core, "send", side_effect=AssertionError("no request should be sent")), \
                        mock.patch.object(gitea, "wait_for_callback", side_effect=AssertionError("no browser login")):
                    with self.assertRaisesRegex(core.ReadOnlyError, "read-only"):
                        tool.main(command)
                    # Nothing reaches stdout, so no token can leak
                    self.assertEqual(out.getvalue(), "")

    def test_read_only_logout_keeps_the_saved_session(self):
        t = taiga.Taiga("https://taiga.example.com")
        t.save_credentials({"auth_token": "t", "refresh": "r", "username": "bot"})
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), self.assertRaises(core.ReadOnlyError):
            taiga.main(["logout"])
        self.assertTrue(t.credentials_file.exists())

    def test_read_only_still_allows_me(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1", "TAIGA_TOKEN": "t", "TAIGA_URL": "https://taiga.example.com"}), \
                mock.patch.object(taiga.Taiga, "call", lambda self, m, p, b: (200, '{"username": "bot"}', {})), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(taiga.main(["me"]) or 0, 0)
        self.assertIn("bot", out.getvalue())

    def test_dry_run_prints_the_request_and_sends_nothing(self):
        code, out, _ = run_api(api_args("PATCH", "issues/1", data='{"status": 2}', dry_run=True), no_call)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"method": "PATCH", "path": "issues/1", "body": {"status": 2}})


class OutputSizeTests(TempConf):
    ISSUES = [{"ref": 1, "subject": "One", "status_extra_info": {"name": "New"}, "description": "x" * 500},
              {"ref": 2, "subject": "Two", "status_extra_info": {"name": "Done"}, "description": "y" * 500}]

    def test_fields_keep_only_what_was_asked_including_nested_ones(self):
        code, out, _ = run_api(api_args(fields="ref,status_extra_info.name"),
                               lambda m, p, b: (200, json.dumps(self.ISSUES), {}))
        self.assertEqual(json.loads(out), [{"ref": 1, "status_extra_info.name": "New"},
                                           {"ref": 2, "status_extra_info.name": "Done"}])

    def test_max_items_truncates_and_says_so(self):
        code, out, err = run_api(api_args(max_items=1), lambda m, p, b: (200, json.dumps(self.ISSUES), {}))
        self.assertEqual(len(json.loads(out)), 1)
        self.assertIn("1 of 2", err)

    def test_all_follows_gitea_and_taiga_pagination_on_the_same_server(self):
        pages = {
            "items": ('[{"n": 1}]', {"Link": '<https://evil.example/api/v1/items?page=2>; rel="next"'}),
            "items?page=2": ('[{"n": 2}]', {"x-pagination-next": "https://srv/api/v1/items?page=3"}),
            "items?page=3": ('[{"n": 3}]', {}),
        }
        seen = []

        def call(method, path, body):
            seen.append(path)
            raw, headers = pages[path]
            return 200, raw, headers

        code, out, _ = run_api(api_args(all=True), call)
        self.assertEqual(json.loads(out), [{"n": 1}, {"n": 2}, {"n": 3}])
        # Only the path of the next page is used, so a foreign host in the header is never contacted
        self.assertEqual(seen, ["items", "items?page=2", "items?page=3"])


class NonInteractiveTests(TempConf):
    def test_taiga_uses_a_token_from_the_environment(self):
        with mock.patch.dict(os.environ, {"TAIGA_TOKEN": "env-token", "TAIGA_URL": "https://taiga.example.com"}):
            self.assertEqual(taiga.Taiga().load_credentials()["auth_token"], "env-token")

    def test_gitea_uses_a_token_from_the_environment(self):
        with mock.patch.dict(os.environ, {"GITEA_TOKEN": "pat", "GITEA_URL": "https://gitea.example.com"}):
            self.assertEqual(gitea.Gitea().fresh_credentials()["access_token"], "pat")

    def test_without_a_session_the_error_says_how_to_get_one(self):
        with mock.patch.dict(os.environ, {"TAIGA_URL": "https://taiga.example.com"}):
            with self.assertRaisesRegex(core.NotLoggedIn, "TAIGA_TOKEN"):
                taiga.Taiga().load_credentials()

    def test_logins_fail_at_once_without_a_terminal(self):
        with mock.patch("sys.stdin", io.StringIO("")):
            with self.assertRaisesRegex(ToolError, "needs a person"):
                taiga.main(["login", "--url", "https://taiga.example.com"])
            with self.assertRaisesRegex(ToolError, "needs a person"):
                gitea.main(["login", "--url", "https://gitea.example.com"])

    def test_taiga_login_can_read_the_password_from_stdin(self):
        sent = []

        def fake(self, method, path, body=None, token=None):
            sent.append(json.loads(body))
            return 200, json.dumps({"auth_token": "t", "refresh": "r", "username": "bot"}), {}

        with mock.patch("sys.stdin", io.StringIO("s3cret\n")), mock.patch.object(taiga.Taiga, "request", fake), \
                contextlib.redirect_stderr(io.StringIO()):
            taiga.main(["login", "--url", "https://taiga.example.com", "--user", "bot", "--password-stdin"])
        self.assertEqual((sent[0]["username"], sent[0]["password"]), ("bot", "s3cret"))
        self.assertEqual(taiga.Taiga().load_credentials()["auth_token"], "t")


if __name__ == "__main__":
    unittest.main()
