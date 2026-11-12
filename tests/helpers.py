import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TempConf(unittest.TestCase):
    """Each test gets its own credentials folder and none of the tools' environment variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TAIGA_", "GITEA_", "GITHUB_", "GH_", "JIRA_", "AZDO_", "AZURE_DEVOPS_",
                                    "JENKINS_", "CLT_"))}
        env.update(TAIGA_CONF_DIR=os.path.join(self.tmp.name, "taiga"), GITEA_CONF_DIR=os.path.join(self.tmp.name, "gitea"),
                   GITHUB_CONF_DIR=os.path.join(self.tmp.name, "github"), JIRA_CONF_DIR=os.path.join(self.tmp.name, "jira"),
                   AZDO_CONF_DIR=os.path.join(self.tmp.name, "azdo"), JENKINS_CONF_DIR=os.path.join(self.tmp.name, "jenkins"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class GitRepo(TempConf):
    """A clone (self.root) of a bare repo (self.origin) with one commit on main, both in a temp folder."""

    def setUp(self):
        super().setUp()
        self.origin, self.root = make_repo(Path(self.tmp.name))

    def git(self, *args):
        return run_git(self.root, *args)

    def commit(self, subject, files=None):
        commit(self.root, subject, files)


def make_repo(base):
    """(origin, root): a bare repo base/origin.git and its clone base/work, with one commit on main."""
    origin, root = base / "origin.git", base / "work"
    run_git(base, "init", "--quiet", "--bare", "-b", "main", str(origin))
    run_git(base, "clone", "--quiet", str(origin), str(root))
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false"),
                       ("tag.gpgsign", "false"), ("core.hooksPath", str(base / "no-hooks"))):
        run_git(root, "config", key, value)
    commit(root, "Initial commit", {"README.md": "demo\n"})
    run_git(root, "push", "--quiet", "-u", "origin", "main")
    run_git(root, "remote", "set-head", "origin", "main")
    return origin, root


def commit(root, subject, files=None):
    files = files or {f"change-{len(list(root.glob('change-*')))}.txt": subject + "\n"}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    run_git(root, "add", "-A")
    run_git(root, "commit", "--quiet", "-m", subject)


def run_git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()
