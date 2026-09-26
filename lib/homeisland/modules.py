"""Module registry.

Every directory under modules/ with a module.json is a module. Core modules
are always enabled; optional modules are listed in modules.enabled.

module.json fields (all optional except name/title/group):

    name, title, group, description
    core            always enabled, cannot be disabled
    compose         the module ships a compose.yml fragment
    host_only       the module runs on the host (collector feature), no containers
    requires_data   needs the data disk; data_dirs are created below DATA_DIR
    data_dirs       list of directories below DATA_DIR
    state_dirs      list of directories below STATE_DIR
    state_owner     "data" to chown state_dirs to HOMEISLAND_DATA_UID/GID
    hosts           {"name": "service:port"} reverse-proxied hostnames,
                    or {"name": null} for a DNS record without proxying
    links           dashboard cards: title, host, path, url, icon, description
    checks          offline readiness checks (see checks.py)
    backup          paths below STATE_DIR to include in backups
    backup_exclude  glob patterns excluded from those paths
    external        external services the module may contact
    resources       human readable resource estimate
    notes           shown after enabling
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .config import Config, valid_module_name
from .util import HomeIslandError, write_atomic

GROUP_ORDER = ["core", "knowledge", "maps", "home", "media", "hardware"]


class Module:
    def __init__(self, path: str, data: dict):
        self.path = path
        self.data = data
        self.name: str = data["name"]
        self.title: str = data["title"]
        self.group: str = data["group"]
        self.description: str = data.get("description", "")
        self.core: bool = bool(data.get("core", False))
        self.host_only: bool = bool(data.get("host_only", False))
        self.requires_data: bool = bool(data.get("requires_data", False))
        self.data_dirs: List[str] = list(data.get("data_dirs", []))
        self.state_dirs: List[str] = list(data.get("state_dirs", []))
        self.state_owner: str = data.get("state_owner", "root")
        self.hosts: Dict[str, Optional[str]] = dict(data.get("hosts", {}))
        self.links: List[dict] = list(data.get("links", []))
        self.checks: List[dict] = list(data.get("checks", []))
        self.backup: List[str] = list(data.get("backup", []))
        self.backup_exclude: List[str] = list(data.get("backup_exclude", []))
        self.external: List[str] = list(data.get("external", []))
        self.resources: str = data.get("resources", "")
        self.notes: str = data.get("notes", "")
        self.experimental: bool = bool(data.get("experimental", False))

    @property
    def compose_file(self) -> Optional[str]:
        path = os.path.join(self.path, "compose.yml")
        return path if os.path.isfile(path) else None

    def __repr__(self) -> str:
        return "<Module %s>" % self.name


def load_modules(cfg: Config) -> Dict[str, Module]:
    base = cfg.repo_path("modules")
    modules: Dict[str, Module] = {}
    for entry in sorted(os.listdir(base)):
        meta = os.path.join(base, entry, "module.json")
        if not os.path.isfile(meta):
            continue
        with open(meta, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise HomeIslandError("invalid JSON in %s: %s" % (meta, exc))
        for key in ("name", "title", "group"):
            if key not in data:
                raise HomeIslandError("%s: missing required field %r" % (meta, key))
        if data["name"] != entry or not valid_module_name(entry):
            raise HomeIslandError("%s: name must match its directory" % meta)
        modules[entry] = Module(os.path.join(base, entry), data)
    return modules


def sort_modules(mods: List[Module]) -> List[Module]:
    def key(m: Module):
        group = GROUP_ORDER.index(m.group) if m.group in GROUP_ORDER else len(GROUP_ORDER)
        return (group, m.name)

    return sorted(mods, key=key)


def active_modules(cfg: Config, registry: Optional[Dict[str, Module]] = None) -> List[Module]:
    """Core modules plus enabled optional modules, in display order."""
    registry = registry if registry is not None else load_modules(cfg)
    enabled = cfg.enabled_modules()
    unknown = [n for n in enabled if n not in registry]
    if unknown:
        raise HomeIslandError("unknown module(s) in %s: %s" % (cfg.modules_file, ", ".join(unknown)))
    return sort_modules([m for m in registry.values() if m.core or m.name in enabled])


def set_enabled(cfg: Config, names: List[str]) -> None:
    header = (
        "# Optional HomeIsland modules, one per line.\n"
        "# Manage with: homeisland module enable|disable <name>\n"
    )
    write_atomic(cfg.modules_file, header + "".join(n + "\n" for n in names))


def enable(cfg: Config, name: str) -> Module:
    registry = load_modules(cfg)
    if name not in registry:
        raise HomeIslandError("unknown module %r (see: homeisland module list)" % name)
    mod = registry[name]
    if mod.core:
        raise HomeIslandError("%s is a core module and is always enabled" % name)
    names = cfg.enabled_modules()
    if name not in names:
        names.append(name)
        set_enabled(cfg, names)
    return mod


def disable(cfg: Config, name: str) -> Module:
    registry = load_modules(cfg)
    if name not in registry:
        raise HomeIslandError("unknown module %r" % name)
    mod = registry[name]
    if mod.core:
        raise HomeIslandError("%s is a core module and cannot be disabled" % name)
    names = [n for n in cfg.enabled_modules() if n != name]
    set_enabled(cfg, names)
    return mod
