# Backups

HomeIsland backs up what is hard to recreate — configuration and state — and
deliberately skips what can be downloaded again.

## What is backed up

| Included | Path |
|---|---|
| HomeIsland settings, generated passwords, module list, DNS records | `/etc/homeisland/` |
| Pi-hole configuration, blocklists, allow/deny lists (`pihole.toml`, `gravity.db`) | `STATE_DIR/pihole/` |
| Pi-hole Teleporter export (importable in any Pi-hole web UI) | `extras/pihole-teleporter.zip` |
| Kiwix library file | `STATE_DIR/kiwix/` |
| Document search index (saves hours of re-indexing on a Pi) | `STATE_DIR/library/` |
| Home Assistant configuration, including its database | `STATE_DIR/homeassistant/` (if enabled) |
| Jellyfin configuration and library database (without image cache) | `STATE_DIR/jellyfin/config/` (if enabled) |

| Not included | Why |
|---|---|
| Wikipedia/ZIM archives, maps, map assets | Replaceable downloads, hundreds of GB |
| Your documents, 3D models, media | Your data; back it up separately (see below) |
| Pi-hole query log, TLS certificate, caches, logs | Regenerated automatically |
| Container images | Pulled again at installation |

SQLite databases are copied with SQLite's online backup API, so a running
Pi-hole or Home Assistant never produces an inconsistent copy.

A typical backup is 1–20 MB (more with a large Home Assistant history).

## Where backups go

Default: `DATA_DIR/backups/` on the data disk — on a *different disk* than the
configuration it protects. Change it with `HOMEISLAND_BACKUP_DIR`, e.g. a
mounted USB stick:

```sh
sudo mkdir -p /mnt/backup-usb
# mount it (see STORAGE.md for fstab with nofail), then:
echo 'HOMEISLAND_BACKUP_DIR=/mnt/backup-usb/homeisland' | sudo tee -a /etc/homeisland/homeisland.env
```

If the destination is not mounted, the backup fails with a clear error instead
of silently writing to the system disk.

## Schedule and retention

`homeisland-backup.timer` runs daily at ~03:30 (randomised by up to 20
minutes; missed runs are caught up after boot). The newest
`HOMEISLAND_BACKUP_KEEP` (14) backups are kept.

```sh
systemctl list-timers homeisland-backup.timer
journalctl -u homeisland-backup
```

## Commands

```sh
sudo homeisland backup                      # create one now
sudo homeisland backup --dest /mnt/usb      # to another directory
homeisland backup list
homeisland backup verify latest             # or a file path
sudo homeisland restore latest              # or a file path
```

## Format and verification

```
homeisland-backup-20260101T033012Z.tar.gz
homeisland-backup-20260101T033012Z.tar.gz.sha256
```

The archive contains `manifest.json` (version, enabled modules, original paths,
SHA-256 of every file). `backup verify` checks the archive checksum, that every
file matches the manifest and that no path could escape the restore
directories. Every new backup is verified right after it is written.

Backups contain your passwords (`secrets.env`) and are written with mode 0600.
Treat copies accordingly.

## Restore

```sh
sudo homeisland restore latest
```

1. The backup is verified.
2. Running services are stopped.
3. Current configuration and state that would be replaced are moved aside to
   `*.pre-restore-<timestamp>` (nothing is deleted).
4. Files are restored with their original owners and permissions.
5. Services are started (`--no-start` to skip).

After a restore on new hardware, check `HOMEISLAND_HOST_IP` if the address
changed. Full procedure: [RECOVERY.md](RECOVERY.md).

## Your own data

HomeIsland's backup is not a backup of your documents and media. For those,
use a separate tool and a separate disk, for example:

```sh
sudo apt install rsync
sudo rsync -a --delete /data/homeisland/library/ /mnt/backup-usb/library/
```

or `restic`/`borg` for versioned, encrypted backups. Follow the 3-2-1 rule for
anything irreplaceable: three copies, two media, one off-site.
