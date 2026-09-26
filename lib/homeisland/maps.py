"""Offline maps: PMTiles archives plus the browser viewer assets.

Maps are pre-built vector tiles (Protomaps basemap, OpenStreetMap data) in a
single PMTiles file per region. The browser renders them with MapLibre GL, so
the server only serves static files with HTTP range requests.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import urllib.request
from typing import Dict, Optional, Tuple

from .config import Config
from .util import HomeIslandError, human_bytes, info, run, table, warn

PMTILES_IMAGE = "protomaps/go-pmtiles:v1.31.2"

# Pinned viewer assets. Update versions and checksums together.
ASSETS = [
    {
        "name": "maplibre-gl",
        "url": "https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-6.11.2.tgz",
        "sha256": "62a21a673efe2eb399f781683422b5b2e88efe20d7574c22b655b896a1064012",
        "files": {
            "package/dist/maplibre-gl.mjs": "vendor/maplibre-gl.mjs",
            "package/dist/maplibre-gl-shared.mjs": "vendor/maplibre-gl-shared.mjs",
            "package/dist/maplibre-gl-worker.mjs": "vendor/maplibre-gl-worker.mjs",
            "package/dist/maplibre-gl.css": "vendor/maplibre-gl.css",
            "package/LICENSE.txt": "vendor/LICENSE.maplibre-gl.txt",
        },
    },
    {
        "name": "pmtiles",
        "url": "https://registry.npmjs.org/pmtiles/-/pmtiles-4.5.0.tgz",
        "sha256": "23ae7c575578ad24cd579377d69c46550631da219e6f179997ec2cf3b8c937e5",
        "files": {
            "package/dist/pmtiles.js": "vendor/pmtiles.js",
        },
    },
    {
        "name": "basemaps",
        "url": "https://registry.npmjs.org/@protomaps/basemaps/-/basemaps-5.7.2.tgz",
        "sha256": "2d5d41b29cdd2364f7092ad439bd9d170b4f38cbec8858cefbe84d0f98125ce6",
        "files": {
            "package/dist/basemaps.js": "vendor/basemaps.js",
        },
    },
    {
        "name": "basemaps-assets",
        "url": "https://codeload.github.com/protomaps/basemaps-assets/tar.gz/028c18f713baecad011301ff7a69acc39bcc2ae7",
        "sha256": "57e40e8c512bd8042d0a3a251f19d0d1c8523ad963c666c3c6643bada4dc92d0",
        "strip": "basemaps-assets-028c18f713baecad011301ff7a69acc39bcc2ae7/",
        "dirs": {"fonts/": "fonts/", "sprites/v4/": "sprites/v4/"},
    },
]

ASSETS_VERSION = "maplibre-6.11.2+pmtiles-4.5.0+basemaps-5.7.2+assets-028c18f"

# min_lon, min_lat, max_lon, max_lat. Sizes: full detail (zoom 15), Protomaps build of 2026-09-26.
REGIONS: Dict[str, Tuple[str, str, str]] = {
    "cz": ("Czech Republic", "12.09,48.55,18.86,51.06", "1.7 GB"),
    "sk": ("Slovakia", "16.83,47.73,22.57,49.61", "0.8 GB"),
    "at": ("Austria", "9.53,46.37,17.16,49.02", "2.0 GB"),
    "pl": ("Poland", "14.12,49.00,24.15,54.84", "4.0 GB"),
    "hu": ("Hungary", "16.11,45.74,22.90,48.59", "0.9 GB"),
    "ch": ("Switzerland", "5.96,45.82,10.49,47.81", "1.0 GB"),
    "de": ("Germany", "5.87,47.27,15.04,55.06", "7.2 GB"),
    "europe": ("Europe (large!)", "-25.0,34.0,45.0,72.0", "48 GB (1.4 GB with --maxzoom 10)"),
}


def maps_dir(cfg: Config) -> str:
    return cfg.data_path("maps")


def assets_dir(cfg: Config) -> str:
    return cfg.data_path("maps", "assets")


def _require_data(cfg: Config) -> None:
    if not os.path.exists(cfg.data_marker):
        raise HomeIslandError("data disk not available at %s" % cfg.data_dir)


def _fetch(url: str, sha256: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "homeisland"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise HomeIslandError("checksum mismatch for %s (expected %s, got %s)" % (url, sha256, digest))
    return data


def _safe_join(base: str, rel: str) -> str:
    target = os.path.normpath(os.path.join(base, rel))
    if not target.startswith(os.path.normpath(base) + os.sep):
        raise HomeIslandError("refusing to write outside %s: %s" % (base, rel))
    return target


def install_assets(cfg: Config, force: bool = False) -> None:
    """Download the pinned map viewer assets to DATA_DIR/maps/assets (checksums verified)."""
    _require_data(cfg)
    dest = assets_dir(cfg)
    stamp = os.path.join(dest, "VERSION")
    if not force and os.path.exists(stamp) and open(stamp).read().strip() == ASSETS_VERSION:
        info("Map viewer assets are up to date (%s)." % ASSETS_VERSION)
        return
    tmp = dest + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for asset in ASSETS:
        info("Fetching %s ..." % asset["name"])
        data = _fetch(asset["url"], asset["sha256"])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                if "files" in asset and name in asset["files"]:
                    out = _safe_join(tmp, asset["files"][name])
                elif "dirs" in asset and name.startswith(asset.get("strip", "")):
                    rel = name[len(asset.get("strip", "")):]
                    match = next((d for d in asset["dirs"] if rel.startswith(d)), None)
                    if match is None:
                        continue
                    out = _safe_join(tmp, asset["dirs"][match] + rel[len(match):])
                else:
                    continue
                os.makedirs(os.path.dirname(out), exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                os.chmod(out, 0o644)
    for dirpath, dirnames, _ in os.walk(tmp):
        for d in dirnames:
            os.chmod(os.path.join(dirpath, d), 0o755)
    os.chmod(tmp, 0o755)
    with open(os.path.join(tmp, "VERSION"), "w") as fh:
        fh.write(ASSETS_VERSION + "\n")
    old = dest + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(dest):
        os.replace(dest, old)
    os.replace(tmp, dest)
    shutil.rmtree(old, ignore_errors=True)
    info("Map viewer assets installed in %s." % dest)


def list_maps(cfg: Config) -> None:
    directory = maps_dir(cfg)
    try:
        files = sorted(f for f in os.listdir(directory) if f.endswith(".pmtiles"))
    except OSError:
        files = []
    rows = [(f, human_bytes(os.path.getsize(os.path.join(directory, f)))) for f in files]
    info("Map archives in %s:" % directory)
    info(table(rows, ("FILE", "SIZE")) if rows else "  none")
    stamp = os.path.join(assets_dir(cfg), "VERSION")
    info("\nViewer assets: %s" % (open(stamp).read().strip() if os.path.exists(stamp) else
                                  "missing (run: homeisland maps assets)"))
    info("\nRegions available for download:")
    info(table([(k, v[0], v[2]) for k, v in REGIONS.items()], ("REGION", "NAME", "APPROX. SIZE")))


def latest_build(cfg: Config) -> str:
    source = cfg.get("HOMEISLAND_MAPS_SOURCE").rstrip("/")
    req = urllib.request.Request("https://build-metadata.protomaps.dev/builds.json",
                                 headers={"User-Agent": "homeisland"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            builds = json.load(resp)
        key = sorted(b["key"] for b in builds if b.get("key", "").endswith(".pmtiles"))[-1]
    except (OSError, ValueError, IndexError, KeyError) as exc:
        raise HomeIslandError("cannot determine the latest map build (offline?): %s" % exc)
    return "%s/%s" % (source, key)


def download(cfg: Config, region: str, bbox: Optional[str] = None, maxzoom: Optional[int] = None,
             source: Optional[str] = None, dry_run: bool = False, name: Optional[str] = None) -> None:
    _require_data(cfg)
    if region in REGIONS:
        title, region_bbox, size = REGIONS[region]
        bbox = bbox or region_bbox
    elif bbox:
        title, size = region, "unknown"
    else:
        raise HomeIslandError("unknown region %r; use one of %s or pass --bbox" % (region, ", ".join(REGIONS)))
    parts = bbox.split(",")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 4 or not (values[0] < values[2] and values[1] < values[3]):
        raise HomeIslandError("bbox must be min_lon,min_lat,max_lon,max_lat")
    filename = (name or region) + ".pmtiles"
    if "/" in filename or filename.startswith("."):
        raise HomeIslandError("invalid output name")
    src = source or latest_build(cfg)
    out_dir = maps_dir(cfg)
    os.makedirs(out_dir, exist_ok=True)
    part = filename + ".part"
    cmd = ["docker", "run", "--rm", "-v", "%s:/out" % out_dir, PMTILES_IMAGE, "extract", src, "/out/" + part,
           "--bbox=" + bbox]
    if maxzoom is not None:
        cmd.append("--maxzoom=%d" % maxzoom)
    if dry_run:
        cmd.append("--dry-run")
    detail = "full detail: ~%s" % size if maxzoom is None else "up to zoom %d" % maxzoom
    info("Extracting %s (%s, %s) from %s" % (title, bbox, detail, src))
    run(cmd)
    if dry_run:
        return
    final = os.path.join(out_dir, filename)
    os.chmod(os.path.join(out_dir, part), 0o644)
    os.replace(os.path.join(out_dir, part), final)
    info("Saved %s (%s). Open http://%s/ to view it." % (final, human_bytes(os.path.getsize(final)),
                                                         cfg.fqdn("maps")))
    if not os.path.exists(os.path.join(assets_dir(cfg), "VERSION")):
        warn("viewer assets are missing; run: homeisland maps assets")
