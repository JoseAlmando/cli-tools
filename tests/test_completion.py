import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools.commands import completion
from cli_tools.core import context, errors
from cli_tools.core.errors import ToolError
from tests.helpers import TempConf

ROOT = Path(__file__).resolve().parents[1]


def find_bash():
    """Git's bash on Windows (System32's bash.exe is WSL's, which cannot see Windows paths); bash elsewhere."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    # git.exe is in Git\cmd or Git\mingw64\bin, depending on the install; bash.exe is in Git\bin
    candidates = [folder / "bin" / "bash.exe" for folder in Path(git).resolve().parents] if git else []
    return next((str(path) for path in candidates if path.is_file()), None)


BASH = find_bash()


def write(path, text):
    """The text exactly as given, line endings included (Path.write_text has no newline= before Python 3.10)."""
    path.write_bytes(text.encode("utf-8"))
    return path


class CandidateTests(unittest.TestCase):
    def test_tools_commands_options_and_choices(self):
        cases = [
            (["gi"], ["gitea", "github"]),
            ([""], sorted([name for name, _, _ in completion.registry.TOOLS] + list(completion.registry.ALIASES)
                          + ["--help", "--version"])),
            (["release", "--bump", ""], ["feat", "fix", "major"]),
            (["taiga", "l"], ["lg", "lo", "login", "logout"]),
            (["taiga", "api", ""], ["DELETE", "GET", "PATCH", "POST", "PUT"]),
            (["jenkins", "log", "Job", "--t"], ["--tail"]),
            (["gt", "GET", ""], []),  # a path: nothing to offer
            (["taiga", "--js"], ["--json"]),
            (["completion", ""], ["bash", "fish", "powershell", "zsh"]),
            (["gitea", "pr", "c"], ["create"]),
            (["nope", ""], []),
        ]
        for words, expected in cases:
            with self.subTest(words=words):
                self.assertEqual(completion.candidates(words), expected)

    def test_every_command_has_the_parser_completion_reads(self):
        for name, module, _ in completion.registry.TOOLS:
            with self.subTest(command=name):
                parser = importlib.import_module(module).build_parser(f"clt {name}")
                self.assertEqual(parser.prog, f"clt {name}")

    def test_the_shell_protocol_reads_the_words_from_the_environment(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {completion.WORDS_VAR: "taiga\x1f--read-only\x1fme\x1f--js\x1f"}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(completion.main([]), 0)
        self.assertEqual(out.getvalue(), "--json\n")

    def test_a_broken_completion_prints_nothing(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {completion.WORDS_VAR: "taiga\x1f"}), \
                mock.patch.object(completion, "candidates", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(completion.main([]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_each_shell_gets_its_script(self):
        for shell, marker in (("bash", "complete -o default -F _clt clt"), ("zsh", "compdef _clt clt"),
                              ("fish", "complete -c clt"), ("powershell", "Register-ArgumentCompleter")):
            with self.subTest(shell=shell):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    completion.main([shell])
                self.assertIn(marker, out.getvalue())


class LineEndingTests(unittest.TestCase):
    def run_clt(self, *argv, env=None):
        return subprocess.run([sys.executable, "-m", "cli_tools", *argv], capture_output=True, timeout=60,
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})}).stdout

    def test_scripts_and_candidates_have_unix_line_endings_everywhere(self):
        # eval "$(clt completion bash)" in Git Bash fails on a \r, and every candidate would end with one
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell):
                out = self.run_clt("completion", shell)
                self.assertIn(b"\n", out)
                self.assertNotIn(b"\r", out)
        out = self.run_clt("completion", env={completion.WORDS_VAR: "release\x1f--bump\x1f\x1f"})
        self.assertEqual(out, b"feat\nfix\nmajor\n")


class InstallTests(TempConf):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        environ = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        patcher = mock.patch.dict(os.environ, environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ZDOTDIR", "XDG_CONFIG_HOME"):
            os.environ.pop(name, None)

    def install(self, shell, ctx=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = completion.main([shell, "--install"], ctx=ctx or context.CLIContext())
        return code, out.getvalue(), err.getvalue()

    def test_bash_adds_one_line_to_bashrc_once(self):
        rc = self.home / ".bashrc"
        write(rc, "alias ll='ls -l'\n")
        self.assertEqual(self.install("bash")[0], 0)
        _, _, err = self.install("bash")
        self.assertEqual(rc.read_text(encoding="utf-8").count('eval "$(clt completion bash)"'), 1)
        self.assertTrue(rc.read_text(encoding="utf-8").startswith("alias ll='ls -l'\n"))
        self.assertIn("Already in", err)

    def test_zsh_loads_compinit_first_when_the_file_does_not(self):
        rc = self.home / ".zshrc"
        self.install("zsh")
        text = rc.read_text(encoding="utf-8")
        self.assertLess(text.index("compinit"), text.index('eval "$(clt completion zsh)"'))
        write(rc, "source $ZSH/oh-my-zsh.sh  # runs compinit\nautoload -Uz compinit\n")
        self.install("zsh")
        self.assertEqual(rc.read_text(encoding="utf-8").count("compinit"), 2)  # none added

    def test_zsh_follows_zdotdir(self):
        with mock.patch.dict(os.environ, ZDOTDIR=str(self.home / "zsh")):
            self.install("zsh")
        self.assertTrue((self.home / "zsh" / ".zshrc").is_file())

    def test_fish_writes_its_completions_file(self):
        self.install("fish")
        path = self.home / ".config" / "fish" / "completions" / "clt.fish"
        self.assertEqual(path.read_text(encoding="utf-8"), completion.FISH)

    def test_powershell_adds_the_line_to_each_profile_and_keeps_its_line_endings(self):
        one, two = self.home / "PowerShell" / "profile.ps1", self.home / "WindowsPowerShell" / "profile.ps1"
        two.parent.mkdir()
        write(two, "Set-PSReadLineOption -EditMode Emacs\r\n")
        with mock.patch.object(completion, "powershell_profiles", lambda: [one, two]):
            code, out, _ = self.install("powershell", ctx=context.CLIContext(json_mode=True))
        line = "clt completion powershell | Out-String | Invoke-Expression"
        self.assertEqual(code, 0)
        self.assertIn(line, one.read_text(encoding="utf-8"))
        self.assertTrue(two.read_bytes().endswith((line + "\r\n").encode()))
        self.assertNotIn(b"\n", two.read_bytes().replace(b"\r\n", b""))
        self.assertEqual(json.loads(out), {"shell": "powershell", "files": [
            {"path": str(one), "action": "added"}, {"path": str(two), "action": "added"}]})

    def test_without_powershell_it_says_where_to_add_the_line(self):
        with mock.patch.object(completion, "powershell_profiles", lambda: []), \
                self.assertRaisesRegex(ToolError, r"\$PROFILE"):
            self.install("powershell")

    def test_read_only_installs_nothing(self):
        with self.assertRaises(errors.ReadOnlyError):
            self.install("bash", ctx=context.CLIContext(read_only=True))
        self.assertFalse((self.home / ".bashrc").exists())


class ShellTests(unittest.TestCase):
    """The real scripts in the real shells, with a stand-in clt on the PATH that runs this checkout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}

    def script(self, shell):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            completion.main([shell])
        return out.getvalue()

    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash(self):
        python = Path(sys.executable).as_posix()
        # The stand-in also keeps what it received, for a failure on another shell version
        received = self.bin / "received.txt"
        shim = write(self.bin / "clt", f'#!/usr/bin/env bash\nprintf "%s" "$CLT_COMPLETE" | od -c >> "{received.as_posix()}"\n'
                                       f'exec "{python}" -m cli_tools "$@"\n')
        if os.name != "nt":
            shim.chmod(0o755)
        script = write(self.bin / "complete-test.bash", self.script("bash")
                       + 'COMP_WORDS=(clt release --bump f); COMP_CWORD=3; _clt\nprintf "%s\\n" "${COMPREPLY[@]}"\n'
                       + 'echo "bash $BASH_VERSION, clt: $(command -v clt)" >&2\n'
                       + "CLT_COMPLETE=$'release\\x1f--bump\\x1ff\\x1f' clt completion >&2; echo \"clt exit $?\" >&2\n")
        done = subprocess.run([BASH, "--noprofile", "--norc", script.as_posix()], capture_output=True, text=True,
                              env=self.env, timeout=120)
        sent = received.read_text(encoding="utf-8") if received.exists() else "(clt was not run)"
        self.assertEqual(done.stdout.split(), ["feat", "fix"], done.stderr + "\n" + sent)

    def posix_shim(self):
        write(self.bin / "clt", f'#!/bin/sh\nexec "{sys.executable}" -m cli_tools "$@"\n').chmod(0o755)

    @unittest.skipUnless(os.name != "nt" and shutil.which("zsh"), "zsh is not installed")
    def test_zsh(self):
        self.posix_shim()
        script = self.bin / "complete-test.zsh"
        # No compinit outside an interactive shell: compdef does nothing, and compadd prints what it is given
        script.write_text("compdef() { :; }\ncompadd() { [[ $1 == -a ]] && print -l ${(P)2} }\n" + self.script("zsh")
                          + "words=(clt release --bump f); CURRENT=4; _clt\n", encoding="utf-8")
        done = subprocess.run(["zsh", "-f", str(script)], capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(done.stdout.split(), ["feat", "fix"], done.stderr)

    @unittest.skipUnless(os.name != "nt" and shutil.which("fish"), "fish is not installed")
    def test_fish(self):
        self.posix_shim()
        script = self.bin / "complete-test.fish"
        script.write_text(self.script("fish") + "complete -C 'clt release --bump f'\n", encoding="utf-8")
        done = subprocess.run(["fish", "--no-config", str(script)], capture_output=True, text=True, env=self.env,
                              timeout=120)
        self.assertEqual([line.split("\t")[0] for line in done.stdout.split("\n") if line], ["feat", "fix"],
                         done.stderr)

    def complete_in_powershell(self, program):
        if os.name == "nt":
            write(self.bin / "clt.cmd", f'@"{sys.executable}" -m cli_tools %*\r\n')
        else:
            self.posix_shim()
        script = self.bin / "complete-test.ps1"  # not clt.ps1: PowerShell would run it for "clt", over and over
        script.write_text(self.script("powershell") + "\n$line = 'clt release --bump f'\n"
                          "(TabExpansion2 -inputScript $line -cursorColumn $line.Length).CompletionMatches |"
                          " ForEach-Object { $_.CompletionText }\n", encoding="utf-8")
        done = subprocess.run([program, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                               str(script)], capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(done.stdout.split(), ["feat", "fix"], done.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_powershell(self):
        self.complete_in_powershell("pwsh")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell is Windows only")
    def test_windows_powershell(self):
        self.complete_in_powershell("powershell")


if __name__ == "__main__":
    unittest.main()
