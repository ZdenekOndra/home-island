import json
import os
import unittest

from helpers import TempInstall, read

from homeisland import generate
from homeisland.modules import Module, active_modules, load_modules
from homeisland.util import HomeIslandError


class GenerateTest(TempInstall):
    def test_generate_all(self):
        changed = generate.generate_all(self.cfg)
        self.assertTrue(changed)
        gen = self.cfg.generated_dir
        caddy = read(os.path.join(gen, "caddy", "Caddyfile"))
        for host in ("home", "status", "pihole", "wiki", "library", "maps"):
            self.assertIn("http://%s.home.arpa {" % host, caddy)
        self.assertIn("reverse_proxy kiwix:8080", caddy)
        self.assertIn("connect-src 'self'", caddy)
        self.assertIn("auto_https off", caddy)
        hosts = read(os.path.join(gen, "dns", "hosts"))
        self.assertIn("192.168.1.10 wiki.home.arpa", hosts)
        self.assertIn("local=/home.arpa/", read(os.path.join(gen, "dnsmasq.d", "10-homeisland.conf")))
        env_path = os.path.join(gen, "compose.env")
        self.assertEqual(os.stat(env_path).st_mode & 0o777, 0o600)
        self.assertIn("PIHOLE_WEB_PASSWORD='test-password'", read(env_path))
        services = json.loads(read(os.path.join(gen, "api", "services.json")))
        titles = [l["title"] for l in services["links"]]
        self.assertIn("Wikipedia", titles)
        self.assertNotIn("Home Assistant", titles)
        # A second run changes nothing.
        self.assertEqual(generate.generate_all(self.cfg), [])

    def test_disabled_module_not_routed(self):
        self.write(self.cfg.modules_file, "")
        generate.generate_all(self.cfg)
        caddy = read(os.path.join(self.cfg.generated_dir, "caddy", "Caddyfile"))
        self.assertNotIn("wiki.home.arpa", caddy)

    def test_non_default_port_in_links(self):
        self.cfg.values["HOMEISLAND_HTTP_PORT"] = "8080"
        services = generate.render_services(self.cfg, active_modules(self.cfg))
        wiki = [l for l in services["links"] if l["title"] == "Wikipedia"][0]
        self.assertEqual(wiki["url"], "http://wiki.home.arpa:8080/")

    def test_host_conflict_detected(self):
        mods = active_modules(self.cfg)
        clash = Module("/nonexistent", {"name": "clash", "title": "t", "group": "home", "hosts": {"wiki": "x:1"}})
        with self.assertRaises(HomeIslandError):
            generate.proxy_routes(mods + [clash])

    def test_extra_record_cannot_shadow_module(self):
        self.write(self.cfg.dns_records_file, "192.168.1.99 wiki\n")
        with self.assertRaises(HomeIslandError):
            generate.dns_records(self.cfg, active_modules(self.cfg))

    def test_env_value_with_quote_rejected(self):
        self.cfg.secrets["PIHOLE_WEB_PASSWORD"] = "it's"
        with self.assertRaises(HomeIslandError):
            generate.render_compose_env(self.cfg)

    def test_invalid_config_refused(self):
        self.cfg.values["HOMEISLAND_HOST_IP"] = ""
        with self.assertRaises(HomeIslandError):
            generate.generate_all(self.cfg)

    def test_every_module_generates(self):
        self.write(self.cfg.modules_file, "".join(
            n + "\n" for n, m in load_modules(self.cfg).items() if not m.core))
        generate.generate_all(self.cfg)


if __name__ == "__main__":
    unittest.main()
