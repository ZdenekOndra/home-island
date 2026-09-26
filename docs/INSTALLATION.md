# Installation

This guide assumes a Raspberry Pi 4 with Raspberry Pi OS Lite (64-bit). Other
64-bit Debian/Ubuntu systems on ARM64 or x86-64 work the same way.

## 1. Prepare the hardware

- **System disk**: boot from a USB SSD if possible. SD cards wear out under
  constant writes (DNS logs, databases). Raspberry Pi Imager can write the OS
  directly to an SSD.
- **Power**: use the official 3 A power supply. A powered USB hub or a disk
  with its own supply is recommended for 3.5" HDDs.
- **Data disk (optional)**: a large HDD for Wikipedia, maps, documents,
  media and backups. See [STORAGE.md](STORAGE.md).
- **Network**: wired Ethernet. The server should not depend on Wi-Fi.

## 2. Install the operating system

With Raspberry Pi Imager choose *Raspberry Pi OS Lite (64-bit)* and, in the
advanced options, set a host name, create your user, enable SSH **with public
key authentication**, and set the time zone. Do not configure Wi-Fi unless you
need it.

After the first boot:

```sh
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## 3. Give the server a fixed address

All `*.home.arpa` names point to one address, and your router will hand that
address to every device as their DNS server. It must never change.

Use **both**:

1. a static address configured on the server itself, so it has an address
   even when the router is still booting after a power cut; and
2. an exclusion or reservation for that address in the router's DHCP settings,
   so no other device receives it.

On Raspberry Pi OS (NetworkManager), for example:

```sh
nmcli connection show                       # find the wired connection name
sudo nmcli connection modify "Wired connection 1" \
  ipv4.method manual ipv4.addresses 192.168.1.10/24 \
  ipv4.gateway 192.168.1.1 ipv4.dns "192.168.1.1"
sudo nmcli connection up "Wired connection 1"
```

Replace the example addresses with values from your network. The server itself
uses the router (not its own Pi-hole) for DNS, so it can always pull images and
updates even while Pi-hole is restarting. If you prefer a
DHCP reservation only, HomeIsland still works; the collector starts the
services as soon as the address appears.

## 4. Mount the data disk (optional but recommended)

HomeIsland never formats or mounts disks. If you use a data disk, prepare and
mount it before installing — [STORAGE.md](STORAGE.md) walks through it. The
default data directory is `/data/homeisland`.

## 5. Install HomeIsland

```sh
sudo git clone https://github.com/ZdenekOndra/home-island.git /opt/homeisland
cd /opt/homeisland
sudo ./install.sh
```

The installer:

1. checks the architecture (ARM64/x86-64) and distribution;
2. installs `git curl python3 smartmontools iproute2 iputils-ping` and, after
   asking, Docker Engine from Docker's official repository;
3. checks that port 53 is free on the LAN address;
4. shows your disks (read-only) and checks that the data directory is on a
   mounted disk; it asks before using the system disk;
5. writes `/etc/homeisland/homeisland.env` and generates passwords in
   `/etc/homeisland/secrets.env` (mode 0600);
6. asks which optional modules to enable;
7. installs the `homeisland` command, the collector, boot and backup units;
8. pulls the pinned container images, starts everything and runs health checks.

Non-interactive example:

```sh
sudo ./install.sh --yes --host-ip 192.168.1.10 --modules kiwix,library,maps,samba
```

Run `./install.sh --help` for all options. Re-running the installer is safe:
existing configuration and passwords are kept.

## 6. Point your network at HomeIsland DNS

Keep your router as the DHCP server and set the DNS server it advertises to
the HomeIsland address. Instructions and redundancy options:
[NETWORKING.md](NETWORKING.md); MikroTik example: [MIKROTIK.md](MIKROTIK.md).

Test from a client:

```sh
nslookup home.home.arpa 192.168.1.10
```

Then open <http://home.home.arpa/>.

## 7. Download offline data

While you still have Internet access:

- Wikipedia and other ZIM archives — [KNOWLEDGE.md](KNOWLEDGE.md)
- maps — [MAPS.md](MAPS.md)
- your own manuals and documents — copy them into the library share

## 8. Verify

```sh
homeisland status
homeisland test offline
homeisland audit
```

Then do a real island test: unplug the WAN cable from the router and run
`homeisland test offline` again ([OFFLINE_MODE.md](OFFLINE_MODE.md)).

## Passwords

```sh
sudo homeisland secrets show pihole   # Pi-hole web interface
sudo homeisland secrets show samba    # SMB user "homeisland"
```

To change one, edit `/etc/homeisland/secrets.env` and run `sudo homeisland apply`.

## Updating

```sh
sudo homeisland update
```

A configuration backup is taken first; configuration, datasets, user files and
backups are never modified. `sudo homeisland update --rollback` returns to the
previous version. Container images are pinned in the compose files, so an
update of HomeIsland is also how image updates arrive. See
[SECURITY.md](SECURITY.md#updates) for the update policy.

## Uninstalling

```sh
sudo ./uninstall.sh            # remove containers, units, CLI; keep config and data
sudo ./uninstall.sh --purge    # also delete /etc/homeisland and the state directory
```

The data directory is never deleted. Remember to change the DNS setting in
your router back.
