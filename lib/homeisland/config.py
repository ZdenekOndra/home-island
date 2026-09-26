"""Configuration loading.

Configuration lives outside the repository:

    /etc/homeisland/homeisland.env   settings (see .env.example)
    /etc/homeisland/secrets.env      generated secrets, mode 0600
    /etc/homeisland/modules.enabled  one optional module name per line
    /etc/homeisland/dns-records.conf extra local DNS records

The config directory can be overridden with HOMEISLAND_CONFIG_DIR, which is
used by the test suite and for development.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .util import HomeIslandError, load_env_file, read_text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_CONFIG_DIR = "/etc/homeisland"

DEFAULTS: Dict[str, str] = {
    "HOMEISLAND_DOMAIN": "home.arpa",
    "HOMEISLAND_HOST_IP": "",
    "HOMEISLAND_BIND_IP": "",
    "HOMEISLAND_HTTP_PORT": "80",
    "HOMEISLAND_DNS_PORT": "53",
    "HOMEISLAND_TZ": "Etc/UTC",
    "HOMEISLAND_STATE_DIR": "/var/lib/homeisland",
    "HOMEISLAND_DATA_DIR": "/data/homeisland",
    "HOMEISLAND_DATA_UID": "1000",
    "HOMEISLAND_DATA_GID": "1000",
    "HOMEISLAND_BACKUP_DIR": "",
    "HOMEISLAND_BACKUP_KEEP": "14",
    "HOMEISLAND_DNS_UPSTREAMS": "9.9.9.9;149.112.112.112",
    "HOMEISLAND_PIHOLE_QUERY_LOG_DAYS": "7",
    "HOMEISLAND_WAN_CHECK": "true",
    "HOMEISLAND_WAN_TARGETS": "1.1.1.1:53 9.9.9.9:53 8.8.8.8:53 208.67.222.222:53",
    "HOMEISLAND_WAN_INTERVAL": "30",
    "HOMEISLAND_COLLECT_INTERVAL": "15",
    "HOMEISLAND_SMART_INTERVAL": "1800",
    "HOMEISLAND_SMART_DEVICES": "",
    "HOMEISLAND_BME280_BUS": "1",
    "HOMEISLAND_BME280_ADDRESS": "0x76",
    "HOMEISLAND_BME280_LABEL": "Rack",
    "HOMEISLAND_UPS_NAME": "ups@localhost",
    "HOMEISLAND_FAN_PWMCHIP": "0",
    "HOMEISLAND_FAN_CHANNEL": "0",
    "HOMEISLAND_FAN_PERIOD_NS": "40000",
    "HOMEISLAND_FAN_SOURCE": "cpu",
    "HOMEISLAND_FAN_MIN_TEMP": "45",
    "HOMEISLAND_FAN_MAX_TEMP": "70",
    "HOMEISLAND_FAN_MIN_DUTY": "20",
    "HOMEISLAND_FAN_MAX_DUTY": "100",
    "HOMEISLAND_FAN_INVERT": "false",
    "HOMEISLAND_LIBRARY_REINDEX_HOURS": "6",
    "HOMEISLAND_MAPS_SOURCE": "https://build.protomaps.com",
}

SECRET_KEYS = ("PIHOLE_WEB_PASSWORD", "SAMBA_PASSWORD")

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_IPV4_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")
_MODULE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def valid_hostname(name: str) -> bool:
    return bool(_HOSTNAME_RE.match(name))


def valid_ipv4(value: str) -> bool:
    return bool(_IPV4_RE.match(value))


def valid_module_name(value: str) -> bool:
    return bool(_MODULE_RE.match(value))


class Config:
    def __init__(self, config_dir: Optional[str] = None, repo_root: Optional[str] = None):
        self.config_dir = config_dir or os.environ.get("HOMEISLAND_CONFIG_DIR") or DEFAULT_CONFIG_DIR
        self.repo_root = repo_root or REPO_ROOT
        self.settings_file = os.path.join(self.config_dir, "homeisland.env")
        self.secrets_file = os.path.join(self.config_dir, "secrets.env")
        self.modules_file = os.path.join(self.config_dir, "modules.enabled")
        self.dns_records_file = os.path.join(self.config_dir, "dns-records.conf")
        self.values: Dict[str, str] = dict(DEFAULTS)
        self.values.update(load_env_file(self.settings_file))
        self.secrets: Dict[str, str] = load_env_file(self.secrets_file)

    # -- basic accessors -------------------------------------------------

    @property
    def installed(self) -> bool:
        return os.path.isfile(self.settings_file)

    def get(self, key: str, default: str = "") -> str:
        value = self.values.get(key)
        if value is None or value == "":
            return DEFAULTS.get(key, default) or default
        return value

    def get_bool(self, key: str) -> bool:
        return self.get(key).strip().lower() in ("1", "true", "yes", "on")

    def get_int(self, key: str) -> int:
        value = self.get(key)
        try:
            return int(value, 0)
        except ValueError:
            raise HomeIslandError("%s must be an integer, got %r" % (key, value))

    def get_float(self, key: str) -> float:
        value = self.get(key)
        try:
            return float(value)
        except ValueError:
            raise HomeIslandError("%s must be a number, got %r" % (key, value))

    # -- derived values ----------------------------------------------------

    @property
    def domain(self) -> str:
        return self.get("HOMEISLAND_DOMAIN").strip(".").lower()

    @property
    def host_ip(self) -> str:
        return self.get("HOMEISLAND_HOST_IP")

    @property
    def bind_ip(self) -> str:
        return self.get("HOMEISLAND_BIND_IP") or self.host_ip or "0.0.0.0"

    @property
    def http_port(self) -> int:
        return self.get_int("HOMEISLAND_HTTP_PORT")

    @property
    def dns_port(self) -> int:
        return self.get_int("HOMEISLAND_DNS_PORT")

    @property
    def local_ip(self) -> str:
        """Address local checks connect to."""
        return self.bind_ip if self.bind_ip != "0.0.0.0" else "127.0.0.1"

    @property
    def state_dir(self) -> str:
        return self.get("HOMEISLAND_STATE_DIR")

    @property
    def data_dir(self) -> str:
        return self.get("HOMEISLAND_DATA_DIR")

    @property
    def backup_dir(self) -> str:
        return self.get("HOMEISLAND_BACKUP_DIR") or os.path.join(self.data_dir, "backups")

    @property
    def generated_dir(self) -> str:
        return os.path.join(self.state_dir, "generated")

    @property
    def status_dir(self) -> str:
        return os.path.join(self.state_dir, "status")

    @property
    def status_file(self) -> str:
        return os.path.join(self.status_dir, "status.json")

    @property
    def data_marker(self) -> str:
        return os.path.join(self.data_dir, ".homeisland-data")

    def fqdn(self, host: str) -> str:
        return "%s.%s" % (host, self.domain)

    def data_path(self, *parts: str) -> str:
        return os.path.join(self.data_dir, *parts)

    def state_path(self, *parts: str) -> str:
        return os.path.join(self.state_dir, *parts)

    def repo_path(self, *parts: str) -> str:
        return os.path.join(self.repo_root, *parts)

    # -- modules -------------------------------------------------------------

    def enabled_modules(self) -> List[str]:
        text = read_text(self.modules_file, "") or ""
        names = []
        for line in text.splitlines():
            name = line.split("#", 1)[0].strip()
            if name and name not in names:
                names.append(name)
        return names

    # -- extra DNS records -----------------------------------------------------

    def extra_dns_records(self) -> List[tuple]:
        """Return (hostname, ip) tuples from dns-records.conf.

        Format: one record per line, "<ip> <name> [<name>...]". Names without
        a dot are placed under the HomeIsland domain.
        """
        records = []
        text = read_text(self.dns_records_file, "") or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2 or not valid_ipv4(parts[0]):
                raise HomeIslandError("%s:%d: expected '<ipv4> <name>...'" % (self.dns_records_file, lineno))
            for name in parts[1:]:
                name = name.lower().rstrip(".")
                if "." not in name:
                    name = self.fqdn(name)
                if not valid_hostname(name):
                    raise HomeIslandError("%s:%d: invalid hostname %r" % (self.dns_records_file, lineno, name))
                records.append((name, parts[0]))
        return records

    # -- validation ----------------------------------------------------------

    def validate(self) -> List[str]:
        """Return a list of configuration problems (empty if valid)."""
        problems = []
        if not valid_hostname(self.domain):
            problems.append("HOMEISLAND_DOMAIN is not a valid domain: %r" % self.domain)
        if self.domain.endswith(".local") or self.domain == "local":
            problems.append("HOMEISLAND_DOMAIN must not use .local (reserved for mDNS); use home.arpa")
        if not self.host_ip:
            problems.append("HOMEISLAND_HOST_IP is not set")
        elif not valid_ipv4(self.host_ip):
            problems.append("HOMEISLAND_HOST_IP is not a valid IPv4 address: %r" % self.host_ip)
        if self.get("HOMEISLAND_BIND_IP") and not valid_ipv4(self.get("HOMEISLAND_BIND_IP")):
            problems.append("HOMEISLAND_BIND_IP is not a valid IPv4 address")
        for key in ("HOMEISLAND_STATE_DIR", "HOMEISLAND_DATA_DIR"):
            if not os.path.isabs(self.get(key)):
                problems.append("%s must be an absolute path" % key)
        if os.path.abspath(self.state_dir).startswith(os.path.abspath(self.repo_root) + os.sep):
            problems.append("HOMEISLAND_STATE_DIR must be outside the repository")
        for key in ("HOMEISLAND_HTTP_PORT", "HOMEISLAND_DNS_PORT", "HOMEISLAND_BACKUP_KEEP", "HOMEISLAND_PIHOLE_QUERY_LOG_DAYS",
                    "HOMEISLAND_DATA_UID", "HOMEISLAND_DATA_GID"):
            try:
                self.get_int(key)
            except HomeIslandError as exc:
                problems.append(str(exc))
        for upstream in self.get("HOMEISLAND_DNS_UPSTREAMS").split(";"):
            host = upstream.strip().split("#", 1)[0]
            if host and not (valid_ipv4(host) or ":" in host):
                problems.append("HOMEISLAND_DNS_UPSTREAMS entry is not an IP address: %r" % upstream)
        for name in self.enabled_modules():
            if not valid_module_name(name):
                problems.append("invalid module name in %s: %r" % (self.modules_file, name))
        try:
            self.extra_dns_records()
        except HomeIslandError as exc:
            problems.append(str(exc))
        return problems

    def require_installed(self) -> None:
        if not self.installed:
            raise HomeIslandError(
                "HomeIsland is not configured (%s missing); run install.sh first" % self.settings_file
            )
