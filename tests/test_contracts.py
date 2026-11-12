"""The public contract of the commands, through the entry points declared in pyproject.toml, with no network.

These tests only use the console scripts (module:function) and the standard library, so they hold whatever the
package looks like inside.
"""
import contextlib
import importlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.helpers import TempConf

ROOT = Path(__file__).resolve().parents[1]
TAIGA = "https://taiga.example.com"
GITEA = "https://gitea.example.com"
GITHUB = "https://api.github.com"
JIRA = "https://example.atlassian.net"
AZDO = "https://dev.azure.com/example"
JENKINS = "https://jenkins.example.com"

CLT_HELP = """\
usage: clt TOOL [arguments]

tools:
  taiga, t     client for the Taiga API
  gitea, gt    client for the Gitea API, with OAuth2 login in the browser
  github, gh   client for the GitHub API, with a browser login or a token
  jira, j      client for the Jira Cloud API, with an API token
  azdo, ad     client for the Azure DevOps Services API, with a personal access token
  jenkins      client for the Jenkins API, including jobs, builds, queue and console logs
  release      bump the version, update the changelog, tag and publish a release
  update       install the latest release of clt, or a given version
  skill        install clt's skill for Claude Code, Codex or Gemini CLI

Each tool has its own help: clt taiga --help, clt github --help.
Short names for commands: login lg, refresh rfh, logout lo, token tk, issue i.

for scripts and AI agents (any position on the command line):
  --json        errors as one JSON line on stderr, and me as JSON (also CLT_JSON=1)
  --read-only   send only GET requests (also CLT_READONLY=1);
                login, refresh, logout and token are blocked too
  exit codes    0 success, 1 error, 2 wrong usage, 3 not logged in, 4 blocked by read-only mode
"""
ERROR_KEYS = {"error", "type", "exit_code", "status"}
ME_KEYS = {"id", "username", "full_name", "email", "url", "credentials_file"}


def _pyproject():
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def console_scripts():
    """{name: (module, function)} from [project.scripts]."""
    table = re.search(r"^\[project\.scripts\][ \t]*$(.*?)(?=^\[|\Z)", _pyproject(), re.M | re.S).group(1)
    return {name: tuple(target.split(":")) for name, target in re.findall(r'^(\w+)\s*=\s*"([^"]+)"', table, re.M)}


SCRIPTS = console_scripts()


def run_process(script, *argv, env=None, stdin=""):
    """Runs a console script in a new process, as the installed command would."""
    module, function = SCRIPTS[script]
    code = f"import importlib, sys; sys.exit(getattr(importlib.import_module({module!r}), {function!r})())"
    environ = {**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})}
    done = subprocess.run([sys.executable, "-c", code, *argv], env=environ, input=stdin, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    return done.returncode, done.stdout, done.stderr


class FakeServer:
    """Answers every HTTP request urllib would send, by (method, URL); records them all."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, req):
        self.requests.append(req)
        key = (req.get_method(), req.full_url)
        if key not in self.routes:
            raise AssertionError(f"unexpected request {key}")
        answer = self.routes[key]
        status, body, headers = answer(req) if callable(answer) else answer
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", headers, io.BytesIO(body.encode("utf-8")))
        response = io.BytesIO(body.encode("utf-8"))
        response.status, response.headers = status, headers
        return response


def run_inline(script, *argv, routes=None, stdin=""):
    """Runs a console script's function in this process, with a fake server instead of the network."""
    module, function = SCRIPTS[script]
    entry = getattr(importlib.import_module(module), function)
    server = FakeServer(routes or {})
    out, err = io.StringIO(), io.StringIO()

    def fake_open(opener, req, data=None, timeout=None):
        return server.open(req)

    with mock.patch.object(urllib.request.OpenerDirector, "open", fake_open), \
            mock.patch.object(sys, "argv", [script, *argv]), mock.patch("sys.stdin", io.StringIO(stdin)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = entry()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue(), server.requests


def json_error(stderr):
    return json.loads(stderr.strip().splitlines()[-1])


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class EntryPointTests(unittest.TestCase):
    def test_clt_is_the_only_command(self):
        self.assertEqual(sorted(SCRIPTS), ["clt"])

    def test_every_entry_point_resolves_to_a_function(self):
        for name, (module, function) in SCRIPTS.items():
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(importlib.import_module(module), function)))

    def test_python_dash_m_runs_clt(self):
        done = subprocess.run([sys.executable, "-m", "cli_tools", "--version"], capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=60)
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith("clt "))


