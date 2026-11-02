import os
import tempfile
import unittest
from unittest import mock


class TempConf(unittest.TestCase):
    """Each test gets its own credentials folder and none of the tools' environment variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TAIGA_", "GITEA_"))}
        env.update(TAIGA_CONF_DIR=os.path.join(self.tmp.name, "taiga"), GITEA_CONF_DIR=os.path.join(self.tmp.name, "gitea"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
