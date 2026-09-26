import io
import json
import os
import sqlite3
import tarfile
import unittest
from unittest import mock

from helpers import TempInstall, read

from homeisland import backup
from homeisland.util import HomeIslandError


class BackupTest(TempInstall):
    def setUp(self):
        super().setUp()
        os.makedirs(self.cfg.backup_dir)
        pihole = os.path.join(self.state_dir, "pihole")
        self.write(os.path.join(pihole, "pihole.toml"), "[dns]\nupstreams = []\n")
        self.write(os.path.join(pihole, "pihole-FTL.db"), "query log that must be excluded")
        self.write(os.path.join(pihole, "tls.pem"), "regenerated key, excluded")
        db = sqlite3.connect(os.path.join(pihole, "gravity.db"))
        db.execute("CREATE TABLE adlist (address TEXT)")
        db.execute("INSERT INTO adlist VALUES ('https://example.invalid/list')")
        db.commit()
        db.close()
        self.write(os.path.join(self.state_dir, "kiwix", "library.xml"), "<library/>")
        # Pi-hole's Teleporter needs Docker; not available in unit tests.
        patcher = mock.patch.object(backup, "pihole_teleporter", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self):
        return backup.create_backup(self.cfg, quiet=True)

    def test_create_and_verify(self):
        path = self.make()
        self.assertTrue(os.path.exists(path + ".sha256"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        manifest = backup.verify_backup(path)
        files = manifest["files"]
        self.assertIn("config/secrets.env", files)
        self.assertIn("state/pihole/pihole.toml", files)
        self.assertIn("state/pihole/gravity.db", files)
        self.assertIn("state/kiwix/library.xml", files)
        self.assertNotIn("state/pihole/pihole-FTL.db", files)
        self.assertNotIn("state/pihole/tls.pem", files)
        self.assertEqual(manifest["modules"], ["kiwix", "library", "maps"])

    def test_corruption_detected(self):
        path = self.make()
        with open(path, "r+b") as fh:
            fh.seek(40)
            fh.write(b"\x00\x00\x00\x00")
        with self.assertRaises(HomeIslandError):
            backup.verify_backup(path)

    def test_manifest_mismatch_detected(self):
        path = self.make()
        os.unlink(path + ".sha256")
        # Rebuild the archive with one member altered but the old manifest.
        tampered = path + ".tampered.tar.gz"
        with tarfile.open(path) as src, tarfile.open(tampered, "w:gz") as dst:
            for member in src.getmembers():
                data = src.extractfile(member).read() if member.isfile() else None
                if member.name == "config/homeisland.env":
                    data = b"HOMEISLAND_HOST_IP=10.0.0.1\n"
                    member.size = len(data)
                dst.addfile(member, io.BytesIO(data) if data is not None else None)
        with self.assertRaises(HomeIslandError):
            backup.verify_backup(tampered)

    def test_unsafe_member_rejected(self):
        evil = os.path.join(self.cfg.backup_dir, "homeisland-backup-evil.tar.gz")
        with tarfile.open(evil, "w:gz") as tar:
            data = json.dumps({"files": {}}).encode()
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
            ti = tarfile.TarInfo("../../etc/passwd")
            ti.size = 1
            tar.addfile(ti, io.BytesIO(b"x"))
        with self.assertRaises(HomeIslandError):
            backup.verify_backup(evil)

    def test_restore_after_total_loss(self):
        path = self.make()
        # Simulate a dead system disk: configuration and state are gone.
        import shutil

        shutil.rmtree(self.config_dir)
        shutil.rmtree(self.state_dir)
        os.makedirs(self.config_dir)
        from homeisland.config import Config

        cfg = Config(config_dir=self.config_dir, repo_root=self.cfg.repo_root)
        backup.restore_backup(cfg, path)
        restored = Config(config_dir=self.config_dir, repo_root=self.cfg.repo_root)
        self.assertEqual(restored.host_ip, "192.168.1.10")
        self.assertEqual(restored.secrets["PIHOLE_WEB_PASSWORD"], "test-password")
        self.assertEqual(os.stat(restored.secrets_file).st_mode & 0o777, 0o600)
        db = sqlite3.connect(os.path.join(self.state_dir, "pihole", "gravity.db"))
        self.assertEqual(db.execute("SELECT count(*) FROM adlist").fetchone()[0], 1)
        db.close()

    def test_restore_keeps_previous_files(self):
        path = self.make()
        self.write(os.path.join(self.state_dir, "pihole", "pihole.toml"), "changed after backup")
        backup.restore_backup(self.cfg, path)
        self.assertIn("[dns]", read(os.path.join(self.state_dir, "pihole", "pihole.toml")))
        asides = [d for d in os.listdir(self.state_dir) if d.startswith("pihole.pre-restore-")]
        self.assertEqual(len(asides), 1)

    def test_prune(self):
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            p = os.path.join(self.cfg.backup_dir, "homeisland-backup-%s.tar.gz" % stamp)
            self.write(p, "x")
            self.write(p + ".sha256", "x")
        backup.prune(self.cfg.backup_dir, 2, quiet=True)
        names = sorted(os.listdir(self.cfg.backup_dir))
        self.assertEqual(len([n for n in names if n.endswith(".tar.gz")]), 2)
        self.assertNotIn("homeisland-backup-20260101T000000Z.tar.gz", names)

    def test_missing_destination(self):
        with self.assertRaises(HomeIslandError):
            backup.create_backup(self.cfg, dest_dir=os.path.join(self.tmp, "not-mounted"), quiet=True)


if __name__ == "__main__":
    unittest.main()
