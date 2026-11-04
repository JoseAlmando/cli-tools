import contextlib
import io
import json
import os
import shutil
import subprocess
from unittest import mock

from cli_tools.core import context, credentials, errors
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf

ATLASSIAN = "https://mcp.atlassian.com/v1/mcp"
NPX = ["npx", "-y", "@azure-devops/mcp", "example"]


def mcp():
    from cli_tools.core import mcp as module
    return module


class CommandTests(TempConf):
    def test_http_commands(self):
        m = mcp()
        self.assertEqual(m.http_command("claude", "atlassian", ATLASSIAN),
                         ["claude", "mcp", "add", "--scope", "user", "--transport", "http", "atlassian", ATLASSIAN])
        self.assertEqual(m.http_command("codex", "atlassian", ATLASSIAN),
                         ["codex", "mcp", "add", "atlassian", "--url", ATLASSIAN])
        self.assertEqual(m.http_command("gemini", "atlassian", ATLASSIAN),
                         ["gemini", "mcp", "add", "--scope", "user", "--transport", "http", "atlassian", ATLASSIAN])

    def test_stdio_commands(self):
        m = mcp()
        self.assertEqual(m.stdio_command("claude", "azure-devops", NPX),
                         ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", "azure-devops", "--", *NPX])
        self.assertEqual(m.stdio_command("codex", "azure-devops", NPX), ["codex", "mcp", "add", "azure-devops", "--", *NPX])
        self.assertEqual(m.stdio_command("gemini", "azure-devops", NPX),
                         ["gemini", "mcp", "add", "--scope", "user", "azure-devops", *NPX])


class ToolMcpTests(TempConf):
    def setUp(self):
        super().setUp()
        self.found = {}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(shutil, "which", lambda name: self.found.get(name)).start()
        self.run_ = mock.patch.object(subprocess, "run", return_value=mock.Mock(returncode=0)).start()
        mock.patch.object(mcp(), "on_windows", lambda: False).start()

    def clt(self, *argv, ctx=None):
        from cli_tools import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), ctx=ctx or context.CLIContext())
        return code, out.getvalue(), err.getvalue()

    def test_print_only(self):
        code, out, err = self.clt("jira", "mcp", "--agent", "codex")
        self.assertEqual(code, 0)
        self.assertIn(f"codex mcp add atlassian --url {ATLASSIAN}", out)
        self.assertIn("codex mcp login atlassian", out + err)
        self.run_.assert_not_called()
        _, out, _ = self.clt("jira", "mcp", "--agent", "claude", ctx=context.CLIContext(json_mode=True))
        self.assertEqual(json.loads(out), {"agent": "claude", "installed": False,
                                           "command": mcp().http_command("claude", "atlassian", ATLASSIAN),
                                           "then": "/mcp in Claude Code to sign in"})

    def test_install_runs_the_agent_cli(self):
        self.found["codex"] = "/bin/codex"
        self.run_.return_value = mock.Mock(returncode=3)
        code, _, _ = self.clt("jira", "mcp", "--agent", "codex", "--install")
        self.assertEqual(code, 3)
        self.run_.assert_called_once()
        self.assertEqual(self.run_.call_args[0][0], ["/bin/codex", "mcp", "add", "atlassian", "--url", ATLASSIAN])
        self.assertNotIn("shell", self.run_.call_args[1])

    def test_install_without_the_cli(self):
        with self.assertRaisesRegex(ToolError, "gemini mcp add"):
            self.clt("jira", "mcp", "--agent", "gemini", "--install")
        self.run_.assert_not_called()

    def test_read_only_blocks_install_not_print(self):
        ctx = context.CLIContext(read_only=True)
        self.assertEqual(self.clt("jira", "mcp", "--agent", "codex", ctx=ctx)[0], 0)
        self.found["codex"] = "/bin/codex"
        with self.assertRaises(errors.ReadOnlyError):
            self.clt("jira", "mcp", "--agent", "codex", "--install", ctx=ctx)
        self.run_.assert_not_called()

    def test_azdo_mcp_needs_an_org(self):
        with self.assertRaisesRegex(ToolError, "No organization configured"):
            self.clt("azdo", "mcp", "--agent", "codex")
        _, out, _ = self.clt("azdo", "mcp", "--agent", "codex", "--org", "example")
        self.assertIn("codex mcp add azure-devops -- npx -y @azure-devops/mcp example", out)
        from cli_tools.tools.azdo.client import AzureDevOps
        credentials.write_private_json(AzureDevOps(require_url=False).credentials_file,
                                       {"url": "https://dev.azure.com/saved"})
        _, out, _ = self.clt("azdo", "mcp", "--agent", "claude")
        self.assertIn("@azure-devops/mcp saved", out)

    def test_windows_wraps_npx(self):
        with mock.patch.object(mcp(), "on_windows", lambda: True), mock.patch.dict(os.environ, {"AZDO_ORG": "example"}):
            _, out, _ = self.clt("azdo", "mcp", "--agent", "codex")
        self.assertIn("codex mcp add azure-devops -- cmd /c npx -y @azure-devops/mcp example", out)
