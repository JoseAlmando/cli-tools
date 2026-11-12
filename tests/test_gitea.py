import base64
import hashlib
import json
import os
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from cli_tools.core import credentials
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.gitea import auth, client
from cli_tools.tools.gitea import cli as gitea
from tests.helpers import TempConf
from tests.test_contracts import run_inline


class PkceTests(unittest.TestCase):
    def test_the_challenge_is_the_s256_of_the_verifier(self):
        verifier, challenge = auth.pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)
        self.assertGreaterEqual(len(verifier), 43)


class GiteaTests(TempConf):
    def client(self, expires_in=3600):
        g = gitea.Gitea("https://gitea.example.com")
        g.save_credentials({"access_token": "old", "refresh_token": "r1", "expires_in": expires_in})
        return g

    def test_login_goes_straight_to_the_provider_and_back_to_authorize(self):
        g = gitea.Gitea("https://gitea.example.com")
        url = auth.login_url(g, "my-oidc", "http://127.0.0.1:38291", "st", "ch")
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(parts.path, "/user/oauth2/my-oidc")
        redirect_to = urllib.parse.parse_qs(parts.query)["redirect_to"][0]
        authorize = urllib.parse.urlsplit(redirect_to)
        self.assertEqual(authorize.path, "/login/oauth/authorize")
        params = urllib.parse.parse_qs(authorize.query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["redirect_uri"], ["http://127.0.0.1:38291"])
        self.assertEqual(params["client_id"], [client.DEFAULT_CLIENT_ID])

    def test_without_a_provider_login_uses_the_gitea_login_page(self):
        g = gitea.Gitea("https://gitea.example.com")
        self.assertTrue(auth.login_url(g, "", "http://127.0.0.1:1", "st", "ch")
                        .startswith("https://gitea.example.com/login/oauth/authorize?"))

    def test_an_expired_token_is_refreshed_before_the_call(self):
        g = self.client(expires_in=-10)
        g.token_request = lambda fields: (200, json.dumps({"access_token": "new", "refresh_token": "r2",
                                                            "expires_in": 3600}), {})
        seen = []
        g.request = lambda method, path, body=None, token=None: seen.append(token) or (200, "{}", {})
        g.call("GET", "user", None)
        self.assertEqual(seen, ["new"])
        creds = g.load_credentials()
        self.assertEqual(creds["refresh_token"], "r2")
        self.assertGreater(creds["expires_at"], time.time() + 3000)

    def test_a_401_refreshes_and_retries_once(self):
        g = self.client()
        g.token_request = lambda fields: (200, json.dumps({"access_token": "new", "expires_in": 3600}), {})
        seen = []

        def fake(method, path, body=None, token=None):
            seen.append(token)
            if token == "old":
                raise HttpError(401, "{}")
            return 200, "{}", {}

        g.request = fake
        g.call("GET", "user", None)
        self.assertEqual(seen, ["old", "new"])
        # Gitea does not always return a new refresh token: the previous one is kept
        self.assertEqual(g.load_credentials()["refresh_token"], "r1")

    def test_the_refresh_uses_the_client_the_session_was_created_with(self):
        g = gitea.Gitea("https://gitea.example.com")
        g.save_credentials({"access_token": "old", "refresh_token": "r1", "expires_in": 3600}, client_id="custom-app")
        sent = []
        g.token_request = lambda fields: sent.append(fields) or (200, json.dumps({"access_token": "new"}), {})
        self.assertTrue(g.refresh(g.load_credentials()))
        self.assertEqual(sent[0]["client_id"], "custom-app")
        self.assertEqual(g.load_credentials()["client_id"], "custom-app")

    def test_a_rejected_refresh_returns_false(self):
        g = self.client()

        def rejected(fields):
            raise HttpError(400, '{"error": "invalid_grant"}')

        g.token_request = rejected
        self.assertFalse(g.refresh(g.load_credentials()))

    def test_a_callback_port_that_is_not_a_number_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"GITEA_CALLBACK_PORT": "abc"}):
            with self.assertRaisesRegex(ToolError, "GITEA_CALLBACK_PORT"):
                gitea.Gitea("https://gitea.example.com")

    def test_logout_works_without_a_configured_server(self):
        self.client()
        self.assertEqual(gitea.main(["logout"]), None)
        self.assertIsNone(credentials.read_json_file(gitea.Gitea("https://gitea.example.com").credentials_file))


GITEA = "https://gitea.example.com"
PULLS = ("POST", f"{GITEA}/api/v1/repos/o/r/pulls")
CREATED = (201, json.dumps({"number": 7, "html_url": f"{GITEA}/o/r/pulls/7"}), {})


class GiteaPrTests(TempConf):
    def setUp(self):
        super().setUp()
        os.environ.update(GITEA_URL=GITEA, GITEA_TOKEN="t")

    def pr(self, *argv, routes=None, stdin=""):
        return run_inline("clt", "gitea", "--json", "pr", "create", "--repo", "o/r", "--head", "feature/x",
                          "--title", "Add x", *argv, routes=routes, stdin=stdin)

    def test_create_opens_the_pull_request_with_a_body_from_a_file(self):
        body = Path(self.tmp.name) / "pr.md"
        body.write_text("Closes #3\n", encoding="utf-8")
        code, out, err, sent = self.pr("--base", "main", "--body-file", str(body), routes={PULLS: CREATED})
        self.assertEqual((code, json.loads(out), err), (0, {"number": 7, "url": f"{GITEA}/o/r/pulls/7"}, ""))
        self.assertEqual(json.loads(sent[0].data), {"head": "feature/x", "base": "main", "title": "Add x",
                                                    "body": "Closes #3\n"})

    def test_the_base_defaults_to_the_default_branch_and_the_body_can_come_from_stdin(self):
        routes = {("GET", f"{GITEA}/api/v1/repos/o/r"): (200, '{"default_branch": "develop"}', {}), PULLS: CREATED}
        code, _, _, sent = self.pr("--body-file", "-", routes=routes, stdin="From stdin")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sent[1].data)["base"], "develop")
        self.assertEqual(json.loads(sent[1].data)["body"], "From stdin")

    def test_dry_run_prints_the_request_and_sends_no_write(self):
        code, out, _, sent = self.pr("--base", "main", "--body", "Text", "--dry-run")
        self.assertEqual((code, sent), (0, []))
        self.assertEqual(json.loads(out), {"method": "POST", "path": "repos/o/r/pulls",
                                           "body": {"head": "feature/x", "base": "main", "title": "Add x",
                                                    "body": "Text"}})

    def test_read_only_blocks_it_and_a_bad_repo_is_wrong_usage(self):
        code, _, _, sent = run_inline("clt", "gitea", "--json", "--read-only", "pr", "create", "--repo", "o/r",
                                      "--head", "x", "--title", "t", "--base", "main")
        self.assertEqual((code, sent), (4, []))
        code, _, _, sent = run_inline("clt", "gitea", "pr", "create", "--repo", "just-a-name", "--head", "x",
                                      "--title", "t")
        self.assertEqual((code, sent), (2, []))


if __name__ == "__main__":
    unittest.main()
