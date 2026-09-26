"""Configuration backups and restore.

A backup is a gzip-compressed tar archive with a sidecar .sha256 file:

    homeisland-backup-20260101T030000Z.tar.gz
    homeisland-backup-20260101T030000Z.tar.gz.sha256

Archive layout:

    manifest.json          metadata and a SHA-256 for every file
    config/...             contents of the config directory (/etc/homeisland)
    state/<path>/...       selected module state below STATE_DIR
    extras/...             exports such as the Pi-hole Teleporter archive

SQLite databases are copied with SQLite's online backup API so a running
service never produces a torn copy. Large replaceable datasets (Wikipedia,
maps, media) are never included.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

from . import __version__
from .config import Config
from .modules import active_modules
from .util import HomeIslandError, human_bytes, info, try_output, warn

PREFIX = "homeisland-backup-"
SUFFIX = ".tar.gz"
FORMAT = 1
SQLITE_MAGIC = b"SQLite format 3\x00"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _excluded(rel: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or rel.startswith(p.rstrip("/") + "/") for p in patterns)


def collect_sources(cfg: Config) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Return ([(absolute_path, archive_name)], state paths) to back up."""
    items: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(cfg.config_dir):
        dirnames.sort()
        if dirpath != cfg.config_dir:
            items.append((dirpath, "config/" + os.path.relpath(dirpath, cfg.config_dir)))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            items.append((full, "config/" + os.path.relpath(full, cfg.config_dir)))
    state_paths = []
    for mod in active_modules(cfg):
        excludes = mod.backup_exclude
        for rel in mod.backup:
            base = cfg.state_path(rel)
            if not os.path.exists(base):
                continue
            state_paths.append(rel)
            for dirpath, dirnames, filenames in os.walk(base):
                reldir = os.path.relpath(dirpath, cfg.state_dir)
                dirnames[:] = sorted(d for d in dirnames if not _excluded(os.path.join(reldir, d), excludes))
                items.append((dirpath, "state/" + reldir))
                for fn in sorted(filenames):
                    relfile = os.path.join(reldir, fn)
                    if _excluded(relfile, excludes) or fn.endswith(("-wal", "-shm", "-journal")):
                        continue
                    full = os.path.join(dirpath, fn)
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    items.append((full, "state/" + relfile))
    return items, state_paths


def pihole_teleporter(cfg: Config, workdir: str) -> Optional[str]:
    """Export Pi-hole settings with its own Teleporter (consistent, importable in the web UI)."""
    from .stack import compose

    try:
        res = compose(cfg, "exec", "-T", "pihole", "sh", "-c",
                      "cd /tmp && rm -f pi-hole_*teleporter*.zip && pihole-FTL --teleporter >/dev/null "
                      "&& ls pi-hole_*teleporter*.zip", check=False, capture=True, timeout=120)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        name = res.stdout.strip().splitlines()[-1]
        dest = os.path.join(workdir, "pihole-teleporter.zip")
        container = try_output(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=homeisland",
                                "--filter", "label=com.docker.compose.service=pihole"])
        if not container:
            return None
        cid = container.split()[0]
        try_output(["docker", "cp", "%s:/tmp/%s" % (cid, name), dest], timeout=60)
        try_output(["docker", "exec", cid, "rm", "-f", "/tmp/" + name], timeout=30)
        return dest if os.path.exists(dest) else None
    except Exception as exc:
        warn("Pi-hole Teleporter export failed: %s" % exc)
        return None


