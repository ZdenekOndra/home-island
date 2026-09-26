# Offline operation ("island mode")

When the WAN link disappears, HomeIsland keeps every local service working:
local DNS names, the dashboard, Wikipedia, the document library and search,
maps, file shares, monitoring, Home Assistant and Jellyfin.

What stops working is everything that needs the Internet: resolving Internet
names, downloading datasets, image updates, and whatever individual apps
(Home Assistant integrations, Jellyfin metadata) fetch from online services.

## How HomeIsland stays useful offline

- **Local names are answered locally.** Pi-hole is authoritative for
  `home.arpa` (`local=/home.arpa/`); these queries never wait for an upstream.
- **No external web dependencies.** The dashboard, library and map viewer ship
  every script, style, font and icon themselves, and a
  Content-Security-Policy with `connect-src 'self'` enforces that.
- **Non-blocking checks.** The Internet check runs in the background with 2.5 s
  timeouts on several independent targets. The dashboard reads a local JSON
  file; an outage can never make it slow.
- **Datasets are local files.** Kiwix archives, PMTiles maps and your
  documents are on the data disk; nothing is streamed from the Internet.
- **Images are pinned and present.** Containers restart from local images; no
  pull is needed after a reboot.

## Offline readiness test

```sh
homeisland test offline
```

```
HOMEISLAND OFFLINE READINESS TEST

[  PASS ] Dashboard           HTTP 200 in 2 ms
[  PASS ] Local DNS           8 local records resolve via 192.168.1.10
[  PASS ] Monitoring          collector updated 4 s ago
[  PASS ] Wikipedia           HTTP 200 in 3 ms
[  PASS ] Wikipedia datasets  found kiwix/*.zim
[  PASS ] Knowledge search    HTTP 200 in 9 ms
[NO DATA] Map datasets        run: homeisland maps download <region>
[  SKIP ] Jellyfin media server  module not enabled

WAN: OFFLINE
LOCAL READINESS: 92%  (11 passed, 0 failed, 1 without data, 1 modules not enabled)
```

- **PASS** — works using only local resources.
- **FAIL** — the module is enabled but not working. Exit code 1.
- **NO DATA** — the software works but has nothing to serve yet (for example no
  maps downloaded). Counts against readiness, not as a failure.
- **SKIP** — module not enabled.

The test only uses local resources; it does not cut your connection. It is
also run automatically by CI against a live stack.

### A real island test

Do this once after installation and after major changes:

1. Unplug the WAN cable from the router (or disable the WAN interface).
2. Wait one minute; the dashboard shows **INTERNET: OFFLINE — island mode**.
3. Run `homeisland test offline` on the server.
4. On a phone or laptop, open `http://home.home.arpa`, search the library,
   open a Wikipedia article and pan the map.
5. Reboot the HomeIsland server while still offline and repeat 3–4.
6. Reconnect the WAN.

Step 5 matters: it proves that nothing needs the Internet during start-up.

## Deeper audit

```sh
homeisland audit          # warnings and failures only
homeisland audit -v       # also list passed checks
```

The audit looks for external references in web assets, restart policies,
anonymous volumes, log limits, container health, DNS records, disk space and
health, backup age, dataset presence, time synchronisation, secrets file
permissions and modules that contact external services.

## Time during long outages

Without the Internet the system clock cannot be synchronised via NTP.

**What happens:**

- While running, a Raspberry Pi keeps time with its crystal oscillator. Drift is
  typically a few seconds per day, i.e. minutes after weeks — harmless for
  HomeIsland.
- **After a reboot without Internet**, a Raspberry Pi 4 has **no battery-backed
  clock**. `systemd-timesyncd` restores the time of the last shutdown (saved in
  `/var/lib/systemd/timesync/clock`), so the clock starts in the past by however
  long the Pi was off. The Raspberry Pi 5 has an RTC but needs a backup battery.

**Effects of a wrong clock:**

- Timestamps in logs, backups and Home Assistant history are wrong.
- TLS certificate validation can fail once you are online again, until NTP
  corrects the time (this happens automatically on reconnection).
- Home Assistant automations based on time of day fire at the wrong time.
- HomeIsland's own dashboard staleness check uses the server clock for both
  sides, so it stays correct.

**HomeIsland does not act as a time server.** Making an unsynchronised Pi the
authority for your whole network would spread wrong time to every device.
Pi-hole's built-in NTP server is explicitly disabled.

**Recommendations for long disconnected operation:**

1. Add a battery-backed RTC module (e.g. DS3231 on I2C). See
   [HARDWARE.md](HARDWARE.md#real-time-clock-rtc).
2. After a reboot, check the time (`timedatectl`) and set it if needed:
   `sudo timedatectl set-time "2026-01-31 12:00:00"` (needs `set-ntp false`
   first, then `set-ntp true` again when online).
3. For a truly independent time source, a GPS receiver with PPS and `chrony`
   works fully offline; this is outside HomeIsland's scope.

The dashboard's status page shows whether the clock is NTP-synchronised and
whether an RTC is present; `homeisland audit` reports both.

## Preparing for an outage

- Download the datasets you might need *before* you need them
  ([KNOWLEDGE.md](KNOWLEDGE.md), [MAPS.md](MAPS.md)).
- Put device manuals, emergency plans and home documentation into the library.
- Make sure a recent backup exists on a second medium.
- Print the dashboard address and the server's IP address, in case DNS on a
  device is misconfigured: `http://192.168.1.10/` always works.
- If you run on a UPS, enable the `ups` module so its state is visible.
