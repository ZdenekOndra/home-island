# Storage

## Reference layout

| Disk | Holds | Why |
|---|---|---|
| Small SSD (system) | OS, Docker, `/etc/homeisland`, `/var/lib/homeisland` (Pi-hole, Home Assistant, search index) | Fast random I/O for databases; everything needed for DNS and the dashboard |
| Large HDD (data) | `/data/homeisland`: Wikipedia, maps, documents, 3D models, media, backups | Cheap capacity for replaceable or bulky data |

Core services never touch the data disk, so DNS, the dashboard and monitoring
keep working when the HDD is missing, asleep or broken.

```
/data/homeisland/
├── .homeisland-data   marker file: "the right disk is mounted"
├── kiwix/             *.zim archives
├── maps/              *.pmtiles + assets/ (viewer scripts, fonts, sprites)
├── library/<Category>/ your documents (PDF, EPUB, TXT, MD, HTML)
├── 3d-models/
├── files/             general Samba share
├── media/             movies/, shows/, music/, photos/ (Jellyfin)
└── backups/           configuration backups
```

Every path is configurable in `/etc/homeisland/homeisland.env`
(`HOMEISLAND_DATA_DIR`, `HOMEISLAND_BACKUP_DIR`, `HOMEISLAND_STATE_DIR`).

## Preparing a data disk

HomeIsland never formats, partitions or mounts disks. These are the manual
steps for a new, empty disk. **Formatting erases the disk — double-check the
device name.**

```sh
lsblk -o NAME,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS   # identify the disk, e.g. /dev/sda
sudo wipefs --all /dev/sdX                          # DESTROYS DATA on /dev/sdX
sudo parted /dev/sdX --script mklabel gpt mkpart data ext4 0% 100%
sudo mkfs.ext4 -L homeisland-data /dev/sdX1
```

Mount it permanently by label (device names can change between boots):

```sh
sudo mkdir -p /data
echo 'LABEL=homeisland-data /data ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo mount /data
findmnt /data
```

- `nofail` lets the system boot normally when the disk is missing — essential
  for the "HDD failed, core keeps running" behaviour. `homeisland audit` warns
  if it is absent.
- `noatime` avoids a write for every read.

Then run (or re-run) `sudo ./install.sh`; it creates `/data/homeisland` and the
marker file.

### An existing disk with data

Mount it as above, then either set `HOMEISLAND_DATA_DIR` to a directory on it
or move your files into the layout above. The installer only creates missing
directories and the marker; it does not move or delete anything.

### No separate disk

The installer asks before placing the data directory on the system disk
(`--allow-system-disk` for non-interactive installs). This is fine for trying
things out or for small datasets; `homeisland audit` keeps reminding you.

## How a missing disk is detected

The file `.homeisland-data` in the data directory proves that the right disk
is mounted. Without it:

- modules that need the disk (`kiwix`, `library`, `maps`, `samba`, `jellyfin`)
  are not started — their bind mounts use `create_host_path: false`, so Docker
  can never silently create empty directories on the SSD and fill it up;
- the dashboard shows the data disk as **MISSING**;
- `homeisland test offline` reports those checks as failed.

When the disk comes back, the collector notices within seconds and starts the
modules again. `sudo homeisland apply` does the same manually.

## HDD spindown

Letting an idle HDD spin down saves ~3–5 W and noise, at the cost of a few
seconds of delay on first access and extra start/stop cycles. HomeIsland does
not change disk power settings for you, but avoids waking disks:

- SMART checks use `smartctl -n standby` and report sleeping disks as "standby";
- disk usage uses `statvfs`, which is served from memory;
- the library re-indexes only every `HOMEISLAND_LIBRARY_REINDEX_HOURS` (6 h).

Kiwix, the library, maps and Jellyfin access the disk only when someone uses
them.

To enable spindown after 20 minutes with `hdparm` (works for most SATA disks
and many USB bridges):

```sh
sudo apt install hdparm
sudo hdparm -S 240 /dev/disk/by-label/homeisland-data   # 240 × 5 s = 20 min, test first
```

Persist it in `/etc/hdparm.conf` only after checking the disk actually sleeps
(`sudo hdparm -C /dev/sdX`). Some USB enclosures ignore it; `hd-idle` is an
alternative. Avoid very short timeouts (< 10 min): frequent spin-up cycles wear
the drive more than spinning.

## SMART monitoring

`smartmontools` is installed by the installer. Some USB–SATA bridges need a
device type; check with:

```sh
sudo smartctl --scan
sudo smartctl -d sat -H /dev/sda
```

If autodetection misses the disk, set `HOMEISLAND_SMART_DEVICES` (e.g.
`/dev/sda`). `homeisland disk status` shows health, temperature and warning
attributes (reallocated, pending and uncorrectable sectors).

## Space planning

| Data | Size |
|---|---|
| Container images (core + knowledge + maps) | ~0.8 GB on the SSD |
| Pi-hole, search index, state | < 500 MB typically |
| English Wikipedia with images (`wikipedia_en_all_maxi`) | ~100+ GB |
| English Wikipedia without images (`nopic`) | ~50 GB |
| Czech Wikipedia with images | ~15–20 GB |
| WikiMed (English, medical) | ~2 GB |
| Map of the Czech Republic, full detail | ~1.7 GB |
| Map of Germany | ~7 GB |
| Map viewer assets | ~15 MB |

Sizes of Kiwix archives change over time; check the Kiwix download page.
