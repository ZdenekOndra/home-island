"""`homeisland test offline`: can every enabled local service work without the Internet?

The test only uses local resources (the Pi-hole on this host, the local
reverse proxy, files on disk). It does not disconnect the WAN; for a real
island test unplug the router's WAN cable and run it again (docs/OFFLINE_MODE.md).
"""

from __future__ import annotations

from typing import List

from . import checks, netcheck
from .config import Config
from .modules import active_modules, load_modules, sort_modules
from .util import color

LABELS = {
    checks.PASS: ("PASS", "green"),
    checks.FAIL: ("FAIL", "red"),
    checks.NODATA: ("NO DATA", "yellow"),
    checks.SKIP: ("SKIP", "dim"),
}


def run_offline_test(cfg: Config, show_skipped: bool = True) -> int:
    active = active_modules(cfg)
    active_names = {m.name for m in active}
    results: List[checks.Result] = checks.run_checks(cfg, active, timeout=4.0)
    registry = load_modules(cfg)
    skipped = [m for m in sort_modules(list(registry.values())) if m.name not in active_names and m.checks]

    print("HOMEISLAND OFFLINE READINESS TEST\n")
    width = max([len(r.name) for r in results] + [10])
    for r in results:
        label, col = LABELS[r.state]
        print("[%s] %s  %s" % (color(label.center(7), col), r.name.ljust(width), color(r.detail, "dim")))
    if show_skipped and skipped:
        print("")
        for m in skipped:
            label, col = LABELS[checks.SKIP]
            print("[%s] %s  %s" % (color(label.center(7), col), m.title.ljust(width), color("module not enabled", "dim")))

    wan = netcheck.wan_check(netcheck.parse_targets(cfg.get("HOMEISLAND_WAN_TARGETS")))
    print("\nWAN: %s" % ("ONLINE" if wan["state"] == "online" else "OFFLINE"))
    passed = sum(1 for r in results if r.state == checks.PASS)
    failed = sum(1 for r in results if r.state == checks.FAIL)
    nodata = sum(1 for r in results if r.state == checks.NODATA)
    total = passed + failed + nodata
    pct = int(round(100.0 * passed / total)) if total else 0
    col = "green" if pct == 100 else "yellow" if failed == 0 else "red"
    print("LOCAL READINESS: %s  (%d passed, %d failed, %d without data, %d modules not enabled)"
          % (color("%d%%" % pct, col), passed, failed, nodata, len(skipped)))
    if wan["state"] == "online":
        print(color("\nThe WAN is currently up. These checks use only local resources, but for a real island test\n"
                    "disconnect the router's WAN cable and run this again.", "dim"))
    return 1 if failed else 0
