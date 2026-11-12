import json
import os
import unittest
from unittest import mock

from cli_tools.core import credentials
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.taiga import cli as taiga
from tests.helpers import TempConf
from tests.test_contracts import run_inline


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


TAIGA = "https://taiga.example.com"
STORY = {"id": 40, "ref": 12, "subject": "Log in", "status_extra_info": {"name": "In progress"},
         "assigned_to_extra_info": {"full_name_display": "Ana"}, "description": "Use the **SSO**.",
         "project_extra_info": {"slug": "web"}, "version": 3}


class TaigaIssueTests(TempConf):
    def setUp(self):
        super().setUp()
        os.environ.update(TAIGA_URL=TAIGA, TAIGA_TOKEN="t")

    def run_taiga(self, *argv, routes=None):
        return run_inline("clt", "taiga", *argv, routes=routes)

    def test_issue_reads_a_story_task_or_issue_by_its_ref(self):
        for key, collection, kind in (("us", "userstories", "story"), ("task", "tasks", "task"),
                                      ("issue", "issues", "issue")):
            with self.subTest(kind=kind):
                routes = {("GET", f"{TAIGA}/api/v1/resolver?project=web&ref=12"): (200, json.dumps({"project": 3, key: 40}), {}),
                          ("GET", f"{TAIGA}/api/v1/{collection}/40"): (200, json.dumps(STORY), {})}
                code, out, err, _ = self.run_taiga("--json", "--read-only", "issue", "12", "--project", "web",
                                                   routes=routes)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), {"kind": kind, "ref": 12, "id": 40, "version": 3, "subject": "Log in",
                                                   "status": "In progress", "assigned_to": "Ana", "project": "web",
                                                   "url": f"{TAIGA}/project/web/{key}/12",
                                                   "description": "Use the **SSO**."})

    def test_the_project_can_come_from_taiga_project_and_text_mode_is_short(self):
        routes = {("GET", f"{TAIGA}/api/v1/resolver?project=web&ref=12"): (200, '{"project": 3, "us": 40}', {}),
                  ("GET", f"{TAIGA}/api/v1/userstories/40"): (200, json.dumps(STORY), {})}
        with mock.patch.dict(os.environ, TAIGA_PROJECT="web"):
            code, out, _, _ = self.run_taiga("i", "12", routes=routes)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[:2], ["story #12 Log in", "In progress · Ana · web"])
        self.assertIn("Use the **SSO**.", out)

    def test_without_a_project_or_with_an_unknown_ref_it_says_so(self):
        code, _, err, sent = self.run_taiga("--read-only", "issue", "12")
        self.assertEqual((code, sent), (1, []))
        self.assertIn("TAIGA_PROJECT", err)
        routes = {("GET", f"{TAIGA}/api/v1/resolver?project=web&ref=99"): (200, '{"project": 3}', {})}
        code, _, err, _ = self.run_taiga("--read-only", "issue", "99", "--project", "web", routes=routes)
        self.assertEqual(code, 1)
        self.assertIn("No story, task or issue #99 in web", err)
        code, _, _, sent = self.run_taiga("--read-only", "issue", "twelve", "--project", "web")
        self.assertEqual((code, sent), (2, []))


if __name__ == "__main__":
    unittest.main()
