"""Offline knowledge: Kiwix ZIM archives and the document library index."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import urllib.parse
from typing import List, Optional

from .config import Config
from .modules import load_modules
from .stack import EMPTY_KIWIX_LIBRARY, compose, module_image
from .util import HomeIslandError, human_bytes, info, read_text, run, table, warn, write_atomic


def zim_files(cfg: Config) -> List[str]:
    directory = cfg.data_path("kiwix")
    try:
        return sorted(f for f in os.listdir(directory) if f.endswith(".zim") and not f.startswith("."))
    except OSError:
        return []


def library_books(cfg: Config) -> List[str]:
    text = read_text(cfg.state_path("kiwix", "library.xml"), "") or ""
    return [os.path.basename(p) for p in re.findall(r'\bpath="([^"]+)"', text)]


def list_knowledge(cfg: Config) -> None:
    enabled = cfg.enabled_modules()
    if "kiwix" in enabled:
        zims = zim_files(cfg)
        served = set(library_books(cfg))
        rows = []
        for name in zims:
            size = os.path.getsize(cfg.data_path("kiwix", name))
            rows.append((name, human_bytes(size), "served" if name in served else "not in library"))
        info("Kiwix archives in %s:" % cfg.data_path("kiwix"))
        info(table(rows, ("FILE", "SIZE", "STATE")) if rows else "  none (see docs/KNOWLEDGE.md)")
        missing = [n for n in zims if n not in served]
        if missing:
            info("\nRun `homeisland knowledge update` to serve new archives.")
    else:
        info("Kiwix module is not enabled.")
    if "library" in enabled:
        info("")
        res = compose(cfg, "exec", "-T", "library", "python3", "/app/library.py", "stats", check=False, capture=True,
                      timeout=30)
        if res.returncode == 0:
            info("Document library index:\n" + res.stdout.strip())
        else:
            warn("document library is not running")


def update_kiwix_library(cfg: Config) -> None:
    """Rebuild library.xml from the ZIM files present. kiwix-serve reloads it automatically."""
    registry = load_modules(cfg)
    if "kiwix" not in cfg.enabled_modules():
        raise HomeIslandError("the kiwix module is not enabled")
    if not os.path.exists(cfg.data_marker):
        raise HomeIslandError("data disk not available at %s" % cfg.data_dir)
    image = module_image(registry["kiwix"])
    state = cfg.state_path("kiwix")
    os.makedirs(state, exist_ok=True)
    new_lib = os.path.join(state, "library.new.xml")
    write_atomic(new_lib, EMPTY_KIWIX_LIBRARY, 0o644)
    added, failed = [], []
    for name in zim_files(cfg):
        res = run(
            ["docker", "run", "--rm", "--network", "none", "--user", "0",
             "-v", "%s:/library" % state, "-v", "%s:/data:ro" % cfg.data_path("kiwix"),
             "--entrypoint", "kiwix-manage", image, "/library/library.new.xml", "add", "/data/" + name],
            check=False, capture=True, timeout=600,
        )
        (added if res.returncode == 0 else failed).append(name)
        if res.returncode != 0:
            warn("kiwix-manage could not add %s (corrupt or incomplete download?)" % name)
    os.chmod(new_lib, 0o644)
    os.replace(new_lib, os.path.join(state, "library.xml"))
    info("Kiwix library updated: %d archive(s) served%s." % (
        len(added), ", %d failed" % len(failed) if failed else ""))


def index_library(cfg: Config) -> None:
    if "library" not in cfg.enabled_modules():
        raise HomeIslandError("the library module is not enabled")
    compose(cfg, "exec", "-T", "library", "python3", "/app/library.py", "index")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_zim(cfg: Config, url: str, checksum: Optional[str] = None) -> str:
    """Download a ZIM file with resume support and verify its checksum when one is published."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise HomeIslandError("only https:// URLs are accepted")
    name = os.path.basename(parsed.path)
    if not name.endswith(".zim") or "/" in name or name.startswith("."):
        raise HomeIslandError("URL must point to a .zim file")
    if not os.path.exists(cfg.data_marker):
        raise HomeIslandError("data disk not available at %s" % cfg.data_dir)
    if shutil.which("curl") is None:
        raise HomeIslandError("curl is required for downloads")
    dest_dir = cfg.data_path("kiwix")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    part = dest + ".part"
    if os.path.exists(dest):
        raise HomeIslandError("%s already exists" % dest)
    info("Downloading %s\n  -> %s (resumable; re-run the command to continue)" % (url, dest))
    run(["curl", "-fL", "--retry", "5", "--retry-delay", "10", "-C", "-", "-o", part, url])
    if checksum is None:
        res = run(["curl", "-fsL", "--max-time", "30", url + ".sha256"], check=False, capture=True)
        if res.returncode == 0 and res.stdout.strip():
            checksum = res.stdout.split()[0]
    if checksum:
        info("Verifying SHA-256 ...")
        actual = _sha256(part)
        if actual.lower() != checksum.lower():
            raise HomeIslandError("checksum mismatch for %s (expected %s, got %s); delete %s and retry"
                                  % (name, checksum, actual, part))
    else:
        warn("no checksum published for %s; file not verified" % name)
    os.chmod(part, 0o644)
    os.replace(part, dest)
    info("Saved %s (%s)." % (dest, human_bytes(os.path.getsize(dest))))
    return dest
