#!/usr/bin/env python3
"""HomeIsland library: offline document browser and full-text search.

Standard library only (plus the `pdftotext` binary for PDFs). Documents are
indexed into a SQLite FTS5 database; the web UI is plain server-rendered HTML
with no external resources.

    library.py serve     run the web server (and the periodic indexer)
    library.py index     index once and exit
    library.py stats     print index statistics as JSON
"""

from __future__ import annotations

import contextlib
import fcntl
import html
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterator, List, Optional, Tuple

INDEX_DB = os.environ.get("LIBRARY_INDEX_DB", "/index/library.db")
LOCK_FILE = os.environ.get("LIBRARY_LOCK_FILE", "/index/.index.lock")
REINDEX_HOURS = float(os.environ.get("LIBRARY_REINDEX_HOURS", "6") or 0)
PORT = int(os.environ.get("LIBRARY_PORT", "8080"))
MAX_TEXT_CHARS = int(os.environ.get("LIBRARY_MAX_TEXT_CHARS", str(4 * 1024 * 1024)))
MAX_FILE_BYTES = int(os.environ.get("LIBRARY_MAX_FILE_BYTES", str(1024 * 1024 * 1024)))
PDF_TIMEOUT = int(os.environ.get("LIBRARY_PDF_TIMEOUT", "300"))
PAGE_SIZE = 20

# name=path:mode;...   mode is "index" (searchable) or "browse" (listing only)
DEFAULT_COLLECTIONS = "library=/data/library:index;3d-models=/data/3d-models:browse"

INDEXED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".xhtml", ".epub"}

COLLECTION_TITLES = {"library": "Library", "3d-models": "3D Models"}


def parse_collections(spec: str) -> Dict[str, Tuple[str, str]]:
    result = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        name, rest = part.split("=", 1)
        path, _, mode = rest.rpartition(":")
        if mode not in ("index", "browse"):
            raise ValueError("collection mode must be index or browse: %r" % part)
        result[name.strip()] = (os.path.realpath(path.strip()), mode)
    return result


COLLECTIONS = parse_collections(os.environ.get("LIBRARY_COLLECTIONS", DEFAULT_COLLECTIONS))


def log(msg: str) -> None:
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title: Optional[str] = None
        self._skip = 0
        self._in_title = False
        self._title_parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            if self._title_parts and not self.title:
                self.title = " ".join("".join(self._title_parts).split())
        elif tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))


def html_to_text(markup: str) -> Tuple[str, Optional[str]]:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    return parser.text(), parser.title


def read_text_file(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_TEXT_CHARS * 2)
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_pdf(path: str) -> str:
    try:
        res = subprocess.run(
            ["pdftotext", "-q", "-enc", "UTF-8", path, "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PDF_TIMEOUT,
        )
    except FileNotFoundError:
        raise RuntimeError("pdftotext is not installed")
    except subprocess.TimeoutExpired:
        raise RuntimeError("pdftotext timed out")
    if res.returncode != 0:
        raise RuntimeError("pdftotext failed (%d)" % res.returncode)
    return res.stdout[: MAX_TEXT_CHARS * 4].decode("utf-8", errors="replace")


def extract_epub(path: str) -> Tuple[str, Optional[str]]:
    parts: List[str] = []
    title = None
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        opf = next((n for n in names if n.endswith(".opf")), None)
        order: List[str] = []
        if opf:
            opf_xml = zf.read(opf).decode("utf-8", errors="replace")
            m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf_xml, re.S | re.I)
            if m:
                title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() or None
            base = os.path.dirname(opf)
            manifest = {
                idm.group(1): idm.group(2)
                for idm in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf_xml)
            }
            for ref in re.finditer(r'<itemref\b[^>]*\bidref="([^"]+)"', opf_xml):
                href = manifest.get(ref.group(1))
                if href:
                    order.append(os.path.normpath(os.path.join(base, urllib.parse.unquote(href))).replace(os.sep, "/"))
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        total = 0
        for name in order:
            if name not in names:
                continue
            text, _ = html_to_text(zf.read(name).decode("utf-8", errors="replace"))
            parts.append(text)
            total += len(text)
            if total > MAX_TEXT_CHARS:
                break
    return "\n".join(parts), title


def pretty_title(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"[_]+", " ", stem)
    return " ".join(stem.split()) or filename


