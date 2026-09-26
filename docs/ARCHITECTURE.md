# Architecture

HomeIsland is deliberately small: a handful of containers managed by Docker
Compose, one host service written in Python (standard library only), and a
static web dashboard. This document explains the moving parts and why they are
the way they are.

## Priorities

Reliability, offline operation, security and simplicity come before features.
Every design decision below follows from one question: *what still works when
the Internet, the data disk, or HomeIsland itself is gone?*

## Components

```
                              HomeIsland host
┌──────────────────────────────────────────────────────────────────────────────┐
│ systemd                                                                      │
│   homeisland-collector.service   host metrics, SMART, WAN/LAN probes,       │
│                                  self-healing → /var/lib/homeisland/status  │
│   homeisland-boot.service        `homeisland start` at boot                  │
│   homeisland-backup.timer        daily `homeisland backup`                   │
│                                                                              │
│ Docker Compose project "homeisland" (network: homeisland)                   │
│   core      proxy (Caddy)   :80  → dashboard + reverse proxy by host name    │
│             pihole          :53  → DNS, local records from generated files   │
│   modules   kiwix, library, maps, samba (:445), homeassistant (host net),    │
│             jellyfin                                                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Piece | Technology | Why |
|---|---|---|
| Orchestration | Docker Compose | Boring, proven, one file per module, restart policies, no cluster |
| Reverse proxy + dashboard | Caddy (static files) | Tiny, single binary, simple config; serves the dashboard directly |
| Dashboard | Static HTML/CSS/vanilla JS | No build step, no framework, nothing to download at runtime |
| Monitoring | Python collector on the host | Needs host access (SMART, sensors, Docker); writes one JSON file |
| DNS | Pi-hole | Widely used, web UI, local records, ad blocking as a bonus |
| Offline Wikipedia | kiwix-serve | The reference ZIM server, low memory use |
| Document search | Own small app, SQLite FTS5 | Full-text search in ~30 MB RAM; no search cluster |
| Maps | PMTiles + MapLibre, static files | No tile server and no rendering on the Pi; the browser renders vector tiles |
| CLI | Python (standard library) | Available on every target OS, testable, no dependencies |

No Kubernetes, no databases beyond SQLite, no message brokers, no Node.js at
runtime.

## Repository layout

```
compose.yml            core services (proxy, pihole)
homeisland             CLI entry point (Python)
install.sh             installer; update.sh / uninstall.sh
.env.example           settings template → /etc/homeisland/homeisland.env
lib/homeisland/        CLI, collector, checks, backup, generators
dashboard/             static dashboard (served read-only by Caddy)
modules/<name>/        module.json metadata, compose.yml fragment, app code
configs/systemd/       unit templates
scripts/               development and CI helpers
tests/                 unit tests (python -m unittest)
docs/                  documentation
examples/              example configuration snippets
```

## Runtime layout

Nothing that is created at runtime lives in the repository.

| Path | Content | Disk |
|---|---|---|
| `/etc/homeisland/homeisland.env` | settings | SSD |
| `/etc/homeisland/secrets.env` | generated passwords (mode 0600) | SSD |
| `/etc/homeisland/modules.enabled` | enabled optional modules | SSD |
| `/etc/homeisland/dns-records.conf` | your extra DNS records | SSD |
| `/var/lib/homeisland/generated/` | rendered Caddyfile, DNS files, compose env, services.json (safe to delete) | SSD |
| `/var/lib/homeisland/status/` | collector output | SSD |
| `/var/lib/homeisland/<module>/` | Pi-hole config, HA config, search index, Kiwix library file | SSD |
| `/data/homeisland/` | datasets, documents, media, backups (`.homeisland-data` marker) | HDD |

All three locations are configurable (`HOMEISLAND_STATE_DIR`,
`HOMEISLAND_DATA_DIR`, `HOMEISLAND_BACKUP_DIR`; the config directory via
`HOMEISLAND_CONFIG_DIR`).

## Modules

A module is a directory under `modules/` with a `module.json` and, if it runs
containers, a `compose.yml` fragment. The CLI combines `compose.yml` with the
fragments of all enabled modules:

```
docker compose -p homeisland --env-file /var/lib/homeisland/generated/compose.env \
  -f compose.yml -f modules/kiwix/compose.yml -f modules/maps/compose.yml ...
