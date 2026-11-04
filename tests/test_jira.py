import contextlib
import io
import json
import os
import unittest
from unittest import mock

from cli_tools.core import context, credentials, errors, http
from cli_tools.core.errors import HttpError, ToolError
from cli_tools.tools.jira import adf, cli, client
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

    def test_tenant_info_unusable_says_the_token_was_rejected(self):
        routes = {("GET", SITE_API + "myself"): unauthorized(), ("GET", TENANT): HttpError(404, "Not Found")}
        with fake(routes), self.assertRaisesRegex(ToolError, "rejected the token on the site"):
            Jira(SITE).detect("me@example.com", "tok")

    def test_dot_segments_cannot_leave_the_api(self):
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok"}
        for path in ("../../../wiki/rest/api/space", "issue/../../x", "%2e%2e/x", "issue/%2E%2E/x", "./myself",
                     "issue/A-1/..?x=1"):
            with self.subTest(path=path), mock.patch.dict(os.environ, env), fake({}) as send, \
                    self.assertRaises(ToolError):
                Jira().call("GET", path, None)
            self.assertEqual(send.requests, [])

    def test_the_query_typed_by_the_user_is_encoded(self):
        env = {"JIRA_URL": SITE, "JIRA_EMAIL": "me@example.com", "JIRA_TOKEN": "tok"}
        path = 'search/jql?jql=assignee=currentUser() AND summary ~ "a#b"&fields=key'
        expected = SITE_API + "search/jql?jql=assignee=currentUser()%20AND%20summary%20~%20%22a%23b%22&fields=key"
        with mock.patch.dict(os.environ, env), fake({("GET", expected): (200, {"issues": []})}) as send:
            Jira().call("GET", path, None)
        self.assertEqual(send.requests[0].full_url, expected)

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

    def test_the_recipes_in_the_reference_run(self):
        recipe = ("search/jql?jql=assignee=currentUser() AND statusCategory!=Done ORDER BY updated DESC"
                  "&fields=key,summary,status")
        url = SITE_API + ("search/jql?jql=assignee=currentUser()%20AND%20statusCategory!=Done%20ORDER%20BY%20updated"
                          "%20DESC&fields=key,summary,status")
        code, out, _, sent = run(["GET", recipe, "--fields", "key", "--all"],
                                 {("GET", url): page({"issues": [{"key": "A-1"}], "isLast": True})},
                                 env={"JIRA_URL": SITE})
        self.assertEqual(json.loads(out), {"issues": [{"key": "A-1"}], "isLast": True})

    def test_max_items_stops_paging_once_it_has_enough(self):
        base = SITE_API + "search/jql?jql=x"
        routes = {("GET", base): page({"issues": [{"key": "A-1"}, {"key": "A-2"}], "nextPageToken": "t1"}),
                  ("GET", base + "&nextPageToken=t1"): page({"issues": [{"key": "A-3"}], "nextPageToken": "t2"}),
                  ("GET", base + "&nextPageToken=t2"): page({"issues": [{"key": "A-4"}], "isLast": True})}
        _, out, err, sent = run(["GET", "search/jql?jql=x", "--all", "--max-items", "3"], routes,
                                env={"JIRA_URL": SITE})
        self.assertEqual([i["key"] for i in json.loads(out)["issues"]], ["A-1", "A-2", "A-3"])
        self.assertEqual(len(sent), 2)
        self.assertIn("there may be more", err)

    def test_fields_reach_issues(self):
        body = {"issues": [{"key": "A-1", "id": "1", "fields": {"status": {"name": "Done"}, "summary": "x"}}]}
        _, out, _, _ = run(["GET", "search/jql?jql=x", "--fields", "key,fields.status.name"],
                           {("GET", SITE_API + "search/jql?jql=x"): page(body)}, env={"JIRA_URL": SITE})
        self.assertEqual(json.loads(out), {"issues": [{"key": "A-1", "fields.status.name": "Done"}]})


def doc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def para(*content):
    return {"type": "paragraph", "content": list(content)}


def text(value, **mark):
    node = {"type": "text", "text": value}
    if mark:
        node["marks"] = [{"type": "link", "attrs": mark}]
    return node


def bullet(*items, ordered=False):
    return {"type": "orderedList" if ordered else "bulletList",
            "content": [{"type": "listItem", "content": list(item)} for item in items]}


