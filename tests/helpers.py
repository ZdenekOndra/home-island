"""Shared test helpers: a throwaway HomeIsland configuration in a temp directory."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

from homeisland.config import Config  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TempInstall(unittest.TestCase):
    """Creates config, state and data directories and a Config pointing at them."""

    modules = ("kiwix", "library", "maps")
    settings = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="homeisland-test-")
        self.config_dir = os.path.join(self.tmp, "etc")
        self.state_dir = os.path.join(self.tmp, "state")
        self.data_dir = os.path.join(self.tmp, "data")
        for d in (self.config_dir, self.state_dir, self.data_dir):
            os.makedirs(d)
        values = {
            "HOMEISLAND_DOMAIN": "home.arpa",
            "HOMEISLAND_HOST_IP": "192.168.1.10",
            "HOMEISLAND_STATE_DIR": self.state_dir,
            "HOMEISLAND_DATA_DIR": self.data_dir,
            "HOMEISLAND_TZ": "Etc/UTC",
        }
        values.update(self.settings)
        with open(os.path.join(self.config_dir, "homeisland.env"), "w") as fh:
            fh.write("".join("%s=%s\n" % kv for kv in values.items()))
        with open(os.path.join(self.config_dir, "secrets.env"), "w") as fh:
            fh.write("PIHOLE_WEB_PASSWORD=test-password\nSAMBA_PASSWORD=test-samba\n")
        os.chmod(os.path.join(self.config_dir, "secrets.env"), 0o600)
        with open(os.path.join(self.config_dir, "modules.enabled"), "w") as fh:
            fh.write("".join(m + "\n" for m in self.modules))
        self.cfg = Config(config_dir=self.config_dir, repo_root=ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, content, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        os.chmod(path, mode)
        return path
