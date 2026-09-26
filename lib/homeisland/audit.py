"""Offline readiness audit: `homeisland audit`.

Each finding has a level (OK, INFO, WARN, FAIL), a short title and, for
anything that is not OK, an actionable hint.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from typing import List, Optional

from . import checks, netcheck
from .backup import list_backups
from .collector import data_disk_state, disk_usage, mount_point
from .config import Config
from .modules import active_modules
from .stack import compose, docker_available
from .util import color, try_output

OK, INFO, WARN, FAIL = "OK", "INFO", "WARN", "FAIL"

# Allowed absolute URLs in shipped web assets (namespaces and documentation only).
URL_ALLOW = re.compile(
    r"https?://(www\.w3\.org/|[a-z0-9.-]*\.home\.arpa|localhost|127\.0\.0\.1|github\.com/maplibre/maplibre-gl-js/blob/)"
)
URL_RE = re.compile(r"""(?:https?:)?//[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s"'<>)]*""")
WEB_EXT = (".html", ".htm", ".css", ".js", ".mjs", ".svg", ".json")


class Finding:
    def __init__(self, level: str, title: str, hint: str = ""):
        self.level, self.title, self.hint = level, title, hint


def scan_external_references(paths: List[str]) -> List[str]:
    """Find absolute URLs to other hosts in web assets that would be loaded by browsers."""
    hits = []
    for base in paths:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(WEB_EXT):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith(("//", "*", "/*", "#")):
                        continue
                    for m in URL_RE.finditer(line):
                        url = m.group(0)
                        if url.startswith("//") and not re.search(r"(src|href|url)\s*=?\s*\(?[\"']?//", line):
                            continue  # a comment or regex, not a protocol-relative URL
                        if URL_ALLOW.match(url if not url.startswith("//") else "http:" + url):
                            continue
                        hits.append("%s:%d: %s" % (os.path.relpath(full), lineno, url[:80]))
    return hits


