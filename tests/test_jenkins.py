import base64
import json
import os
from pathlib import Path
from unittest import mock

from tests.helpers import TempConf
from tests.test_contracts import run_inline

JENKINS = "https://jenkins.example.com:8443/ci"
WHO = "/whoAmI/api/json"


class JenkinsTests(TempConf):
    def setUp(self):
        super().setUp()
        os.environ["JENKINS_URL"] = JENKINS
        self.saved = Path(os.environ["JENKINS_CONF_DIR"]) / "credentials.json"

    def run_jenkins(self, *argv, routes=None, stdin=""):
        return run_inline("clt", "jenkins", "--json", *argv, routes=routes, stdin=stdin)

    def test_anonymous_jobs_at_root_with_api_page_as_configured_url(self):
        os.environ["JENKINS_URL"] += "/api/"
        url = f"{JENKINS}/api/json?tree=jobs%5Bname%2Curl%2Ccolor%5D"
        data = {"name": "Compile", "url": f"{JENKINS}/job/Compile/", "color": "blue"}
        code, out, err, requests = self.run_jenkins("--read-only", "jobs", routes={
            ("GET", url): (200, json.dumps({"jobs": [data]}), {}),
        })
        self.assertEqual((code, json.loads(out), err), (0, [data], ""))
        self.assertIsNone(requests[0].get_header("Authorization"))

    def test_nested_job_build_and_console_use_encoded_path_segments(self):
        route = f"{JENKINS}/job/Release%20Jobs/job/Feature%20Build"
        build = {"number": 12, "building": False, "result": "FAILURE"}
        code, out, err, _ = self.run_jenkins("--read-only", "build", "Release Jobs/Feature Build", routes={
            ("GET", f"{route}/lastBuild/api/json"): (200, json.dumps(build), {}),
        })
        self.assertEqual((code, json.loads(out), err), (0, build, ""))
        self.assertEqual(self.run_jenkins("--read-only", "log", "Release Jobs/Feature Build", "12", routes={
            ("GET", f"{route}/12/consoleText"): (200, "line one\nline two\n", {}),
        })[:3], (0, "line one\nline two\n", ""))

    def test_queue_can_be_read_as_a_table(self):
        url = f"{JENKINS}/queue/api/json?tree=items%5Bid%2Cwhy%2Ctask%5Bname%2Curl%5D%5D"
        data = {"items": [{"id": 5, "why": "Waiting", "task": {"name": "Compile", "url": "job/Compile/"}}]}
        self.assertEqual(self.run_jenkins("--read-only", "queue", "--table", routes={
            ("GET", url): (200, json.dumps(data), {}),
        })[:3], (0, "id\tname\twhy\turl\n5\tCompile\tWaiting\tjob/Compile/\n", ""))

    def test_http_requires_opt_in_and_uses_basic_auth_only_for_configured_server(self):
        url = "http://jenkins.example.com:8080"
        with mock.patch.dict(os.environ, JENKINS_URL=url, JENKINS_USER="reader", JENKINS_TOKEN="example-token"):
            code, _, err, requests = self.run_jenkins("--read-only", "GET", "api/json")
            self.assertEqual((code, requests), (1, []))
            self.assertIn("JENKINS_ALLOW_HTTP", err)
            os.environ["JENKINS_ALLOW_HTTP"] = "1"
            code, out, err, requests = self.run_jenkins("--read-only", "GET", "/api/json", routes={
                ("GET", f"{url}/api/json"): (200, '{"jobs": []}', {}),
            })
            self.assertEqual((code, json.loads(out), err), (0, {"jobs": []}, ""))
            self.assertEqual(requests[0].get_header("Authorization"),
                             "Basic " + base64.b64encode(b"reader:example-token").decode())

    def test_login_checks_token_before_saving_and_never_uses_session_on_another_server(self):
        route = ("GET", JENKINS + WHO)
        code, out, err, _ = self.run_jenkins("login", "--user", "reader", "--token-stdin", stdin="example-token\n",
                                             routes={route: (200, '{"authenticated":false,"name":"anonymous"}', {})})
        self.assertEqual((code, self.saved.exists()), (1, False))
        self.assertNotIn("example-token", out + err)

        code, out, err, _ = self.run_jenkins("login", "--user", "reader", "--token-stdin", stdin="example-token\n",
                                             routes={route: (200, '{"authenticated":true,"name":"reader"}', {})})
        self.assertEqual((code, self.saved.exists()), (0, True))
        self.assertNotIn("example-token", out + err)
        del os.environ["JENKINS_URL"]
        code, out, err, requests = self.run_jenkins("--read-only", "me", routes={
            route: (200, '{"authenticated":true,"name":"reader"}', {}),
        })
        self.assertEqual((code, json.loads(out)["username"], err), (0, "reader", ""))
        self.assertTrue(requests[0].get_header("Authorization"))
        os.environ["JENKINS_URL"] = "https://other.example.com"
        code, _, err, requests = self.run_jenkins("--read-only", "GET", "api/json")
        self.assertEqual((code, requests), (1, []))
        self.assertIn("saved session", err)

    def test_read_only_blocks_api_writes_and_login_without_accessing_the_server(self):
        del os.environ["JENKINS_URL"]
        for args in (("POST", "job/Compile/build"), ("login", "--user", "reader"), ("logout",)):
            with self.subTest(args=args):
                code, out, _, requests = self.run_jenkins("--read-only", *args)
                self.assertEqual((code, out, requests), (4, "", []))

    def test_api_dry_run_does_not_need_a_server(self):
        del os.environ["JENKINS_URL"]
        code, out, err, requests = self.run_jenkins("POST", "job/Compile/build", "--dry-run")
        self.assertEqual((code, json.loads(out), err, requests),
                         (0, {"method": "POST", "path": "job/Compile/build", "body": None}, "", []))

    def test_paths_cannot_switch_server_or_leave_context_path(self):
        for path in ("https://other.example.com/api/json", "//other.example.com/api/json", "../api/json",
                     "%2e%2e/api/json", "job/%2e%2e%2fapi/json"):
            with self.subTest(path=path):
                code, _, _, requests = self.run_jenkins("--read-only", "GET", path)
                self.assertEqual((code, requests), (1, []))

