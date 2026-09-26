# Offline knowledge

Two modules provide offline knowledge:

- **kiwix** — serves ZIM archives: Wikipedia, Wiktionary, Wikibooks, Wikivoyage,
  WikiMed, Stack Exchange sites, Project Gutenberg, iFixit and more.
- **library** — your own documents (PDF, EPUB, TXT, Markdown, HTML), organised
  in categories, with full-text search.

```sh
sudo homeisland module enable kiwix
sudo homeisland module enable library
```

Nothing is downloaded automatically. Download what you need while online.

## Kiwix (ZIM archives)

### Choosing archives

Browse <https://library.kiwix.org> or the directory listing at
<https://download.kiwix.org/zim/>. File names encode the content:

```
wikipedia_cs_all_maxi_2026-08.zim
│         │  │   │    └ date
│         │  │   └ maxi = with images, nopic = no images, mini = intro only
│         │  └ all articles (or a topic selection, e.g. "medicine", "top")
│         └ language
└ project
```

Suggestions for an offline home:

| Archive | Approx. size |
|---|---|
| Wikipedia in your language (`…_all_maxi`) | 5–100+ GB |
| English Wikipedia without images (`wikipedia_en_all_nopic`) | ~50 GB |
| WikiMed medical encyclopedia (`mdwiki_en_all_maxi`) | ~2 GB |
| Wiktionary (`wiktionary_<lang>_all_maxi`) | 1–10 GB |
| Wikivoyage (`wikivoyage_<lang>_all_maxi`) | 0.1–1 GB |
| iFixit repair guides (`ifixit_en_all`) | ~3 GB |

### Downloading

```sh
sudo homeisland knowledge download https://download.kiwix.org/zim/wikipedia/<file>.zim
```

This downloads into `DATA_DIR/kiwix/` with resume support (re-run the same
command after an interruption), verifies the SHA-256 checksum that Kiwix
publishes next to each file, and adds the archive to the Kiwix library.
Large archives take hours; consider a `tmux` or `screen` session.

Alternatively, download with any tool (a BitTorrent client is often faster and
also verifies the data), copy the `.zim` files to `DATA_DIR/kiwix/` — for
example through the `kiwix` Samba share — and run:

```sh
sudo homeisland knowledge update
```

kiwix-serve reloads its library automatically; no restart is needed.

### Updating and removing

Download the newer archive, delete the old file, run
`sudo homeisland knowledge update`. `homeisland knowledge list` shows which
files are served.

## Document library

### Layout

```
DATA_DIR/library/
├── Emergency/        ├── Plumbing/
├── Medical/          ├── Construction/
├── Electrical/       ├── Food/
├── Electronics/      ├── Radio/
├── Networking/       ├── Maps/
├── Linux/            ├── Home Documentation/
├── Raspberry Pi/     ├── Manuals/
├── 3D Printing/      ├── Books/
├── Vehicles/         └── (any folder you create)
├── Generator/
└── Heating/
```

The top-level folder is the category. Subfolders are fine. Create new
categories by creating folders.

Supported for search: `.pdf`, `.epub`, `.txt`, `.md`, `.markdown`, `.rst`,
`.html`, `.htm`, `.xhtml`. Other files (images, STL models, ZIP archives) can be
stored and browsed but are not searched. Scanned PDFs without a text layer
cannot be searched; run OCR first (e.g. `ocrmypdf` on another computer).

### Adding documents

- via the Samba share `library` (module `samba`), or
- by copying files to `DATA_DIR/library/<Category>/` on the server.

The library indexes new and changed files every 6 hours
(`HOMEISLAND_LIBRARY_REINDEX_HOURS`). To index immediately:

```sh
homeisland knowledge index
```

### Searching

Open <http://library.home.arpa>. Search is case- and diacritics-insensitive
(`vymenik` finds `výměník`), matches word prefixes, and can be limited to a
category. Results show a highlighted snippet.

There is also a JSON API for scripts: `GET /api/search?q=...&cat=...`.

### How it works

A small Python service extracts text (with `pdftotext` for PDFs) and stores it
in a SQLite FTS5 index in `STATE_DIR/library/library.db` on the SSD. Indexing is
incremental: unchanged files are skipped. If the data disk is missing during a
run, the existing index is kept. Documents are served read-only; HTML
documents are sandboxed so they cannot run scripts or load external content.

### 3D models

`DATA_DIR/3d-models/` can be browsed and downloaded at
<http://library.home.arpa/browse/3d-models/> and written through the Samba
share `3d-models`. It is not full-text indexed.

## Getting documents worth having offline

- Manuals for your appliances, boiler, generator, car, UPS, router (PDFs from
  manufacturers' websites).
- Your own home documentation: wiring plans, where the main valves are, network
  diagram, insurance papers (keep sensitive documents on encrypted storage or
  out of the library — it is readable by anyone on your LAN).
- First aid and emergency preparedness guides from your national authorities.
- Raspberry Pi, Linux and networking documentation.

Respect the licences of the documents you store.
