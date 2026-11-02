import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools import core, github
from cli_tools.core import HttpError, ToolError
from tests.helpers import TempConf


class FakeGitHub:
    """Answers core.send by URL and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, cacert):
        self.requests.append(req)
        answer = self.routes[req.full_url]
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(argv, routes, stdin=""):
    fake = FakeGitHub(routes)
    out = io.StringIO()
    with mock.patch.object(core, "send", fake), mock.patch("sys.stdin", io.StringIO(stdin)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = github.main(argv)
    return code, out.getvalue(), fake.requests


USER = "https://api.github.com/user"


class GitHubLoginTests(TempConf):
    def test_login_checks_the_token_and_saves_it(self):
        _, _, sent = run(["login", "--token-stdin"], {USER: (200, '{"login": "bot"}', {})}, stdin="tok\n")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer tok")
        for name, value in github.HEADERS.items():
            self.assertEqual(sent[0].get_header(name.capitalize()), value)
        self.assertEqual(core.read_json_file(github.GitHub().credentials_file), {"token": "tok"})

    def test_a_rejected_token_is_not_saved(self):
        with self.assertRaisesRegex(ToolError, "rejected"):
            run(["login", "--token-stdin"], {USER: HttpError(401, '{"message": "Bad credentials"}')}, stdin="bad\n")
        self.assertFalse(github.GitHub().credentials_file.exists())

    def test_login_without_a_terminal_needs_token_stdin(self):
        with self.assertRaisesRegex(ToolError, "needs a person"):
            run(["login"], {})

    def test_tokens_from_the_environment_win_over_the_file(self):
        core.write_private_json(github.GitHub().credentials_file, {"token": "saved"})
        with mock.patch.dict(os.environ, {"GH_TOKEN": "gh"}):
            self.assertEqual(github.GitHub().load_credentials()["token"], "gh")
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "github"}):
                self.assertEqual(github.GitHub().load_credentials()["token"], "github")
        self.assertEqual(github.GitHub().load_credentials()["token"], "saved")

    def test_without_a_token_it_says_how_to_get_one(self):
        with self.assertRaisesRegex(core.NotLoggedIn, "GITHUB_TOKEN"):
            github.GitHub().load_credentials()


class GitHubApiTests(TempConf):
    def setUp(self):
        super().setUp()
        core.write_private_json(github.GitHub().credentials_file, {"token": "secret-token"})

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
                with self.subTest(argv=argv), self.assertRaises(core.ReadOnlyError):
                    run(argv, {}, stdin="tok\n")
            code, _, _ = run(["GET", "user"], {USER: (200, '{"login": "bot"}', {})})
        self.assertEqual(code, 0)
        self.assertTrue(github.GitHub().credentials_file.exists())


if __name__ == "__main__":
    unittest.main()
