import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools.core import context, credentials, errors, http
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.jira import cli, client
from cli_tools.tools.jira.client import Jira
from tests.helpers import TempConf

SITE = "https://example.atlassian.net"
CLOUD = "11111111-2222-3333-4444-555555555555"
GATEWAY_API = f"https://api.atlassian.com/ex/jira/{CLOUD}/rest/api/3/"
SITE_API = f"{SITE}/rest/api/3/"
TENANT = f"{SITE}/_edge/tenant_info"
ME = {"accountId": "acc-1", "displayName": "Bot User", "emailAddress": "me@example.com"}


class FakeJira:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, cacert):
        self.requests.append(req)
        key = (req.get_method(), req.full_url)
        if key not in self.routes:
            raise AssertionError(f"unexpected request {key}")
        answer = self.routes[key]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return status, body if isinstance(body, str) else json.dumps(body), {}


def fake(routes):
    return mock.patch.object(http, "send", FakeJira(routes))


def unauthorized():
    return HttpError(401, '{"message": "Client must be authenticated"}')


class JiraClientTests(TempConf):
    def test_basic_header(self):
        self.assertEqual(client.basic_header("me@example.com", "tok"), "Basic bWVAZXhhbXBsZS5jb206dG9r")

    def test_classic_token_stays_on_the_site(self):
        with fake({("GET", SITE_API + "myself"): (200, ME)}) as send:
            creds, me = Jira(SITE).detect("me@example.com", "tok")
        self.assertEqual((creds["mode"], creds["cloud_id"]), ("site", None))
        self.assertEqual(me["accountId"], "acc-1")
        self.assertEqual(len(send.requests), 1)
        self.assertEqual(send.requests[0].get_header("Authorization"), client.basic_header("me@example.com", "tok"))

    def test_scoped_token_is_detected_through_tenant_info(self):
        routes = {("GET", SITE_API + "myself"): unauthorized(), ("GET", TENANT): (200, {"cloudId": CLOUD}),
                  ("GET", GATEWAY_API + "myself"): (200, ME)}
        with fake(routes) as send:
            creds, _ = Jira(SITE).detect("me@example.com", "tok")
        self.assertEqual(creds, {"url": SITE, "email": "me@example.com", "token": "tok", "mode": "scoped",
                                 "cloud_id": CLOUD})
        tenant = next(r for r in send.requests if r.full_url == TENANT)
        self.assertIsNone(tenant.get_header("Authorization"))

    def test_scoped_flag_skips_the_site(self):
        with fake({("GET", GATEWAY_API + "myself"): (200, ME)}) as send:
            creds, _ = Jira(SITE).detect("me@example.com", "tok", cloud_id=CLOUD, scoped=True)
        self.assertEqual(creds["mode"], "scoped")
        self.assertEqual([r.full_url for r in send.requests], [GATEWAY_API + "myself"])

    def test_rejected_everywhere_saves_nothing(self):
        routes = {("GET", SITE_API + "myself"): unauthorized(), ("GET", TENANT): (200, {"cloudId": CLOUD}),
                  ("GET", GATEWAY_API + "myself"): unauthorized()}
        with fake(routes), self.assertRaisesRegex(ToolError, "rejected"):
            Jira(SITE).detect("me@example.com", "bad")
        self.assertFalse(Jira(SITE).credentials_file.exists())

    def test_tenant_info_unusable_says_how_to_pass_the_cloud_id(self):
        for answer in (HttpError(404, "Not Found"), (200, "not json"), (200, {}), ToolError("Cannot connect")):
            with self.subTest(answer=answer):
                routes = {("GET", SITE_API + "myself"): unauthorized(), ("GET", TENANT): answer}
                with fake(routes), self.assertRaisesRegex(ToolError, "--cloud-id"):
                    Jira(SITE).detect("me@example.com", "tok")

    def test_cloud_id_must_be_a_uuid(self):
        for value in ("../x", "abc", "", None, CLOUD + "/x"):
            with self.subTest(value=value), self.assertRaises(ToolError):
                client.check_cloud_id(value)
        self.assertEqual(client.check_cloud_id(CLOUD.upper()), CLOUD)
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok", "JIRA_CLOUD_ID": "../x"}
        with mock.patch.dict(os.environ, env), fake({}) as send, self.assertRaises(ToolError):
            Jira().call("GET", "myself", None)
        self.assertEqual(send.requests, [])

    def test_environment_wins_and_picks_the_mode(self):
        Jira(SITE).save_credentials({"url": SITE, "email": "saved@example.com", "token": "saved", "mode": "site",
                                     "cloud_id": None})
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env):
            with fake({("GET", SITE_API + "myself"): (200, ME)}) as send:
                Jira().call("GET", "myself", None)
            self.assertEqual(send.requests[0].get_header("Authorization"), client.basic_header("me@example.com", "tok"))
            with mock.patch.dict(os.environ, {"JIRA_CLOUD_ID": CLOUD}), \
                    fake({("GET", GATEWAY_API + "myself"): (200, ME)}) as send:
                Jira().call("GET", "myself", None)
            self.assertEqual(send.requests[0].full_url, GATEWAY_API + "myself")

    def test_saved_scoped_session_goes_through_the_gateway(self):
        Jira(SITE).save_credentials({"url": SITE, "email": "me@example.com", "token": "tok", "mode": "scoped",
                                     "cloud_id": CLOUD})
        with mock.patch.dict(os.environ, {"JIRA_URL": SITE}), \
                fake({("GET", GATEWAY_API + "issue/A-1"): (200, {"key": "A-1"})}) as send:
            Jira().call("GET", "issue/A-1", None)
        self.assertEqual(send.requests[0].full_url, GATEWAY_API + "issue/A-1")
        saved = credentials.read_json_file(Jira(SITE).credentials_file)
        self.assertEqual(saved["mode"], "scoped")

    def test_not_logged_in(self):
        with mock.patch.dict(os.environ, {"JIRA_URL": SITE}), self.assertRaisesRegex(errors.NotLoggedIn, "JIRA_TOKEN"):
            Jira().load_credentials()

    def test_session_for_another_site_is_refused(self):
        Jira("https://other.atlassian.net").save_credentials(
            {"url": "https://other.atlassian.net", "email": "e", "token": "t", "mode": "site", "cloud_id": None})
        with mock.patch.dict(os.environ, {"JIRA_URL": SITE}), self.assertRaisesRegex(ToolError, "other.atlassian.net"):
            Jira().load_credentials()

    def test_env_site_401_mentions_jira_cloud_id(self):
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env), fake({("GET", SITE_API + "myself"): unauthorized()}), \
                self.assertRaisesRegex(ToolError, "JIRA_CLOUD_ID"):
            Jira().call("GET", "myself", None)

    def test_paths_cannot_leave_the_api(self):
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env), fake({}) as send, self.assertRaises(ToolError):
            Jira().call("GET", "https://evil.example/x", None)
        self.assertEqual(send.requests, [])


