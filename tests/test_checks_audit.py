import os
import unittest

from helpers import ROOT, TempInstall

from homeisland import checks
from homeisland.audit import scan_external_references
from homeisland.modules import active_modules


class DatasetTest(TempInstall):
    def test_dataset_states(self):
        mods = {m.name: m for m in active_modules(self.cfg)}
        check = {"name": "Wikipedia datasets", "type": "dataset", "glob": "kiwix/*.zim"}
        # No marker: the data disk counts as missing.
        self.assertEqual(checks.run_check(self.cfg, mods["kiwix"], check, []).state, checks.FAIL)
        self.write(self.cfg.data_marker, "")
        self.assertEqual(checks.run_check(self.cfg, mods["kiwix"], check, []).state, checks.NODATA)
        self.write(os.path.join(self.data_dir, "kiwix", "wikipedia.zim"), "")
        self.assertEqual(checks.run_check(self.cfg, mods["kiwix"], check, []).state, checks.PASS)

    def test_recursive_glob(self):
        self.assertFalse(checks.dataset_present(self.data_dir, "library/**/*.*"))
        self.write(os.path.join(self.data_dir, "library", "A", "B", "doc.pdf"), "")
        self.assertTrue(checks.dataset_present(self.data_dir, "library/**/*.*"))

    def test_status_check(self):
        mod = [m for m in active_modules(self.cfg) if m.name == "monitoring"][0]
        check = {"name": "Monitoring", "type": "status", "max_age": 60}
        self.assertEqual(checks.run_check(self.cfg, mod, check, []).state, checks.FAIL)
        import json
        import time

        self.write(self.cfg.status_file, json.dumps({"generated_at": time.time()}))
        self.assertEqual(checks.run_check(self.cfg, mod, check, []).state, checks.PASS)
        self.write(self.cfg.status_file, json.dumps({"generated_at": time.time() - 600}))
        self.assertEqual(checks.run_check(self.cfg, mod, check, []).state, checks.FAIL)

    def test_module_states(self):
        R = checks.Result
        states = checks.module_states([R("a", "x", checks.PASS), R("a", "y", checks.NODATA),
                                       R("b", "x", checks.PASS), R("c", "x", checks.FAIL), R("c", "y", checks.PASS)])
        self.assertEqual(states, {"a": "nodata", "b": "up", "c": "down"})


class ExternalReferenceTest(TempInstall):
    def test_repository_web_assets_are_offline(self):
        hits = scan_external_references([os.path.join(ROOT, "dashboard"), os.path.join(ROOT, "modules")])
        self.assertEqual(hits, [])

    def test_detects_external_urls(self):
        web = os.path.join(self.tmp, "web")
        self.write(os.path.join(web, "index.html"),
                   '<link href="https://fonts.googleapis.com/css?family=Inter">\n'
                   '<script src="//cdn.example.com/lib.js"></script>\n'
                   '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n'
                   '<a href="http://wiki.home.arpa/">ok</a>\n')
        self.write(os.path.join(web, "app.css"), "@import url(https://cdn.example.net/x.css);\n")
        hits = scan_external_references([web])
        self.assertEqual(len(hits), 3, hits)


if __name__ == "__main__":
    unittest.main()
