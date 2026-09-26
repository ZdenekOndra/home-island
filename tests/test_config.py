import os
import unittest

from helpers import TempInstall

from homeisland.config import valid_hostname, valid_ipv4
from homeisland.util import HomeIslandError, parse_env_file


class ParseEnvTest(unittest.TestCase):
    def test_parsing(self):
        env = parse_env_file(
            "# comment\n\nA=1\nexport B=two\nC='quoted # not comment'\nD=\"dq\"\nE=value # trailing\nbroken line\n"
        )
        self.assertEqual(env, {"A": "1", "B": "two", "C": "quoted # not comment", "D": "dq", "E": "value"})

    def test_example_file_parses(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".env.example")) as fh:
            env = parse_env_file(fh.read())
        self.assertEqual(env["HOMEISLAND_DOMAIN"], "home.arpa")
        self.assertTrue(all(k.startswith("HOMEISLAND_") for k in env))


class ValidatorsTest(unittest.TestCase):
    def test_ipv4(self):
        self.assertTrue(valid_ipv4("192.168.1.10"))
        for bad in ("256.1.1.1", "1.2.3", "a.b.c.d", "01.2.3.4.5", ""):
            self.assertFalse(valid_ipv4(bad), bad)

    def test_hostname(self):
        self.assertTrue(valid_hostname("wiki.home.arpa"))
        for bad in ("-bad.home.arpa", "under_score.arpa", "UPPER", "a..b", ""):
            self.assertFalse(valid_hostname(bad), bad)


class ConfigTest(TempInstall):
    def test_valid(self):
        self.assertEqual(self.cfg.validate(), [])
        self.assertEqual(self.cfg.bind_ip, "192.168.1.10")
        self.assertEqual(self.cfg.backup_dir, os.path.join(self.data_dir, "backups"))
        self.assertEqual(self.cfg.enabled_modules(), ["kiwix", "library", "maps"])

    def test_local_domain_rejected(self):
        self.cfg.values["HOMEISLAND_DOMAIN"] = "home.local"
        self.assertTrue(any(".local" in p for p in self.cfg.validate()))

    def test_bad_values(self):
        self.cfg.values["HOMEISLAND_HOST_IP"] = "999.1.1.1"
        self.cfg.values["HOMEISLAND_HTTP_PORT"] = "eighty"
        self.cfg.values["HOMEISLAND_DNS_UPSTREAMS"] = "dns.example.com"
        problems = "\n".join(self.cfg.validate())
        self.assertIn("HOMEISLAND_HOST_IP", problems)
        self.assertIn("HOMEISLAND_HTTP_PORT", problems)
        self.assertIn("HOMEISLAND_DNS_UPSTREAMS", problems)

    def test_state_dir_inside_repo_rejected(self):
        self.cfg.values["HOMEISLAND_STATE_DIR"] = os.path.join(self.cfg.repo_root, "state")
        self.assertTrue(any("outside the repository" in p for p in self.cfg.validate()))

    def test_extra_dns_records(self):
        self.write(self.cfg.dns_records_file, "# c\n192.168.1.20 printer nas.example.lan\n\n")
        self.assertEqual(self.cfg.extra_dns_records(),
                         [("printer.home.arpa", "192.168.1.20"), ("nas.example.lan", "192.168.1.20")])

    def test_extra_dns_records_invalid(self):
        self.write(self.cfg.dns_records_file, "printer 192.168.1.20\n")
        with self.assertRaises(HomeIslandError):
            self.cfg.extra_dns_records()


if __name__ == "__main__":
    unittest.main()
