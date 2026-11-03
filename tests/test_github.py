import contextlib
import io
import json
import os
import unittest
import urllib.parse
from unittest import mock

from cli_tools.core import credentials, errors, http
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
        self.assertEqual(self.saved_token(), {"token": "oauth-token"})
        self.assertIn("ABCD-1234", err)
        self.assertIn("Only enter this code if it appeared in your own terminal just now", err)
        self.assertNotIn("oauth-token", err)
        self.assertEqual(self.opened, ["https://github.com/login/device"])
        self.assertEqual(self.sleeps, [5, 5, 10])
        first = dict(urllib.parse.parse_qsl(sent[0].data.decode()))
        self.assertEqual(first, {"client_id": "client-123", "scope": "repo"})
        poll = dict(urllib.parse.parse_qsl(sent[1].data.decode()))
        self.assertEqual(poll, {"client_id": "client-123", "device_code": "dev-code",
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        self.assertTrue(all(r.get_header("Authorization") is None for r in sent[:4]))
        self.assertEqual(sent[4].get_header("Authorization"), "Bearer oauth-token")

    def test_a_cancelled_or_expired_authorization_saves_nothing(self):
        for error, message in (("access_denied", "cancelled"), ("expired_token", "expired")):
            with self.subTest(error=error), self.assertRaisesRegex(ToolError, message):
                self.login(["login", "--browser"], {DEVICE: CODES, TOKEN: (200, json.dumps({"error": error}), {})})
            self.assertFalse(github.GitHub().credentials_file.exists())

    def test_the_built_in_oauth_app_is_used_unless_another_is_set(self):
        self.assertEqual(auth.client_id(), "Ov23liksTJsgDjqbhwaS")
        with mock.patch.dict(os.environ, {"GITHUB_CLIENT_ID": "other"}):
            self.assertEqual(auth.client_id(), "other")

    def test_browser_authorization_needs_an_oauth_app(self):
        with mock.patch.object(auth, "DEFAULT_CLIENT_ID", ""), \
                self.assertRaisesRegex(ToolError, "GITHUB_CLIENT_ID.*--create-token"):
            self.login(["login", "--browser"], {}, env={})

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
        self.assertEqual(self.saved_token(), {"token": "oauth-token"})
        with mock.patch.object(github.getpass, "getpass", lambda prompt: "pasted"):
            self.login(["login"], {USER: (200, '{"login": "b"}', {})}, stdin="2\n")
        self.assertEqual(self.saved_token(), {"token": "pasted"})

    def test_without_an_oauth_app_the_menu_defaults_to_creating_a_token(self):
        with mock.patch.object(auth, "DEFAULT_CLIENT_ID", ""), \
                mock.patch.object(github.getpass, "getpass", lambda prompt: "pasted"):
            err, _ = self.login(["login"], {USER: (200, '{"login": "b"}', {})}, stdin="\n", env={})
        self.assertEqual(self.saved_token(), {"token": "pasted"})
        self.assertIn("not set up", err)

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
                                           "credentials_file": str(github.GitHub().credentials_file)})
        self.assertNotIn("secret-token", out)

    def test_read_only_blocks_session_commands_and_writes(self):
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}):
            for argv in (["login", "--token-stdin"], ["logout"], ["token"], ["POST", "user/repos", "-d", "{}"]):
                with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                    run(argv, {}, stdin="tok\n")
            code, _, _ = run(["GET", "user"], {USER: (200, '{"login": "bot"}', {})})
        self.assertEqual(code, 0)
        self.assertTrue(github.GitHub().credentials_file.exists())


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
