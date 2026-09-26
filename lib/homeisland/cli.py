"""The `homeisland` command line interface."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import List, Optional

from . import __version__
from .config import Config
from .util import (HomeIslandError, color, confirm, error, human_bytes, human_duration, info, require_root, table,
                   warn)


def _cfg() -> Config:
    cfg = Config()
    cfg.require_installed()
    return cfg


# -- status / health -------------------------------------------------------------------


def cmd_status(args) -> int:
    cfg = _cfg()
    from .collector import data_disk_state
    from .modules import active_modules
    from .stack import running_services

    try:
        with open(cfg.status_file) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    age = time.time() - st.get("generated_at", 0) if st else None
    wan = (st.get("internet") or {}).get("state", "unknown")
    lan = (st.get("lan") or {}).get("state", "unknown")
    print(color("HOMEISLAND", "bold") + "  %s  v%s" % (cfg.fqdn("home"), __version__))
    print("")
    print("Internet       %s" % (color("ONLINE", "green") if wan == "online" else
                                 color("OFFLINE", "blue") if wan == "offline" else wan.upper()))
    print("Local network  %s" % (color(lan.upper(), "green") if lan == "operational" else lan.upper()))
    sysinfo = st.get("system") or {}
    mem = sysinfo.get("memory") or {}
    print("Uptime         %s" % human_duration(sysinfo.get("uptime_s")))
    if sysinfo:
        cpu = sysinfo.get("cpu_percent")
        print("CPU            %s %%   load %s   temp %s" % (
            "-" if cpu is None else cpu, " ".join(str(x) for x in sysinfo.get("load") or []) or "-",
            ("%s °C" % sysinfo["cpu_temp_c"]) if sysinfo.get("cpu_temp_c") is not None else "-"))
        print("Memory         %s / %s" % (human_bytes(mem.get("used")), human_bytes(mem.get("total"))))
    data = data_disk_state(cfg)
    print("Data disk      %s" % (("%s (%.0f%% used)" % (cfg.data_dir, data.get("percent", 0))) if data["present"]
                                 else color("MISSING at %s" % cfg.data_dir, "red")))
    if age is None:
        print(color("\nNo monitoring data yet (is homeisland-collector running?)", "yellow"))
    elif age > 90:
        print(color("\nMonitoring data is %d s old (check: systemctl status homeisland-collector)" % age, "yellow"))

    print("\nMODULES")
    states = st.get("modules") or {}
    services = running_services(cfg)
    from .stack import module_services

    rows = []
    for m in active_modules(cfg):
        svcs = module_services(m)
        ctr = ", ".join("%s:%s" % (s, services.get(s, "absent")) for s in svcs) or "host"
        state = states.get(m.name, "-")
        rows.append((m.name, m.group, state, ctr))
    print(table(rows, ("MODULE", "GROUP", "CHECK", "CONTAINERS")))
    return 0


def cmd_health(args) -> int:
    cfg = _cfg()
    from . import checks
    from .modules import active_modules

    results = checks.run_checks(cfg, active_modules(cfg), kinds={"dns", "http", "tcp", "status"})
    bad = 0
    for r in results:
        col = "green" if r.state == checks.PASS else "red"
        bad += r.state == checks.FAIL
        print("[%s] %s  %s" % (color(r.state, col), r.name, color(r.detail, "dim")))
    print("\n%s" % (color("healthy", "green") if not bad else color("%d check(s) failed" % bad, "red")))
    return 1 if bad else 0


def cmd_audit(args) -> int:
    from .audit import audit, print_report

    return print_report(audit(_cfg()), verbose=args.verbose)


def cmd_test(args) -> int:
    from .offline import run_offline_test

    if args.what != "offline":
        raise HomeIslandError("unknown test %r (available: offline)" % args.what)
    return run_offline_test(_cfg())


# -- stack -----------------------------------------------------------------------------------


def cmd_apply(args) -> int:
    require_root("apply")
    from .stack import apply

    cfg = _cfg()
    skipped = apply(cfg, pull=args.pull)
    port = "" if cfg.http_port == 80 else ":%d" % cfg.http_port
    info("HomeIsland is up: http://%s%s/" % (cfg.fqdn("home"), port))
    return 2 if skipped else 0


def cmd_stop(args) -> int:
    require_root("stop")
    from .stack import compose

    compose(_cfg(), "stop", *args.services)
    return 0


def cmd_restart(args) -> int:
    require_root("restart")
    from .stack import compose

    compose(_cfg(), "restart", *args.services)
    return 0


def cmd_logs(args) -> int:
    from .stack import compose

    extra = ["--tail", str(args.tail)]
    if args.follow:
        extra.append("--follow")
    try:
        compose(_cfg(), "logs", *(extra + args.services))
    except KeyboardInterrupt:
        pass
    return 0


# -- modules -----------------------------------------------------------------------------------


def cmd_module(args) -> int:
    from . import modules as modmod
    from .stack import apply

    cfg = _cfg()
    if args.action == "list":
        registry = modmod.load_modules(cfg)
        enabled = cfg.enabled_modules()
        rows = []
        for m in modmod.sort_modules(list(registry.values())):
            state = "core" if m.core else ("enabled" if m.name in enabled else "-")
            title = m.title + (" [experimental]" if m.experimental else "")
            rows.append((m.name, m.group, state, title))
        print(table(rows, ("MODULE", "GROUP", "STATE", "DESCRIPTION")))
        return 0
    if args.action == "info":
        registry = modmod.load_modules(cfg)
        for name in args.names:
            if name not in registry:
                raise HomeIslandError("unknown module %r" % name)
            m = registry[name]
            print(color(m.title, "bold") + " (%s, %s)" % (m.name, m.group))
            print(m.description)
            if m.resources:
                print("Resources: %s" % m.resources)
            if m.hosts:
                print("Host names: %s" % ", ".join(cfg.fqdn(h) for h in m.hosts))
            if m.requires_data:
                print("Needs the data disk: %s" % ", ".join(cfg.data_path(d) for d in m.data_dirs[:3]))
            for ext in m.external:
                print("External access: %s" % ext)
            if m.notes:
                print("Notes: %s" % m.notes)
            print("")
        return 0
    require_root("module %s" % args.action)
    if not args.names:
        raise HomeIslandError("module name required")
    touched = []
    for name in args.names:
        if args.action == "enable":
            touched.append(modmod.enable(cfg, name))
        else:
            touched.append(modmod.disable(cfg, name))
    cfg = Config()
    if not args.no_apply:
        apply(cfg)
    for m in touched:
        info("%s %s." % (m.title, "enabled" if args.action == "enable" else "disabled"))
        if args.action == "enable" and m.notes:
            info("  " + m.notes)
        if args.action == "disable" and (m.data_dirs or m.state_dirs):
            info("  Its data and configuration were kept.")
    return 0


# -- backup -------------------------------------------------------------------------------------


def cmd_backup(args) -> int:
    from . import backup

    cfg = _cfg()
    if args.action == "list":
        directory = args.dest or cfg.backup_dir
        items = backup.list_backups(directory) if os.path.isdir(directory) else []
        rows = [(os.path.basename(p), human_bytes(os.path.getsize(p)),
                 time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))) for p in items]
        print("Backups in %s:" % directory)
        print(table(rows, ("FILE", "SIZE", "CREATED")) if rows else "  none")
        return 0
    if args.action == "verify":
        if not args.file:
            raise HomeIslandError("usage: homeisland backup verify <file|latest>")
        path = backup.resolve_backup(cfg, args.file)
        manifest = backup.verify_backup(path)
        info("OK: %s (%d files, created %s, modules: %s)" % (
            os.path.basename(path), len(manifest.get("files", {})), manifest.get("created_at"),
            ", ".join(manifest.get("modules", [])) or "none"))
        return 0
    require_root("backup")
    backup.create_backup(cfg, dest_dir=args.dest, keep=args.keep, quiet=args.quiet)
    return 0


def cmd_restore(args) -> int:
    require_root("restore")
    from . import backup
    from .stack import apply, docker_available, stop_all

    cfg = Config()
    path = backup.resolve_backup(cfg, args.file)
    manifest = backup.verify_backup(path)
    info("Backup %s\n  created %s, HomeIsland %s, modules: %s" % (
        os.path.basename(path), manifest.get("created_at"), manifest.get("homeisland_version"),
        ", ".join(manifest.get("modules", [])) or "none"))
    if not confirm("Replace the current configuration with this backup?", args.yes):
        info("Aborted.")
        return 1
    if cfg.installed and docker_available():
        try:
            stop_all(cfg)
        except HomeIslandError as exc:
            warn("could not stop running services: %s" % exc)
    backup.restore_backup(cfg, path)
    cfg = Config()
    info("Configuration restored.")
    if args.no_start:
        info("Start the services with: sudo homeisland apply")
        return 0
    apply(cfg)
    info("Services started. Run `homeisland test offline` to check them.")
    return 0


# -- storage / knowledge / maps -------------------------------------------------------------------


def cmd_disk(args) -> int:
    from .collector import data_disk_state, disk_usage, smart_devices, smart_read

    cfg = _cfg()
    root = disk_usage("/") or {}
    print("System disk  /  %.0f%% used, %s free" % (root.get("percent", 0), human_bytes(root.get("free"))))
    data = data_disk_state(cfg)
    if data["present"]:
        print("Data disk    %s  %.0f%% used, %s free, mount %s%s" % (
            cfg.data_dir, data.get("percent", 0), human_bytes(data.get("free")), data["mount"],
            "" if data["separate_device"] else color("  (on the system disk!)", "yellow")))
    else:
        print("Data disk    %s  %s" % (cfg.data_dir, color("NOT AVAILABLE", "red")))
    devices = smart_devices(cfg)
    if devices:
        print("\nSMART (sleeping disks are not woken up):")
        rows = []
        for dev in devices:
            s = smart_read(dev)
            rows.append((dev, s.get("model") or "-", s.get("state"),
                         ("%s °C" % s["temperature_c"]) if "temperature_c" in s else "-",
                         ", ".join(s.get("warnings", [])) or s.get("detail", "")))
        print(table(rows, ("DEVICE", "MODEL", "HEALTH", "TEMP", "NOTES")))
    return 0


def cmd_knowledge(args) -> int:
    from . import knowledge

    cfg = _cfg()
    if args.action == "list":
        knowledge.list_knowledge(cfg)
    elif args.action == "update":
        require_root("knowledge update")
        knowledge.update_kiwix_library(cfg)
    elif args.action == "index":
        knowledge.index_library(cfg)
    elif args.action == "download":
        require_root("knowledge download")
        if not args.url:
            raise HomeIslandError("usage: homeisland knowledge download <https://...zim> [--sha256 HASH]")
        knowledge.download_zim(cfg, args.url, args.sha256)
        if "kiwix" in cfg.enabled_modules():
            knowledge.update_kiwix_library(cfg)
    return 0


def cmd_maps(args) -> int:
    from . import maps

    cfg = _cfg()
    if args.action == "list":
        maps.list_maps(cfg)
    elif args.action == "assets":
        require_root("maps assets")
        maps.install_assets(cfg, force=args.force)
    elif args.action == "download":
        require_root("maps download")
        if not args.region:
            raise HomeIslandError("usage: homeisland maps download <region> [--bbox ...] [--maxzoom N]")
        maps.download(cfg, args.region, bbox=args.bbox, maxzoom=args.maxzoom, source=args.source,
                      dry_run=args.dry_run, name=args.name)
    return 0


# -- DNS / secrets / config --------------------------------------------------------------------------


def cmd_dns(args) -> int:
    from .generate import dns_records
    from .modules import active_modules

    cfg = _cfg()
    rows = dns_records(cfg, active_modules(cfg))
    print(table(rows, ("NAME", "ADDRESS")))
    print("\nAdd your own records in %s, then run: sudo homeisland apply" % cfg.dns_records_file)
    return 0


def cmd_secrets(args) -> int:
    require_root("secrets")
    cfg = _cfg()
    key = {"pihole": "PIHOLE_WEB_PASSWORD", "samba": "SAMBA_PASSWORD"}.get(args.name)
    if key is None:
        raise HomeIslandError("unknown secret %r (pihole, samba)" % args.name)
    value = cfg.secrets.get(key)
    if not value:
        raise HomeIslandError("%s is not set in %s" % (key, cfg.secrets_file))
    print(value)
    return 0


def cmd_config(args) -> int:
    cfg = _cfg()
    if args.action == "check":
        problems = cfg.validate()
        for p in problems:
            error(p)
        if not problems:
            info("Configuration is valid.")
        return 1 if problems else 0
    if args.action == "validate":
        # Render everything and let Compose validate the result, without starting anything.
        from .generate import generate_all
        from .stack import compose

        generate_all(cfg)
        compose(cfg, "config", "--quiet")
        info("Configuration and compose files are valid.")
        return 0
    for key in sorted(cfg.values):
        print("%s=%s" % (key, cfg.values[key]))
    print("# secrets in %s: %s" % (cfg.secrets_file, ", ".join(sorted(cfg.secrets)) or "none"))
    return 0


# -- update / system ------------------------------------------------------------------------------------


def cmd_update(args) -> int:
    require_root("update")
    from . import update

    cfg = _cfg()
    if args.rollback:
        return update.rollback(cfg, assume_yes=args.yes)
    return update.update(cfg, ref=args.ref, assume_yes=args.yes, backup_first=not args.no_backup)


def cmd_system(args) -> int:
    require_root("system")
    from . import update

    cfg = Config()
    if args.action == "install-units":
        update.install_units(cfg)
    elif args.action == "post-update":
        update.install_units(cfg)
        from .stack import apply

        apply(_cfg(), pull=True)
    return 0


def cmd_collector(args) -> int:
    from .collector import Collector

    Collector(_cfg()).run(once=args.once)
    return 0


def cmd_version(args) -> int:
    print("homeisland %s" % __version__)
    return 0


# -- parser ----------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="homeisland", description="HomeIsland: an offline-first home server.")
    p.add_argument("--version", action="version", version="homeisland %s" % __version__)
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add(name, func, help_text, **kw):
        if help_text is not None:
            kw["help"] = help_text
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(func=func)
        return sp

    add("status", cmd_status, "show system and module status")
    add("health", cmd_health, "run live service checks (exit 1 on failure)")
    sp = add("audit", cmd_audit, "offline readiness audit with actionable warnings")
    sp.add_argument("-v", "--verbose", action="store_true", help="also list passed checks")
    sp = add("test", cmd_test, "run a test suite: offline")
    sp.add_argument("what", choices=["offline"])

    sp = add("apply", cmd_apply, "regenerate configuration and (re)start services")
    sp.add_argument("--pull", action="store_true", help="pull pinned images first")
    sp = add("start", cmd_apply, "start all enabled services (same as apply)")
    sp.set_defaults(pull=False)
    sp = add("stop", cmd_stop, "stop services")
    sp.add_argument("services", nargs="*")
    sp = add("restart", cmd_restart, "restart services")
    sp.add_argument("services", nargs="*")
    sp = add("logs", cmd_logs, "show container logs")
    sp.add_argument("services", nargs="*")
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("-n", "--tail", type=int, default=100)

    sp = add("module", cmd_module, "list, enable or disable modules")
    sp.add_argument("action", choices=["list", "enable", "disable", "info"])
    sp.add_argument("names", nargs="*")
    sp.add_argument("--no-apply", action="store_true", help="only change the module list")

    sp = add("backup", cmd_backup, "create, list or verify configuration backups")
    sp.add_argument("action", nargs="?", default="create", choices=["create", "list", "verify"])
    sp.add_argument("file", nargs="?", help="backup file for verify (or 'latest')")
    sp.add_argument("--dest", help="destination directory (default: HOMEISLAND_BACKUP_DIR)")
    sp.add_argument("--keep", type=int, help="number of backups to keep")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp = add("restore", cmd_restore, "restore configuration from a backup")
    sp.add_argument("file", help="backup file or 'latest'")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.add_argument("--no-start", action="store_true", help="do not start services after restoring")

    sp = add("disk", cmd_disk, "disk usage and SMART health")
    sp.add_argument("action", nargs="?", default="status", choices=["status"])

    sp = add("knowledge", cmd_knowledge, "Kiwix archives and the document library")
    sp.add_argument("action", choices=["list", "update", "index", "download"])
    sp.add_argument("url", nargs="?")
    sp.add_argument("--sha256", help="expected checksum for download")

    sp = add("maps", cmd_maps, "offline map archives and viewer assets")
    sp.add_argument("action", choices=["list", "assets", "download"])
    sp.add_argument("region", nargs="?")
    sp.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat for a custom region")
    sp.add_argument("--maxzoom", type=int, help="limit detail to save space (default: full detail)")
    sp.add_argument("--source", help="PMTiles source URL (default: latest Protomaps build)")
    sp.add_argument("--name", help="output file name without extension")
    sp.add_argument("--dry-run", action="store_true", help="only estimate the download size")
    sp.add_argument("--force", action="store_true", help="re-download viewer assets")

    add("dns", cmd_dns, "list local DNS records")
    sp = add("secrets", cmd_secrets, "print a generated password")
    sp.add_argument("name", choices=["pihole", "samba"])
    sp.add_argument("action", nargs="?", default="show", choices=["show"])
    sp = add("config", cmd_config, "show or check configuration")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "check", "validate"])

    sp = add("update", cmd_update, "update HomeIsland from git (backs up first)")
    sp.add_argument("--ref", help="tag, branch or commit to update to (default: upstream of current branch)")
    sp.add_argument("--rollback", action="store_true", help="return to the version before the last update")
    sp.add_argument("--no-backup", action="store_true")
    sp.add_argument("-y", "--yes", action="store_true")
    sp = add("system", cmd_system, None)  # internal
    sp.add_argument("action", choices=["install-units", "post-update"])
    sp = add("collector", cmd_collector, None)  # internal: run by systemd
    sp.add_argument("--once", action="store_true")
    add("version", cmd_version, "print the version")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except HomeIslandError as exc:
        error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