class HelpAndVersionTests(TempConf):
    def test_clt_help_lists_every_tool(self):
        self.assertEqual(run_process("clt", "--help"), (0, CLT_HELP, ""))
        self.assertEqual(run_process("clt", "help")[:2], (0, CLT_HELP))

    def test_clt_without_arguments_shows_the_usage_and_fails(self):
        self.assertEqual(run_process("clt")[:2], (2, CLT_HELP))

    def test_clt_version(self):
        version = re.search(r'^version = "([^"]+)"', _pyproject(), re.M).group(1)
        for flag in ("--version", "-V"):
            with self.subTest(flag=flag):
                self.assertEqual(run_process("clt", flag), (0, f"clt {version}\n", ""))

    def test_each_command_names_itself_in_its_usage(self):
        cases = [("clt", ["taiga"], "usage: clt taiga "), ("clt", ["gitea"], "usage: clt gitea "),
                 ("clt", ["github"], "usage: clt github "), ("clt", ["jira"], "usage: clt jira "),
                 ("clt", ["azdo"], "usage: clt azdo "), ("clt", ["jenkins"], "usage: clt jenkins "),
                 ("clt", ["release"], "usage: clt release "),
                 ("clt", ["update"], "usage: clt update "), ("clt", ["t"], "usage: clt taiga "),
                 ("clt", ["gt"], "usage: clt gitea "), ("clt", ["gh"], "usage: clt github "),
                 ("clt", ["j"], "usage: clt jira "), ("clt", ["j", "i"], "usage: clt jira issue "),
                 ("clt", ["ad"], "usage: clt azdo "), ("clt", ["skill"], "usage: clt skill ")]
        for script, argv, usage in cases:
            with self.subTest(script=script, argv=argv):
                code, out, _ = run_process(script, *argv, "--help")
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith(usage), out[:80])

    def test_the_api_clients_list_their_commands(self):
        for script, argv, commands in (("clt", ["taiga"], "login refresh logout me token mine api"),
                                       ("clt", ["gitea"], "login refresh logout me token mine api"),
                                       ("clt", ["github"], "login refresh logout me token protect mine api"),
                                       ("clt", ["jira"], "login logout me token issue mine mcp api"),
                                       ("clt", ["azdo"], "login logout me token mcp wiql mine api"),
                                       ("clt", ["jenkins"], "login logout me jobs queue build log api")):
            with self.subTest(script=script):
                out = run_process(script, *argv, "--help")[1]
                for command in commands.split():
                    self.assertRegex(out, rf"(?m)^    {command} ")

    def test_a_tool_without_a_command_shows_its_help_and_fails(self):
        code, out, _ = run_process("clt", "taiga")
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("usage: clt taiga "))


