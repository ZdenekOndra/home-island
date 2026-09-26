# Troubleshooting

Start with:

```sh
homeisland status
homeisland health
homeisland audit
```

## Names like `home.home.arpa` do not resolve

1. Does Pi-hole answer? `nslookup home.home.arpa 192.168.1.10` (your server's address).
   - No answer: `homeisland logs pihole`; check that port 53 is free
     (`sudo ss -lnup 'sport = :53'`).
2. Does the client use HomeIsland as DNS? Windows: `ipconfig /all`; macOS:
   `scutil --dns`; Linux: `resolvectl status`. If not, fix the router's DHCP DNS
   setting ([NETWORKING.md](NETWORKING.md)) and renew the lease.
3. Browsers with "secure DNS" (DNS-over-HTTPS) bypass the LAN resolver. Disable
   it or add an exception; it also breaks offline name resolution.
4. The IP address always works: `http://192.168.1.10/`.

## The dashboard shows "Monitoring data was last updated … ago"

```sh
sudo systemctl status homeisland-collector
journalctl -u homeisland-collector -n 50
```

## A service card is red / "This service is not responding"

```sh
homeisland status
homeisland logs <service>        # kiwix, library, maps, samba, jellyfin, pihole, proxy
sudo homeisland restart <service>
```

If the data disk is missing, modules that need it are not started; the status
shows the data disk as MISSING. Mount the disk; the collector starts them
automatically, or run `sudo homeisland apply`.

## "data disk not available"

- `findmnt /data` — is the disk mounted?
- `ls -la /data/homeisland/.homeisland-data` — the marker must exist. If you
  mounted the right disk but the marker is missing (e.g. you moved data), create
  it: `sudo touch /data/homeisland/.homeisland-data`.

## Containers do not start after a power cut

If the server booted before the router, a DHCP-assigned address may have been
missing and the containers could not bind to it. The collector starts them as
soon as the address appears. Give the server a static address to avoid this
([INSTALLATION.md](INSTALLATION.md#3-give-the-server-a-fixed-address)).

## Wikipedia shows no archives

`homeisland knowledge list`. After copying `.zim` files run
`sudo homeisland knowledge update`. A file listed as failing is incomplete or
corrupt — download it again.

## Library search finds nothing

- `homeisland knowledge index` and watch the output for errors.
- Scanned PDFs have no text; OCR them first.
- Files in hidden folders (starting with `.`) are ignored.

## Maps: "Map viewer assets are missing" or a blank map

- `sudo homeisland maps assets` (needs Internet once).
- `homeisland maps list` — is there a `.pmtiles` file?
- The browser must support WebGL. Very old devices may not.

## Samba: access denied

User `homeisland`, password from `sudo homeisland secrets show samba`. Windows
may cache old credentials: `net use * /delete`. Guest access is disabled on
purpose.

## Port 80 or 53 already in use

```sh
sudo ss -lntup 'sport = :80 or sport = :53'
```

Stop the conflicting service (another web server, `dnsmasq`, an older
Pi-hole). A resolver stub on `127.0.0.53` (systemd-resolved) does not conflict,
because HomeIsland binds to the LAN address only.

## `homeisland update` refuses to run

It refuses when files in the repository were modified. See `git status` in the
repository. Move site-specific container changes to
`/etc/homeisland/compose.override.yml` ([MODULES.md](MODULES.md)), then
`git checkout -- .`.

## Something broke after an update

```sh
sudo homeisland update --rollback
```

If configuration was damaged, `sudo homeisland restore latest` (a backup is
taken before every update).

## Memory warnings on Raspberry Pi OS

`docker compose` may warn that the kernel does not support memory limits. On
Raspberry Pi OS the memory cgroup controller can be disabled; the limits in
the compose files are then ignored (services still run). To enable them, add
`cgroup_enable=memory` to the single line in `/boot/firmware/cmdline.txt` and
reboot.

## Collecting information for a bug report

```sh
homeisland version; git -C /opt/homeisland rev-parse --short HEAD
homeisland status; homeisland audit
docker ps -a
journalctl -u homeisland-collector -n 100
```

Remove passwords, public IPs and anything personal before posting.