```

`module.json` declares host names (for DNS and the reverse proxy), dashboard
links, health checks, data and state directories, backup paths and any
external services the module contacts. See `lib/homeisland/modules.py` for
the full schema and [MODULES.md](MODULES.md) for the list.

`homeisland apply` is the single idempotent operation that makes the system
match the configuration:

1. validate settings;
2. render the Caddyfile, Pi-hole's local records, `services.json` and the
   compose environment file;
3. create missing state/data directories (data directories only when the data
   disk is present);
4. `docker compose up -d --remove-orphans` for the enabled modules;
5. reload DNS or restart the proxy only if their generated files changed.

Disabling a module removes its containers but keeps its data and state.

## Request flow

- A browser asks Pi-hole for `wiki.home.arpa`. Pi-hole answers from
  `/etc/homeisland-dns/hosts` (generated), without contacting anyone:
  `local=/home.arpa/` makes the whole domain authoritative locally.
- The browser connects to Caddy on port 80, which routes by host name to the
  `kiwix` container on the internal Docker network.
- If a module's container is down, Caddy shows a local "service not responding"
  page with a link back to the dashboard.

## Monitoring and the dashboard

The collector runs on the host as root (for SMART, I2C, PWM and the Docker
CLI) under systemd sandboxing. It writes `status.json` every 15 s. Slow probes
run in their own threads with short timeouts:

- Internet: parallel TCP handshakes to several independent public resolvers
  every 30 s; online if any answers. No single third party decides the state.
- LAN: local addresses, default gateway ping, and a DNS query to the local Pi-hole.
- SMART: every 30 min with `smartctl -n standby`, so sleeping disks stay asleep.

The dashboard is static. It fetches `/api/services.json` and
`/api/status.json` from the same origin with a 5 s timeout. Loss of Internet
can therefore never make it slow: nothing on the page depends on anything
outside the LAN. Staleness is computed with the server's `Date` header, not the
client clock.

The collector also heals the stack: it runs `apply` when the data disk
reappears, when the LAN address appears late (router booted after the server),
and at start-up if core containers are not running.

## Failure behaviour

| Failure | Effect |
|---|---|
| WAN down | All local services keep working; Pi-hole cannot resolve Internet names; dashboard shows *OFFLINE — island mode* |
| Data disk missing | DNS, dashboard, monitoring, backups-to-other-destinations keep running. Modules that need the disk are not started (bind mounts use `create_host_path: false`, so nothing is ever written to the SSD by mistake). They start automatically when the disk returns |
| One container crashes | Docker restarts it (`restart: unless-stopped`); other services are unaffected; the proxy shows a friendly error page meanwhile |
| Collector stops | Services keep working; dashboard shows a stale-data warning |
| HomeIsland host dies | The router still does DHCP; devices keep IP connectivity. Name resolution fails unless clients have a second DNS server — see [NETWORKING.md](NETWORKING.md) |
| Server boots before the router | Containers cannot bind to the LAN address until it exists; the collector starts them as soon as it does. A static address on the server avoids this entirely |

## Security boundaries

Containers run with `no-new-privileges`, read-only root filesystems and all
capabilities dropped where the software allows it, and never get the Docker
socket. Published ports bind to the LAN address only. See
[SECURITY.md](SECURITY.md) for the threat model.

## Decisions and alternatives

- **Caddy vs. nginx**: both would work; Caddy's configuration is shorter and
  easier to generate. HTTPS is off because there is no public certificate
  authority reachable offline; see [SECURITY.md](SECURITY.md) for LAN TLS options.
- **Pi-hole in bridge mode** (not host networking): keeps port 80 free for the
  proxy and limits Pi-hole to DNS.
- **Home Assistant on the host network**: required for device discovery (mDNS/SSDP).
- **Own search app instead of a search engine**: Elasticsearch/OpenSearch or
  Meilisearch would need far more RAM than a Pi should spend on this. SQLite
  FTS5 handles tens of thousands of documents easily.
- **Vector maps from PMTiles** instead of a tile server: the Pi serves byte
  ranges of one file; rendering happens on the client. A Czech Republic
  extract is ~1.7 GB for full detail.
- **Collector as a systemd service, not a container**: it needs the host's
  view of disks, sensors and Docker; giving a container the Docker socket
  would be equivalent to root anyway.
- **Python standard library only on the host**: nothing to `pip install`, no
  virtualenv to break during OS upgrades.