def extract(path: str) -> Tuple[str, str]:
    """Return (title, text) for a supported document."""
    ext = os.path.splitext(path)[1].lower()
    title: Optional[str] = None
    if ext == ".pdf":
        text = extract_pdf(path)
    elif ext == ".epub":
        text, title = extract_epub(path)
    elif ext in (".html", ".htm", ".xhtml"):
        text, title = html_to_text(read_text_file(path))
    else:
        text = read_text_file(path)
        if ext in (".md", ".markdown"):
            m = re.search(r"^#\s+(.+)$", text, re.M)
            if m:
                title = m.group(1).strip()
    return (title or pretty_title(os.path.basename(path)))[:300], text[:MAX_TEXT_CHARS]


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    relpath TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    indexed_at REAL NOT NULL,
    error TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    key UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
"""


def connect(readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        if not os.path.exists(INDEX_DB):
            raise FileNotFoundError(INDEX_DB)
        conn = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(INDEX_DB), uri=True, timeout=10,
                               check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(INDEX_DB), exist_ok=True)
        conn = sqlite3.connect(INDEX_DB, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def walk_documents(name: str, root: str) -> Iterator[Tuple[str, str, os.stat_result]]:
    """Yield (relpath, abspath, stat) for indexable files, skipping hidden entries and symlinks."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() not in INDEXED_EXTENSIONS:
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            yield os.path.relpath(full, root).replace(os.sep, "/"), full, st


