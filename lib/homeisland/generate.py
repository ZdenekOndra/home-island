"""Render runtime configuration from settings and enabled modules.

Everything written here lives in STATE_DIR/generated and is safe to delete;
`homeisland apply` recreates it.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

from . import __version__
from .config import Config, valid_hostname
from .modules import Module, active_modules
from .util import HomeIslandError, write_atomic, write_json_atomic

DASHBOARD = "@dashboard"
HEADER = "# Managed by HomeIsland (homeisland apply). Local edits are overwritten.\n"

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
)


def render_template(value: str, cfg: Config) -> str:
    return value.replace("{domain}", cfg.domain).replace("{host_ip}", cfg.host_ip)


def proxy_routes(mods: List[Module]) -> Dict[str, Optional[str]]:
    """Map short host name -> upstream (or None for DNS-only names)."""
    routes: Dict[str, Optional[str]] = {}
    owner: Dict[str, str] = {}
    for mod in mods:
        for host, upstream in mod.hosts.items():
            if host in routes:
                raise HomeIslandError("host name %r is claimed by both %s and %s" % (host, owner[host], mod.name))
            if not valid_hostname(host) or "." in host:
                raise HomeIslandError("module %s: invalid host name %r" % (mod.name, host))
            routes[host] = upstream
            owner[host] = mod.name
    return routes


def dns_records(cfg: Config, mods: List[Module]) -> List[Tuple[str, str]]:
    records = [(cfg.fqdn(host), cfg.host_ip) for host in proxy_routes(mods)]
    seen = {name for name, _ in records}
    for name, ip in cfg.extra_dns_records():
        if name in seen:
            raise HomeIslandError("%s: %s is already provided by a module" % (cfg.dns_records_file, name))
        seen.add(name)
        records.append((name, ip))
    return records


def render_hosts(cfg: Config, mods: List[Module]) -> str:
    lines = [HEADER]
    for name, ip in dns_records(cfg, mods):
        lines.append("%s %s\n" % (ip, name))
    return "".join(lines)


def render_dnsmasq(cfg: Config) -> str:
    return (
        HEADER
        + "# Answer everything below the HomeIsland domain locally; never forward it upstream.\n"
        + "local=/%s/\n" % cfg.domain
        + "addn-hosts=/etc/homeisland-dns/hosts\n"
    )


def render_caddyfile(cfg: Config, mods: List[Module]) -> str:
    routes = proxy_routes(mods)
    port = "" if cfg.http_port == 80 else ":%d" % cfg.http_port
    out = [
        HEADER,
        "{\n",
        "\tauto_https off\n",
        "\tadmin off\n",
        "\tpersist_config off\n",
        "\thttp_port 8080\n",
        "}\n\n",
        "(common) {\n",
        "\theader {\n",
        "\t\t-Server\n",
        "\t\tX-Content-Type-Options nosniff\n",
        "\t\tReferrer-Policy no-referrer\n",
        "\t}\n",
        "\thandle_errors 502 503 504 {\n",
        "\t\tvars dashboard_url \"http://%s%s/\"\n" % (cfg.fqdn("home"), port),
        "\t\troot * /srv/dashboard\n",
        "\t\trewrite * /unavailable.html\n",
        "\t\ttemplates\n",
        "\t\tfile_server\n",
        "\t}\n",
        "}\n\n",
        "(dashboard) {\n",
        "\timport common\n",
        "\tencode gzip\n",
        '\theader Content-Security-Policy "%s"\n' % CSP,
        "\theader X-Frame-Options SAMEORIGIN\n",
        "\thandle /healthz {\n\t\trespond \"ok\" 200\n\t}\n",
        "\thandle /api/status.json {\n",
        "\t\troot * /srv/status\n",
        "\t\trewrite * /status.json\n",
        "\t\theader Cache-Control no-store\n",
        "\t\tfile_server\n",
        "\t}\n",
        "\thandle /api/services.json {\n",
        "\t\troot * /srv/generated\n",
        "\t\trewrite * /services.json\n",
        "\t\theader Cache-Control no-store\n",
        "\t\tfile_server\n",
        "\t}\n",
        "\thandle {\n",
        "\t\troot * /srv/dashboard\n",
        "\t\tfile_server\n",
        "\t}\n",
        "}\n\n",
    ]
    for host in sorted(routes):
        upstream = routes[host]
        if upstream is None:
            continue
        out.append("http://%s {\n" % cfg.fqdn(host))
        if upstream == DASHBOARD:
            if host == "status":
                out.append("\trewrite / /status.html\n")
            out.append("\timport dashboard\n")
        else:
            out.append("\timport common\n")
            if host == "pihole":
                out.append("\tredir / /admin/\n")
            out.append("\treverse_proxy %s\n" % upstream)
        out.append("}\n\n")
    out.append("# Any other host name or the plain IP address shows the dashboard.\n")
    out.append(":8080 {\n\timport dashboard\n}\n")
    return "".join(out)


def link_url(cfg: Config, link: dict) -> str:
    if "url" in link:
        return render_template(link["url"], cfg)
    host = link["host"]
    path = link.get("path", "/")
    port = cfg.http_port
    suffix = "" if port == 80 else ":%d" % port
    return "http://%s%s%s" % (cfg.fqdn(host), suffix, path)


def render_services(cfg: Config, mods: List[Module]) -> dict:
    links = []
    for mod in mods:
        for idx, link in enumerate(mod.links):
            links.append(
                {
                    "id": "%s-%d" % (mod.name, idx),
                    "module": mod.name,
                    "group": mod.group,
                    "title": link["title"],
                    "description": render_template(link.get("description", ""), cfg),
                    "url": link_url(cfg, link),
                    "icon": link.get("icon", "box"),
                }
            )
    return {
        "version": __version__,
        "generated_at": int(time.time()),
        "domain": cfg.domain,
        "modules": [
            {"name": m.name, "title": m.title, "group": m.group, "core": m.core} for m in mods
        ],
        "links": links,
    }


def _quote_env(key: str, value: str) -> str:
    if "'" in value or "\n" in value:
        raise HomeIslandError("value of %s must not contain quotes or newlines" % key)
    return "%s='%s'\n" % (key, value)


def compose_env(cfg: Config) -> Dict[str, str]:
    env = {
        "HOMEISLAND_REPO": cfg.repo_root,
        "HOMEISLAND_TZ": cfg.get("HOMEISLAND_TZ"),
        "HOMEISLAND_DOMAIN": cfg.domain,
        "HOMEISLAND_HOST_IP": cfg.host_ip,
        "HOMEISLAND_BIND_IP": cfg.bind_ip,
        "HOMEISLAND_HTTP_PORT": str(cfg.http_port),
        "HOMEISLAND_DNS_PORT": str(cfg.dns_port),
        "HOMEISLAND_STATE_DIR": cfg.state_dir,
        "HOMEISLAND_DATA_DIR": cfg.data_dir,
        "HOMEISLAND_GENERATED_DIR": cfg.generated_dir,
        "HOMEISLAND_DATA_UID": str(cfg.get_int("HOMEISLAND_DATA_UID")),
        "HOMEISLAND_DATA_GID": str(cfg.get_int("HOMEISLAND_DATA_GID")),
        "HOMEISLAND_DNS_UPSTREAMS": cfg.get("HOMEISLAND_DNS_UPSTREAMS"),
        "HOMEISLAND_PIHOLE_QUERY_LOG_DAYS": str(cfg.get_int("HOMEISLAND_PIHOLE_QUERY_LOG_DAYS")),
        "HOMEISLAND_LIBRARY_REINDEX_HOURS": cfg.get("HOMEISLAND_LIBRARY_REINDEX_HOURS"),
    }
    for key in ("PIHOLE_WEB_PASSWORD", "SAMBA_PASSWORD"):
        env[key] = cfg.secrets.get(key, "")
    return env


def render_compose_env(cfg: Config) -> str:
    return HEADER + "".join(_quote_env(k, v) for k, v in sorted(compose_env(cfg).items()))


def generate_all(cfg: Config) -> List[str]:
    """Write all generated files; return the list of files that changed."""
    problems = cfg.validate()
    if problems:
        raise HomeIslandError("invalid configuration:\n  - " + "\n  - ".join(problems))
    mods = active_modules(cfg)
    gen = cfg.generated_dir
    changed = []
    outputs = [
        (os.path.join(gen, "caddy", "Caddyfile"), render_caddyfile(cfg, mods), 0o644),
        (os.path.join(gen, "dnsmasq.d", "10-homeisland.conf"), render_dnsmasq(cfg), 0o644),
        (os.path.join(gen, "dns", "hosts"), render_hosts(cfg, mods), 0o644),
        (os.path.join(gen, "compose.env"), render_compose_env(cfg), 0o600),
    ]
    for path, content, mode in outputs:
        if write_atomic(path, content, mode):
            changed.append(path)
    services_path = os.path.join(gen, "api", "services.json")
    services = render_services(cfg, mods)
    # generated_at changes every run; only rewrite if something else changed.
    old = None
    try:
        with open(services_path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        pass
    if not old or {k: v for k, v in old.items() if k != "generated_at"} != {
        k: v for k, v in services.items() if k != "generated_at"
    }:
        write_json_atomic(services_path, services)
        changed.append(services_path)
    os.makedirs(cfg.status_dir, exist_ok=True)
    return changed
