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
        with mock.patch.dict(os.environ, {"CLT_READONLY": "1"}), self.assertRaises(core.ReadOnlyError):
            calls, _ = self.protect([])

    def test_needs_owner_and_repo(self):
        with self.assertRaisesRegex(ToolError, "OWNER/REPO"), contextlib.redirect_stderr(io.StringIO()):
            github.main(["protect", "not-a-repo"])


if __name__ == "__main__":
    unittest.main()