class AliasTests(TempConf):
    def test_aliases_of_session_commands_are_blocked_by_read_only(self):
        env = {"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "secret-taiga", "GITEA_URL": GITEA, "GITEA_TOKEN": "secret-gitea",
               "GITHUB_TOKEN": "secret-github", "JIRA_URL": JIRA, "JIRA_EMAIL": "e@example.com",
               "JIRA_TOKEN": "secret-jira", "AZDO_ORG": "example", "AZDO_TOKEN": "secret-azdo"}
        for argv in (["t", "tk"], ["taiga", "rfh"], ["gt", "lo"], ["gitea", "lg"], ["gh", "tk"], ["github", "rfh"],
                     ["j", "tk"], ["jira", "lo"], ["ad", "tk"], ["azdo", "lo"], ["azdo", "lg"]):
            with self.subTest(argv=argv):
                code, out, err = run_process("clt", "--read-only", *argv, env=env)
                self.assertEqual((code, out), (4, ""))
                self.assertNotIn("secret", err)

    def test_an_alias_runs_its_command(self):
        code, out, _ = run_process("clt", "t", "tk", env={"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "tok"})
        self.assertEqual((code, out), (0, "tok\n"))

    def test_messages_name_clt(self):
        for argv, env, login in ((["taiga", "me"], {"TAIGA_URL": TAIGA}, "clt taiga login"),
                                 (["gt", "me"], {"GITEA_URL": GITEA}, "clt gitea login")):
            with self.subTest(argv=argv):
                code, _, err = run_process("clt", *argv, env=env)
                self.assertEqual(code, 3)
                self.assertIn(login, err)


class ExitCodeTests(TempConf):
    def test_unknown_tool_is_an_error(self):
        code, out, err = run_process("clt", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Unknown tool 'nope'", err)

    def test_wrong_usage(self):
        for script, argv in (("clt", ["taiga", "api", "FOO", "users/me"]), ("clt", ["taiga", "api", "FOO", "x"]),
                             ("clt", ["gitea", "nope"]), ("clt", ["release", "--bump", "huge"])):
            with self.subTest(argv=argv):
                self.assertEqual(run_process(script, *argv)[0], 2)

    def test_not_logged_in(self):
        for script, argv, env in (("clt", ["taiga", "me"], {"TAIGA_URL": TAIGA}),
                                  ("clt", ["gitea", "me"], {"GITEA_URL": GITEA}),
                                  ("clt", ["github", "me"], {}), ("clt", ["jira", "me"], {"JIRA_URL": JIRA}),
                                  ("clt", ["azdo", "me"], {"AZDO_ORG": "example"})):
            with self.subTest(script=script, argv=argv):
                code, out, err = run_process(script, *argv, env=env)
                self.assertEqual((code, out), (3, ""))
                self.assertIn("Not logged in", err)

    def test_read_only_blocks_writes_and_session_commands(self):
        env = {"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "secret-taiga", "GITEA_URL": GITEA, "GITEA_TOKEN": "secret-gitea",
               "AZDO_ORG": "example", "AZDO_TOKEN": "secret-azdo"}
        for script, argv in (("clt", ["taiga", "--read-only", "POST", "tasks", "-d", "{}"]),
                             ("clt", ["taiga", "--read-only", "DELETE", "tasks/1"]),
                             ("clt", ["gitea", "--read-only", "token"]), ("clt", ["gitea", "--read-only", "logout"]),
                             ("clt", ["github", "--read-only", "token"]), ("clt", ["azdo", "--read-only", "token"]),
                             ("clt", ["azdo", "--read-only", "PATCH", "_apis/wit/workitems/1", "-d", "[]"])):
            with self.subTest(argv=argv):
                code, out, err = run_process(script, *argv, env=env)
                self.assertEqual((code, out), (4, ""))
                self.assertIn("read-only", err)
                self.assertNotIn("secret", err)

    def test_no_server_configured_is_an_error(self):
        code, _, err = run_process("clt", "taiga", "me")
        self.assertEqual(code, 1)
        self.assertIn("login --url", err)

    def test_only_https_servers(self):
        code, _, err = run_process("clt", "taiga", "me", env={"TAIGA_URL": "http://taiga.example.com"})
        self.assertEqual(code, 1)
        self.assertIn("https://", err)


class JsonErrorTests(TempConf):
    def test_errors_are_one_json_line_with_a_fixed_shape(self):
        code, out, err = run_process("clt", "taiga", "--json", "me", env={"TAIGA_URL": TAIGA})
        self.assertEqual((code, out), (3, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        line = json.loads(err)
        self.assertEqual(set(line), ERROR_KEYS)
        self.assertEqual((line["type"], line["exit_code"], line["status"]), ("NotLoggedIn", 3, None))

    def test_unknown_tool_ends_with_the_json_line(self):
        code, _, err = run_process("clt", "--json", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(json_error(err), {"error": "Unknown tool 'nope'", "type": "ToolError", "exit_code": 1,
                                           "status": None})

    def test_read_only_error_as_json(self):
        code, _, err = run_process("clt", "taiga", "--json", "--read-only", "POST", "tasks", "-d", "{}",
                                   env={"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "t"})
        self.assertEqual(code, 4)
        self.assertEqual(json_error(err), {"error": "POST tasks was not sent: read-only mode (--read-only or CLT_READONLY)",
                                           "type": "ReadOnlyError", "exit_code": 4, "status": None})


class GlobalFlagTests(TempConf):
    ENV = {"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "t"}

    def test_read_only_in_any_position(self):
        for argv in (["--read-only", "taiga", "POST", "tasks", "-d", "{}"],
                     ["taiga", "--read-only", "POST", "tasks", "-d", "{}"],
                     ["taiga", "POST", "--read-only", "tasks", "-d", "{}"],
                     ["taiga", "POST", "tasks", "-d", "{}", "--read-only"]):
            with self.subTest(argv=argv):
                self.assertEqual(run_process("clt", *argv, env=self.ENV)[0], 4)

    def test_json_in_any_position(self):
        for argv in (["--json", "taiga", "me"], ["taiga", "--json", "me"], ["taiga", "me", "--json"]):
            with self.subTest(argv=argv):
                code, _, err = run_process("clt", *argv, env={"TAIGA_URL": TAIGA})
                self.assertEqual(code, 3)
                self.assertEqual(json_error(err)["type"], "NotLoggedIn")

    def test_the_environment_variables_turn_them_on(self):
        for value in ("1", "true", "yes", "TRUE"):
            with self.subTest(value=value):
                code, _, err = run_process("clt", "taiga", "me", env={"TAIGA_URL": TAIGA, "CLT_JSON": value})
                self.assertEqual((code, json_error(err)["type"]), (3, "NotLoggedIn"))
                code, _, _ = run_process("clt", "taiga", "POST", "tasks", "-d", "{}", env={**self.ENV, "CLT_READONLY": value})
                self.assertEqual(code, 4)

    def test_other_values_leave_them_off(self):
        code, _, err = run_process("clt", "taiga", "me", env={"TAIGA_URL": TAIGA, "CLT_JSON": "0"})
        self.assertEqual(code, 3)
        self.assertTrue(err.startswith("✗ "))
        code, out, _ = run_process("clt", "taiga", "POST", "tasks", "-d", '{"a": 1}', "--dry-run",
                                   env={**self.ENV, "CLT_READONLY": "no"})
        self.assertEqual((code, json.loads(out)), (0, {"method": "POST", "path": "tasks", "body": {"a": 1}}))


class EnvironmentTests(TempConf):
    def test_global_flags_are_not_written_to_the_environment(self):
        before = dict(os.environ)
        routes = {("GET", f"{TAIGA}/api/v1/users/me"): (200, '{"id": 1}', {})}
        with mock.patch.dict(os.environ, {"TAIGA_URL": TAIGA, "TAIGA_TOKEN": "t"}):
            code, out, _, _ = run_inline("clt", "--json", "--read-only", "taiga", "GET", "users/me", routes=routes)
            self.assertEqual((code, json.loads(out)), (0, {"id": 1}))
            self.assertEqual({k for k in os.environ if k.startswith("CLT_")}, set())
        self.assertEqual(set(os.environ), set(before))


class SessionTests(TempConf):
    """Login, me, token renewal and where the credentials live, against a fake server."""

    def assert_private(self, path):
        self.assertTrue(path.is_file())
        self.assertEqual([p.name for p in path.parent.iterdir()], ["credentials.json"])
        if os.name != "nt":  # on Windows the user profile's ACL protects it
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_taiga_login_me_and_renewal(self):
        creds = Path(os.environ["TAIGA_CONF_DIR"]) / "credentials.json"
        login = {("POST", f"{TAIGA}/api/v1/auth"): (200, json.dumps({"auth_token": "tok-1", "refresh": "r1",
                                                                      "username": "bot"}), {})}
        code, out, err, sent = run_inline("clt", "taiga", "login", "--url", TAIGA, "--user", "bot", "--password-stdin",
                                          routes=login, stdin="s3cret\n")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(json.loads(sent[0].data), {"type": "normal", "username": "bot", "password": "s3cret"})
        self.assert_private(creds)
        self.assertEqual(json.loads(creds.read_text()), {"url": TAIGA, "auth_token": "tok-1", "refresh": "r1"})

        def me(req):
            if req.get_header("Authorization") == "Bearer tok-1":
                return 401, '{"detail": "expired"}', {}
            return 200, json.dumps({"id": 7, "username": "bot", "full_name_display": "Bot User",
                                    "email": "bot@example.com"}), {}

        routes = {("GET", f"{TAIGA}/api/v1/users/me"): me,
                  ("POST", f"{TAIGA}/api/v1/auth/refresh"): (200, '{"auth_token": "tok-2"}', {})}
        code, out, err, sent = run_inline("clt", "taiga", "--json", "me", routes=routes)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"id": 7, "username": "bot", "full_name": "Bot User",
                                           "email": "bot@example.com", "url": TAIGA, "credentials_file": str(creds)})
        self.assertEqual([r.get_header("Authorization") for r in sent], ["Bearer tok-1", None, "Bearer tok-2"])
        self.assertIn("token renewed", err)
        self.assertNotIn("tok-", out + err)
        self.assert_private(creds)
        self.assertEqual(json.loads(creds.read_text())["refresh"], "r1")

    def test_gitea_me_and_refresh(self):
        creds = Path(os.environ["GITEA_CONF_DIR"]) / "credentials.json"
        write_json(creds, {"url": GITEA, "client_id": "app", "access_token": "tok-1", "refresh_token": "r1",
                           "expires_at": 4102444800})
        user = (200, json.dumps({"id": 3, "login": "bot", "full_name": "Bot User", "email": "bot@example.com"}), {})
        code, out, _, sent = run_inline("clt", "gitea", "me", "--json", routes={("GET", f"{GITEA}/api/v1/user"): user})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"id": 3, "username": "bot", "full_name": "Bot User",
                                           "email": "bot@example.com", "url": GITEA, "expires_at": 4102444800,
                                           "credentials_file": str(creds)})
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer tok-1")

        token = (200, json.dumps({"access_token": "tok-2", "refresh_token": "r2", "expires_in": 3600}), {})
        code, _, err, sent = run_inline("clt", "gitea", "refresh",
                                        routes={("POST", f"{GITEA}/login/oauth/access_token"): token})
        self.assertEqual(code, 0)
        self.assertIn("Token renewed", err)
        self.assertEqual(sent[0].data.decode(), "grant_type=refresh_token&refresh_token=r1&client_id=app")
        self.assert_private(creds)
        saved = json.loads(creds.read_text())
        self.assertEqual((saved["access_token"], saved["refresh_token"], saved["url"]), ("tok-2", "r2", GITEA))

    def test_github_login_and_me(self):
        creds = Path(os.environ["GITHUB_CONF_DIR"]) / "credentials.json"
        user = (200, json.dumps({"id": 5, "login": "bot", "name": "Bot User", "email": "bot@example.com"}), {})
        routes = {("GET", f"{GITHUB}/user"): user}
        code, out, _, sent = run_inline("clt", "github", "login", "--token-stdin", routes=routes, stdin="tok-1\n")
        self.assertEqual((code, out), (0, ""))
        self.assert_private(creds)
        self.assertEqual(json.loads(creds.read_text()), {"token": "tok-1"})
        code, out, _, sent = run_inline("clt", "--json", "github", "me", routes=routes)
        self.assertEqual(json.loads(out), {"id": 5, "username": "bot", "full_name": "Bot User",
                                           "email": "bot@example.com", "url": "https://github.com",
                                           "credentials_file": str(creds)})
        self.assertEqual(sent[0].get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer tok-1")

    def test_default_credentials_folders_are_under_the_home_config(self):
        home = Path(self.tmp.name) / "home"
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        with mock.patch.dict(os.environ, env):
            for name in ("TAIGA_CONF_DIR", "GITEA_CONF_DIR", "GITHUB_CONF_DIR", "JIRA_CONF_DIR", "AZDO_CONF_DIR"):
                os.environ.pop(name)
            self.assertEqual(Path.home(), home)  # the real ~/.config is never touched
            write_json(home / ".config" / "taiga" / "credentials.json", {"url": TAIGA, "auth_token": "t"})
            write_json(home / ".config" / "gitea" / "credentials.json", {"url": GITEA, "access_token": "t"})
            write_json(home / ".config" / "github" / "credentials.json", {"token": "t"})
            write_json(home / ".config" / "jira" / "credentials.json",
                       {"url": JIRA, "email": "e@example.com", "token": "t", "mode": "site", "cloud_id": None})
            write_json(home / ".config" / "azdo" / "credentials.json", {"url": AZDO, "token": "t"})
            cases = [("clt", ["taiga"], {("GET", f"{TAIGA}/api/v1/users/me"): (200, '{"id": 1}', {})}),
                     ("clt", ["gitea"], {("GET", f"{GITEA}/api/v1/user"): (200, '{"id": 1}', {})}),
                     ("clt", ["github"], {("GET", f"{GITHUB}/user"): (200, '{"id": 1}', {})}),
                     ("clt", ["jira"], {("GET", f"{JIRA}/rest/api/3/myself"): (200, '{"accountId": "a"}', {})}),
                     ("clt", ["azdo"], {("GET", f"{AZDO}/_apis/connectionData?api-version=7.1-preview"):
                                        (200, '{"authenticatedUser": {"id": "a"}}', {})})]
            for script, argv, routes in cases:
                tool = argv[0] if argv else script
                with self.subTest(tool=tool):
                    code, out, _, _ = run_inline(script, *argv, "--json", "me", routes=routes)
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(out)["credentials_file"],
                                     str(home / ".config" / tool / "credentials.json"))

    def test_jenkins_me_and_its_default_credentials_folder(self):
        home = Path(self.tmp.name) / "home"
        who = {("GET", f"{JENKINS}/whoAmI/api/json"): (200, '{"name": "reader", "authenticated": true}', {})}
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            os.environ.pop("JENKINS_CONF_DIR")
            write_json(home / ".config" / "jenkins" / "credentials.json",
                       {"url": JENKINS, "user": "reader", "token": "t"})
            code, out, _, sent = run_inline("clt", "jenkins", "--json", "me", routes=who)
        self.assertEqual((code, json.loads(out)), (0, {"username": "reader", "authenticated": True, "url": JENKINS}))
        self.assertTrue(sent[0].get_header("Authorization").startswith("Basic "))  # the saved session was used

    def test_a_token_from_the_environment_has_no_credentials_file(self):
        user = (200, json.dumps({"id": 1, "login": "bot"}), {})
        with mock.patch.dict(os.environ, {"GITEA_URL": GITEA, "GITEA_TOKEN": "pat"}):
            code, out, _, sent = run_inline("clt", "gitea", "--json", "me", routes={("GET", f"{GITEA}/api/v1/user"): user})
        me = json.loads(out)
        self.assertEqual(set(me), ME_KEYS | {"expires_at"})
        self.assertIsNone(me["credentials_file"])
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer pat")

    def test_logout_deletes_the_session(self):
        creds = Path(os.environ["TAIGA_CONF_DIR"]) / "credentials.json"
        write_json(creds, {"url": TAIGA, "auth_token": "t"})
        code, _, err, sent = run_inline("clt", "taiga", "logout")
        self.assertEqual((code, sent), (0, []))
        self.assertFalse(creds.exists())
        self.assertIn("Logged out", err)


class ApiCommandTests(TempConf):
    def setUp(self):
        super().setUp()
        for name, url, key in (("TAIGA", TAIGA, "auth_token"), ("GITEA", GITEA, "access_token")):
            write_json(Path(os.environ[f"{name}_CONF_DIR"]) / "credentials.json", {"url": url, key: "tok"})

    def test_the_method_shortcut_is_the_api_command(self):
        pulls = (200, '[{"number": 1, "title": "One"}]', {})
        for script, argv, url in (("clt", ["gitea"], f"{GITEA}/api/v1/repos/owner/repo/pulls"),
                                  ("clt", ["taiga"], f"{TAIGA}/api/v1/users/me"),
                                  ("clt", ["taiga"], f"{TAIGA}/api/v1/users/me")):
            path = url.split("/api/v1/")[1]
            with self.subTest(script=script, argv=argv):
                short = run_inline(script, *argv, "GET", path, routes={("GET", url): pulls})
                full = run_inline(script, *argv, "api", "GET", path, routes={("GET", url): pulls})
                self.assertEqual(short[:3], full[:3])
                self.assertEqual(short[0], 0)
                self.assertEqual(json.loads(short[1]), json.loads(pulls[1]))
                self.assertEqual([r.full_url for r in short[3]], [url])
                self.assertEqual(short[3][0].get_header("Authorization"), "Bearer tok")

    def test_pagination_and_filters(self):
        base = f"{GITEA}/api/v1/repos/o/r/pulls"
        routes = {("GET", base): (200, '[{"number": 1, "title": "a"}]', {"Link": f'<{base}?page=2>; rel="next"'}),
                  ("GET", base + "?page=2"): (200, '[{"number": 2, "title": "b"}]', {})}
        code, out, err, sent = run_inline("clt", "gitea", "GET", "repos/o/r/pulls", "--all", "--fields", "number",
                                          "--max-items", "1", routes=routes)
        self.assertEqual((code, json.loads(out)), (0, [{"number": 1}]))
        self.assertEqual(len(sent), 1)
        self.assertIn("Showing 1 items; there may be more", err)
        code, out, err, sent = run_inline("clt", "gitea", "GET", "repos/o/r/pulls", "--all", "--fields", "number",
                                          "--max-items", "5", routes=routes)
        self.assertEqual((code, json.loads(out)), (0, [{"number": 1}, {"number": 2}]))
        self.assertEqual(len(sent), 2)

    def test_http_errors(self):
        routes = {("GET", f"{GITEA}/api/v1/repos/o/r"): (404, '{"message": "Not Found"}', {})}
        code, out, err, _ = run_inline("clt", "gitea", "GET", "repos/o/r", routes=routes)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("GET repos/o/r -> HTTP 404", err)
        code, out, err, _ = run_inline("clt", "gitea", "--json", "GET", "repos/o/r", routes=routes)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(json.loads(err), {"error": "Not Found", "status": 404, "method": "GET", "path": "repos/o/r",
                                           "body": {"message": "Not Found"}})

    def test_dry_run_sends_nothing(self):
        code, out, _, sent = run_inline("clt", "taiga", "PATCH", "issues/1", "-d", '{"status": 2}', "--dry-run")
        self.assertEqual((code, sent), (0, []))
        self.assertEqual(json.loads(out), {"method": "PATCH", "path": "issues/1", "body": {"status": 2}})

    def test_the_token_only_goes_to_the_configured_server(self):
        for path in ("https://evil.example/x", "//evil.example/x"):
            with self.subTest(path=path):
                code, _, err, sent = run_inline("clt", "taiga", "GET", path)
                self.assertEqual((code, sent), (1, []))
                self.assertIn("Only paths relative to", err)

    def test_a_session_for_another_server_is_not_used(self):
        with mock.patch.dict(os.environ, {"TAIGA_URL": "https://other.example.com"}):
            code, _, err, sent = run_inline("clt", "taiga", "me")
        self.assertEqual((code, sent), (1, []))
        self.assertIn("not https://other.example.com", err)


if __name__ == "__main__":
    unittest.main()