class AdfTests(unittest.TestCase):
    def test_none_and_empty_doc(self):
        self.assertEqual(adf.to_text(None), "")
        self.assertEqual(adf.to_text(doc()), "")
        self.assertEqual(adf.to_text("plain"), "plain")

    def test_paragraphs_are_separated_by_a_blank_line(self):
        self.assertEqual(adf.to_text(doc(para(text("A")), para(text("B")))), "A\n\nB")

    def test_heading(self):
        self.assertEqual(adf.to_text(doc({"type": "heading", "attrs": {"level": 2}, "content": [text("Title")]})),
                         "## Title")

    def test_lists_and_nesting(self):
        self.assertEqual(adf.to_text(doc(bullet([para(text("one"))], [para(text("two"))]))), "- one\n- two")
        self.assertEqual(adf.to_text(doc(bullet([para(text("one"))], [para(text("two"))], ordered=True))),
                         "1. one\n2. two")
        nested = bullet([para(text("a")), bullet([para(text("b"))])])
        self.assertEqual(adf.to_text(doc(nested)), "- a\n  - b")

    def test_ordered_list_keeps_its_first_number(self):
        node = bullet([para(text("a"))], [para(text("b"))], ordered=True)
        node["attrs"] = {"order": 4}
        self.assertEqual(adf.to_text(doc(node)), "4. a\n5. b")
        node["attrs"] = {"order": "x"}
        self.assertEqual(adf.to_text(doc(node)), "1. a\n2. b")

    def test_marks_become_markdown(self):
        def marked(value, *kinds, href=None):
            marks = [{"type": kind} for kind in kinds]
            if href:
                marks.append({"type": "link", "attrs": {"href": href}})
            return {"type": "text", "text": value, "marks": marks}
        inline = para(marked("bold", "strong"), text(" "), marked("it", "em"), text(" "),
                      marked("of type 1", "strike"), text(" "), marked("f()", "code"), text(" "),
                      marked("both", "strong", "em"), text(" "), marked("under", "underline"), text(" a"),
                      marked(" pad ", "strong"), text("b "), marked("site", "strong", href="https://example.com"))
        self.assertEqual(adf.to_text(doc(inline)),
                         "**bold** *it* ~~of type 1~~ `f()` ***both*** under a **pad** b [**site**](https://example.com)")

    def test_code_block(self):
        node = {"type": "codeBlock", "attrs": {"language": "python"}, "content": [text("x = 1")]}
        self.assertEqual(adf.to_text(doc(node)), "```python\nx = 1\n```")

    def test_blockquote_and_rule(self):
        self.assertEqual(adf.to_text(doc({"type": "blockquote", "content": [para(text("q"))]}, {"type": "rule"})),
                         "> q\n\n---")

    def test_inline_nodes(self):
        inline = para(text("a"), {"type": "hardBreak"}, text("b "),
                      {"type": "mention", "attrs": {"text": "@Ana"}}, text(" "),
                      {"type": "mention", "attrs": {"text": "Luis"}}, text(" "),
                      text("site", href="https://example.com"), text(" "),
                      {"type": "inlineCard", "attrs": {"url": "https://example.com/x"}}, text(" "),
                      {"type": "emoji", "attrs": {"shortName": ":smile:", "text": "😀"}},
                      {"type": "emoji", "attrs": {"shortName": ":wave:"}})
        self.assertEqual(adf.to_text(doc(inline)),
                         "a\nb @Ana @Luis [site](https://example.com) https://example.com/x 😀:wave:")

    def test_table_rows(self):
        def row(*cells):
            return {"type": "tableRow", "content": [{"type": "tableCell", "content": [para(text(c))]} for c in cells]}
        self.assertEqual(adf.to_text(doc({"type": "table", "content": [row("a", "b"), row("c|x", "d")]})),
                         "| a | b |\n| --- | --- |\n| c\\|x | d |")
        self.assertEqual(adf.to_text(doc({"type": "table", "content": [row("a", "b"), row("c")]})),
                         "| a | b |\n| --- | --- |\n| c |  |")

    def test_cards_and_media_leave_a_trace(self):
        node = doc({"type": "blockCard", "attrs": {"url": "https://example.com/a"}},
                   {"type": "embedCard", "attrs": {"url": "https://example.com/b", "layout": "center"}},
                   {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "1", "alt": "shot.png"}}]},
                   {"type": "mediaGroup", "content": [{"type": "media", "attrs": {"id": "2"}}]})
        self.assertEqual(adf.to_text(node),
                         "https://example.com/a\n\nhttps://example.com/b\n\n[image: shot.png]\n\n[attachment]")

    def test_unknown_nodes_keep_their_text(self):
        node = doc({"type": "panel", "attrs": {"panelType": "info"}, "content": [para(text("Note"))]},
                   para({"type": "status", "attrs": {"text": "DONE"}}))
        result = adf.to_text(node)
        self.assertIn("Note", result)
        self.assertIn("DONE", result)


ISSUE_FIELDS = "summary,status,issuetype,priority,assignee,reporter,labels,parent,description,subtasks,issuelinks,comment"
ISSUE_URL = SITE_API + "issue/A-1?fields=" + ISSUE_FIELDS


def comment(n):
    return {"author": {"displayName": f"User {n}"}, "created": f"2026-11-0{n % 9 + 1}T10:00:00.000+0000",
            "body": doc(para(text(f"comment {n}")))}


