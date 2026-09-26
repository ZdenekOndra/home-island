"""Host monitoring collector (runs as the homeisland-collector systemd service).

Writes STATE_DIR/status/status.json, which the dashboard reads as a static
file. Slow probes (Internet, SMART) run in their own threads on their own
schedules, so a WAN outage or a sleeping disk never delays the status data.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import List, Optional

from . import __version__, checks, netcheck
from .config import Config
from .modules import active_modules
from .sensors import configured_sensors, read_all
from .util import read_text, try_output, write_json_atomic


# -- system metrics -------------------------------------------------------------


class CpuSampler:
    def __init__(self) -> None:
        self._last: Optional[tuple] = None

    @staticmethod
    def _read() -> Optional[tuple]:
        line = (read_text("/proc/stat", "") or "").split("\n", 1)[0]
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return None
        values = [int(v) for v in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def percent(self) -> Optional[float]:
        cur = self._read()
        if cur is None:
            return None
        last, self._last = self._last, cur
        if last is None:
            return None
        total, idle = cur[0] - last[0], cur[1] - last[1]
        if total <= 0:
            return None
        return round(100.0 * (total - idle) / total, 1)


def memory() -> dict:
    info = {}
    for line in (read_text("/proc/meminfo", "") or "").splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            continue
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if total is None or avail is None:
        return {}
    return {"total": total, "used": total - avail, "percent": round(100.0 * (total - avail) / total, 1),
            "swap_total": info.get("SwapTotal", 0), "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0)}


def cpu_temperature() -> Optional[float]:
    base = "/sys/class/thermal"
    try:
        zones = sorted(z for z in os.listdir(base) if z.startswith("thermal_zone"))
    except OSError:
        return None
    preferred, fallback = None, None
    for zone in zones:
        ztype = (read_text(os.path.join(base, zone, "type"), "") or "").strip().lower()
        raw = read_text(os.path.join(base, zone, "temp"))
        try:
            temp = int(raw.strip()) / 1000.0 if raw else None
        except ValueError:
            temp = None
        if temp is None:
            continue
        if fallback is None:
            fallback = temp
        if any(k in ztype for k in ("cpu", "soc", "x86_pkg", "k10temp", "coretemp")):
            preferred = temp
            break
    value = preferred if preferred is not None else fallback
    return round(value, 1) if value is not None else None


def uptime() -> Optional[float]:
    raw = read_text("/proc/uptime")
    try:
        return float(raw.split()[0]) if raw else None
    except (ValueError, IndexError):
        return None


def loadavg() -> Optional[List[float]]:
    raw = read_text("/proc/loadavg")
    try:
        return [float(x) for x in raw.split()[:3]] if raw else None
    except ValueError:
        return None


def mount_point(path: str) -> Optional[str]:
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return path


def disk_usage(path: str) -> Optional[dict]:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - st.f_bfree * st.f_frsize
    return {"total": total, "used": used, "free": free,
            "percent": round(100.0 * used / (used + free), 1) if (used + free) else 0.0}


def data_disk_state(cfg: Config) -> dict:
    """Describe the data disk without spinning it up unnecessarily (stat/statvfs only)."""
    marker = os.path.exists(cfg.data_marker)
    mp = mount_point(cfg.data_dir) if os.path.exists(cfg.data_dir) else None
    separate = mp not in (None, "/")
    entry = {"id": "data", "label": "Data disk", "path": cfg.data_dir, "mount": mp,
             "present": marker, "separate_device": separate}
    if marker:
        usage = disk_usage(cfg.data_dir)
        if usage:
            entry.update(usage)
    return entry


def time_state() -> dict:
    synced = try_output(["timedatectl", "show", "--property=NTPSynchronized", "--value"], timeout=3)
    return {
        "synchronized": None if synced is None else synced.strip() == "yes",
        "rtc": os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"),
        "now": int(time.time()),
    }


# -- SMART ------------------------------------------------------------------------


def smart_devices(cfg: Config) -> List[str]:
    configured = cfg.get("HOMEISLAND_SMART_DEVICES").split()
    if configured:
        return configured
    out = try_output(["smartctl", "--scan"], timeout=10) or ""
    devices = []
    for line in out.splitlines():
        dev = line.split("#", 1)[0].split()
        if dev and dev[0].startswith("/dev/") and not dev[0].startswith("/dev/mmcblk"):
            devices.append(dev[0])
    return devices


def smart_read(device: str) -> dict:
    """Read SMART health.

    `-n standby` makes smartctl skip disks that are spun down instead of
    waking them, so monitoring does not defeat HDD spindown.
    """
    entry: dict = {"device": device, "checked_at": int(time.time())}
    if shutil.which("smartctl") is None:
        entry["state"] = "unavailable"
        entry["detail"] = "smartmontools not installed"
        return entry
    try:
        # smartctl reports findings through exit-status bits, so the code alone is not an error.
        res = subprocess.run(["smartctl", "-n", "standby", "-j", "-H", "-A", "-i", device],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                             universal_newlines=True)
        data = json.loads(res.stdout or "")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        entry["state"] = "unknown"
        entry["detail"] = "no SMART data"
        return entry
    messages = " ".join(m.get("string", "") for m in (data.get("smartctl") or {}).get("messages", []))
    if "STANDBY" in messages.upper() or "SLEEP" in messages.upper():
        entry["state"] = "standby"
        return entry
    entry["model"] = data.get("model_name") or data.get("model_family")
    passed = (data.get("smart_status") or {}).get("passed")
    entry["state"] = "passed" if passed is True else "failed" if passed is False else "unknown"
    if entry["state"] == "unknown" and messages:
        entry["detail"] = messages[:200]
    temp = (data.get("temperature") or {}).get("current")
    if temp is not None:
        entry["temperature_c"] = temp
    for attr in (data.get("ata_smart_attributes") or {}).get("table", []):
        # Reallocated, pending and uncorrectable sectors are early signs of a failing disk.
        if attr.get("id") in (5, 197, 198):
            raw = (attr.get("raw") or {}).get("value")
            if raw:
                entry.setdefault("warnings", []).append("%s=%s" % (attr.get("name"), raw))
    return entry


# -- containers ---------------------------------------------------------------------


def containers() -> Optional[List[dict]]:
    out = try_output(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=homeisland", "--format", "{{json .}}"],
        timeout=10,
    )
    if out is None:
        return None
    result = []
    for line in out.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        labels = dict(
            kv.split("=", 1) for kv in (item.get("Labels") or "").split(",") if "=" in kv
        )
        status = item.get("Status", "")
        health = None
        m = re.search(r"\((healthy|unhealthy|health: starting)\)", status)
        if m:
            health = m.group(1).replace("health: ", "")
        result.append({
            "name": item.get("Names"),
            "service": labels.get("com.docker.compose.service"),
            "state": item.get("State"),
            "status": status,
            "health": health,
            "image": item.get("Image"),
        })
    return sorted(result, key=lambda c: c.get("service") or "")


# -- UPS --------------------------------------------------------------------------------


def ups_state(name: str) -> dict:
    out = try_output(["upsc", name], timeout=5)
    if out is None:
        return {"ok": False, "error": "upsc %s failed (is NUT installed and configured?)" % name}
    values = {}
    for line in out.splitlines():
        key, _, val = line.partition(":")
        values[key.strip()] = val.strip()
    status = values.get("ups.status", "")
    return {
        "ok": True,
        "name": name,
        "status": status,
        "on_battery": "OB" in status.split(),
        "low_battery": "LB" in status.split(),
        "charge_pct": _num(values.get("battery.charge")),
        "runtime_s": _num(values.get("battery.runtime")),
        "load_pct": _num(values.get("ups.load")),
    }


def _num(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


# -- collector -----------------------------------------------------------------------


class Collector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cpu = CpuSampler()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wan: dict = {"state": "unknown", "checked_at": None}
        self.lan: dict = {"state": "unknown"}
        self.smart: List[dict] = []
        self.fan = None
        self._data_present: Optional[bool] = None
        self._ip_present: Optional[bool] = None
        self._apply_thread: Optional[threading.Thread] = None

    # background loops -----------------------------------------------------------

    def _loop(self, interval: float, func) -> None:
        while not self.stop.is_set():
            try:
                func()
            except Exception as exc:  # keep the loop alive whatever happens
                print("collector: %s failed: %s" % (func.__name__, exc), flush=True)
            self.stop.wait(interval)

    def check_network(self) -> None:
        cfg = self.cfg
        if cfg.get_bool("HOMEISLAND_WAN_CHECK"):
            wan = netcheck.wan_check(netcheck.parse_targets(cfg.get("HOMEISLAND_WAN_TARGETS")))
        else:
            wan = {"state": "disabled", "checked_at": int(time.time())}
        gateway = netcheck.default_gateway()
        gw_ok = netcheck.ping(gateway) if gateway else None
        rcode, addrs = netcheck.dns_query(cfg.local_ip, cfg.fqdn("home"), port=cfg.dns_port, timeout=2)
        dns_ok = cfg.host_ip in addrs
        addresses = netcheck.local_ipv4_addresses()
        if not addresses:
            state = "down"
        elif dns_ok and gw_ok is not False:
            state = "operational"
        else:
            state = "degraded"
        lan = {
            "state": state,
            "addresses": addresses,
            "host_ip_present": cfg.host_ip in addresses if addresses else None,
            "gateway": gateway,
            "gateway_reachable": gw_ok,
            "local_dns": dns_ok,
            "local_dns_rcode": rcode,
            "checked_at": int(time.time()),
        }
        with self.lock:
            self.wan, self.lan = wan, lan

    def check_smart(self) -> None:
        results = [smart_read(dev) for dev in smart_devices(self.cfg)]
        with self.lock:
            previous = {s["device"]: s for s in self.smart}
            merged = []
            for entry in results:
                # A sleeping disk keeps its last known health, marked as standby.
                if entry.get("state") == "standby" and entry["device"] in previous:
                    old = dict(previous[entry["device"]])
                    old["standby"] = True
                    merged.append(old)
                else:
                    merged.append(entry)
            self.smart = merged

    def watch_conditions(self) -> None:
        """Start services automatically when their preconditions appear.

        - the data disk is mounted again: start the modules that need it
        - the LAN address appears (e.g. the router came up after the server,
          so DHCP was late): containers that could not bind to it are started
        - at collector start: core containers that are not running are started
        """
        data_present = os.path.exists(self.cfg.data_marker)
        ip_present = self.cfg.host_ip in netcheck.local_ipv4_addresses()
        reason = None
        if self._data_present is False and data_present:
            reason = "data disk available again"
        elif self._ip_present is False and ip_present:
            reason = "LAN address %s available" % self.cfg.host_ip
        elif self._ip_present is None and ip_present:
            states = {c.get("service"): c.get("state") for c in (containers() or [])}
            if any(states.get(s) != "running" for s in ("proxy", "pihole")):
                reason = "core services not running"
        self._data_present, self._ip_present = data_present, ip_present
        if reason and not (self._apply_thread and self._apply_thread.is_alive()):
            print("collector: %s; starting services" % reason, flush=True)
            self._apply_thread = threading.Thread(target=self._apply, name="apply", daemon=True)
            self._apply_thread.start()

    @staticmethod
    def _apply() -> None:
        from .stack import apply

        try:
            apply(Config())
        except Exception as exc:
            print("collector: starting services failed: %s" % exc, flush=True)

    # main snapshot -----------------------------------------------------------------

    def snapshot(self) -> dict:
        # Re-read settings so `homeisland apply` changes (addresses, modules) show up without a restart.
        try:
            self.cfg = Config(self.cfg.config_dir, self.cfg.repo_root)
        except Exception as exc:
            print("collector: keeping previous configuration: %s" % exc, flush=True)
        cfg = self.cfg
        enabled = cfg.enabled_modules()
        try:
            mods = active_modules(cfg)
        except Exception:
            mods = []
        results = checks.run_checks(cfg, mods, kinds={"http", "tcp"}, timeout=3.0)
        system_disk = disk_usage("/") or {}
        system_disk.update({"id": "system", "label": "System disk", "path": "/", "present": True})
        with self.lock:
            wan, lan, smart = dict(self.wan), dict(self.lan), list(self.smart)
        temps = {"cpu": cpu_temperature()}
        sensors = read_all(configured_sensors(cfg, enabled))
        for s in sensors:
            if s.get("ok") and "temperature_c" in s.get("values", {}):
                temps.setdefault("sensor", s["values"]["temperature_c"])
        hdd_temps = [s["temperature_c"] for s in smart if s.get("temperature_c") is not None]
        if hdd_temps:
            temps["hdd"] = max(hdd_temps)
        status = {
            "version": __version__,
            "generated_at": int(time.time()),
            "hostname": socket.gethostname(),
            "system": {
                "uptime_s": uptime(),
                "load": loadavg(),
                "cpu_percent": self.cpu.percent(),
                "cpu_count": os.cpu_count(),
                "cpu_temp_c": temps["cpu"],
                "memory": memory(),
                "arch": platform.machine(),
                "kernel": platform.release(),
            },
            "internet": wan,
            "lan": lan,
            "disks": [system_disk, data_disk_state(cfg)],
            "smart": smart,
            "containers": containers(),
            "checks": [r.as_dict() for r in results],
            "modules": checks.module_states(results),
            "sensors": sensors,
            "time": time_state(),
            "enabled_modules": enabled,
        }
        if "ups" in enabled:
            status["ups"] = ups_state(cfg.get("HOMEISLAND_UPS_NAME"))
        if self.fan is not None:
            status["fan"] = self.fan.update(temps)
        return status

    def setup_fan(self) -> None:
        if "fan" not in self.cfg.enabled_modules():
            return
        from .fan import FanController, FanCurve, SysfsPwm

        cfg = self.cfg
        curve = FanCurve(cfg.get_float("HOMEISLAND_FAN_MIN_TEMP"), cfg.get_float("HOMEISLAND_FAN_MAX_TEMP"),
                         cfg.get_float("HOMEISLAND_FAN_MIN_DUTY"), cfg.get_float("HOMEISLAND_FAN_MAX_DUTY"))
        driver = SysfsPwm(cfg.get_int("HOMEISLAND_FAN_PWMCHIP"), cfg.get_int("HOMEISLAND_FAN_CHANNEL"),
                          cfg.get_int("HOMEISLAND_FAN_PERIOD_NS"), cfg.get_bool("HOMEISLAND_FAN_INVERT"))
        self.fan = FanController(curve, driver, cfg.get("HOMEISLAND_FAN_SOURCE"))

    def run(self, once: bool = False) -> None:
        cfg = self.cfg
        os.makedirs(cfg.status_dir, exist_ok=True)
        self.setup_fan()
        if once:
            self.check_network()
            self.check_smart()
            write_json_atomic(cfg.status_file, self.snapshot())
            return
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        threads = [
            threading.Thread(target=self._loop, args=(cfg.get_float("HOMEISLAND_WAN_INTERVAL"), self.check_network),
                             daemon=True, name="network"),
            threading.Thread(target=self._loop, args=(cfg.get_float("HOMEISLAND_SMART_INTERVAL"), self.check_smart),
                             daemon=True, name="smart"),
        ]
        for t in threads:
            t.start()
        interval = cfg.get_float("HOMEISLAND_COLLECT_INTERVAL")
        self.cpu.percent()  # prime the CPU sampler
        while not self.stop.is_set():
            try:
                self.watch_conditions()
                write_json_atomic(cfg.status_file, self.snapshot())
            except Exception as exc:
                print("collector: snapshot failed: %s" % exc, flush=True)
            self.stop.wait(interval)
        if self.fan is not None:
            # Leave the fan at full speed when the collector stops.
            try:
                self.fan.driver.set_duty(100)
            except Exception:
                pass