def run(argv, routes, stdin="", env=None):
    out, err = io.StringIO(), io.StringIO()
    with fake(routes) as send, mock.patch.dict(os.environ, env or {}), mock.patch("sys.stdin", io.StringIO(stdin)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue(), send.requests


def save_site_session():
    Jira(SITE).save_credentials({"url": SITE, "email": "me@example.com", "token": "tok", "mode": "site",
                                 "cloud_id": None})


class JiraCliTests(TempConf):
    def test_login_token_stdin_saves_the_detected_mode(self):
        routes = {("GET", SITE_API + "myself"): unauthorized(), ("GET", TENANT): (200, {"cloudId": CLOUD}),
                  ("GET", GATEWAY_API + "myself"): (200, ME)}
        code, out, err, _ = run(["login", "--url", SITE, "--email", "me@example.com", "--token-stdin"], routes,
                                stdin="tok\n")
        self.assertEqual((code, out), (None, ""))
        saved = credentials.read_json_file(Jira(SITE).credentials_file)
        self.assertEqual((saved["mode"], saved["cloud_id"], saved["token"]), ("scoped", CLOUD, "tok"))
        self.assertIn("Bot User", err)

    def test_login_token_stdin_needs_an_email(self):
        with self.assertRaisesRegex(ToolError, "--email"):
            run(["login", "--url", SITE, "--token-stdin"], {}, stdin="tok\n")

    def test_login_without_a_terminal_needs_token_stdin(self):
        with self.assertRaisesRegex(ToolError, "needs a person"):
            run(["login", "--url", SITE], {})

    def test_me_json_shape_and_no_token(self):
        save_site_session()
        ctx = context.CLIContext(json_mode=True)
        out = io.StringIO()
        with fake({("GET", SITE_API + "myself"): (200, ME)}), mock.patch.dict(os.environ, {"JIRA_URL": SITE}), \
                contextlib.redirect_stdout(out):
            cli.main(["me"], ctx=ctx)
        me = json.loads(out.getvalue())
        self.assertEqual(me, {"id": "acc-1", "username": "me@example.com", "full_name": "Bot User",
                              "email": "me@example.com", "url": SITE,
                              "credentials_file": str(Jira(SITE).credentials_file)})
        self.assertNotIn("tok", out.getvalue().replace("credentials", ""))

    def test_logout_deletes_the_session(self):
        save_site_session()
        run(["logout"], {}, env={"JIRA_URL": SITE})
        self.assertFalse(Jira(SITE).credentials_file.exists())

    def test_read_only_blocks_login_and_token(self):
        ctx = context.CLIContext(read_only=True)
        for argv in (["login", "--url", SITE], ["token"]):
            with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                cli.main(argv, ctx=ctx)


def page(body):
    return 200, body


class JiraPagingTests(TempConf):
    def setUp(self):
        super().setUp()
        save_site_session()

    def test_search_jql_follows_next_page_token(self):
        base = SITE_API + "search/jql?jql=project%3DA&fields=key"
        routes = {("GET", base): page({"issues": [{"key": "A-1"}, {"key": "A-2"}], "nextPageToken": "t1"}),
                  ("GET", base + "&nextPageToken=t1"): page({"issues": [{"key": "A-3"}], "isLast": True})}
        code, out, _, sent = run(["GET", "search/jql?jql=project%3DA&fields=key", "--all"], routes,
                                 env={"JIRA_URL": SITE})
        self.assertEqual(json.loads(out), {"issues": [{"key": "A-1"}, {"key": "A-2"}, {"key": "A-3"}]})
        self.assertEqual(len(sent), 2)

    def test_repeated_token_stops(self):
        base = SITE_API + "search/jql?jql=x"
        routes = {("GET", base): page({"issues": [{"key": "A-1"}], "nextPageToken": "t1"}),
                  ("GET", base + "&nextPageToken=t1"): page({"issues": [{"key": "A-2"}], "nextPageToken": "t1"})}
        _, out, _, sent = run(["GET", "search/jql?jql=x", "--all"], routes, env={"JIRA_URL": SITE})
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(json.loads(out)["issues"]), 2)

    def test_start_at_paging_for_values(self):
        base = SITE_API + "project/search"
        routes = {("GET", base): page({"startAt": 0, "maxResults": 2, "total": 3, "values": [{"id": 1}, {"id": 2}]}),
                  ("GET", base + "?startAt=2"): page({"startAt": 2, "maxResults": 2, "total": 3, "values": [{"id": 3}]})}
        _, out, _, sent = run(["GET", "project/search", "--all"], routes, env={"JIRA_URL": SITE})
        self.assertEqual(json.loads(out), {"total": 3, "values": [{"id": 1}, {"id": 2}, {"id": 3}]})
        self.assertEqual(len(sent), 2)

    def test_fields_reach_issues(self):
        body = {"issues": [{"key": "A-1", "id": "1", "fields": {"status": {"name": "Done"}, "summary": "x"}}]}
        _, out, _, _ = run(["GET", "search/jql?jql=x", "--fields", "key,fields.status.name"],
                           {("GET", SITE_API + "search/jql?jql=x"): page(body)}, env={"JIRA_URL": SITE})
        self.assertEqual(json.loads(out), {"issues": [{"key": "A-1", "fields.status.name": "Done"}]})


if __name__ == "__main__":
    unittest.main()
