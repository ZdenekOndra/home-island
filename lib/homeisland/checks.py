"""Service checks shared by `homeisland health`, `homeisland test offline` and the collector.

Result states:

    PASS     the check succeeded
    FAIL     the module is enabled but the check failed
    NODATA   the software works (or cannot be judged) but its dataset is missing
    SKIP     the module is not enabled
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from .config import Config
from .generate import dns_records, render_template
from .modules import Module
from . import netcheck

PASS, FAIL, NODATA, SKIP = "PASS", "FAIL", "NODATA", "SKIP"


class Result:
    def __init__(self, module: str, name: str, state: str, detail: str = "", kind: str = ""):
        self.module = module
        self.name = name
        self.state = state
        self.detail = detail
        self.kind = kind

    def as_dict(self) -> dict:
        return {"module": self.module, "name": self.name, "state": self.state, "detail": self.detail,
                "kind": self.kind}

    def __repr__(self) -> str:
        return "<Result %s %s %s>" % (self.module, self.name, self.state)


def _http(cfg: Config, mod: Module, check: dict, timeout: float) -> Result:
    if "url" in check:
        url = urlsplit(render_template(check["url"], cfg))
        ip, port, host, path = url.hostname or cfg.host_ip, url.port or 80, None, url.path or "/"
        if url.query:
            path += "?" + url.query
    else:
        ip, port = cfg.local_ip, cfg.http_port
        host, path = cfg.fqdn(check["host"]), check.get("path", "/")
    status, body, latency = netcheck.http_probe(ip, port, host, path, timeout=timeout)
    name = check["name"]
    target = "http://%s%s" % (host or "%s:%d" % (ip, port), path)
    if status is None:
        return Result(mod.name, name, FAIL, "no response from %s" % target, "http")
    if status >= 500 or status == 404:
        return Result(mod.name, name, FAIL, "HTTP %d from %s" % (status, target), "http")
    if check.get("contains") and check["contains"] not in body:
        return Result(mod.name, name, FAIL, "unexpected response from %s" % target, "http")
    return Result(mod.name, name, PASS, "HTTP %d in %d ms" % (status, latency or 0), "http")


def _tcp(cfg: Config, mod: Module, check: dict, timeout: float) -> Result:
    ip = cfg.local_ip
    latency = netcheck.tcp_probe(ip, int(check["port"]), timeout)
    if latency is None:
        return Result(mod.name, check["name"], FAIL, "port %s not reachable on %s" % (check["port"], ip), "tcp")
    return Result(mod.name, check["name"], PASS, "port %s open" % check["port"], "tcp")


def _dns(cfg: Config, mod: Module, check: dict, timeout: float, mods: List[Module]) -> Result:
    records = dns_records(cfg, mods)
    server = cfg.local_ip
    bad = []
    for name, ip in records:
        rcode, addrs = netcheck.dns_query(server, name, port=cfg.dns_port, timeout=timeout)
        if ip not in addrs:
            bad.append("%s (%s)" % (name, rcode if rcode != "NOERROR" else ",".join(addrs) or "no address"))
    if bad:
        return Result(mod.name, check["name"], FAIL, "not resolving: " + ", ".join(bad[:5]), "dns")
    return Result(mod.name, check["name"], PASS, "%d local records resolve via %s" % (len(records), server), "dns")


def dataset_present(root: str, pattern: str) -> bool:
    """True if any file below root matches pattern ('**' matches any depth). Stops at the first hit."""
    if "**" in pattern:
        base, _, tail = pattern.partition("/**/")
        start = os.path.join(root, base)
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not fn.startswith(".") and fnmatch.fnmatch(fn, tail):
                    return True
        return False
    directory, _, filepat = pattern.rpartition("/")
    try:
        return any(fnmatch.fnmatch(fn, filepat) for fn in os.listdir(os.path.join(root, directory)))
    except OSError:
        return False


def _dataset(cfg: Config, mod: Module, check: dict) -> Result:
    if not os.path.exists(cfg.data_marker):
        return Result(mod.name, check["name"], FAIL, "data disk not available at %s" % cfg.data_dir, "dataset")
    if dataset_present(cfg.data_dir, check["glob"]):
        return Result(mod.name, check["name"], PASS, "found %s" % check["glob"], "dataset")
    return Result(mod.name, check["name"], NODATA, check.get("hint", "no files match %s" % check["glob"]), "dataset")


def _status(cfg: Config, mod: Module, check: dict) -> Result:
    try:
        with open(cfg.status_file, "r") as fh:
            data = json.load(fh)
        age = time.time() - float(data.get("generated_at", 0))
    except (OSError, ValueError):
        return Result(mod.name, check["name"], FAIL, "no status data (is homeisland-collector running?)", "status")
    if age > float(check.get("max_age", 120)):
        return Result(mod.name, check["name"], FAIL, "status data is %d s old" % age, "status")
    return Result(mod.name, check["name"], PASS, "collector updated %d s ago" % age, "status")


def run_check(cfg: Config, mod: Module, check: dict, mods: List[Module], timeout: float = 3.0) -> Result:
    kind = check["type"]
    try:
        if kind == "http":
            return _http(cfg, mod, check, timeout)
        if kind == "tcp":
            return _tcp(cfg, mod, check, timeout)
        if kind == "dns":
            return _dns(cfg, mod, check, timeout, mods)
        if kind == "dataset":
            return _dataset(cfg, mod, check)
        if kind == "status":
            return _status(cfg, mod, check)
    except Exception as exc:  # a broken check must not break the report
        return Result(mod.name, check.get("name", kind), FAIL, "check error: %s" % exc, kind)
    return Result(mod.name, check.get("name", kind), FAIL, "unknown check type %r" % kind, kind)


def run_checks(
    cfg: Config, mods: List[Module], kinds: Optional[set] = None, timeout: float = 3.0
) -> List[Result]:
    jobs = []
    for mod in mods:
        for check in mod.checks:
            if kinds is None or check["type"] in kinds:
                jobs.append((mod, check))
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        return list(pool.map(lambda j: run_check(cfg, j[0], j[1], mods, timeout), jobs))


def module_states(results: List[Result]) -> Dict[str, str]:
    """Summarise results per module: up / down / nodata."""
    states: Dict[str, str] = {}
    for r in results:
        current = states.get(r.module)
        if r.state == FAIL:
            states[r.module] = "down"
        elif r.state == NODATA and current != "down":
            states[r.module] = "nodata"
        elif current is None:
            states[r.module] = "up"
    return states
