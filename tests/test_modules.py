"""Static checks of every module definition and compose fragment."""

import os
import re
import unittest

from helpers import ROOT, TempInstall, read

from homeisland.modules import load_modules
from homeisland.stack import module_services

IMAGE_RE = re.compile(r"^\s+image:\s*(\S+)", re.M)


def compose_texts():
    files = [os.path.join(ROOT, "compose.yml")]
    for name in sorted(os.listdir(os.path.join(ROOT, "modules"))):
        path = os.path.join(ROOT, "modules", name, "compose.yml")
        if os.path.exists(path):
            files.append(path)
    return [(f, read(f)) for f in files]


class ModuleDefinitionTest(TempInstall):
    def test_registry(self):
        mods = load_modules(self.cfg)
        self.assertTrue({"dashboard", "dns", "monitoring", "backup"} <= {n for n, m in mods.items() if m.core})
        for mod in mods.values():
            self.assertIn(mod.group, ("core", "knowledge", "maps", "home", "media", "hardware"), mod.name)
            if not mod.host_only and not mod.core:
                self.assertIsNotNone(mod.compose_file, "%s has no compose.yml" % mod.name)
            if mod.host_only:
                self.assertIsNone(mod.compose_file, mod.name)
            for check in mod.checks:
                self.assertIn(check["type"], ("http", "tcp", "dns", "dataset", "status"), mod.name)
                self.assertIn("name", check)
            for link in mod.links:
                self.assertTrue("url" in link or "host" in link, mod.name)
            if mod.compose_file:
                self.assertTrue(module_services(mod), mod.name)


class ComposeFragmentTest(unittest.TestCase):
    def test_images_pinned(self):
        for path, text in compose_texts():
            for image in IMAGE_RE.findall(text):
                if image.startswith("homeisland/"):
                    self.assertTrue(image.endswith(":local"), image)
                    continue
                self.assertRegex(image, r":[^:]+$", "%s: image without tag" % path)
                self.assertNotRegex(image, r":(latest|stable|main|nightly)$", "%s: floating tag %s" % (path, image))

    def test_restart_and_logging(self):
        for path, text in compose_texts():
            body = re.split(r"^services:\s*$", text, flags=re.M)[1].split("\nnetworks:")[0]
            services = len(re.findall(r"^  [a-z0-9_-]+:\s*$", body, re.M))
            self.assertEqual(text.count("restart: unless-stopped"), services, path)
            if "logging: *logging" not in text:
                self.assertEqual(text.count("max-size"), services, path)

    def test_bind_mounts_never_create_paths(self):
        for path, text in compose_texts():
            binds = text.count("type: bind")
            self.assertEqual(text.count("create_host_path: false"), binds, path)

    def test_ports_bound_to_bind_ip(self):
        for path, text in compose_texts():
            for port in re.findall(r'^\s+- "([^"]+)"$', text, re.M):
                if re.match(r"^[\d${}]", port) and ":" in port:
                    self.assertTrue(port.startswith("${HOMEISLAND_BIND_IP:?}:"), "%s: %s" % (path, port))

    def test_no_privileged_or_docker_socket(self):
        for path, text in compose_texts():
            self.assertNotIn("privileged: true", text, path)
            self.assertNotIn("docker.sock", text, path)


if __name__ == "__main__":
    unittest.main()
