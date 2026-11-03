"""What makes the tools safe to drive from scripts and AI agents."""
import argparse
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools.core import api, context, errors, http, runner
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.gitea import auth as gitea_auth
from cli_tools.tools.gitea import cli as gitea
from cli_tools.tools.taiga import cli as taiga
from tests.helpers import TempConf


def api_args(method="GET", path="items", **overrides):
    values = dict(method=method, path=path, data=None, raw=False, headers=False, verbose=False,
                  fields=None, all=False, max_items=None, dry_run=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def run_api(args, call):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = api.run_api(args, call)
    return code, out.getvalue(), err.getvalue()


def no_call(method, path, body):
    raise AssertionError("no request should have been sent")


class GlobalFlagsTests(TempConf):
    def test_are_taken_from_any_position(self):
        ctx, rest = context.parse_global_flags(["GET", "--json", "items", "--read-only", "-r"])
        self.assertEqual(rest, ["GET", "items", "-r"])
        self.assertTrue(ctx.json_mode)
        self.assertTrue(ctx.read_only)

    def test_can_also_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CLT_JSON": "1", "CLT_READONLY": "true"}):
            self.assertTrue(context.CLIContext.from_env().json_mode)
            self.assertTrue(context.parse_global_flags([])[0].read_only)
        self.assertFalse(context.CLIContext.from_env().json_mode)
        self.assertFalse(context.parse_global_flags([])[0].read_only)


    def test_the_environment_is_read_but_never_written(self):
        ctx, rest = context.parse_global_flags(["me", "--json"], environ={"CLT_READONLY": "yes"})
        self.assertEqual((ctx, rest), (context.CLIContext(json_mode=True, read_only=True), ["me"]))
        self.assertEqual({k for k in os.environ if k.startswith("CLT_")}, set())

    def test_the_context_passed_to_a_tool_is_the_one_used(self):
        with self.assertRaises(errors.ReadOnlyError), contextlib.redirect_stderr(io.StringIO()):
            taiga.main(["logout"], ctx=context.CLIContext(read_only=True))
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(taiga.main(["logout"], ctx=context.CLIContext()))


class ExitCodeTests(TempConf):
    def run_main(self, error, *flags):
        def main(argv, ctx):
            raise error

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = runner.run(main, list(flags))
        return code, err.getvalue()

    def test_each_kind_of_error_has_its_own_code(self):
        self.assertEqual(self.run_main(ToolError("boom"))[0], errors.EXIT_ERROR)
        self.assertEqual(self.run_main(errors.NotLoggedIn("no session"))[0], errors.EXIT_NOT_LOGGED_IN)
        self.assertEqual(self.run_main(errors.ReadOnlyError("blocked"))[0], errors.EXIT_READ_ONLY)

    def test_with_json_the_error_is_one_json_line(self):
        code, err = self.run_main(errors.NotLoggedIn("Not logged in"), "--json")
        self.assertEqual(code, errors.EXIT_NOT_LOGGED_IN)
        line = json.loads(err.strip())
        self.assertEqual(line["type"], "NotLoggedIn")
        self.assertEqual(line["exit_code"], errors.EXIT_NOT_LOGGED_IN)
        self.assertEqual(line["error"], "Not logged in")

    def test_with_json_an_http_error_keeps_its_status_and_body(self):
        def call(method, path, body):
            raise HttpError(404, '{"message": "Not found"}')

        with mock.patch.dict(os.environ, {"CLT_JSON": "1"}):
            code, out, err = run_api(api_args(), call)
        self.assertEqual(code, errors.EXIT_ERROR)
        self.assertEqual(out, "")
        line = json.loads(err.strip())
        self.assertEqual((line["status"], line["error"], line["body"]), (404, "Not found", {"message": "Not found"}))


class ReadOnlyAndDryRunTests(TempConf):
    def test_read_only_blocks_every_method_but_get(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method), self.assertRaises(errors.ReadOnlyError):
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
                        mock.patch.object(http, "send", side_effect=AssertionError("no request should be sent")), \
                        mock.patch.object(gitea_auth, "wait_for_callback", side_effect=AssertionError("no browser login")):
                    with self.assertRaisesRegex(errors.ReadOnlyError, "read-only"):
                        tool.main(command)
                    # Nothing reaches stdout, so no token can leak
                    self.assertEqual(out.getvalue(), "")

    def test_read_only_logout_keeps_the_saved_session(self):
        t = taiga.Taiga("https://taiga.example.com")
        t.save_credentials({"auth_token": "t", "refresh": "r", "username": "bot"})
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), self.assertRaises(errors.ReadOnlyError):
            taiga.main(["logout"])
        self.assertTrue(t.credentials_file.exists())

    def test_read_only_still_allows_me(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1", "TAIGA_TOKEN": "t", "TAIGA_URL": "https://taiga.example.com"}), \
                mock.patch.object(taiga.Taiga, "call", lambda self, m, p, b: (200, '{"username": "bot"}', {})), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(taiga.main(["me"]) or 0, 0)
        self.assertIn("bot", out.getvalue())

    def test_require_writes_blocks_only_in_read_only_mode(self):
        self.assertIsNone(context.CLIContext.from_env().require_writes("protect"))
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), self.assertRaisesRegex(errors.ReadOnlyError, "protect"):
            context.CLIContext.from_env().require_writes("protect")

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


