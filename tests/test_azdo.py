import base64
import contextlib
import io
import json
import os
import urllib.parse
from unittest import mock

from cli_tools.core import context, credentials, errors, http
from cli_tools.core.errors import HttpError, NotLoggedIn, ToolError
from tests.helpers import TempConf

ORG = "https://dev.azure.com/example"


class FakeAzdo:
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
        status, body, *headers = answer
        return status, body if isinstance(body, str) else json.dumps(body), headers[0] if headers else {}


def fake(routes):
    return mock.patch.object(http, "send", FakeAzdo(routes))


def client():
    from cli_tools.tools.azdo import client as module
    return module


class AzdoHelperTests(TempConf):
    def test_org_url_accepts_a_name_or_the_url(self):
        c = client()
        self.assertEqual(c.org_url("example"), ORG)
        self.assertEqual(c.org_url("https://dev.azure.com/example/"), ORG)
        for bad in ("evil.example.com/x", "https://example.visualstudio.com", "a/../b", "", "-x",
                    "https://dev.azure.com/example/project", "http://dev.azure.com/example"):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                c.org_url(bad)

    def test_basic_header(self):
        self.assertEqual(client().basic_header("pat"), "Basic " + base64.b64encode(b":pat").decode())

    def test_api_version_added_once(self):
        c = client()
        self.assertEqual(c.with_api_version("_apis/projects", "7.1"), "_apis/projects?api-version=7.1")
        self.assertEqual(c.with_api_version("_apis/projects?$top=5", "7.1"), "_apis/projects?$top=5&api-version=7.1")
        kept = "Demo/_apis/wit/workItems/1/comments?API-Version=7.1-preview.4"
        self.assertEqual(c.with_api_version(kept, "7.1"), kept)

    def test_json_patch_only_for_work_item_create_and_update(self):
        c = client()
        patch = "application/json-patch+json"
        self.assertEqual(c.content_type("POST", "Demo/_apis/wit/workitems/$Task?api-version=7.1"), patch)
        self.assertEqual(c.content_type("PATCH", "_apis/wit/WorkItems/42"), patch)
        for method, path in (("POST", "Demo/_apis/wit/workitems/42/comments"), ("POST", "_apis/wit/workitemsbatch"),
                             ("PATCH", "Demo/_apis/git/repositories/web/pullrequests/7"),
                             ("GET", "_apis/wit/workitems/42"), ("POST", "_apis/wit/workitems/42")):
            with self.subTest(method=method, path=path):
                self.assertEqual(c.content_type(method, path), "application/json")


