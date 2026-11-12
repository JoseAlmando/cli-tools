import contextlib
import io
import json
import os
import stat
import time
import unittest
import urllib.parse
from unittest import mock

from cli_tools.core import credentials, errors, http, runner
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.github import auth
from cli_tools.tools.github import cli as github
from cli_tools.tools.github.client import HEADERS
from tests.helpers import TempConf


class FakeGitHub:
    """Answers http.send by URL and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, cacert):
        self.requests.append(req)
        answer = self.routes[req.full_url]
        if isinstance(answer, list):  # one answer per call, in order
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(argv, routes, stdin=""):
    fake = FakeGitHub(routes)
    out = io.StringIO()
    with mock.patch.object(http, "send", fake), mock.patch("sys.stdin", io.StringIO(stdin)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = github.main(argv)
    return code, out.getvalue(), fake.requests


USER = "https://api.github.com/user"


class GitHubLoginTests(TempConf):
    def test_login_checks_the_token_and_saves_it(self):
        _, _, sent = run(["login", "--token-stdin"], {USER: (200, '{"login": "bot"}', {})}, stdin="tok\n")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer tok")
        for name, value in HEADERS.items():
            self.assertEqual(sent[0].get_header(name.capitalize()), value)
        self.assertEqual(credentials.read_json_file(github.GitHub().credentials_file), {"token": "tok"})

    def test_a_rejected_token_is_not_saved(self):
        with self.assertRaisesRegex(ToolError, "rejected"):
            run(["login", "--token-stdin"], {USER: HttpError(401, '{"message": "Bad credentials"}')}, stdin="bad\n")
        self.assertFalse(github.GitHub().credentials_file.exists())

    def test_login_without_a_terminal_needs_token_stdin(self):
        with self.assertRaisesRegex(ToolError, "needs a person"):
            run(["login"], {})

    def test_tokens_from_the_environment_win_over_the_file(self):
        credentials.write_private_json(github.GitHub().credentials_file, {"token": "saved"})
        with mock.patch.dict(os.environ, {"GH_TOKEN": "gh"}):
            self.assertEqual(github.GitHub().load_credentials()["token"], "gh")
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "github"}):
                self.assertEqual(github.GitHub().load_credentials()["token"], "github")
        self.assertEqual(github.GitHub().load_credentials()["token"], "saved")

    def test_without_a_token_it_says_how_to_get_one(self):
        with self.assertRaisesRegex(errors.NotLoggedIn, "GITHUB_TOKEN"):
            github.GitHub().load_credentials()


class TTY(io.StringIO):
    def isatty(self):
        return True


DEVICE = "https://github.com/login/device/code"
TOKEN = "https://github.com/login/oauth/access_token"
CODES = (200, json.dumps({"device_code": "dev-code", "user_code": "ABCD-1234", "verification_uri":
                          "https://github.com/login/device", "expires_in": 900, "interval": 5}), {})


class BrowserLoginTests(TempConf):
    """Device flow (authorize in the browser) and creating a token in the browser."""

    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        self.sleeps = []
        mock.patch.object(auth.time, "sleep", self.sleeps.append).start()
        self.opened = []
        mock.patch.object(auth.webbrowser, "open", self.opened.append).start()

    def login(self, argv, routes, stdin="", tty=True, env=None):
        fake = FakeGitHub(routes)
        err = io.StringIO()
        with mock.patch.object(http, "send", fake), mock.patch("sys.stdin", (TTY if tty else io.StringIO)(stdin)), \
                mock.patch.dict(os.environ, {"GITHUB_CLIENT_ID": "client-123"} if env is None else env), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            github.main(argv)
        return err.getvalue(), fake.requests

    def saved_token(self):
        return credentials.read_json_file(github.GitHub().credentials_file)

    def test_authorizing_in_the_browser_saves_the_token(self):
        polls = [(200, '{"error": "authorization_pending"}', {}), (200, '{"error": "slow_down", "interval": 10}', {}),
                 (200, '{"access_token": "oauth-token", "token_type": "bearer", "scope": "repo"}', {})]
        err, sent = self.login(["login", "--browser"], {DEVICE: CODES, TOKEN: polls, USER: (200, '{"login": "bot"}', {})})
        # Without expiring tokens (an OAuth app setting) there is nothing to renew
        self.assertEqual(self.saved_token(), {"token": "oauth-token", "refresh_token": None, "expires_at": None,
                                              "refresh_expires_at": None, "client_id": "client-123"})
        self.assertIn("ABCD-1234", err)
        self.assertIn("Only enter this code if it appeared in your own terminal just now", err)
        self.assertNotIn("oauth-token", err)
        self.assertEqual(self.opened, ["https://github.com/login/device"])
        self.assertEqual(self.sleeps, [5, 5, 10])
        first = dict(urllib.parse.parse_qsl(sent[0].data.decode()))
        self.assertEqual(first, {"client_id": "client-123", "scope": "repo project"})
        poll = dict(urllib.parse.parse_qsl(sent[1].data.decode()))
        self.assertEqual(poll, {"client_id": "client-123", "device_code": "dev-code",
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        self.assertTrue(all(r.get_header("Authorization") is None for r in sent[:4]))
        self.assertEqual(sent[4].get_header("Authorization"), "Bearer oauth-token")

    def test_expiring_tokens_are_saved_with_their_refresh_token_and_expiry(self):
        answer = {"access_token": "ghu_access", "token_type": "bearer", "scope": "project,repo", "expires_in": 28800,
                  "refresh_token": "ghr_refresh", "refresh_token_expires_in": 15897600}
        before = int(time.time())
        err, _ = self.login(["login", "--browser"], {DEVICE: CODES, TOKEN: (200, json.dumps(answer), {}),
                                                     USER: (200, '{"login": "bot"}', {})})
        saved = self.saved_token()
        self.assertEqual(set(saved), {"token", "refresh_token", "expires_at", "refresh_expires_at", "client_id"})
        self.assertEqual((saved["token"], saved["refresh_token"], saved["client_id"]),
                         ("ghu_access", "ghr_refresh", "client-123"))
        self.assertIsInstance(saved["expires_at"], int)
        self.assertTrue(before + 28800 <= saved["expires_at"] <= int(time.time()) + 28800)
        self.assertTrue(before + 15897600 <= saved["refresh_expires_at"] <= int(time.time()) + 15897600)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(github.GitHub().credentials_file.stat().st_mode), 0o600)
        self.assertNotIn("ghu_access", err)
        self.assertNotIn("ghr_refresh", err)

    def test_a_cancelled_or_expired_authorization_saves_nothing(self):
        for error, message in (("access_denied", "cancelled"), ("expired_token", "expired")):
            with self.subTest(error=error), self.assertRaisesRegex(ToolError, message):
                self.login(["login", "--browser"], {DEVICE: CODES, TOKEN: (200, json.dumps({"error": error}), {})})
            self.assertFalse(github.GitHub().credentials_file.exists())

    def test_the_built_in_oauth_app_is_used_unless_another_is_set(self):
        self.assertEqual(auth.client_id(), "Ov23liksTJsgDjqbhwaS")
        with mock.patch.dict(os.environ, {"GITHUB_CLIENT_ID": "other"}):
            self.assertEqual(auth.client_id(), "other")

    def test_an_empty_client_id_variable_uses_the_built_in_app(self):
        # So there is always an OAuth app, and the browser login is always offered first
        with mock.patch.dict(os.environ, {"GITHUB_CLIENT_ID": ""}):
            self.assertEqual(auth.client_id(), auth.DEFAULT_CLIENT_ID)

    def test_an_unknown_oauth_app_is_named(self):
        with self.assertRaisesRegex(ToolError, "client-123.*GITHUB_CLIENT_ID"):
            self.login(["login", "--browser"], {DEVICE: HttpError(404, "Not Found")})

    def test_no_browser_only_prints_the_link(self):
        err, _ = self.login(["login", "--browser", "--no-browser"],
                            {DEVICE: CODES, TOKEN: (200, '{"access_token": "t"}', {}), USER: (200, '{"login": "b"}', {})},
                            tty=False)
        self.assertEqual(self.opened, [])
        self.assertIn("https://github.com/login/device", err)

    def test_creating_a_token_opens_the_prefilled_page_and_reads_the_paste(self):
        with mock.patch.object(github.getpass, "getpass", lambda prompt: " pasted-token \n"):
            self.login(["login", "--create-token"], {USER: (200, '{"login": "bot"}', {})})
        self.assertEqual(self.saved_token(), {"token": "pasted-token"})
        url = urllib.parse.urlsplit(self.opened[0])
        self.assertEqual(url.scheme + "://" + url.netloc + url.path, "https://github.com/settings/personal-access-tokens/new")
        query = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual({k: query[k] for k in ("name", "contents", "administration")},
                         {"name": "clt", "contents": "write", "administration": "write"})

    def test_the_menu_defaults_to_the_browser_and_offers_the_others(self):
        routes = {DEVICE: CODES, TOKEN: (200, '{"access_token": "oauth-token"}', {}), USER: (200, '{"login": "b"}', {})}
        self.login(["login"], routes, stdin="\n")
        self.assertEqual(self.saved_token()["token"], "oauth-token")
        with mock.patch.object(github.getpass, "getpass", lambda prompt: "pasted"):
            self.login(["login"], {USER: (200, '{"login": "b"}', {})}, stdin="2\n")
        self.assertEqual(self.saved_token(), {"token": "pasted"})

    def test_browser_and_create_token_need_a_person(self):
        for flag in ("--browser", "--create-token"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ToolError, "needs a person"):
                self.login(["login", flag], {}, tty=False)


class GitHubApiTests(TempConf):
    def setUp(self):
        super().setUp()
        credentials.write_private_json(github.GitHub().credentials_file, {"token": "secret-token"})

    def test_all_follows_only_same_host_pages(self):
        pulls = "https://api.github.com/repos/o/r/pulls"
        routes = {
            pulls: (200, '[{"n": 1}]', {"Link": f'<{pulls}?page=2>; rel="next"'}),
            pulls + "?page=2": (200, '[{"n": 2}]', {"Link": '<https://evil.example/repos/o/r/pulls?page=3>; rel="next"'}),
        }
        code, out, sent = run(["GET", "repos/o/r/pulls", "--all"], routes)
        self.assertEqual((code, json.loads(out)), (0, [{"n": 1}, {"n": 2}]))
        self.assertEqual([r.full_url for r in sent], [pulls, pulls + "?page=2"])

    def test_me_as_json_never_shows_the_token(self):
        body = '{"id": 5, "login": "bot", "name": "Bot User", "email": "bot@example.com"}'
        with mock.patch.dict(os.environ, {"CLT_JSON": "1"}):
            _, out, _ = run(["me"], {USER: (200, body, {})})
        self.assertEqual(json.loads(out), {"id": 5, "username": "bot", "full_name": "Bot User", "email": "bot@example.com",
                                           "url": "https://github.com",
                                           "credentials_file": str(github.GitHub().credentials_file),
                                           "token_source": "credentials_file"})
        self.assertNotIn("secret-token", out)

    def test_read_only_blocks_session_commands_and_writes(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}):
            for argv in (["login", "--token-stdin"], ["refresh"], ["logout"], ["token"],
                         ["POST", "user/repos", "-d", "{}"]):
                with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                    run(argv, {}, stdin="tok\n")
            code, _, _ = run(["GET", "user"], {USER: (200, '{"login": "bot"}', {})})
        self.assertEqual(code, 0)
        self.assertTrue(github.GitHub().credentials_file.exists())


def run_cli(argv, routes):
    """Through runner.run, as the console script runs it: (exit code, stdout, stderr, requests)."""
    fake = FakeGitHub(routes)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(http, "send", fake), mock.patch("sys.stdin", io.StringIO("")), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = runner.run(github.main, argv)
    return code, out.getvalue(), err.getvalue(), fake.requests


def form(req):
    return dict(urllib.parse.parse_qsl(req.data.decode()))


RENEWED = (200, json.dumps({"access_token": "ghu_new", "token_type": "bearer", "expires_in": 28800,
                            "refresh_token": "ghr_new", "refresh_token_expires_in": 15897600}), {})
REFUSED = (200, json.dumps({"error": "bad_refresh_token",
                            "error_description": "The refresh token passed is incorrect or expired."}), {})
ME = (200, '{"login": "bot"}', {})
BAD_CREDENTIALS = '{"message": "Bad credentials"}'
SECRETS = ("ghu_old", "ghr_old", "ghu_new", "ghr_new")


class SessionRenewalTests(TempConf):
    """Expiring user tokens from the device flow renew themselves; other tokens are used as they are."""

    def save_session(self, expires_in=28800, refresh_expires_in=15897600, client_id="session-app"):
        now = int(time.time())
        credentials.write_private_json(github.GitHub().credentials_file, {
            "token": "ghu_old", "refresh_token": "ghr_old", "expires_at": now + expires_in,
            "refresh_expires_at": now + refresh_expires_in, "client_id": client_id})

    def saved(self):
        return credentials.read_json_file(github.GitHub().credentials_file)

    def assert_no_secrets(self, *texts):
        for text in texts:
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def test_a_token_about_to_expire_is_renewed_before_the_call(self):
        self.save_session(expires_in=30)
        code, out, err, sent = run_cli(["GET", "user"], {TOKEN: RENEWED, USER: ME})
        self.assertEqual(code, 0)
        self.assertEqual([r.full_url for r in sent], [TOKEN, USER])
        self.assertEqual(form(sent[0]), {"client_id": "session-app", "grant_type": "refresh_token",
                                         "refresh_token": "ghr_old"})
        self.assertEqual(sent[0].get_header("Accept"), "application/json")
        self.assertEqual(sent[0].get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertIsNone(sent[0].get_header("Authorization"))
        self.assertEqual(sent[1].get_header("Authorization"), "Bearer ghu_new")
        saved = self.saved()
        self.assertEqual((saved["token"], saved["refresh_token"], saved["client_id"]),
                         ("ghu_new", "ghr_new", "session-app"))
        self.assertGreater(saved["expires_at"], time.time() + 28000)
        self.assertGreater(saved["refresh_expires_at"], time.time() + 15000000)
        self.assert_no_secrets(err)

    def test_a_token_far_from_expiry_is_not_renewed(self):
        self.save_session()
        code, _, _, sent = run_cli(["GET", "user"], {USER: ME})
        self.assertEqual((code, [r.full_url for r in sent]), (0, [USER]))
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer ghu_old")

    def test_a_401_renews_and_retries_once(self):
        self.save_session()
        code, out, err, sent = run_cli(["GET", "user"], {USER: [HttpError(401, BAD_CREDENTIALS), ME], TOKEN: RENEWED})
        self.assertEqual(code, 0)
        self.assertEqual([r.full_url for r in sent], [USER, TOKEN, USER])
        self.assertEqual(sent[2].get_header("Authorization"), "Bearer ghu_new")
        self.assertEqual(self.saved()["token"], "ghu_new")
        self.assertIn("renewed", err)
        self.assert_no_secrets(out, err)

    def test_a_second_401_is_not_retried_again(self):
        self.save_session()
        bad = HttpError(401, BAD_CREDENTIALS)
        code, out, err, sent = run_cli(["GET", "user"], {USER: [bad, bad], TOKEN: [RENEWED]})
        self.assertEqual(code, 1)
        self.assertEqual([r.full_url for r in sent], [USER, TOKEN, USER])
        self.assert_no_secrets(out, err)

    def test_a_refused_renewal_says_to_log_in_again(self):
        cases = {"expired, 200 with an error": (-10, {TOKEN: REFUSED}),
                 "expired, HTTP 400": (-10, {TOKEN: HttpError(400, '{"error": "bad_refresh_token"}')}),
                 "a 401": (28800, {USER: HttpError(401, BAD_CREDENTIALS), TOKEN: REFUSED})}
        for name, (expires_in, routes) in cases.items():
            with self.subTest(name):
                self.save_session(expires_in=expires_in)
                code, out, err, sent = run_cli(["GET", "user"], routes)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("clt github login", err)
                self.assertEqual([r.full_url for r in sent].count(TOKEN), 1)
                self.assert_no_secrets(out, err)

    def test_a_refused_renewal_as_json_has_exit_code_1(self):
        self.save_session(expires_in=-10)
        code, out, err, _ = run_cli(["--json", "GET", "user"], {TOKEN: REFUSED})
        error = json.loads(err.strip().splitlines()[-1])
        self.assertEqual((code, error["exit_code"]), (1, 1))
        self.assertIn("clt github login", error["error"])
        self.assert_no_secrets(out, err)

    def test_a_session_renewed_meanwhile_by_another_process_is_used(self):
        self.save_session(expires_in=-10)

        def renewed_elsewhere(cid, refresh_token, cacert):
            github.GitHub().save_session(json.loads(RENEWED[1]), cid, refresh_token)
            return None  # GitHub replaced ghr_old, so it refuses it here

        with mock.patch.object(auth, "refresh_tokens", renewed_elsewhere):
            code, out, err, sent = run_cli(["GET", "user"], {USER: ME})
        self.assertEqual((code, [r.full_url for r in sent]), (0, [USER]))
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer ghu_new")

    def test_an_expired_refresh_token_is_not_sent(self):
        self.save_session(expires_in=-10, refresh_expires_in=-5)
        code, out, err, sent = run_cli(["GET", "user"], {})
        self.assertEqual((code, sent), (1, []))
        self.assertIn("clt github login", err)

    def test_the_refresh_command_renews_now(self):
        self.save_session()
        code, out, err, sent = run_cli(["refresh"], {TOKEN: RENEWED})
        self.assertEqual((code, out, [r.full_url for r in sent]), (0, "", [TOKEN]))
        self.assertEqual(self.saved()["token"], "ghu_new")
        self.assertIn("renewed", err)
        self.assert_no_secrets(out, err)

    def test_the_refresh_command_reports_a_refusal(self):
        self.save_session()
        code, out, err, _ = run_cli(["refresh"], {TOKEN: REFUSED})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("clt github login", err)
        self.assertEqual(self.saved()["token"], "ghu_old")
        self.assert_no_secrets(out, err)

    def test_sessions_without_a_client_id_renew_with_the_built_in_app(self):
        self.save_session(expires_in=-10, client_id=None)
        _, _, _, sent = run_cli(["GET", "user"], {TOKEN: RENEWED, USER: ME})
        self.assertEqual(form(sent[0])["client_id"], auth.DEFAULT_CLIENT_ID)

    def test_tokens_from_the_environment_are_never_renewed(self):
        self.save_session(expires_in=-10)
        for var in ("GITHUB_TOKEN", "GH_TOKEN"):
            with self.subTest(var=var), mock.patch.dict(os.environ, {var: "env-token"}):
                code, _, _, sent = run_cli(["GET", "user"], {USER: HttpError(401, BAD_CREDENTIALS)})
                self.assertEqual((code, [r.full_url for r in sent]), (1, [USER]))
                self.assertEqual(sent[0].get_header("Authorization"), "Bearer env-token")
                code, out, err, sent = run_cli(["refresh"], {})
                self.assertEqual((code, sent), (1, []))
                self.assertIn("cannot be renewed", err)
                self.assertNotIn("env-token", out + err)
        self.assertEqual(self.saved()["token"], "ghu_old")

    def test_pasted_tokens_and_old_session_files_work_and_are_never_renewed(self):
        credentials.write_private_json(github.GitHub().credentials_file, {"token": "pasted-token"})
        code, _, _, sent = run_cli(["GET", "user"], {USER: ME})
        self.assertEqual((code, sent[0].get_header("Authorization")), (0, "Bearer pasted-token"))
        code, _, _, sent = run_cli(["GET", "user"], {USER: HttpError(401, BAD_CREDENTIALS)})
        self.assertEqual((code, [r.full_url for r in sent]), (1, [USER]))
        code, out, err, sent = run_cli(["refresh"], {})
        self.assertEqual((code, sent), (1, []))
        self.assertIn("cannot be renewed", err)
        self.assertNotIn("pasted-token", out + err)
        self.assertEqual(self.saved(), {"token": "pasted-token"})

    def test_the_token_command_prints_a_renewed_token(self):
        self.save_session(expires_in=-10)
        code, out, err, _ = run_cli(["token"], {TOKEN: RENEWED})
        self.assertEqual((code, out), (0, "ghu_new\n"))
        self.assertNotIn("ghr_new", out + err)

    def test_refresh_is_blocked_by_read_only_mode(self):
        self.save_session()
        code, out, err, sent = run_cli(["--read-only", "refresh"], {})
        self.assertEqual((code, out, sent), (4, "", []))
        self.assertIn("read-only", err)
        self.assertEqual(self.saved()["token"], "ghu_old")


class ProtectTests(TempConf):
    def protect(self, existing, *flags):
        calls = []

        def call(self, method, path, body):
            calls.append((method, path, json.loads(body) if body else None))
            return (200, json.dumps(existing), {}) if method == "GET" else (200, '{"id": 1}', {})

        out = io.StringIO()
        with mock.patch.object(github.GitHub, "call", call), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            github.main(["protect", "o/r", *flags])
        return calls, out.getvalue()

    def test_creates_both_rulesets_when_there_are_none(self):
        calls, _ = self.protect([])
        self.assertEqual(calls, [("GET", "repos/o/r/rulesets", None),
                                 ("POST", "repos/o/r/rulesets", github.RULESETS[0]),
                                 ("POST", "repos/o/r/rulesets", github.RULESETS[1])])

    def test_the_rulesets_are_the_ones_of_the_design(self):
        admin = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        main, tags = github.RULESETS
        self.assertEqual(main, {"name": "main", "target": "branch", "enforcement": "active", "bypass_actors": admin,
                                "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}]})
        self.assertEqual(tags, {"name": "v* tags", "target": "tag", "enforcement": "active", "bypass_actors": admin,
                                "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
                                "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]})

    def test_updates_existing_rulesets_by_name(self):
        calls, _ = self.protect([{"id": 11, "name": "main"}, {"id": 12, "name": "v* tags"}])
        self.assertEqual([c[:2] for c in calls], [("GET", "repos/o/r/rulesets"), ("PUT", "repos/o/r/rulesets/11"),
                                                  ("PUT", "repos/o/r/rulesets/12")])

    def test_dry_run_only_reads(self):
        calls, out = self.protect([{"id": 11, "name": "main"}], "--dry-run")
        self.assertEqual([c[0] for c in calls], ["GET"])
        planned = json.loads(out)
        self.assertEqual([(r["method"], r["path"]) for r in planned],
                         [("PUT", "repos/o/r/rulesets/11"), ("POST", "repos/o/r/rulesets")])
        self.assertEqual(planned[1]["body"], github.RULESETS[1])

    def test_read_only_blocks_it(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), self.assertRaises(errors.ReadOnlyError):
            calls, _ = self.protect([])

    def test_needs_owner_and_repo(self):
        with self.assertRaisesRegex(ToolError, "OWNER/REPO"), contextlib.redirect_stderr(io.StringIO()):
            github.main(["protect", "not-a-repo"])


if __name__ == "__main__":
    unittest.main()
