# Modules

```sh
homeisland module list
homeisland module info <name>
sudo homeisland module enable <name> [<name> ...]
sudo homeisland module disable <name>
```

Enabling a module creates its directories, starts its containers and adds its
DNS names and dashboard cards. Disabling removes its containers but keeps its
configuration and data. Core modules cannot be disabled.

| Module | Group | Host name(s) | Needs data disk | RAM (approx.) |
|---|---|---|---|---|
| dashboard | core | home, status | no | ~25 MB |
| dns | core | pihole | no | 30–60 MB with blocklists |
| monitoring | core | – (host service) | no | ~20 MB |
| backup | core | – (timer) | destination only | – |
| kiwix | knowledge | wiki | yes | 10–150 MB |
| library | knowledge | library | yes | ~25 MB idle |
| maps | maps | maps | yes | ~20 MB |
| samba | home | files | yes | ~30 MB |
| homeassistant | home | ha (port 8123) | no | 300–500 MB |
| jellyfin | media | media | yes | 200–400 MB |
| bme280 | hardware | – | no | – |
| ups | hardware | – | no | – |
| fan | hardware (experimental) | – | no | – |

RAM figures for dashboard, library, maps, kiwix and monitoring were measured
idle on an ARM64 test system; the others are estimates (see
[STATUS.md](../STATUS.md)).

## Core

**dashboard** — Caddy serves the static dashboard and proxies all web modules
by host name. Status page: `http://status.home.arpa`.

**dns** — Pi-hole. Web UI at `http://pihole.home.arpa/admin/`
(password: `sudo homeisland secrets show pihole`). The password is set from
`secrets.env` and cannot be changed in the web UI; edit the file and run
`sudo homeisland apply` instead. DHCP and Pi-hole's NTP server are disabled.
The query log keeps `HOMEISLAND_PIHOLE_QUERY_LOG_DAYS` (7) days to limit SSD
writes. External access: upstream DNS servers, blocklist updates.

**monitoring** — the `homeisland-collector` systemd service. External access:
TCP handshakes to `HOMEISLAND_WAN_TARGETS` (can be disabled).

**backup** — daily at ~03:30 via `homeisland-backup.timer`. See [BACKUP.md](BACKUP.md).

## Knowledge and maps

**kiwix**, **library** — see [KNOWLEDGE.md](KNOWLEDGE.md). **maps** — see [MAPS.md](MAPS.md).

## Home

**samba** — SMB shares `files`, `library`, `3d-models`, `kiwix`, `maps`, `media`
on the data disk. User `homeisland`, password
`sudo homeisland secrets show samba`. No guest access, SMB1 disabled
(minimum SMB2). Connect with `\\files.home.arpa\files` (Windows),
`smb://files.home.arpa/files` (macOS, Linux file managers). Network discovery
(WS-Discovery, mDNS, NetBIOS) is off; use the name or IP directly. Files are
owned by `HOMEISLAND_DATA_UID`.

**homeassistant** — Home Assistant Container at `http://ha.home.arpa:8123`.
Runs with host networking (needed for discovery), configuration in
`STATE_DIR/homeassistant` (SSD), included in backups (history database copied
consistently). HomeIsland does not depend on it. USB radios (Zigbee, Z-Wave):
add a `devices:` entry in `/etc/homeisland/compose.override.yml`, see below. Home Assistant contacts online
services for integrations, updates and optional cloud features; configure that
in Home Assistant itself. For Home Assistant's own backup and restore
features, see its documentation; HomeIsland backups cover the configuration
directory.

## Media

**jellyfin** — media server at `http://media.home.arpa`. Put files under
`DATA_DIR/media/{movies,shows,music,photos}`; the container sees them
read-only. The Raspberry Pi 4 cannot transcode 4K/HEVC well — prefer formats
your clients can direct-play (H.264/AAC). For fully offline use, disable the
online metadata providers in the library settings.

## Hardware

**bme280**, **ups**, **fan** — see [HARDWARE.md](HARDWARE.md). They add
readings to the collector; the dashboard shows them when enabled.

## Local changes to a module

Do not edit files in the repository; `homeisland update` refuses to run with
local changes. Put site-specific container changes in
`/etc/homeisland/compose.override.yml` instead. It is merged last, included in
backups and survives updates. Example: a Zigbee stick for Home Assistant.

```yaml
services:
  homeassistant:
    devices:
      - /dev/serial/by-id/usb-EXAMPLE_Zigbee_Adapter-if00-port0:/dev/ttyUSB0
```

Validate and apply:

```sh
sudo homeisland config validate
sudo homeisland apply
```

Overrides for services of modules that are not enabled make Compose fail;
remove them when you disable a module.

## Writing a module

1. Create `modules/<name>/module.json` (see `lib/homeisland/modules.py` for all
   fields) and, for containers, `modules/<name>/compose.yml`.
2. Pin the image to an exact version that exists for arm64 and amd64
   (`scripts/check-images.sh`).
3. Use `restart: unless-stopped`, a `logging` size limit, bind mounts with
   `create_host_path: false`, and publish ports only on `${HOMEISLAND_BIND_IP}`.
4. Add health checks (`http`, `tcp`, `dataset`) so the offline test covers it.
5. List every external service the module contacts in `external`.
6. Run the unit tests — `tests/test_modules.py` enforces most of the above.