class AzdoRequestTests(TempConf):
    def setUp(self):
        super().setUp()
        os.environ["AZDO_ORG"] = "example"

    def test_request_url_headers_and_encoding(self):
        c = client()
        url = f"{ORG}/Demo%20Project/_apis/wit/workitems/$Task?api-version=7.1"
        with fake({("POST", url): (200, {"id": 1})}) as sent:
            os.environ["AZDO_TOKEN"] = "pat"
            c.AzureDevOps().call("POST", "Demo Project/_apis/wit/workitems/$Task", "[]")
        req = sent.requests[0]
        self.assertEqual(req.get_header("Authorization"), c.basic_header("pat"))
        self.assertEqual(req.get_header("X-tfs-fedauthredirect"), "Suppress")
        self.assertEqual(req.get_header("Content-type"), "application/json-patch+json")
        self.assertNotIn("pat", req.full_url)

    def test_api_version_from_the_environment(self):
        os.environ.update(AZDO_TOKEN="pat", AZDO_API_VERSION="7.0")
        with fake({("GET", f"{ORG}/_apis/projects?api-version=7.0"): (200, {"value": []})}) as sent:
            client().AzureDevOps().call("GET", "_apis/projects", None)
        self.assertEqual(len(sent.requests), 1)

    def test_env_token_precedence(self):
        c = client()
        credentials.write_private_json(c.AzureDevOps().credentials_file, {"url": ORG, "token": "saved"})
        self.assertEqual(c.AzureDevOps().load_credentials()["token"], "saved")
        os.environ["AZURE_DEVOPS_EXT_PAT"] = "cli"
        self.assertEqual(c.AzureDevOps().load_credentials()["token"], "cli")
        os.environ["AZDO_TOKEN"] = "mine"
        self.assertEqual(c.AzureDevOps().load_credentials()["token"], "mine")

    def test_not_logged_in(self):
        with self.assertRaises(NotLoggedIn) as caught:
            client().AzureDevOps().load_credentials()
        self.assertIn("clt azdo login", str(caught.exception))
        self.assertIn("AZDO_TOKEN", str(caught.exception))

    def test_sign_in_page_is_a_rejected_token(self):
        os.environ["AZDO_TOKEN"] = "pat"
        url = f"{ORG}/_apis/projects?api-version=7.1"
        for answer in ((203, "<html>sign in</html>"), HttpError(302, "", {"Location": "https://login.example.com"})):
            with self.subTest(answer=answer), fake({("GET", url): answer}):
                with self.assertRaises(ToolError) as caught:
                    client().AzureDevOps().call("GET", "_apis/projects", None)
                self.assertIn("did not accept the token", str(caught.exception))
                self.assertNotIn("pat", str(caught.exception))

    def test_saved_session_for_another_org_is_refused(self):
        c = client()
        credentials.write_private_json(c.AzureDevOps().credentials_file,
                                       {"url": "https://dev.azure.com/other", "token": "x"})
        with self.assertRaises(ToolError) as caught:
            c.AzureDevOps().load_credentials()
        self.assertIn("other", str(caught.exception))
        self.assertIn("example", str(caught.exception))

    def test_saved_url_on_another_host_is_refused(self):
        os.environ.pop("AZDO_ORG")
        c = client()
        credentials.write_private_json(c.AzureDevOps(require_url=False).credentials_file,
                                       {"url": "https://evil.example.com", "token": "x"})
        with self.assertRaises(ToolError):
            c.AzureDevOps()

    def test_no_organization_says_how_to_set_one(self):
        os.environ.pop("AZDO_ORG")
        with self.assertRaises(ToolError) as caught:
            client().AzureDevOps()
        self.assertIn("clt azdo login --org", str(caught.exception))
        self.assertIn("AZDO_ORG", str(caught.exception))
        self.assertNotIn("--url", str(caught.exception))

    def test_dot_segments_are_refused(self):
        os.environ["AZDO_TOKEN"] = "pat"
        with fake({}), self.assertRaises(ToolError):
            client().AzureDevOps().call("GET", "Demo/../_apis/projects", None)


class AzdoPagingTests(TempConf):
    def test_continuation_token_header(self):
        c = client()
        self.assertEqual(c.next_page({"X-MS-ContinuationToken": "abc"}, '{"value": [1]}', "_apis/projects?$top=2"),
                         "_apis/projects?%24top=2&continuationToken=abc")
        self.assertIsNone(c.next_page({"x-ms-continuationtoken": "abc"}, '{"value": [1]}',
                                      "_apis/projects?continuationToken=abc"))

    def test_top_and_skip(self):
        c = client()
        path = "Demo/_apis/git/repositories/web/pullrequests?$top=2"
        full = '{"count": 2, "value": [1, 2]}'
        first = c.next_page({}, full, path)
        self.assertEqual(urllib.parse.parse_qs(first.partition("?")[2]), {"$top": ["2"], "$skip": ["2"]})
        second = c.next_page({}, full, first)
        self.assertEqual(urllib.parse.parse_qs(second.partition("?")[2]), {"$top": ["2"], "$skip": ["4"]})
        self.assertIsNone(c.next_page({}, '{"count": 1, "value": [1]}', path))
        self.assertIsNone(c.next_page({}, full, "Demo/_apis/git/repositories/web/pullrequests"))
        self.assertIsNone(c.next_page({}, "<html>", path))
        self.assertIsNone(c.next_page({}, full, None))

    def test_join_updates_count(self):
        c = client()
        self.assertEqual(c.join([{"count": 2, "value": [1, 2]}, {"count": 1, "value": [3]}]),
                         {"count": 3, "value": [1, 2, 3]})
        self.assertEqual(c.items_key({"value": []}), "value")
        self.assertIsNone(c.items_key({"id": 1}))
        self.assertIsNone(c.items_key([1]))


