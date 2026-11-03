import json
import unittest

from cli_tools.core import credentials
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.taiga import cli as taiga
from tests.helpers import TempConf


class TaigaTests(TempConf):
    def client(self):
        t = taiga.Taiga("https://taiga.example.com")
        t.save_credentials({"auth_token": "old", "refresh": "r1"})
        return t

    def test_login_saves_the_server_with_the_session(self):
        t = self.client()
        self.assertEqual(credentials.read_json_file(t.credentials_file)["url"], "https://taiga.example.com")
        self.assertEqual(taiga.Taiga().url, "https://taiga.example.com")

    def test_a_401_refreshes_the_token_and_retries_once(self):
        t = self.client()
        calls = []

        def fake(method, path, body=None, token=None):
            calls.append((path, token))
            if path == "auth/refresh":
                return 200, json.dumps({"auth_token": "new"}), {}
            if token == "old":
                raise HttpError(401, "{}")
            return 200, '{"ok": true}', {}

        t.request = fake
        self.assertEqual(t.call("GET", "users/me", None)[1], '{"ok": true}')
        self.assertEqual(calls, [("users/me", "old"), ("auth/refresh", None), ("users/me", "new")])
        # If the server returns no new refresh token, the previous one is kept
        self.assertEqual(t.load_credentials()["refresh"], "r1")

    def test_a_rejected_refresh_surfaces_the_original_401(self):
        t = self.client()

        def fake(method, path, body=None, token=None):
            raise HttpError(401, "{}")

        t.request = fake
        with self.assertRaises(HttpError):
            t.call("GET", "users/me", None)

    def test_a_session_for_another_server_is_not_used(self):
        self.client()
        with self.assertRaisesRegex(ToolError, "not https://other.example.com"):
            taiga.Taiga("https://other.example.com").load_credentials()

    def test_logout_works_without_a_configured_server(self):
        self.assertEqual(taiga.main(["logout"]), None)


if __name__ == "__main__":
    unittest.main()
