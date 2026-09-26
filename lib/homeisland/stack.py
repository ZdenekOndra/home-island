"""Docker Compose orchestration for the core stack and enabled modules."""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import subprocess
from typing import Dict, List, Optional

from .config import Config
from .generate import generate_all
from .modules import Module, active_modules
from .util import HomeIslandError, run, try_output, warn, write_atomic

PROJECT = "homeisland"
EMPTY_KIWIX_LIBRARY = '<?xml version="1.0" encoding="UTF-8" ?>\n<library version="20110515">\n</library>\n'


def compose_env() -> Dict[str, str]:
    """Environment for docker compose without HomeIsland variables from the caller's shell.

    Shell variables take precedence over --env-file in Compose, so a stray
    exported variable could otherwise silently override the configuration.
    """
    prefixes = ("HOMEISLAND_", "PIHOLE_", "SAMBA_", "COMPOSE_")
    return {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}


def data_available(cfg: Config) -> bool:
    return os.path.exists(cfg.data_marker)


def override_file(cfg: Config) -> str:
    return os.path.join(cfg.config_dir, "compose.override.yml")


def compose_files(cfg: Config, mods: List[Module]) -> List[str]:
    files = [cfg.repo_path("compose.yml")]
    files += [m.compose_file for m in mods if m.compose_file]
    # Site-specific changes (USB devices, extra mounts) live outside the repository.
    if os.path.isfile(override_file(cfg)):
        files.append(override_file(cfg))
    return files


def compose_base(cfg: Config, mods: Optional[List[Module]] = None) -> List[str]:
    mods = mods if mods is not None else active_modules(cfg)
    args = [
        "docker", "compose",
        "--project-name", PROJECT,
        "--project-directory", cfg.repo_root,
        "--env-file", os.path.join(cfg.generated_dir, "compose.env"),
    ]
    for path in compose_files(cfg, mods):
        args += ["-f", path]
    return args


def compose(cfg: Config, *args: str, mods: Optional[List[Module]] = None, check: bool = True,
            capture: bool = False, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    return run(compose_base(cfg, mods) + list(args), check=check, capture=capture, env=compose_env(),
               timeout=timeout)


def module_services(mod: Module) -> List[str]:
    """Service names defined in a module's compose fragment."""
    if mod.core:
        return {"dashboard": ["proxy"], "dns": ["pihole"]}.get(mod.name, [])
    path = mod.compose_file
    if not path:
        return []
    services, in_services = [], False
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if re.match(r"^services:\s*$", line):
                in_services = True
                continue
            if in_services:
                if re.match(r"^\S", line):
                    break
                m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
                if m:
                    services.append(m.group(1))
    return services


def running_services(cfg: Config) -> Dict[str, str]:
    out = try_output(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=%s" % PROJECT,
         "--format", '{{.Label "com.docker.compose.service"}}\t{{.State}}'],
        timeout=15,
    ) or ""
    states = {}
    for line in out.splitlines():
        if "\t" in line:
            svc, state = line.split("\t", 1)
            states[svc] = state
    return states


def _chown(path: str, uid: int, gid: int) -> None:
    if hasattr(os, "chown") and os.geteuid() == 0:
        os.chown(path, uid, gid)


def prepare_directories(cfg: Config, mods: List[Module]) -> None:
    uid, gid = cfg.get_int("HOMEISLAND_DATA_UID"), cfg.get_int("HOMEISLAND_DATA_GID")
    for path in (cfg.state_dir, cfg.status_dir):
        os.makedirs(path, mode=0o755, exist_ok=True)
    for mod in mods:
        for rel in mod.state_dirs:
            path = cfg.state_path(rel)
            if not os.path.isdir(path):
                os.makedirs(path, mode=0o755)
                if mod.state_owner == "data":
                    _chown(path, uid, gid)
                    parent = os.path.dirname(path)
                    if parent != cfg.state_dir:
                        _chown(parent, uid, gid)
    kiwix_lib = cfg.state_path("kiwix", "library.xml")
    if os.path.isdir(cfg.state_path("kiwix")) and not os.path.exists(kiwix_lib):
        write_atomic(kiwix_lib, EMPTY_KIWIX_LIBRARY, 0o644)
    if not data_available(cfg):
        return
    for mod in mods:
        for rel in mod.data_dirs:
            path = cfg.data_path(rel)
            if not os.path.isdir(path):
                os.makedirs(path, mode=0o755)
                _chown(path, uid, gid)


@contextlib.contextmanager
def apply_lock(cfg: Config):
    """Serialise apply runs (boot unit, collector, installer and users may overlap)."""
    os.makedirs(cfg.state_dir, exist_ok=True)
    with open(os.path.join(cfg.state_dir, ".apply.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def apply(cfg: Config, pull: bool = False) -> List[str]:
    """Generate configuration and bring the stack to the desired state.

    Returns the list of modules that were skipped because the data disk is missing.
    """
    cfg.require_installed()
    with apply_lock(cfg):
        return _apply(cfg, pull)


def _apply(cfg: Config, pull: bool) -> List[str]:
    mods = active_modules(cfg)
    changed = generate_all(cfg)
    prepare_directories(cfg, mods)

    skipped: List[str] = []
    if data_available(cfg):
        up_mods = mods
    else:
        up_mods = [m for m in mods if not m.requires_data]
        skipped = [m.name for m in mods if m.requires_data]
        if skipped:
            warn("data disk not available at %s (marker %s missing); not starting: %s"
                 % (cfg.data_dir, os.path.basename(cfg.data_marker), ", ".join(skipped)))

    if pull:
        compose(cfg, "pull", "--ignore-buildable", mods=mods)
        compose(cfg, "build", "--pull", mods=mods)
    services = [s for m in up_mods for s in module_services(m)]
    before = running_services(cfg)
    # Missing locally built images are built automatically; updates rebuild them via pull=True.
    up_args = ["up", "-d", "--quiet-pull"]
    if not skipped:
        up_args.append("--remove-orphans")
    compose(cfg, *(up_args + services), mods=mods)

    # Pick up regenerated configuration in containers that were already running.
    gen = cfg.generated_dir
    if before.get("proxy") == "running" and any(p.startswith(os.path.join(gen, "caddy")) for p in changed):
        compose(cfg, "restart", "proxy", mods=mods)
    if before.get("pihole") == "running":
        if any(p.startswith(os.path.join(gen, "dnsmasq.d")) for p in changed):
            # dnsmasq option files (e.g. a new domain) are only read at start-up.
            compose(cfg, "restart", "pihole", mods=mods)
        elif any(p.startswith(os.path.join(gen, "dns") + os.sep) for p in changed):
            reload_dns(cfg, mods)
    return skipped


def reload_dns(cfg: Config, mods: Optional[List[Module]] = None) -> None:
    res = compose(cfg, "exec", "-T", "pihole", "pihole", "reloaddns", mods=mods, check=False, capture=True,
                  timeout=60)
    if res.returncode != 0:
        warn("pihole reloaddns failed; restarting the pihole container")
        compose(cfg, "restart", "pihole", mods=mods)


def stop_all(cfg: Config) -> None:
    compose(cfg, "stop")


def module_image(mod: Module) -> str:
    path = mod.compose_file
    if not path:
        raise HomeIslandError("module %s has no compose file" % mod.name)
    with open(path, "r", encoding="utf-8") as fh:
        m = re.search(r"^\s+image:\s*(\S+)", fh.read(), re.M)
    if not m:
        raise HomeIslandError("no image found in %s" % path)
    return m.group(1)


def docker_available() -> bool:
    return try_output(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15) is not None
