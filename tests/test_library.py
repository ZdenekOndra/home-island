"""Tests for the document library app (modules/library/app/library.py)."""

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile

from helpers import ROOT


def load_library(tmp):
    os.environ["LIBRARY_INDEX_DB"] = os.path.join(tmp, "index", "library.db")
    os.environ["LIBRARY_LOCK_FILE"] = os.path.join(tmp, "index", ".lock")
    os.environ["LIBRARY_COLLECTIONS"] = "library=%s:index;3d-models=%s:browse" % (
        os.path.join(tmp, "library"), os.path.join(tmp, "models"))
    spec = importlib.util.spec_from_file_location(
        "library_app", os.path.join(ROOT, "modules", "library", "app", "library.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="homeisland-lib-")
        lib = os.path.join(self.tmp, "library")
        os.makedirs(os.path.join(lib, "Heating"))
        os.makedirs(os.path.join(lib, "Medical"))
        os.makedirs(os.path.join(lib, ".hidden"))
        os.makedirs(os.path.join(self.tmp, "models"))
        with open(os.path.join(lib, "Heating", "boiler.md"), "w") as fh:
            fh.write("# Boiler manual\n\nBleed the radiators and check the výměník pressure.\n")
        with open(os.path.join(lib, "Medical", "aid.html"), "w") as fh:
            fh.write("<html><head><title>First aid</title><script>var secretword=1</script></head>"
                     "<body><p>Apply pressure to stop bleeding.</p></body></html>")
        with open(os.path.join(lib, ".hidden", "private.txt"), "w") as fh:
            fh.write("hiddenword")
        with zipfile.ZipFile(os.path.join(lib, "Medical", "book.epub"), "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("OEBPS/content.opf",
                        '<package><metadata><dc:title>Field Guide</dc:title></metadata>'
                        '<manifest><item id="c1" href="c1.xhtml"/></manifest><spine><itemref idref="c1"/></spine>'
                        '</package>')
            zf.writestr("OEBPS/c1.xhtml", "<html><body><p>Splint a broken arm.</p></body></html>")
        self.lib = load_library(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_and_search(self):
        stats = self.lib.build_index(verbose=False)
        self.assertEqual(stats["added"], 3)
        results, total = self.lib.search("radiators")
        self.assertEqual(total, 1)
        self.assertEqual(results[0]["title"], "Boiler manual")
        self.assertEqual(results[0]["category"], "Heating")
        # Diacritics-insensitive and prefix matching.
        self.assertEqual(self.lib.search("vymenik")[1], 1)
        self.assertEqual(self.lib.search("splin")[1], 1)
        self.assertEqual(self.lib.search("Field Guide")[0][0]["title"], "Field Guide")
        # Scripts and hidden folders are not indexed.
        self.assertEqual(self.lib.search("secretword")[1], 0)
        self.assertEqual(self.lib.search("hiddenword")[1], 0)
        # Category filter.
        self.assertEqual(self.lib.search("pressure", category="Medical")[1], 1)
        self.assertEqual(self.lib.search("pressure")[1], 2)

    def test_incremental(self):
        self.lib.build_index(verbose=False)
        stats = self.lib.build_index(verbose=False)
        self.assertEqual(stats["unchanged"], 3)
        os.unlink(os.path.join(self.tmp, "library", "Heating", "boiler.md"))
        stats = self.lib.build_index(verbose=False)
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(self.lib.search("radiators")[1], 0)

    def test_missing_collection_keeps_index(self):
        self.lib.build_index(verbose=False)
        shutil.move(os.path.join(self.tmp, "library"), os.path.join(self.tmp, "unplugged"))
        stats = self.lib.build_index(verbose=False)
        self.assertEqual(stats["removed"], 0)
        self.assertEqual(self.lib.search("radiators")[1], 1)

    def test_query_sanitising(self):
        self.assertIsNone(self.lib.fts_query('"*()'))
        self.assertEqual(self.lib.fts_query('boil" OR 1=1 --'), '"boil" AND "OR" AND "1" AND "1"*')
        self.lib.build_index(verbose=False)
        for q in ('"', "NEAR(", "a AND", "*", "col:x", "^"):
            self.lib.search(q)  # must not raise

    def test_path_resolution(self):
        root = os.path.realpath(os.path.join(self.tmp, "library"))
        self.assertEqual(self.lib.resolve("library", "Heating/boiler.md"), os.path.join(root, "Heating", "boiler.md"))
        self.assertIsNone(self.lib.resolve("library", "../models"))
        self.assertIsNone(self.lib.resolve("library", "Heating/../../../etc/passwd"))
        self.assertIsNone(self.lib.resolve("library", ".hidden/private.txt"))
        self.assertIsNone(self.lib.resolve("unknown", "x"))
        os.symlink("/etc", os.path.join(root, "escape"))
        self.assertIsNone(self.lib.resolve("library", "escape/passwd"))


if __name__ == "__main__":
    unittest.main()
