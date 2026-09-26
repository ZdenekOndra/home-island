"""Safe updates from git, rollback, and systemd unit installation.

Updates never touch configuration (/etc/homeisland), state, datasets or
backups; they change only the repository checkout and pinned image versions.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from .config import Config
from .util import HomeIslandError, confirm, info, run, warn, write_atomic

UNITS = ["homeisland-collector.service", "homeisland-boot.service", "homeisland-backup.service",
         "homeisland-backup.timer"]
ENABLE = ["homeisland-collector.service", "homeisland-boot.service", "homeisland-backup.timer"]
SYSTEMD_DIR = "/etc/systemd/system"


def install_units(cfg: Config) -> None:
    src_dir = cfg.repo_path("configs", "systemd")
    changed = False
    for unit in UNITS:
        with open(os.path.join(src_dir, unit)) as fh:
            content = fh.read().replace("@HOMEISLAND_HOME@", cfg.repo_root)
        changed |= write_atomic(os.path.join(SYSTEMD_DIR, unit), content, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--quiet"] + ENABLE)
    run(["systemctl", "start", "homeisland-backup.timer"])
    run(["systemctl", "restart" if changed else "start", "homeisland-collector.service"])


def _git(cfg: Config, *args: str, capture: bool = True) -> str:
    res = run(["git", "-C", cfg.repo_root] + list(args), capture=capture)
    return (res.stdout or "").strip() if capture else ""


def history_file(cfg: Config) -> str:
    return cfg.state_path("update-history")


def _history(cfg: Config) -> List[List[str]]:
    try:
        with open(history_file(cfg)) as fh:
            return [line.split() for line in fh if line.strip()]
    except OSError:
        return []


def _post_update(cfg: Config) -> None:
    # Run the *new* code for the remaining steps.
    run([cfg.repo_path("homeisland"), "system", "post-update"])


def update(cfg: Config, ref: Optional[str] = None, assume_yes: bool = False, backup_first: bool = True) -> int:
    if not os.path.isdir(cfg.repo_path(".git")):
        raise HomeIslandError("%s is not a git checkout; update manually" % cfg.repo_root)
    dirty = _git(cfg, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise HomeIslandError("the repository has local changes; commit or stash them first:\n" + dirty)
    current = _git(cfg, "rev-parse", "HEAD")
    _git(cfg, "fetch", "--tags", "--quiet", "origin", capture=False)
    if ref:
        target_ref = ref
    else:
        try:
            target_ref = _git(cfg, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        except HomeIslandError:
            raise HomeIslandError("no upstream branch; pass --ref <tag|branch>")
    target = _git(cfg, "rev-parse", target_ref + "^{commit}")
    if target == current:
        info("Already up to date (%s)." % current[:10])
        return 0
    log = _git(cfg, "log", "--oneline", "--no-decorate", "%s..%s" % (current, target))
    info("Changes %s..%s:\n%s" % (current[:10], target[:10], log or "(not a fast-forward; switching versions)"))
    if not confirm("Update to %s?" % target_ref, assume_yes):
        info("Aborted.")
        return 1
    if backup_first:
        from .backup import create_backup

        try:
            create_backup(cfg, keep=0)
        except HomeIslandError as exc:
            raise HomeIslandError("pre-update backup failed (%s); use --no-backup to skip" % exc)
    branch = _git(cfg, "rev-parse", "--abbrev-ref", "HEAD")
    if not ref and branch != "HEAD":
        _git(cfg, "merge", "--ff-only", "--quiet", target, capture=False)
    else:
        _git(cfg, "checkout", "--quiet", target, capture=False)
    with open(history_file(cfg), "a") as fh:
        fh.write("%s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), current, target))
    _post_update(cfg)
    info("Updated to %s. If anything misbehaves: sudo homeisland update --rollback" % target[:10])
    return 0


def rollback(cfg: Config, assume_yes: bool = False) -> int:
    entries = _history(cfg)
    if not entries:
        raise HomeIslandError("no recorded update to roll back")
    _, previous, updated = entries[-1][:3]
    current = _git(cfg, "rev-parse", "HEAD")
    if current != updated:
        warn("the checkout (%s) is not the version installed by the last update (%s)" % (current[:10], updated[:10]))
    if not confirm("Roll back to %s?" % previous[:10], assume_yes):
        return 1
    _git(cfg, "checkout", "--quiet", previous, capture=False)
    with open(history_file(cfg), "a") as fh:
        fh.write("%s %s %s rollback\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), current, previous))
    _post_update(cfg)
    info("Rolled back to %s. Configuration is unchanged; if a restore is needed: sudo homeisland restore latest"
         % previous[:10])
    return 0
