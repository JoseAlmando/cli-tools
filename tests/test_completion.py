import contextlib
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli_tools.commands import completion

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

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_powershell(self):
        if os.name == "nt":
            write(self.bin / "clt.cmd", f'@"{sys.executable}" -m cli_tools %*\r\n')
        else:
            self.posix_shim()
        script = self.bin / "complete-test.ps1"  # not clt.ps1: PowerShell would run it for "clt", over and over
        script.write_text(self.script("powershell") + "\n$line = 'clt release --bump f'\n"
                          "(TabExpansion2 -inputScript $line -cursorColumn $line.Length).CompletionMatches |"
                          " ForEach-Object { $_.CompletionText }\n", encoding="utf-8")
        done = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True,
                              text=True, env=self.env, timeout=120)
        self.assertEqual(done.stdout.split(), ["feat", "fix"], done.stderr)


if __name__ == "__main__":
    unittest.main()