def audit(cfg: Config) -> List[Finding]:
    f: List[Finding] = []
    add = lambda *a: f.append(Finding(*a))  # noqa: E731
    mods = active_modules(cfg)
    names = [m.name for m in mods]

    # -- configuration -------------------------------------------------------------
    problems = cfg.validate()
    if problems:
        for p in problems:
            add(FAIL, "Configuration: " + p, "edit %s" % cfg.settings_file)
    else:
        add(OK, "Configuration is valid")
    if os.path.exists(cfg.secrets_file):
        mode = stat.S_IMODE(os.stat(cfg.secrets_file).st_mode)
        if mode & 0o077:
            add(FAIL, "Secrets file is readable by other users (mode %o)" % mode,
                "sudo chmod 600 %s" % cfg.secrets_file)
        else:
            add(OK, "Secrets file permissions are restrictive")
    else:
        add(FAIL, "Secrets file missing", "re-run install.sh to generate secrets")
    if cfg.bind_ip == "0.0.0.0":
        add(WARN, "Services are published on all interfaces",
            "set HOMEISLAND_BIND_IP (or HOMEISLAND_HOST_IP) to the LAN address; Docker bypasses host firewalls")

    addrs = netcheck.local_ipv4_addresses()
    if addrs and cfg.host_ip not in addrs:
        add(FAIL, "HOMEISLAND_HOST_IP %s is not assigned to this machine (has %s)" % (cfg.host_ip, ", ".join(addrs)),
            "reserve a fixed address for this server in the router (DHCP reservation) and update the setting")

    # -- web assets: no external dependencies -----------------------------------------
    hits = scan_external_references([cfg.repo_path("dashboard"), cfg.repo_path("modules")])
    if hits:
        add(FAIL, "Web assets reference external hosts (%d)" % len(hits), "\n".join(hits[:10]))
    else:
        add(OK, "Dashboard and module web assets load nothing from the Internet")
    caddyfile = os.path.join(cfg.generated_dir, "caddy", "Caddyfile")
    text = open(caddyfile).read() if os.path.exists(caddyfile) else ""
    if "connect-src 'self'" in text:
        add(OK, "Dashboard Content-Security-Policy blocks external requests")
    else:
        add(WARN, "Dashboard Content-Security-Policy not found", "run: sudo homeisland apply")

    # -- containers ---------------------------------------------------------------------
    if not docker_available():
        add(FAIL, "Docker is not running or not accessible", "sudo systemctl status docker")
    else:
        res = compose(cfg, "config", "--format", "json", check=False, capture=True, timeout=60)
        if res.returncode != 0:
            add(FAIL, "docker compose config failed", (res.stderr or "").strip()[:300])
        else:
            conf = json.loads(res.stdout)
            for svc, spec in sorted(conf.get("services", {}).items()):
                restart = spec.get("restart")
                if restart not in ("unless-stopped", "always"):
                    add(FAIL, "Service %s has restart policy %r" % (svc, restart),
                        "set restart: unless-stopped in its compose file")
                for vol in spec.get("volumes", []):
                    if vol.get("type") == "volume" and not vol.get("source"):
                        add(WARN, "Service %s uses an anonymous volume at %s" % (svc, vol.get("target")),
                            "anonymous volumes are lost when the container is replaced; use a bind mount")
                logging = (spec.get("logging") or {}).get("options", {})
                if "max-size" not in logging:
                    add(WARN, "Service %s has no log size limit" % svc, "add a json-file max-size option")
            add(OK, "Compose configuration: restart policies, persistent bind mounts and log limits checked")
        from .collector import containers as list_containers

        ctrs = list_containers() or []
        bad = [c for c in ctrs if c.get("state") != "running" or c.get("health") == "unhealthy"]
        for c in bad:
            add(FAIL, "Container %s is %s%s" % (c.get("service"), c.get("state"),
                                                 " (unhealthy)" if c.get("health") == "unhealthy" else ""),
                "homeisland logs %s" % c.get("service"))
        if ctrs and not bad:
            add(OK, "All %d containers are running" % len(ctrs))

    # -- DNS and services ------------------------------------------------------------------
    for r in checks.run_checks(cfg, mods, kinds={"dns", "http", "tcp", "status", "dataset"}):
        if r.state == checks.PASS:
            add(OK, "%s: %s" % (r.name, r.detail))
        elif r.state == checks.NODATA:
            add(WARN, "%s: dataset missing" % r.name, r.detail)
        else:
            hint = {"dns": "homeisland logs pihole; check HOMEISLAND_HOST_IP",
                    "status": "sudo systemctl status homeisland-collector"}.get(r.kind, "homeisland logs")
            add(FAIL, "%s: %s" % (r.name, r.detail), hint)
    upstreams = [u for u in cfg.get("HOMEISLAND_DNS_UPSTREAMS").split(";") if u]
    if len(upstreams) < 2:
        add(INFO, "Only one upstream DNS server configured", "add a second provider for resilience")

    # -- storage ---------------------------------------------------------------------------
    sysdisk = disk_usage("/")
    if sysdisk:
        pct = sysdisk["percent"]
        lvl = FAIL if pct >= 95 else WARN if pct >= 85 else OK
        add(lvl, "System disk %.0f%% used (%.1f GB free)" % (pct, sysdisk["free"] / 1e9),
            "" if lvl == OK else "docker system prune; check logs and backups on the system disk")
    data = data_disk_state(cfg)
    needs_data = [m.name for m in mods if m.requires_data]
    if not data["present"]:
        add(FAIL if needs_data else WARN, "Data disk not available at %s" % cfg.data_dir,
            "mount the data disk (see docs/STORAGE.md); affected modules: %s" % (", ".join(needs_data) or "none"))
    else:
        if not data["separate_device"]:
            add(WARN, "Data directory is on the system disk",
                "large datasets and backups should live on a separate disk (docs/STORAGE.md)")
        pct = data.get("percent", 0)
        lvl = FAIL if pct >= 97 else WARN if pct >= 90 else OK
        add(lvl, "Data disk %.0f%% used" % pct, "" if lvl == OK else "remove old datasets or add storage")
        if data["separate_device"]:
            fstab = try_output(["findmnt", "--fstab", "-no", "OPTIONS", "--mountpoint", data["mount"]])
            if fstab is not None and "nofail" not in fstab:
                add(WARN, "Data disk /etc/fstab entry has no 'nofail' option",
                    "add nofail so the system still boots when the disk is missing (docs/STORAGE.md)")

    status = _load_status(cfg)
    if status:
        for s in status.get("smart", []):
            if s.get("state") == "failed":
                add(FAIL, "SMART health FAILED on %s" % s["device"], "back up now and replace the disk")
            elif s.get("warnings"):
                add(WARN, "SMART warnings on %s: %s" % (s["device"], ", ".join(s["warnings"])),
                    "the disk is developing bad sectors; plan a replacement")
            elif s.get("state") == "passed":
                add(OK, "SMART health passed on %s" % s["device"])
        if not status.get("smart") and data["present"] and data["separate_device"]:
            add(INFO, "No SMART data for the data disk",
                "install smartmontools; some USB bridges need HOMEISLAND_SMART_DEVICES='-d sat /dev/sda'")

    # -- backups -------------------------------------------------------------------------------
    backups = list_backups(cfg.backup_dir) if os.path.isdir(cfg.backup_dir) else []
    if not backups:
        add(FAIL, "No backups found in %s" % cfg.backup_dir, "sudo homeisland backup")
    else:
        age_h = (time.time() - os.path.getmtime(backups[-1])) / 3600
        lvl = FAIL if age_h > 24 * 7 else WARN if age_h > 48 else OK
        add(lvl, "Latest backup is %.0f hours old (%s)" % (age_h, os.path.basename(backups[-1])),
            "" if lvl == OK else "check: systemctl status homeisland-backup.timer")
        if mount_point(cfg.backup_dir) == "/":
            add(WARN, "Backups are stored on the system disk", "set HOMEISLAND_BACKUP_DIR to another disk")

    # -- time -------------------------------------------------------------------------------------
    t = (status or {}).get("time") or {}
    synced = t.get("synchronized")
    if synced is False:
        add(WARN, "System clock is not NTP-synchronized", "normal during WAN outages; see docs/OFFLINE_MODE.md")
    elif synced:
        add(OK, "System clock is NTP-synchronized")
    if not (os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc")):
        add(INFO, "No hardware real-time clock",
            "after a reboot during a long outage the clock starts at the last saved time; add an RTC (docs/HARDWARE.md)")

    # -- modules ------------------------------------------------------------------------------------
    for m in mods:
        if m.experimental:
            add(INFO, "Experimental module enabled: %s" % m.name)
        for ext in m.external:
            add(INFO, "%s may contact external services: %s" % (m.title, ext))
    if "backup" in names and not os.path.exists("/etc/systemd/system/homeisland-backup.timer"):
        add(WARN, "Daily backup timer not installed", "re-run install.sh")
    return f


def _load_status(cfg: Config) -> Optional[dict]:
    try:
        with open(cfg.status_file) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def print_report(findings: List[Finding], verbose: bool = False) -> int:
    colors = {OK: "green", INFO: "blue", WARN: "yellow", FAIL: "red"}
    print("HOMEISLAND OFFLINE READINESS AUDIT\n")
    for item in findings:
        if item.level == OK and not verbose:
            continue
        print("[%s] %s" % (color(item.level.ljust(4), colors[item.level]), item.title))
        if item.hint and item.level != OK:
            for line in item.hint.splitlines():
                print("       " + color(line, "dim"))
    counts = {lvl: sum(1 for i in findings if i.level == lvl) for lvl in (OK, INFO, WARN, FAIL)}
    print("\n%d ok, %d info, %d warnings, %d failures%s" % (
        counts[OK], counts[INFO], counts[WARN], counts[FAIL], "" if verbose else " (use --verbose to list passed checks)"))
    return 1 if counts[FAIL] else 0