FULL_ISSUE = {"key": "A-1", "fields": {
    "summary": "Fix login", "status": {"name": "In Progress"}, "issuetype": {"name": "Story"},
    "priority": {"name": "High"}, "assignee": {"displayName": "Ana"}, "reporter": {"displayName": "Luis"},
    "labels": ["auth"], "parent": {"key": "A-0"}, "description": doc(para(text("It fails"))),
    "subtasks": [{"key": "A-2", "fields": {"summary": "Write test", "status": {"name": "To Do"}}}],
    "issuelinks": [
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
         "outwardIssue": {"key": "A-9", "fields": {"summary": "Release", "status": {"name": "Done"}}}},
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
         "inwardIssue": {"key": "A-8", "fields": {"summary": "Design", "status": {"name": "Done"}}}}],
    "comment": {"comments": [comment(1)], "total": 1}}}


class IssueTests(TempConf):
    def setUp(self):
        super().setUp()
        save_site_session()

    def issue(self, argv, routes):
        ctx = context.CLIContext(json_mode=True)
        out = io.StringIO()
        with fake(routes) as send, mock.patch.dict(os.environ, {"JIRA_URL": SITE}), contextlib.redirect_stdout(out):
            cli.main(["issue", *argv], ctx=ctx)
        return json.loads(out.getvalue()), send.requests

    def test_issue_json_shape(self):
        result, sent = self.issue(["A-1"], {("GET", ISSUE_URL): (200, FULL_ISSUE)})
        self.assertEqual(result, {
            "key": "A-1", "summary": "Fix login", "type": "Story", "status": "In Progress", "priority": "High",
            "assignee": "Ana", "reporter": "Luis", "labels": ["auth"], "parent": "A-0", "description": "It fails",
            "subtasks": [{"key": "A-2", "summary": "Write test", "status": "To Do"}],
            "links": [{"type": "blocks", "key": "A-9", "summary": "Release", "status": "Done"},
                      {"type": "is blocked by", "key": "A-8", "summary": "Design", "status": "Done"}],
            "comments": [{"author": "User 1", "created": "2026-11-02T10:00:00.000+0000", "body": "comment 1"}],
            "url": SITE + "/browse/A-1"})
        self.assertEqual(len(sent), 1)

    def test_issue_with_nothing_set(self):
        bare = {"key": "A-1", "fields": {"summary": "Bare", "status": {"name": "To Do"}, "issuetype": {"name": "Task"},
                                         "priority": None, "assignee": None, "reporter": None, "labels": [],
                                         "parent": None, "description": None, "subtasks": [], "issuelinks": [],
                                         "comment": {"comments": [], "total": 0}}}
        result, sent = self.issue(["A-1"], {("GET", ISSUE_URL): (200, bare)})
        self.assertEqual((result["priority"], result["assignee"], result["parent"], result["description"]),
                         (None, None, None, None))
        self.assertEqual((result["subtasks"], result["links"], result["comments"]), ([], [], []))
        self.assertEqual(len(sent), 1)

    def test_more_comments_are_fetched(self):
        issue = json.loads(json.dumps(FULL_ISSUE))
        issue["fields"]["comment"] = {"comments": [comment(n) for n in range(1, 6)], "total": 12}
        more = SITE_API + "issue/A-1/comment?startAt=9&maxResults=3"
        routes = {("GET", ISSUE_URL): (200, issue),
                  ("GET", more): (200, {"comments": [comment(n) for n in (10, 11, 12)], "total": 12})}
        result, sent = self.issue(["A-1", "--comments", "3"], routes)
        self.assertEqual([c["body"] for c in result["comments"]], ["comment 10", "comment 11", "comment 12"])
        self.assertEqual(sent[1].full_url, more)

    def test_comments_zero(self):
        result, sent = self.issue(["A-1", "--comments", "0"], {("GET", ISSUE_URL): (200, FULL_ISSUE)})
        self.assertEqual(result["comments"], [])
        self.assertEqual(len(sent), 1)

    def test_url_uses_the_site_in_scoped_mode(self):
        Jira(SITE).save_credentials({"url": SITE, "email": "me@example.com", "token": "tok", "mode": "scoped",
                                     "cloud_id": CLOUD})
        result, sent = self.issue(["A-1"], {("GET", GATEWAY_API + "issue/A-1?fields=" + ISSUE_FIELDS): (200, FULL_ISSUE)})
        self.assertEqual(result["url"], SITE + "/browse/A-1")

    def test_issue_text_mode_mentions_summary_status_and_description(self):
        out = io.StringIO()
        with fake({("GET", ISSUE_URL): (200, FULL_ISSUE)}), mock.patch.dict(os.environ, {"JIRA_URL": SITE}), \
                contextlib.redirect_stdout(out):
            cli.main(["issue", "A-1"], ctx=context.CLIContext())
        for expected in ("A-1", "Fix login", "In Progress", "It fails", "comment 1", "A-9"):
            self.assertIn(expected, out.getvalue())


if __name__ == "__main__":
    unittest.main()
