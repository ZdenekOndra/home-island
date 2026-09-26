# Disaster recovery

This procedure rebuilds HomeIsland after the Raspberry Pi died, the system SSD
died, or the installation is otherwise gone. It assumes the data disk (with
`homeisland/backups/`) survived. If it did not, use a backup copy from another
medium (USB stick, another computer).

Expected time: 30–60 minutes, most of it OS installation and image downloads.

## What you need

- Replacement hardware (any supported 64-bit system) and a system disk.
- Internet access for installing the OS, Docker and container images.
  (Datasets do **not** need to be downloaded again — they are on the data disk.)
- The data disk, or a copy of a `homeisland-backup-*.tar.gz` file.

## Procedure

### 1. Install the operating system

Raspberry Pi OS Lite (64-bit) or another supported OS, with SSH and your user,
as in [INSTALLATION.md](INSTALLATION.md#2-install-the-operating-system).

### 2. Give the server its old address

Configure the same static IP address as before (see
[INSTALLATION.md](INSTALLATION.md#3-give-the-server-a-fixed-address)). Then
clients, whose DHCP-provided DNS server points at that address, work again
without touching the router. If you must use a different address, update the
router's DNS setting afterwards.

### 3. Attach and mount the data disk

Do **not** format it. Mount it at the same place as before, by label:

```sh
lsblk -o NAME,SIZE,LABEL,FSTYPE,MOUNTPOINTS
sudo mkdir -p /data
echo 'LABEL=homeisland-data /data ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /data
ls /data/homeisland/backups/
```

### 4. Clone HomeIsland

```sh
sudo git clone https://github.com/ZdenekOndra/home-island.git /opt/homeisland
cd /opt/homeisland
```

To get exactly the version you ran before, check the backup's manifest
(`git_commit`), then `sudo git checkout <commit>`:

```sh
tar -xzOf /data/homeisland/backups/<latest>.tar.gz manifest.json | grep git_commit
```

### 5. Install with restore

```sh
ls /data/homeisland/backups/
sudo ./install.sh --restore /data/homeisland/backups/homeisland-backup-<latest>.tar.gz
```

The installer verifies the backup, restores `/etc/homeisland` and the saved
state (Pi-hole, Home Assistant, search index, …), installs Docker and the
systemd units, pulls the pinned images and starts everything.

If the backup came from another medium, copy it to the server first, e.g.
`scp homeisland-backup-*.tar.gz* user@192.168.1.10:/tmp/`, and pass that
path.

### 6. Check

```sh
homeisland status
homeisland test offline
homeisland audit
```

If the IP address changed, the installer warns you. Update
`HOMEISLAND_HOST_IP` in `/etc/homeisland/homeisland.env`, run
`sudo homeisland apply`, and change the DNS server in your router.

## Variations

**The data disk is gone too.** Restore from your off-disk backup copy as above,
then download datasets again ([KNOWLEDGE.md](KNOWLEDGE.md), [MAPS.md](MAPS.md))
and restore your documents from your own backups. Before running the
installer, mount an empty data disk; the installer creates the layout.

**Only the SD card/SSD is corrupt, the Pi is fine.** Same procedure on the same
hardware.

**No backup at all.** Run `sudo ./install.sh` normally. Your datasets and
documents on the data disk are detected and served again; Pi-hole settings,
Home Assistant configuration and passwords start fresh. Run
`sudo homeisland knowledge update` and `homeisland knowledge index` to
re-register Kiwix archives and rebuild the search index.

**Moving to new hardware deliberately.** Run `sudo homeisland backup` on the
old system, move the data disk, then follow this procedure.

## Rehearse it

A recovery procedure that has never been tried is a hope, not a plan. Once a
year, restore the latest backup onto a spare SD card and Raspberry Pi (without
the data disk: pass `--allow-system-disk`), and check that Pi-hole and the
dashboard come up.
