# Security

HomeIsland is a LAN service. It is designed to be safe on a home network with
untrusted devices on it (guests, IoT gadgets, a compromised laptop) and to
never be reachable from the Internet. It does not lower its guard because it
is "only" on a home LAN.

To report a vulnerability, see [SECURITY.md in the repository root](../SECURITY.md).

## Threat model

**Assets**: availability of local DNS and services; your documents and media;
Home Assistant (which may control physical devices); generated passwords;
the host itself.

**In scope**

| Threat | Mitigation |
|---|---|
| Services exposed to the Internet | No port forwarding is ever configured; ports bind to the LAN address only; docs say never to forward them |
| A malicious or compromised device on the LAN | Password-protected Samba and Pi-hole admin; no default passwords; no guest shares; read-only web access to the library only; no admin actions over HTTP without authentication |
| Container escape / lateral movement | `no-new-privileges`, capabilities dropped, read-only root filesystems, non-root users where the software allows; no container gets the Docker socket; no privileged containers |
| Secrets leaking into Git | Configuration lives in `/etc/homeisland`; `.gitignore`; `scripts/check-repo.sh` runs in CI |
| Malicious documents in the library | Served with a sandboxing Content-Security-Policy (no scripts, no external loads); `nosniff` |
| Supply chain (images, viewer assets) | Images pinned to exact versions; map viewer assets pinned and SHA-256 verified; ZIM downloads checksum-verified; Docker installed from Docker's signed apt repository |
| Tampered or damaged backups | SHA-256 per archive and per file; path traversal rejected on restore |
| DNS spoofing of local names | Pi-hole is authoritative for `home.arpa`; local names never go upstream |

**Out of scope / accepted risks**

- **Plain HTTP on the LAN.** Web traffic between clients and HomeIsland is not
  encrypted, because no public CA can issue certificates for `home.arpa` and
  offline operation rules out ACME. Anyone who can sniff your LAN (e.g. on an
  open Wi-Fi) can read it. Protect the LAN itself: WPA2/WPA3, a separate guest
  network. Options for TLS are below.
- The library, maps, Kiwix and the dashboard are readable by every device on
  the LAN without login. Do not put confidential documents in the library.
- Physical access to the server (disks are not encrypted by HomeIsland).
- Vulnerabilities in upstream software (Pi-hole, Home Assistant, …) until
  they are patched upstream and pinned here.
- Home Assistant uses host networking (required for discovery) and publishes
  port 8123 on all host interfaces.

## Host hardening checklist

- **SSH keys only.** In `/etc/ssh/sshd_config.d/hardening.conf`:
  ```
  PasswordAuthentication no
  PermitRootLogin no
  KbdInteractiveAuthentication no
  ```
  then `sudo systemctl reload ssh`. Test a second login before closing the first.
- **Automatic security updates:**
  ```sh
  sudo apt install unattended-upgrades
  sudo dpkg-reconfigure -plow unattended-upgrades
  ```
  Consider excluding Docker packages if you want to update them deliberately.
- **Firewall.** Docker-published ports bypass `ufw`/`iptables` INPUT rules, which
  is why HomeIsland binds ports to the LAN address instead of relying on a
  firewall. You can still use `ufw` for SSH and other host services:
  ```sh
  sudo apt install ufw
  sudo ufw default deny incoming
  sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
  sudo ufw enable
  ```
  To restrict published container ports by source, add rules to the
  `DOCKER-USER` chain.
- **No port forwarding** on the router to this host, ever. For remote access
  use a VPN (WireGuard) that terminates on the router.
- **Change generated passwords** if they were ever shown on a shared screen:
  edit `/etc/homeisland/secrets.env`, run `sudo homeisland apply`.
- Keep the router's firmware updated and its admin interface off the WAN.

## Container settings

| Service | User | Root FS | Capabilities | Notes |
|---|---|---|---|---|
| proxy (Caddy) | 65534 | read-only | none¹ | |
| maps (Caddy) | 65534 | read-only | none¹ | data mounted read-only |
| kiwix | 1001 | read-only | none | data mounted read-only |
| library | data UID | read-only | none | documents mounted read-only |
| pihole | root → drops to pihole | writable | Docker default | needs to own `/etc/pihole` |
| samba | root → per-user | writable | Docker default | password-only access |
| jellyfin | data UID | writable | Docker default | media mounted read-only |
| homeassistant | root | writable | Docker default | host network, not privileged |

¹ `NET_BIND_SERVICE` stays in the bounding set only because the Caddy binary
carries that file capability and would otherwise refuse to start; Caddy listens
on unprivileged port 8080 inside the container.

`tests/test_modules.py` enforces pinned images, restart policies, log limits,
non-creating bind mounts, LAN-bound ports, and the absence of `privileged` and
the Docker socket.

## TLS on the LAN (optional)

If you need encryption on the LAN, options are:

1. A VPN (WireGuard) for all remote and Wi-Fi clients.
2. A private CA (e.g. `step-ca` or `mkcert`) whose root you install on every
   device, and Caddy configured with those certificates. This adds certificate
   rotation that must keep working offline; HomeIsland does not automate it yet.
3. A public domain you own with DNS-01 certificates issued while online. They
   expire after 90 days, which conflicts with long offline periods.

## Updates

- **HomeIsland**: `sudo homeisland update` (backup first, rollback available).
  Container images are pinned in the compose files; updates to them arrive as
  HomeIsland releases after being checked for arm64/amd64 availability.
- **Pinning strategy**: exact version tags (no `latest`/`stable`), so a restart
  or reinstall never changes software silently. The library image is built
  locally from `python:3.13-slim-trixie` (a patch-level tag receiving Debian
  security updates) and rebuilt on `homeisland update`.
- **Operating system**: unattended-upgrades as above; reboot when a new kernel
  is installed (`/var/run/reboot-required`).
- If a critical upstream fix is released before a HomeIsland release, you can
  bump a tag in a local branch, or open an issue.

## Secrets

- `/etc/homeisland/secrets.env` (mode 0600, root) holds generated passwords.
- `/var/lib/homeisland/generated/compose.env` (mode 0600) holds a copy for
  Docker Compose.
- Backups contain the secrets and are written with mode 0600.
- `homeisland audit` fails if `secrets.env` is readable by other users.
- Nothing secret is ever written to the repository or to logs.