CONNECTION = f"{ORG}/_apis/connectionData?api-version=7.1-preview"
USER = {"authenticatedUser": {"id": "u-1", "providerDisplayName": "Bot User",
                              "properties": {"Account": {"$type": "System.String", "$value": "me@example.com"}}}}


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


def run(argv, routes, stdin="", env=None, ctx=None):
    from cli_tools.tools.azdo import cli
    out, err = io.StringIO(), io.StringIO()
    with fake(routes) as send, mock.patch.dict(os.environ, env or {}), mock.patch("sys.stdin", stdin), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, ctx=ctx)
    return code, out.getvalue(), err.getvalue(), send.requests


class AzdoCliTests(TempConf):
    def test_login_with_token_stdin_checks_and_saves(self):
        code, out, err, _ = run(["login", "--org", "example", "--token-stdin"], {("GET", CONNECTION): (200, USER)},
                                stdin=io.StringIO("pat\n"))
        self.assertEqual((code, out), (None, ""))
        saved = credentials.read_json_file(client().AzureDevOps("example").credentials_file)
        self.assertEqual(saved, {"url": ORG, "token": "pat"})
        self.assertIn("Bot User", err)
        self.assertNotIn("pat", err)

    def test_login_rejected_saves_nothing(self):
        with self.assertRaises(ToolError):
            run(["login", "--org", "example", "--token-stdin"],
                {("GET", CONNECTION): HttpError(401, "Unauthorized")}, stdin=io.StringIO("pat\n"))
        self.assertFalse(client().AzureDevOps("example").credentials_file.exists())

    def test_login_without_terminal_fails_at_once(self):
        with self.assertRaisesRegex(ToolError, "needs a person"):
            run(["login", "--org", "example"], {}, stdin=io.StringIO(""))

    def test_create_token_prints_page_and_scopes(self):
        from cli_tools.tools.azdo import cli
        opened = []
        with mock.patch.object(cli.webbrowser, "open", opened.append), \
                mock.patch.object(cli.getpass, "getpass", lambda prompt: "pat"):
            _, _, err, _ = run(["login", "--org", "example", "--create-token", "--no-browser"],
                               {("GET", CONNECTION): (200, USER)}, stdin=FakeTerminal(""))
        self.assertIn(f"{ORG}/_usersSettings/tokens", err)
        for scope in client().TOKEN_SCOPES:
            self.assertIn(scope, err)
        self.assertEqual(opened, [])

    def test_me_json_shape(self):
        ctx = context.CLIContext(json_mode=True)
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        _, out, _, _ = run(["me"], {("GET", CONNECTION): (200, USER)}, env=env, ctx=ctx)
        self.assertEqual(json.loads(out), {"id": "u-1", "username": "me@example.com", "full_name": "Bot User",
                                           "email": "me@example.com", "url": ORG, "credentials_file": None})
        bare = {"authenticatedUser": {"id": "u-2", "providerDisplayName": "No Mail"}}
        _, out, _, _ = run(["me"], {("GET", CONNECTION): (200, bare)}, env=env, ctx=ctx)
        self.assertEqual((json.loads(out)["email"], json.loads(out)["username"]), (None, None))

    def test_api_get_all_fields(self):
        base = f"{ORG}/Demo/_apis/git/repositories/web/pullrequests?$top=2&api-version=7.1"
        routes = {("GET", base): (200, {"count": 2, "value": [{"pullRequestId": 1, "x": 0},
                                                              {"pullRequestId": 2, "x": 0}]}),
                  ("GET", f"{ORG}/Demo/_apis/git/repositories/web/pullrequests?%24top=2&%24skip=2&api-version=7.1"):
                      (200, {"count": 1, "value": [{"pullRequestId": 3, "x": 0}]})}
        _, out, _, sent = run(["GET", "Demo/_apis/git/repositories/web/pullrequests?$top=2", "--all",
                               "--fields", "pullRequestId"], routes, env={"AZDO_ORG": "example", "AZDO_TOKEN": "pat"})
        self.assertEqual(json.loads(out), {"count": 3, "value": [{"pullRequestId": 1}, {"pullRequestId": 2},
                                                                 {"pullRequestId": 3}]})
        self.assertEqual(len(sent), 2)

    def test_api_patch_work_item_sends_json_patch(self):
        url = f"{ORG}/_apis/wit/workitems/42?api-version=7.1"
        body = '[{"op": "add", "path": "/fields/System.State", "value": "Active"}]'
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        _, _, _, sent = run(["PATCH", "_apis/wit/workitems/42", "-d", body], {("PATCH", url): (200, {"id": 42})},
                            env=env)
        self.assertEqual(sent[0].get_header("Content-type"), "application/json-patch+json")
        with self.assertRaises(errors.ReadOnlyError):
            run(["PATCH", "_apis/wit/workitems/42", "-d", body], {}, env=env, ctx=context.CLIContext(read_only=True))

    def test_login_with_a_bad_token_says_so(self):
        with self.assertRaisesRegex(ToolError, "did not accept the token"):
            run(["login", "--org", "example", "--token-stdin"],
                {("GET", CONNECTION): HttpError(401, "<html><title>401</title></html>")}, stdin=io.StringIO("pat\n"))

    def test_help_examples_survive_the_shell(self):
        from cli_tools.tools.azdo import cli
        self.assertNotRegex(cli.build_parser("clt azdo").epilog, r'"[^"\n]*\$')

    def test_all_stops_when_the_server_repeats_a_page(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        base = f"{ORG}/Demo/_apis/git/repositories/web/commits?"
        same = (200, {"count": 2, "value": [{"commitId": "a"}, {"commitId": "b"}]})
        routes = {("GET", base + "$top=2&api-version=7.1"): same}
        for skip in range(2, 400, 2):
            routes[("GET", base + f"%24top=2&%24skip={skip}&api-version=7.1")] = same
        _, out, _, sent = run(["GET", "Demo/_apis/git/repositories/web/commits?$top=2", "--all"], routes, env=env)
        self.assertEqual(len(sent), 2)
        self.assertEqual(json.loads(out), {"count": 2, "value": [{"commitId": "a"}, {"commitId": "b"}]})
        projects = f"{ORG}/_apis/projects?"
        routes = {("GET", projects + "api-version=7.1"): (200, {"value": [1]}, {"x-ms-continuationtoken": "A"}),
                  ("GET", projects + "continuationToken=A&api-version=7.1"): (200, {"value": [2]},
                                                                             {"x-ms-continuationtoken": "B"}),
                  ("GET", projects + "continuationToken=B&api-version=7.1"): (200, {"value": [3]},
                                                                             {"x-ms-continuationtoken": "A"})}
        _, out, _, sent = run(["GET", "_apis/projects", "--all"], routes, env=env)
        self.assertEqual(len(sent), 3)
        self.assertEqual(json.loads(out)["value"], [1, 2, 3])

    def test_logout_and_token(self):
        client().AzureDevOps("example").save_credentials("pat")
        _, out, _, _ = run(["token"], {}, env={"AZDO_ORG": "example"})
        self.assertEqual(out, "pat\n")
        run(["logout"], {})
        self.assertEqual(credentials.read_json_file(client().AzureDevOps("example").credentials_file), {"url": ORG})

    def test_logout_keeps_the_organization(self):
        client().AzureDevOps("example").save_credentials("pat")
        _, _, err, _ = run(["logout"], {})
        self.assertIn("revoke", err)
        with self.assertRaises(NotLoggedIn):
            client().AzureDevOps().load_credentials()
        os.environ["AZURE_DEVOPS_EXT_PAT"] = "cli"
        self.assertEqual(client().AzureDevOps().load_credentials()["token"], "cli")

    def test_login_warns_when_a_token_is_in_the_environment(self):
        _, _, err, _ = run(["login", "--org", "example", "--token-stdin"], {("GET", CONNECTION): (200, USER)},
                           stdin=io.StringIO("pat\n"), env={"AZURE_DEVOPS_EXT_PAT": "other"})
        self.assertIn("AZURE_DEVOPS_EXT_PAT", err)
        self.assertNotIn("other", err.replace("AZURE_DEVOPS_EXT_PAT", ""))

    def test_me_names_the_token_variable(self):
        _, out, _, _ = run(["me"], {("GET", CONNECTION): (200, USER)},
                           env={"AZDO_ORG": "example", "AZURE_DEVOPS_EXT_PAT": "pat"})
        self.assertIn("AZURE_DEVOPS_EXT_PAT", out)

    def test_wiql_queries_then_reads_the_selected_columns_in_one_command(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        query = "SELECT [System.Id], [System.State], [System.Title], [System.AssignedTo] FROM WorkItems"
        wiql = {"columns": [{"referenceName": name} for name in
                            ("System.Id", "System.State", "System.Title", "System.AssignedTo")],
                "workItems": [{"id": 7}, {"id": 3}, {"id": 9}]}
        fields = "System.State%2CSystem.Title%2CSystem.AssignedTo"
        items = {"value": [{"id": 3, "fields": {"System.State": "Done", "System.Title": "B",
                                                "System.AssignedTo": {"displayName": "Ana", "id": "x"}}},
                           {"id": 7, "fields": {"System.State": "To Do", "System.Title": "A"}}, None]}
        routes = {("POST", f"{ORG}/Demo/_apis/wit/wiql?$top=200&api-version=7.1"): (200, wiql),
                  ("GET", f"{ORG}/_apis/wit/workitems?ids=7%2C3%2C9&fields={fields}&errorPolicy=omit"
                          "&api-version=7.1"): (200, items)}
        ctx = context.CLIContext(json_mode=True, read_only=True)
        code, out, _, sent = run(["wiql", query, "--project", "Demo"], routes, env=env, ctx=ctx)
        self.assertIsNone(code)
        self.assertEqual(json.loads(sent[0].data), {"query": query})
        self.assertEqual(json.loads(out), [
            {"id": 7, "System.State": "To Do", "System.Title": "A", "System.AssignedTo": None},
            {"id": 3, "System.State": "Done", "System.Title": "B", "System.AssignedTo": "Ana"}])
        _, out, _, _ = run(["wiql", query, "--project", "Demo"], routes, env=env)
        self.assertEqual(out.splitlines(), ["id\tSystem.State\tSystem.Title\tSystem.AssignedTo",
                                            "7\tTo Do\tA\t", "3\tDone\tB\tAna"])

    def test_wiql_with_only_ids_reads_title_and_state_in_batches_of_200(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        ids = list(range(1, 251))
        wiql = {"columns": [{"referenceName": "System.Id"}], "workItems": [{"id": i} for i in ids]}
        fields = "System.Title%2CSystem.State"

        def batch(part):
            return (("GET", f"{ORG}/_apis/wit/workitems?ids={'%2C'.join(map(str, part))}&fields={fields}"
                            "&errorPolicy=omit&api-version=7.1"),
                    (200, {"value": [{"id": i, "fields": {"System.Title": f"t{i}"}} for i in part]}))
        routes = dict([batch(ids[:200]), batch(ids[200:]),
                       (("POST", f"{ORG}/_apis/wit/wiql?$top=300&api-version=7.1"), (200, wiql))])
        _, out, _, sent = run(["wiql", "SELECT [System.Id] FROM WorkItems", "--top", "300"], routes, env=env,
                              ctx=context.CLIContext(json_mode=True))
        self.assertEqual(len(sent), 3)
        self.assertEqual([row["id"] for row in json.loads(out)], ids)

    def test_wiql_with_no_results_sends_one_request(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        routes = {("POST", f"{ORG}/_apis/wit/wiql?$top=200&api-version=7.1"):
                      (200, {"columns": [{"referenceName": "System.Id"}], "workItems": []})}
        _, out, _, sent = run(["wiql", "SELECT [System.Id] FROM WorkItems"], routes, env=env,
                              ctx=context.CLIContext(json_mode=True))
        self.assertEqual((json.loads(out), len(sent)), ([], 1))

    def test_wiql_says_when_it_stopped_at_top(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        wiql = {"columns": [{"referenceName": "System.Id"}], "workItems": [{"id": 1}, {"id": 2}]}
        routes = {("POST", f"{ORG}/_apis/wit/wiql?$top=2&api-version=7.1"): (200, wiql),
                  ("GET", f"{ORG}/_apis/wit/workitems?ids=1%2C2&fields=System.Title%2CSystem.State"
                          "&errorPolicy=omit&api-version=7.1"):
                      (200, {"value": [{"id": 1, "fields": {}}, {"id": 2, "fields": {}}]})}
        _, _, err, _ = run(["wiql", "SELECT [System.Id] FROM WorkItems", "--top", "2"], routes, env=env,
                           ctx=context.CLIContext(json_mode=True))
        self.assertIn("--top", err)

    def test_wiql_refuses_link_queries(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        routes = {("POST", f"{ORG}/_apis/wit/wiql?$top=200&api-version=7.1"):
                      (200, {"queryType": "oneHop", "workItemRelations": []})}
        with self.assertRaisesRegex(ToolError, "links"):
            run(["wiql", "SELECT [System.Id] FROM WorkItemLinks"], routes, env=env)

    def test_mine_lists_open_work_items_as_rows(self):
        env = {"AZDO_ORG": "example", "AZDO_TOKEN": "pat"}
        names = ("System.Id", "System.WorkItemType", "System.State", "System.Title", "System.TeamProject")
        wiql = {"columns": [{"referenceName": n} for n in names], "workItems": [{"id": 5}]}
        fields = "%2C".join(names[1:])
        routes = {("POST", f"{ORG}/Demo/_apis/wit/wiql?$top=50&api-version=7.1"): (200, wiql),
                  ("GET", f"{ORG}/_apis/wit/workitems?ids=5&fields={fields}&errorPolicy=omit&api-version=7.1"):
                      (200, {"value": [{"id": 5, "fields": {"System.WorkItemType": "Bug", "System.State": "Active",
                                                            "System.Title": "Fix it", "System.TeamProject": "Demo"}}]})}
        ctx = context.CLIContext(json_mode=True, read_only=True)
        _, out, _, sent = run(["mine", "--project", "Demo", "--type", "O'Bug"], routes, env=env, ctx=ctx)
        query = json.loads(sent[0].data)["query"]
        self.assertIn("[System.AssignedTo] = @Me", query)
        self.assertIn("[System.WorkItemType] = 'O''Bug'", query)
        self.assertIn("[System.TeamProject] = @project", query)
        self.assertEqual(json.loads(out), [{"kind": "Bug", "id": 5, "state": "Active", "title": "Fix it",
                                            "where": "Demo"}])
        _, out, _, _ = run(["mine", "--project", "Demo", "--table"], routes, env=env, ctx=ctx)
        self.assertEqual(out.splitlines(), ["kind\tid\tstate\ttitle\twhere", "Bug\t5\tActive\tFix it\tDemo"])

    def test_read_only_blocks_session_commands(self):
        ctx = context.CLIContext(read_only=True)
        for argv in (["login", "--org", "example"], ["logout"], ["token"]):
            with self.subTest(argv=argv), self.assertRaises(errors.ReadOnlyError):
                run(argv, {}, env={"AZDO_ORG": "example"}, ctx=ctx)
