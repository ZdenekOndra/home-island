# Project status

State of HomeIsland 0.1.0 (2026-09-26). This file distinguishes what was
**tested**, what was only **inspected or validated**, and what **needs real
hardware** to confirm.

## Implemented

- Core: Caddy reverse proxy + static offline dashboard and status page;
  Pi-hole with generated `home.arpa` records (DHCP and NTP disabled, logs in
  RAM, short query history); host collector; daily backups.
- CLI: `status`, `health`, `audit`, `test offline`, `apply/start/stop/restart/logs`,
  `module list/info/enable/disable`, `backup [list|verify]`, `restore`,
  `disk status`, `knowledge list/update/index/download`,
  `maps list/assets/download`, `dns`, `secrets`, `config show/check/validate`,
  `update [--ref|--rollback]`.
- Modules: kiwix, library (SQLite FTS5 search for PDF/EPUB/MD/HTML/TXT),
  maps (PMTiles + MapLibre), samba, homeassistant, jellyfin, bme280, ups, fan
  (experimental).
- Installer (`install.sh`) with restore mode, `update.sh`, `uninstall.sh`.
- Self-healing: modules start when the data disk or the LAN address appears;
  core containers are started if found stopped when the collector starts.
- Site-specific overrides in `/etc/homeisland/compose.override.yml`.
- Documentation in `docs/`, CI workflow in `.github/workflows/ci.yml`.

## Automated tests performed

| Test | Where | Result |
|---|---|---|
| 57 unit tests (config, generators, module definitions, backup/restore incl. tamper and path-traversal cases, library indexing/search, BME280 compensation, fan curve and sysfs driver, DNS client, WAN check, audit URL scan) | Python 3.14 (macOS) and Python 3.9 (`python:3.9-slim` container) | pass |
| ShellCheck 0.11 on all shell scripts | macOS | pass |
| `docker compose config` with every module enabled | Docker 29 / Compose | pass |
| All pinned images exist for linux/arm64 and linux/amd64 (`scripts/check-images.sh`) | registry manifests | pass (8/8) |
| Repository scan for secrets, personal data, datasets (`scripts/check-repo.sh`) | | pass |

## Integration tests performed

On **Docker Desktop (Apple Silicon, arm64 Linux VM)**, stack run as a normal
user via `scripts/dev-env.sh`:

- core + kiwix + library + maps started; offline test 100 % with test datasets;
- Kiwix: real (small) ZIM downloaded with `knowledge download`, SHA-256
  verified, served;
- library: PDF, EPUB, HTML, Markdown and text indexed; diacritics-insensitive
  and prefix search; scripts in HTML not indexed;
- maps: viewer assets downloaded and checksum-verified; Czech Republic extract
  (zoom ≤ 8) rendered in headless Chrome; all requests stayed on
  `*.home.arpa` (also checked for dashboard, status page, library, Kiwix and
  Pi-hole login);
- extra DNS records reloaded without restarting Pi-hole;
- Samba enabled: authenticated share listing works, wrong password and guest
  access denied; module disabled cleanly;
- backup → deletion of config and state → restore → 100 % readiness;
- data directory removed: core kept running, data modules skipped; directory
  returned: the collector started the modules automatically;
- a module's backend stopped: the proxy served the local "service not
  responding" page.

On **Debian 13 (trixie) arm64 with systemd**, in a privileged container with
nested Docker (closest available stand-in for a Raspberry Pi OS host):

- `install.sh --yes` from scratch, including Docker CE installation from
  download.docker.com, systemd units, health checks; re-running the installer
  kept configuration and secrets;
- **WAN cut** with firewall rules for host and container traffic: dashboard
  < 1 ms, local names answered instantly, collector reported OFFLINE / LAN
  OPERATIONAL, offline test 100 % after copying datasets in "by USB";
- reboot while the WAN was cut: all services came back, 100 % readiness;
- LAN address removed (server "booted before the router"): proxy and Pi-hole
  failed to bind, as expected; address restored: collector restarted them;
- disaster recovery: containers, units, `/etc/homeisland`, `/var/lib/homeisland`
  and the locally built image deleted; `install.sh --restore <backup>` restored
  Pi-hole configuration, DNS records and the search index;
- `homeisland update` (fast-forward with pre-update backup) and
  `homeisland update --rollback`;
- `homeisland audit` output reviewed.

## Not tested — requires physical hardware

