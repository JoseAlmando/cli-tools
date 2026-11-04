import base64
import json
import os
import urllib.parse
from unittest import mock

from cli_tools.core import credentials, http
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