def create_backup(cfg: Config, dest_dir: Optional[str] = None, keep: Optional[int] = None,
                  quiet: bool = False) -> str:
    cfg.require_installed()
    dest_dir = dest_dir or cfg.backup_dir
    if not os.path.isdir(dest_dir):
        raise HomeIslandError("backup destination %s does not exist (data disk or USB drive not mounted?)" % dest_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = PREFIX + stamp + SUFFIX
    final = os.path.join(dest_dir, name)
    items, state_paths = collect_sources(cfg)
    manifest: Dict[str, object] = {
        "format": FORMAT,
        "homeisland_version": __version__,
        "created_at": stamp,
        "config_dir": cfg.config_dir,
        "state_dir": cfg.state_dir,
        "data_dir": cfg.data_dir,
        "modules": cfg.enabled_modules(),
        "state_paths": state_paths,
        "files": {},
    }
    git = try_output(["git", "-C", cfg.repo_root, "rev-parse", "HEAD"], timeout=5)
    if git:
        manifest["git_commit"] = git.strip()

    workdir = tempfile.mkdtemp(prefix="homeisland-backup-")
    tmp_archive = os.path.join(dest_dir, "." + name + ".partial")
    try:
        files: Dict[str, str] = manifest["files"]  # type: ignore[assignment]
        with tarfile.open(tmp_archive, "w:gz", compresslevel=6) as tar:
            for idx, (src, arcname) in enumerate(items):
                path = src
                if os.path.isdir(src):
                    ti = tar.gettarinfo(src, arcname)
                    ti.uname = ti.gname = ""
                    tar.addfile(ti)
                    continue
                if _is_sqlite(src):
                    # Consistent snapshot of a live database.
                    path = os.path.join(workdir, "db-%d.sqlite" % idx)
                    try:
                        with sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(src), uri=True,
                                             timeout=30) as source, \
                                sqlite3.connect(path) as target:
                            source.backup(target)
                    except sqlite3.Error as exc:
                        warn("SQLite backup of %s failed (%s); copying the file instead" % (src, exc))
                        path = src
                try:
                    files[arcname] = _sha256_file(path)
                    st = os.stat(src)
                    ti = tar.gettarinfo(path, arcname)
                    ti.uid, ti.gid, ti.mode, ti.mtime = st.st_uid, st.st_gid, st.st_mode & 0o7777, st.st_mtime
                    ti.uname = ti.gname = ""
                    with open(path, "rb") as fh:
                        tar.addfile(ti, fh)
                except OSError as exc:
                    warn("skipping %s: %s" % (src, exc))
                    files.pop(arcname, None)
            if "dns" in [m.name for m in active_modules(cfg)]:
                tele = pihole_teleporter(cfg, workdir)
                if tele:
                    files["extras/pihole-teleporter.zip"] = _sha256_file(tele)
                    tar.add(tele, "extras/pihole-teleporter.zip")
            data = json.dumps(manifest, indent=2, sort_keys=True).encode()
            ti = tarfile.TarInfo("manifest.json")
            ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o600
            tar.addfile(ti, io.BytesIO(data))
        os.chmod(tmp_archive, 0o600)
        digest = _sha256_file(tmp_archive)
        with open(final + ".sha256", "w") as fh:
            fh.write("%s  %s\n" % (digest, name))
        os.chmod(final + ".sha256", 0o600)
        os.replace(tmp_archive, final)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if os.path.exists(tmp_archive):
            os.unlink(tmp_archive)
    verify_backup(final)
    if not quiet:
        info("Backup written: %s (%s, %d files)" % (final, human_bytes(os.path.getsize(final)),
                                                     len(manifest["files"])))  # type: ignore[arg-type]
    prune(dest_dir, keep if keep is not None else cfg.get_int("HOMEISLAND_BACKUP_KEEP"), quiet=quiet)
    return final


def list_backups(directory: str) -> List[str]:
    return sorted(glob.glob(os.path.join(directory, PREFIX + "*" + SUFFIX)))


def prune(directory: str, keep: int, quiet: bool = False) -> None:
    if keep <= 0:
        return
    for old in list_backups(directory)[:-keep]:
        for path in (old, old + ".sha256"):
            if os.path.exists(path):
                os.unlink(path)
        if not quiet:
            info("Removed old backup %s" % os.path.basename(old))


def read_manifest(path: str) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        member = tar.getmember("manifest.json")
        fh = tar.extractfile(member)
        if fh is None:
            raise HomeIslandError("manifest missing in %s" % path)
        return json.load(fh)


