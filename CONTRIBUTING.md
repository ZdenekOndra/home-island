# Contributing

Thank you for helping. HomeIsland is meant to stay a **small, reliable,
offline-first digital island for the home** — not a general homelab
distribution. The most valuable contributions make it more reliable, easier to
install and recover, or better documented.

## Before you start

- Open an issue for anything larger than a small fix, so we can agree on the
  approach first.
- New features must work without Internet access. Web assets may not load
  anything from other hosts (no CDNs, web fonts or external APIs).
- Before adding a dependency or a module, ask: *does this materially improve an
  offline-first home server?* Prefer boring, proven technology and small
  resource use; the reference hardware is a Raspberry Pi 4.

## Development setup

You need Linux or macOS with Docker, Python 3.9+ and `shellcheck`.

```sh
git clone https://github.com/ZdenekOndra/home-island.git
cd home-island
python3 -m unittest discover -s tests -t tests       # unit tests
shellcheck install.sh update.sh uninstall.sh scripts/*.sh
scripts/check-repo.sh                                 # secrets / personal data scan
```

Run a throwaway stack as a normal user on high ports (HTTP 8088, DNS 15353):

```sh
eval "$(scripts/dev-env.sh /tmp/homeisland-dev)"
./homeisland apply
./homeisland collector --once
./homeisland test offline
curl -H 'Host: home.home.arpa' http://127.0.0.1:8088/
docker compose -p homeisland down          # when finished
```

`HOMEISLAND_DEV=1` (set by the script) only skips the root check; never use it
in production.

## Guidelines

- **Python**: standard library only on the host. Keep compatibility with
  Python 3.9. Keep functions small and error messages actionable.
- **Shell**: `set -euo pipefail`, pass `shellcheck`.
- **Containers**: pin exact image versions available for arm64 and amd64
  (`scripts/check-images.sh`), `restart: unless-stopped`, log limits, bind
  mounts with `create_host_path: false`, ports bound to `${HOMEISLAND_BIND_IP}`,
  least privilege. `tests/test_modules.py` checks this.
- **Installer**: never format, partition or mount disks, never change router
  settings, never expose ports to the Internet, never overwrite existing
  configuration.
- **Documentation**: update the relevant file in `docs/` and `CHANGELOG.md`.
- **Tests**: add unit tests for new logic. Say honestly in the pull request
  what you tested on real hardware and what you did not.
- **Privacy**: no telemetry of any kind. Anything that contacts an external
  service must be optional, off by default where possible, and listed in the
  module's `external` field.

## Commits

Use short conventional commit messages:

```
feat: add UPS runtime to the dashboard
fix: keep local DNS answering during WAN outage
docs: explain HDD spindown
```

Never commit real configuration, passwords, IP addresses of your network,
backups or datasets. `scripts/check-repo.sh` runs in CI.

## Licence

By contributing you agree that your contributions are licensed under the MIT
licence of this project.
