# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-26

First public version.

### Added

- Core stack: Caddy reverse proxy with an offline dashboard, Pi-hole DNS with
  generated local records under `home.arpa`, host monitoring collector,
  daily verified backups.
- `homeisland` CLI: status, health, audit, offline readiness test, modules,
  backup/restore, disk status, knowledge and maps management, updates with
  rollback.
- Modules: Kiwix, document library with SQLite FTS5 search, offline vector
  maps (PMTiles + MapLibre), Samba, Home Assistant, Jellyfin, BME280 sensor,
  UPS via NUT, experimental PWM fan control.
- Installer for Raspberry Pi OS / Debian / Ubuntu on ARM64 and x86-64; never
  formats disks or changes router settings.
- Self-healing: modules start automatically when the data disk or the LAN
  address (re)appears.
- Documentation for installation, networking (incl. MikroTik), storage,
  offline operation, backup, recovery, security, hardware and troubleshooting.
- CI: unit tests (Python 3.9–3.13), ShellCheck, compose validation, image
  architecture check, repository hygiene scan, and a smoke test of the live stack.
