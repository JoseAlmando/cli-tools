import base64
import hashlib
import json
import time
import unittest
import urllib.parse

from cli_tools import core, gitea
from cli_tools.core import HttpError
from tests.helpers import TempConf


class PkceTests(unittest.TestCase):
    def test_the_challenge_is_the_s256_of_the_verifier(self):
        verifier, challenge = gitea.pkce_pair()
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
        url = gitea.login_url(g, "my-oidc", "http://127.0.0.1:38291", "st", "ch")
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(parts.path, "/user/oauth2/my-oidc")
        redirect_to = urllib.parse.parse_qs(parts.query)["redirect_to"][0]
        authorize = urllib.parse.urlsplit(redirect_to)
        self.assertEqual(authorize.path, "/login/oauth/authorize")
        params = urllib.parse.parse_qs(authorize.query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["redirect_uri"], ["http://127.0.0.1:38291"])
        self.assertEqual(params["client_id"], [gitea.DEFAULT_CLIENT_ID])

    def test_without_a_provider_login_uses_the_gitea_login_page(self):
        g = gitea.Gitea("https://gitea.example.com")
        self.assertTrue(gitea.login_url(g, "", "http://127.0.0.1:1", "st", "ch")
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
        # Gitea no siempre devuelve un refresh nuevo: se conserva el anterior
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

    def test_logout_works_without_a_configured_server(self):
        self.client()
        self.assertEqual(gitea.main(["logout"]), None)
        self.assertIsNone(core.read_json_file(gitea.Gitea("https://gitea.example.com").credentials_file))


if __name__ == "__main__":
    unittest.main()