- An actual **Raspberry Pi 4**: performance, temperatures, power draw,
  Raspberry Pi OS specifics (e.g. memory cgroup, `/boot/firmware` paths).
- Real disks: SMART via USB–SATA bridges, `smartctl -n standby` behaviour,
  HDD spindown, `nofail` boot without the disk.
- BME280 sensor on I2C, UPS via NUT, PWM fan (fan module is experimental; only
  simulated sysfs PWM was tested), DS3231 RTC.
- Samba from Windows/macOS/Android clients; Jellyfin playback; Home Assistant
  (compose validated and image checked, containers not started in tests).

## Not tested — requires actual WAN disconnection

The WAN outage was simulated with firewall rules in a test VM. Not yet done:
unplugging a real router's WAN port with real clients (phones, laptops) using
HomeIsland DNS via DHCP, including client-side behaviour such as browsers'
DNS-over-HTTPS and captive-portal checks.

## Also not yet done

- The GitHub Actions workflow has not run yet (the repository was not pushed
  as part of this work).
- The MikroTik commands in `docs/MIKROTIK.md` were written from RouterOS 7
  documentation, not executed on a router.
- x86-64: all images exist for amd64 and nothing is architecture-specific, but
  the stack was only run on arm64.
- Large datasets (full Wikipedia, 48 GB Europe map) were not served on a Pi.

## Known limitations

- Web services use plain HTTP on the LAN (no public CA can serve `home.arpa`
  offline); see `docs/SECURITY.md` for options.
- Library, maps, Kiwix and the dashboard have no login; they are readable by
  every LAN device.
- Home Assistant uses host networking and listens on all interfaces (port 8123).
- No offline address search or routing in maps.
- Scanned PDFs need OCR before they are searchable.
- One Pi-hole instance; DNS redundancy is documented, not automated.
- Without an RTC the clock is wrong after a reboot during a long outage.
- `homeisland update` requires a clean git checkout.
- Entries in `compose.override.yml` for services of disabled modules make
  Compose fail until removed.
- While the data disk is missing, a module disabled in that time keeps its
  (stopped) containers until the next `apply` with the disk present.

## Supported architectures

| Architecture | Status |
|---|---|
| linux/arm64 (Raspberry Pi 4/5, other 64-bit SBCs) | primary target; tested in an arm64 VM, not on a Pi |
| linux/amd64 | images available; not run |
| 32-bit ARM | not supported (installer refuses) |

## Resource usage

Measured idle on the arm64 test VM (`docker stats`, small test datasets, no
Pi-hole blocklists downloaded):

| Component | RAM |
|---|---|
| proxy (Caddy) | ~24 MB |
| pihole | ~10 MB (expect 30–60 MB with default blocklists; estimate) |
| collector (host process RSS) | ~21 MB |
| library | ~22 MB |
| maps (Caddy) | ~19 MB |
| kiwix | ~6 MB (grows with archive size and use) |

- **Core idle RAM**: ~55 MB measured (+ blocklists); with knowledge and maps
  ~100–150 MB. Home Assistant (300–500 MB) and Jellyfin (200–400 MB) are
  estimates from upstream experience, not measured here.
- **Idle CPU**: < 1 % on the test VM; the collector wakes every 15 s.
  (Raspberry Pi figures not measured.)
- **Storage**: container images ~0.6 GB for core + knowledge + maps (library
  image 271 MB, Pi-hole 166 MB, Caddy 85 MB, Kiwix 88 MB); state < 10 MB plus
  Pi-hole and Home Assistant databases; datasets see `docs/STORAGE.md`.
- **Power** (estimate from public Raspberry Pi 4 measurements, not measured
  here): ~5 W idle with SSD and sleeping HDD; 3.5" HDD spinning adds 3–6 W.

## Recommended next steps

1. Install on a real Raspberry Pi 4 with Raspberry Pi OS Lite 64-bit, SSD and
   USB HDD; run `homeisland audit`, then a real WAN-unplug test with clients.
2. Push the repository and let CI run; enable GitHub private vulnerability
   reporting (referenced in `SECURITY.md`).
3. Measure idle RAM/CPU/power on the Pi and replace estimates in this file.
4. Test SMART and spindown with common USB–SATA bridges; test BME280, NUT and
   the fan driver on hardware, then drop the fan module's experimental label.
5. Consider optional LAN TLS with a local CA, and a documented second-Pi-hole
   sync for DNS redundancy.