class MeJsonTests(TempConf):
    def run_me(self, tool, body, env):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                mock.patch.object(tool.Taiga if tool is taiga else tool.Gitea, "call",
                                  lambda self, m, p, b: (200, json.dumps(body), {})):
            tool.main(["me"])
        return out.getvalue()

    def test_taiga_me_prints_one_json_object(self):
        t = taiga.Taiga("https://taiga.example.com")
        t.save_credentials({"auth_token": "secret-token", "refresh": "r"})
        out = self.run_me(taiga, {"id": 7, "username": "bot", "full_name_display": "Bot User", "email": "bot@example.com"},
                          {"CLT_JSON": "1"})
        self.assertEqual(json.loads(out), {"id": 7, "username": "bot", "full_name": "Bot User", "email": "bot@example.com",
                                           "url": "https://taiga.example.com", "credentials_file": str(t.credentials_file)})
        self.assertNotIn("secret-token", out)

    def test_gitea_me_prints_one_json_object_with_the_expiry(self):
        g = gitea.Gitea("https://gitea.example.com")
        g.save_credentials({"access_token": "secret-token", "refresh_token": "r", "expires_in": 3600})
        out = self.run_me(gitea, {"id": 3, "login": "bot", "full_name": "Bot User", "email": "bot@example.com"},
                          {"CLT_JSON": "1"})
        me = json.loads(out)
        self.assertEqual({k: v for k, v in me.items() if k != "expires_at"},
                         {"id": 3, "username": "bot", "full_name": "Bot User", "email": "bot@example.com",
                          "url": "https://gitea.example.com", "credentials_file": str(g.credentials_file)})
        self.assertIsInstance(me["expires_at"], int)
        self.assertNotIn("secret-token", out)

    def test_a_session_from_the_environment_has_no_credentials_file(self):
        out = self.run_me(taiga, {"id": 7, "username": "bot"},
                          {"CLT_JSON": "1", "TAIGA_TOKEN": "t", "TAIGA_URL": "https://taiga.example.com"})
        self.assertIsNone(json.loads(out)["credentials_file"])


class NonInteractiveTests(TempConf):
    def test_taiga_uses_a_token_from_the_environment(self):
        with mock.patch.dict(os.environ, {"TAIGA_TOKEN": "env-token", "TAIGA_URL": "https://taiga.example.com"}):
            self.assertEqual(taiga.Taiga().load_credentials()["auth_token"], "env-token")

    def test_gitea_uses_a_token_from_the_environment(self):
        with mock.patch.dict(os.environ, {"GITEA_TOKEN": "pat", "GITEA_URL": "https://gitea.example.com"}):
            self.assertEqual(gitea.Gitea().fresh_credentials()["access_token"], "pat")

    def test_without_a_session_the_error_says_how_to_get_one(self):
        with mock.patch.dict(os.environ, {"TAIGA_URL": "https://taiga.example.com"}):
            with self.assertRaisesRegex(errors.NotLoggedIn, "TAIGA_TOKEN"):
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