def verify_backup(path: str) -> dict:
    """Verify the archive checksum and every file against the manifest. Returns the manifest."""
    if not os.path.isfile(path):
        raise HomeIslandError("backup not found: %s" % path)
    sidecar = path + ".sha256"
    if os.path.exists(sidecar):
        expected = open(sidecar).read().split()[0]
        if _sha256_file(path) != expected:
            raise HomeIslandError("archive checksum mismatch: %s is damaged" % path)
    else:
        warn("no .sha256 file next to %s; checking contents only" % path)
    try:
        manifest = read_manifest(path)
        files = manifest.get("files", {})
        seen = set()
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                if member.name == "manifest.json":
                    continue
                _check_member_name(member.name)
                if not member.isfile():
                    continue
                fh = tar.extractfile(member)
                h = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):  # type: ignore[union-attr]
                    h.update(chunk)
                if files.get(member.name) != h.hexdigest():
                    raise HomeIslandError("file %s does not match the manifest" % member.name)
                seen.add(member.name)
        missing = set(files) - seen
        if missing:
            raise HomeIslandError("files listed in the manifest are missing: %s" % ", ".join(sorted(missing)[:5]))
    except (tarfile.TarError, OSError, EOFError, KeyError, ValueError) as exc:
        raise HomeIslandError("cannot read backup %s: %s" % (path, exc))
    return manifest


def _check_member_name(name: str) -> None:
    norm = os.path.normpath(name)
    if name.startswith("/") or norm.startswith("..") or not norm.split(os.sep)[0] in ("config", "state", "extras"):
        raise HomeIslandError("unsafe path in backup: %s" % name)


def resolve_backup(cfg: Config, spec: str) -> str:
    if spec == "latest":
        backups = list_backups(cfg.backup_dir)
        if not backups:
            raise HomeIslandError("no backups found in %s" % cfg.backup_dir)
        return backups[-1]
    return os.path.abspath(spec)


def restore_backup(cfg: Config, path: str, target_state_dir: Optional[str] = None) -> dict:
    """Restore configuration and state from a verified backup.

    Existing files that would be replaced are moved aside to *.pre-restore-<time>
    directories first, so a restore can be undone by hand.
    """
    manifest = verify_backup(path)
    state_dir = target_state_dir or manifest.get("state_dir") or cfg.state_dir
    config_dir = cfg.config_dir
    stamp = time.strftime("%Y%m%dT%H%M%S")
    moved = []
    if os.path.isdir(config_dir) and os.listdir(config_dir):
        aside = "%s.pre-restore-%s" % (config_dir.rstrip("/"), stamp)
        shutil.copytree(config_dir, aside)
        moved.append(aside)
        shutil.rmtree(config_dir)
    for rel in manifest.get("state_paths", []):
        current = os.path.join(state_dir, rel)
        if os.path.exists(current):
            aside = "%s.pre-restore-%s" % (current.rstrip("/"), stamp)
            os.replace(current, aside)
            moved.append(aside)
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    os.makedirs(state_dir, mode=0o755, exist_ok=True)
    as_root = hasattr(os, "chown") and os.geteuid() == 0
    dirs = []
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            if member.name == "manifest.json" or not (member.isfile() or member.isdir()):
                continue
            _check_member_name(member.name)
            top, _, rel = member.name.partition("/")
            if top == "config":
                dest = os.path.join(config_dir, rel)
            elif top == "state":
                dest = os.path.join(state_dir, rel)
            else:
                dest = os.path.join(state_dir, "restored-extras", rel)
            if member.isdir():
                os.makedirs(dest, exist_ok=True)
                dirs.append((dest, member))
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with open(dest, "wb") as fh:
                shutil.copyfileobj(src, fh)
            os.chmod(dest, member.mode & 0o7777)
            if as_root:
                os.chown(dest, member.uid, member.gid)
            os.utime(dest, (member.mtime, member.mtime))
    # Directory permissions last, so read-only directories do not block extraction.
    for dest, member in reversed(dirs):
        os.chmod(dest, member.mode & 0o7777)
        if as_root:
            os.chown(dest, member.uid, member.gid)
    if moved:
        info("Previous files were kept in:\n  " + "\n  ".join(moved))
    return manifest
