import argparse
import contextlib
import io
import json
import unittest
import urllib.parse

from cli_tools.core import api, context, output
from cli_tools.core.errors import ToolError


class FakeClient:
    """A client whose call() answers from routes {(method, path): (body, headers)} and records the paths."""

    def __init__(self, routes):
        self.routes = routes
        self.paths = []

    def call(self, method, path, body):
        self.paths.append(path)
        if (method, path) not in self.routes:
            raise AssertionError(f"unexpected request {(method, path)}")
        body, *headers = self.routes[(method, path)]
        return 200, json.dumps(body), headers[0] if headers else {}


def mine(cmd, client, json_mode=True, table=False, limit=50, **extra):
    args = argparse.Namespace(limit=limit, table=table, ctx=context.CLIContext(json_mode=json_mode), **extra)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cmd(client, args)
    return out.getvalue()


ROW = ("kind", "id", "state", "title", "where")


class TableTests(unittest.TestCase):
    def test_cells_are_flat_and_tabs_never_split_a_cell(self):
        self.assertEqual(output.table([{"a": 1, "b": None}, {"b": {"x": [1]}, "c": "two\tthree\nfour"},
                                       {"a": True}]),
                         "a\tb\tc\n1\t\t\n\t{\"x\":[1]}\ttwo three four\ntrue\t\t")

    def test_print_rows_json_only_without_table(self):
        rows = [{"id": 1}]
        for json_mode, table, expected in ((True, False, '[{"id": 1}]\n'), (True, True, "id\n1\n"),
                                           (False, False, "id\n1\n")):
            out = io.StringIO()
            with self.subTest(json_mode=json_mode, table=table), contextlib.redirect_stdout(out):
                output.print_rows(rows, table, json_mode)
                self.assertEqual(out.getvalue(), expected)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            output.print_rows([], True, True)
        self.assertEqual(out.getvalue(), "")


class ApiTableTests(unittest.TestCase):
    def run_api(self, argv, routes, items_key=None):
        parser = argparse.ArgumentParser()
        api.add_api_parser(parser.add_subparsers(dest="command"), "clt x", "x", None)
        args = parser.parse_args(["api"] + argv)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api.run_api(args, FakeClient(routes).call, context.CLIContext(), items_key=items_key)
        return out.getvalue()

    def test_table_prints_a_list_or_an_enveloped_list(self):
        routes = {("GET", "a"): ([{"id": 1, "n": "x"}, {"id": 2, "n": "y"}],),
                  ("GET", "b"): ({"count": 1, "value": [{"id": 3, "n": "z"}]},)}
        self.assertEqual(self.run_api(["GET", "a", "--table", "--fields", "n"], routes), "n\nx\ny\n")
        self.assertEqual(self.run_api(["GET", "b", "--table"], routes, lambda d: "value"), "id\tn\n3\tz\n")

    def test_table_leaves_a_single_object_as_json(self):
        out = self.run_api(["GET", "a", "--table"], {("GET", "a"): ({"id": 1},)})
        self.assertEqual(json.loads(out), {"id": 1})


class FetchListTests(unittest.TestCase):
    def test_follows_pages_until_the_limit(self):
        routes = {("GET", "l?page=1"): ([1, 2], {"x-pagination-next": "https://h/api/v1/l?page=2"}),
                  ("GET", "l?page=2"): ([3, 4], {"x-pagination-next": "https://h/api/v1/l?page=3"})}
        client = FakeClient(routes)
        self.assertEqual(api.fetch_list(client.call, "l?page=1", 3), [1, 2, 3])
        self.assertEqual(client.paths, ["l?page=1", "l?page=2"])

    def test_refuses_an_answer_that_is_not_a_list(self):
        with self.assertRaises(ToolError):
            api.fetch_list(FakeClient({("GET", "l"): ({"id": 1},)}).call, "l", 5)


class GitHubMineTests(unittest.TestCase):
    def test_assigned_issues_and_pull_requests(self):
        from cli_tools.tools.github import cli
        items = [{"number": 4, "title": "Bug", "state": "open", "repository": {"full_name": "o/r"}},
                 {"number": 5, "title": "PR", "state": "open", "pull_request": {"url": "u"},
                  "repository": {"full_name": "o/s"}}]
        client = FakeClient({("GET", "issues?filter=assigned&state=open&per_page=50"): (items,)})
        self.assertEqual(json.loads(mine(cli.cmd_mine, client)),
                         [dict(zip(ROW, ("issue", 4, "open", "Bug", "o/r"))),
                          dict(zip(ROW, ("pr", 5, "open", "PR", "o/s")))])


class GiteaMineTests(unittest.TestCase):
    def test_assigned_issues_and_pull_requests(self):
        from cli_tools.tools.gitea import cli
        items = [{"number": 2, "title": "Fix", "state": "open", "pull_request": {"merged": False},
                  "repository": {"full_name": "team/app"}}]
        client = FakeClient({("GET", "repos/issues/search?state=open&assigned=true&limit=10"): (items,)})
        self.assertEqual(mine(cli.cmd_mine, client, json_mode=False, limit=10),
                         "kind\tid\tstate\ttitle\twhere\npr\t2\topen\tFix\tteam/app\n")


class TaigaMineTests(unittest.TestCase):
    def test_stories_tasks_and_issues_assigned_to_me(self):
        from cli_tools.tools.taiga import cli

        def item(ref, name):
            return {"ref": ref, "subject": name, "status_extra_info": {"name": "New"},
                    "project_extra_info": {"slug": "demo"}}
        open_items = "assigned_to=9&status__is_closed=false"
        client = FakeClient({("GET", "users/me"): ({"id": 9},),
                             ("GET", "userstories?assigned_users=9&status__is_closed=false"): ([item(1, "Story")],),
                             ("GET", f"tasks?{open_items}"): ([item(2, "Task")],),
                             ("GET", f"issues?{open_items}"): ([item(3, "Issue")],)})
        self.assertEqual([(r["kind"], r["id"], r["where"]) for r in json.loads(mine(cli.cmd_mine, client))],
                         [("story", 1, "demo"), ("task", 2, "demo"), ("issue", 3, "demo")])

    def test_stops_asking_once_the_limit_is_reached(self):
        from cli_tools.tools.taiga import cli
        client = FakeClient({("GET", "users/me"): ({"id": 9},),
                             ("GET", "userstories?assigned_users=9&status__is_closed=false"): ([{"ref": 1}, {"ref": 2}],)})
        self.assertEqual(len(json.loads(mine(cli.cmd_mine, client, limit=2))), 2)
        self.assertEqual(client.paths, ["users/me", "userstories?assigned_users=9&status__is_closed=false"])


class JiraMineTests(unittest.TestCase):
    def test_open_tickets_with_a_project_filter(self):
        from cli_tools.tools.jira import cli
        jql = urllib.parse.quote('project = "A\\"B" AND ' + cli.MINE_JQL)
        path = f"search/jql?jql={jql}&fields=summary,status,issuetype,project&maxResults=50"
        issues = {"isLast": True, "issues": [{"key": "AB-1", "fields": {
            "summary": "Do it", "status": {"name": "In Progress"}, "issuetype": {"name": "Story"},
            "project": {"key": "AB"}}}]}
        out = mine(cli.cmd_mine, FakeClient({("GET", path): (issues,)}), project='A"B')
        self.assertEqual(json.loads(out), [dict(zip(ROW, ("Story", "AB-1", "In Progress", "Do it", "AB")))])


if __name__ == "__main__":
    unittest.main()