@contextlib.contextmanager
def index_lock(blocking: bool = True):
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w") as fh:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fh, flags)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def build_index(verbose: bool = True) -> dict:
    stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "errors": 0}
    with index_lock(blocking=False) as locked:
        if not locked:
            log("indexer already running; skipping")
            return stats
        conn = connect()
        try:
            known = {r["key"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT key, size, mtime FROM files")}
            seen = set()
            for cname, (root, mode) in COLLECTIONS.items():
                if mode != "index":
                    continue
                if not os.path.isdir(root):
                    log("collection %s: %s is missing (data disk not mounted?)" % (cname, root))
                    # Keep the existing index for a temporarily missing disk.
                    seen.update(k for k in known if k.startswith(cname + "/"))
                    continue
                for relpath, full, st in walk_documents(cname, root):
                    key = "%s/%s" % (cname, relpath)
                    seen.add(key)
                    if known.get(key) == (st.st_size, st.st_mtime):
                        stats["unchanged"] += 1
                        continue
                    category = relpath.split("/", 1)[0] if "/" in relpath else ""
                    ext = os.path.splitext(relpath)[1].lower().lstrip(".")
                    title, text, err = pretty_title(os.path.basename(relpath)), "", None
                    if st.st_size > MAX_FILE_BYTES:
                        err = "file too large to index"
                    else:
                        try:
                            title, text = extract(full)
                        except Exception as exc:  # one bad file must not stop indexing
                            err = str(exc)[:200]
                    if err:
                        stats["errors"] += 1
                        if verbose:
                            log("error indexing %s: %s" % (key, err))
                    conn.execute("DELETE FROM docs WHERE key = ?", (key,))
                    conn.execute("INSERT INTO docs (key, title, body) VALUES (?, ?, ?)", (key, title, text))
                    conn.execute(
                        "INSERT OR REPLACE INTO files (key, collection, relpath, category, title, ext, size, mtime,"
                        " indexed_at, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (key, cname, relpath, category, title, ext, st.st_size, st.st_mtime, time.time(), err),
                    )
                    conn.commit()
                    stats["updated" if key in known else "added"] += 1
            for key in set(known) - seen:
                conn.execute("DELETE FROM docs WHERE key = ?", (key,))
                conn.execute("DELETE FROM files WHERE key = ?", (key,))
                stats["removed"] += 1
            conn.execute("INSERT OR REPLACE INTO meta (name, value) VALUES ('last_indexed', ?)", (str(time.time()),))
            conn.commit()
            if stats["added"] or stats["updated"] or stats["removed"]:
                conn.execute("INSERT INTO docs(docs) VALUES ('optimize')")
                conn.commit()
        finally:
            conn.close()
    if verbose:
        log("index complete: %s" % json.dumps(stats, sort_keys=True))
    return stats


def fts_query(text: str) -> Optional[str]:
    """Turn free text into a safe FTS5 query (AND of terms, prefix on the last)."""
    words = re.findall(r"\w+", text, re.UNICODE)
    words = [w for w in words if w][:12]
    if not words:
        return None
    terms = ['"%s"' % w.replace('"', "") for w in words]
    terms[-1] += "*"
    return " AND ".join(terms)


def search(query: str, category: str = "", page: int = 1) -> Tuple[List[dict], int]:
    match = fts_query(query)
    if not match:
        return [], 0
    try:
        conn = connect(readonly=True)
    except (FileNotFoundError, sqlite3.Error):
        return [], 0
    try:
        where = "docs MATCH ?"
        params: List[object] = [match]
        if category:
            where += " AND f.category = ?"
            params.append(category)
        total = conn.execute(
            "SELECT count(*) FROM docs JOIN files f ON f.key = docs.key WHERE " + where, params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT f.key, f.collection, f.relpath, f.category, f.title, f.ext, f.size,"
            " snippet(docs, 2, char(2), char(3), ' … ', 24) AS snip"
            " FROM docs JOIN files f ON f.key = docs.key WHERE " + where +
            " ORDER BY bm25(docs, 0.0, 8.0, 1.0) LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, (max(page, 1) - 1) * PAGE_SIZE],
        ).fetchall()
        return [dict(r) for r in rows], total
    except sqlite3.Error as exc:
        log("search error: %s" % exc)
        return [], 0
    finally:
        conn.close()


def index_stats() -> dict:
    try:
        conn = connect(readonly=True)
    except (FileNotFoundError, sqlite3.Error):
        return {"documents": 0, "errors": 0, "last_indexed": None, "categories": {}}
    try:
        docs = conn.execute("SELECT count(*) FROM files").fetchone()[0]
        errors = conn.execute("SELECT count(*) FROM files WHERE error IS NOT NULL").fetchone()[0]
        row = conn.execute("SELECT value FROM meta WHERE name = 'last_indexed'").fetchone()
        cats = {
            r[0]: r[1]
            for r in conn.execute("SELECT category, count(*) FROM files WHERE collection='library' GROUP BY category")
        }
        return {"documents": docs, "errors": errors, "last_indexed": float(row[0]) if row else None,
                "categories": cats}
    except sqlite3.Error:
        return {"documents": 0, "errors": 0, "last_indexed": None, "categories": {}}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#262d36;--text:#e6edf3;--muted:#8b949e;--accent:#4cc2a4;--mark:#3b3314}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--panel:#fff;--line:#dde1e6;--text:#1c2128;--muted:#5d6670;--accent:#0f7b63;--mark:#fff2b3}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid var(--line);background:var(--panel)}
.wrap{max-width:960px;margin:0 auto;padding:16px}
.top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:.08em;font-size:13px;color:var(--text)}
.brand span{color:var(--muted);font-weight:500;letter-spacing:.02em}
form.search{display:flex;gap:8px;flex:1;min-width:240px}
input[type=search],select{flex:1;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--text);font:inherit}
select{flex:0 0 auto;max-width:40%}
button{padding:10px 16px;border-radius:8px;border:0;background:var(--accent);color:#04120e;font:inherit;font-weight:600;cursor:pointer}
h1{font-size:20px;margin:8px 0 16px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:24px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
.tile{display:block;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--text)}
.tile:hover{border-color:var(--accent);text-decoration:none}
.tile small{display:block;color:var(--muted)}
.result{padding:12px 0;border-bottom:1px solid var(--line)}
.result .meta{color:var(--muted);font-size:13px}
.result p{margin:4px 0 0;overflow-wrap:anywhere}
mark{background:var(--mark);color:inherit;border-radius:2px;padding:0 1px}
table{width:100%;border-collapse:collapse}td{padding:8px 4px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
td.r{text-align:right;color:var(--muted);white-space:nowrap;font-size:13px}
.crumbs{color:var(--muted);margin-bottom:12px;overflow-wrap:anywhere}
.muted{color:var(--muted)}.pager{display:flex;gap:16px;margin:16px 0}
.note{padding:12px 14px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024
    return "%d B" % num


def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def page(title: str, body: str, query: str = "", category: str = "") -> bytes:
    stats = index_stats()
    options = ['<option value="">All categories</option>']
    for cat in sorted(stats["categories"]):
        if cat:
            sel = " selected" if cat == category else ""
            options.append('<option value="%s"%s>%s</option>' % (esc(cat), sel, esc(cat)))
    doc = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"color-scheme\" content=\"dark light\"><link rel=\"icon\" href=\"data:,\">"
        "<title>%s · Library</title><style>%s</style></head><body>"
        "<header><div class=\"wrap top\"><a class=\"brand\" href=\"/\">HOMEISLAND <span>Library</span></a>"
        "<form class=\"search\" action=\"/search\" method=\"get\" role=\"search\">"
        "<input type=\"search\" name=\"q\" value=\"%s\" placeholder=\"Search documents…\" aria-label=\"Search\">"
        "<select name=\"cat\" aria-label=\"Category\">%s</select>"
        "<button type=\"submit\">Search</button></form></div></header>"
        "<main class=\"wrap\">%s</main></body></html>"
    ) % (esc(title), CSS, esc(query), "".join(options), body)
    return doc.encode("utf-8")


def resolve(collection: str, relpath: str) -> Optional[str]:
    """Resolve a path inside a collection; None if it escapes the root."""
    if collection not in COLLECTIONS:
        return None
    root = COLLECTIONS[collection][0]
    target = os.path.realpath(os.path.join(root, relpath.lstrip("/")))
    if target != root and not target.startswith(root + os.sep):
        return None
    parts = os.path.relpath(target, root).split(os.sep)
    if any(p.startswith(".") and p not in (".",) for p in parts):
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeIslandLibrary"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet access log; errors still go to stderr
        pass

    def send_body(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8",
                  extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
                "frame-ancestors 'self'; base-uri 'none'",
            )
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query)
        path = urllib.parse.unquote(url.path)
        try:
            if path == "/healthz":
                return self.send_body(200, b"ok", "text/plain")
            if path == "/":
                return self.home()
            if path == "/search":
                return self.search_page(params)
            if path == "/api/search":
                return self.api_search(params)
            if path == "/api/status":
                return self.send_body(200, json.dumps(index_stats()).encode(), "application/json")
            if path.startswith("/browse/"):
                rest = path[len("/browse/"):]
                collection, _, relpath = rest.partition("/")
                return self.browse(collection, relpath)
            return self.not_found()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def not_found(self):
        self.send_body(404, page("Not found", "<h1>Not found</h1><p><a href=\"/\">Back to the library</a></p>"))

    def home(self):
        stats = index_stats()
        counts = {c: n for c, n in stats["categories"].items() if c}
        root = COLLECTIONS.get("library", ("", ""))[0]
        if root and os.path.isdir(root):
            for entry in os.listdir(root):
                if not entry.startswith(".") and os.path.isdir(os.path.join(root, entry)):
                    counts.setdefault(entry, 0)
        tiles = []
        for cat in sorted(counts, key=str.lower):
            n = counts[cat]
            label = "empty" if n == 0 else "%d document%s" % (n, "" if n == 1 else "s")
            tiles.append('<a class="tile" href="/browse/library/%s/">%s<small>%s</small></a>'
                         % (quote_path(cat), esc(cat), label))
        collections = []
        for name, (croot, mode) in COLLECTIONS.items():
            state = "" if os.path.isdir(croot) else " (missing)"
            collections.append('<a class="tile" href="/browse/%s/">%s<small>%s%s</small></a>'
                               % (quote_path(name), esc(COLLECTION_TITLES.get(name, name)),
                                  "searchable" if mode == "index" else "browse only", state))
        last = stats["last_indexed"]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "never"
        body = "<h1>Offline library</h1>"
        body += '<p class="muted">%d documents indexed · last index run: %s</p>' % (stats["documents"], esc(when))
        if not stats["documents"]:
            body += ('<p class="note">No documents are indexed yet. Copy PDF, TXT, Markdown, HTML or EPUB files '
                     "into the category folders of the library share, then run "
                     "<code>homeisland knowledge index</code> (or wait for the periodic indexer).</p>")
        body += "<h2>Categories</h2><div class=\"grid\">%s</div>" % ("".join(tiles) or '<p class="muted">none</p>')
        body += "<h2>Collections</h2><div class=\"grid\">%s</div>" % "".join(collections)
        self.send_body(200, page("Library", body))

    def search_page(self, params):
        query = (params.get("q") or [""])[0][:200]
        category = (params.get("cat") or [""])[0][:100]
        try:
            pageno = max(1, int((params.get("page") or ["1"])[0]))
        except ValueError:
            pageno = 1
        results, total = search(query, category, pageno)
        out = ['<h1>%d result%s for “%s”</h1>' % (total, "" if total == 1 else "s", esc(query))]
        for r in results:
            snippet = esc(r["snip"] or "").replace("\x02", "<mark>").replace("\x03", "</mark>")
            href = "/browse/%s/%s" % (quote_path(r["collection"]), quote_path(r["relpath"]))
            meta = " · ".join(x for x in (r["category"], r["ext"].upper(), human_size(r["size"])) if x)
            out.append('<div class="result"><a href="%s">%s</a><div class="meta">%s</div><p>%s</p></div>'
                       % (esc(href), esc(r["title"]), esc(meta), snippet))
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if pages > 1:
            base = "/search?q=%s&cat=%s&page=" % (urllib.parse.quote(query), urllib.parse.quote(category))
            nav = []
            if pageno > 1:
                nav.append('<a href="%s%d">← Previous</a>' % (esc(base), pageno - 1))
            nav.append('<span class="muted">Page %d of %d</span>' % (pageno, pages))
            if pageno < pages:
                nav.append('<a href="%s%d">Next →</a>' % (esc(base), pageno + 1))
            out.append('<div class="pager">%s</div>' % "".join(nav))
        self.send_body(200, page("Search", "".join(out), query, category))

    def api_search(self, params):
        query = (params.get("q") or [""])[0][:200]
        results, total = search(query, (params.get("cat") or [""])[0][:100])
        for r in results:
            r["snippet"] = (r.pop("snip") or "").replace("\x02", "").replace("\x03", "")
        self.send_body(200, json.dumps({"query": query, "total": total, "results": results}).encode(),
                       "application/json")

    def browse(self, collection: str, relpath: str):
        target = resolve(collection, relpath)
        if target is None or not os.path.exists(target):
            return self.not_found()
        if os.path.isdir(target):
            if relpath and not relpath.endswith("/"):
                return self.send_body(301, b"", extra={"Location": "/browse/%s/%s/" % (
                    quote_path(collection), quote_path(relpath))})
            return self.listing(collection, relpath, target)
        return self.send_file(target)

    def listing(self, collection: str, relpath: str, target: str):
        crumbs = ['<a href="/">Library</a>', '<a href="/browse/%s/">%s</a>' % (
            quote_path(collection), esc(COLLECTION_TITLES.get(collection, collection)))]
        acc = ""
        for part in [p for p in relpath.split("/") if p]:
            acc += part + "/"
            crumbs.append('<a href="/browse/%s/%s">%s</a>' % (quote_path(collection), quote_path(acc), esc(part)))
        rows = []
        try:
            entries = sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            entries = []
        for e in entries:
            if e.name.startswith(".") or e.is_symlink():
                continue
            href = "/browse/%s/%s%s%s" % (quote_path(collection), quote_path(relpath), quote_path(e.name),
                                           "/" if e.is_dir() else "")
            if e.is_dir():
                rows.append('<tr><td><a href="%s">%s/</a></td><td class="r">folder</td></tr>' % (esc(href), esc(e.name)))
            else:
                try:
                    size = human_size(e.stat().st_size)
                except OSError:
                    size = "?"
                rows.append('<tr><td><a href="%s">%s</a></td><td class="r">%s</td></tr>' % (esc(href), esc(e.name), size))
        body = '<div class="crumbs">%s</div>' % " / ".join(crumbs)
        body += "<table>%s</table>" % ("".join(rows) or '<tr><td class="muted">This folder is empty.</td></tr>')
        self.send_body(200, page(os.path.basename(target.rstrip("/")) or "Library", body))

    def send_file(self, target: str):
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        try:
            fh = open(target, "rb")
        except OSError:
            return self.not_found()
        with fh:
            size = os.fstat(fh.fileno()).st_size
            start, end = 0, size - 1
            status = 200
            rng = self.headers.get("Range", "")
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if m and size:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            # User documents may reference external resources or scripts; keep them sandboxed and offline.
            self.send_header("Content-Security-Policy",
                             "sandbox; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                             "media-src 'self'")
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if self.command == "HEAD":
                return
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def periodic_indexer() -> None:
    while True:
        try:
            build_index(verbose=True)
        except Exception as exc:
            log("indexer error: %s" % exc)
        if REINDEX_HOURS <= 0:
            return
        time.sleep(REINDEX_HOURS * 3600)


def serve() -> None:
    try:
        connect().close()
    except sqlite3.Error as exc:
        log("cannot open index %s: %s" % (INDEX_DB, exc))
    threading.Thread(target=periodic_indexer, name="indexer", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    log("library listening on :%d" % PORT)
    server.serve_forever()


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "serve":
        serve()
    elif cmd == "index":
        with index_lock(blocking=True):
            pass  # wait for a running periodic index to finish
        build_index(verbose=True)
    elif cmd == "stats":
        print(json.dumps(index_stats(), indent=2, sort_keys=True))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
