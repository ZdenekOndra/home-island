# Offline maps

The `maps` module serves OpenStreetMap vector maps that work in any modern
browser on your LAN without Internet access.

## How it works

- Map data is a single **PMTiles** archive per region: pre-built vector tiles
  from the [Protomaps](https://protomaps.com) daily build of OpenStreetMap.
- The browser renders the tiles with **MapLibre GL JS** using the Protomaps
  basemap style, fonts and icons.
- The server only serves static files and byte ranges (a tiny Caddy
  container). There is no tile server and no rendering on the Raspberry Pi, so
  panning and zooming cost the Pi almost nothing.

Why not a raster tile server such as `tileserver-gl` or a Nominatim/OSRM stack?
They need much more RAM and CPU, and pre-rendered raster tiles for a whole
country need far more storage than vector tiles.

## Setup

```sh
sudo homeisland module enable maps
sudo homeisland maps assets           # viewer scripts, fonts, sprites (~15 MB)
sudo homeisland maps download cz      # Czech Republic, ~1.7 GB, full detail
```

Open <http://maps.home.arpa>.

`maps assets` downloads pinned versions of MapLibre GL JS, PMTiles, the
Protomaps basemap style and its fonts/sprites, verifies their SHA-256
checksums and stores them in `DATA_DIR/maps/assets/`. They live on the data
disk so a rebuilt server finds them again after recovery, even offline.

## Regions

`homeisland maps list` shows available presets:

| Region | Size, full detail (zoom 15) |
|---|---|
| `cz` Czech Republic | 1.7 GB |
| `sk` Slovakia | 0.8 GB |
| `at` Austria | 2.0 GB |
| `hu` Hungary | 0.9 GB |
| `ch` Switzerland | 1.0 GB |
| `pl` Poland | 4.0 GB |
| `de` Germany | 7.2 GB |
| `europe` Europe | 48 GB (1.4 GB with `--maxzoom 10`) |

Sizes were measured with `--dry-run` against the Protomaps build of
2026-09-26 and grow slowly over time. Check before downloading:

```sh
sudo homeisland maps download pl --dry-run
```

Presets are bounding boxes, so they include border areas of neighbouring
countries.

### Custom areas and less detail

```sh
# any bounding box: min_lon,min_lat,max_lon,max_lat
sudo homeisland maps download home --bbox 14.2,49.9,14.8,50.2

# fewer zoom levels: much smaller, still fine for navigation between towns
sudo homeisland maps download europe --maxzoom 10 --name europe-z10
```

Zoom 15 shows individual buildings and footpaths; zoom 12 shows streets in
towns; zoom 10 shows roads between towns.

Downloads use `pmtiles extract` from the pinned `protomaps/go-pmtiles` image and
fetch only the needed parts of the planet file over HTTP range requests.

### Europe on a Raspberry Pi

Serving a full-detail Europe extract (~48 GB) should need no more memory than
a small one, because the server only reads the byte ranges a browser asks for
(this has not been tested on a Pi with a file of that size). The limiting
factor is the initial download. A practical combination is a
full-detail map of your country plus `europe --maxzoom 10`.

## Updating

OpenStreetMap changes constantly, but a map that is a few months old is fine
for an offline fallback. To refresh, download again under the same name; the
file is replaced only after the download completed.

## Using the viewer

- The *Map* selector switches between downloaded archives.
- *Labels* chooses the label language (falls back to local names).
- ◐ switches between light and dark style.
- The URL contains the position (`#zoom/lat/lon`), so you can bookmark places.

There is no address search or routing offline: those need large additional
indexes (Nominatim, OSRM/Valhalla) and far more RAM than this project wants to
spend by default.

## Licences

Map data © OpenStreetMap contributors, ODbL. Protomaps basemap, MapLibre GL JS
and PMTiles are BSD-licensed; the fonts are under the SIL Open Font License.
The viewer shows the required attribution.
