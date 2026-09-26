// Offline map viewer: MapLibre GL + PMTiles + Protomaps basemap style.
// All scripts, fonts, sprites and tiles are served by this HomeIsland server.

const $ = (id) => document.getElementById(id);
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

function message(title, html) {
  const box = $("message");
  box.innerHTML = `<h2>${title}</h2>${html}`;
  box.hidden = false;
}

async function listArchives() {
  const res = await fetch("/tiles/", { headers: { Accept: "application/json" } });
  if (!res.ok) return [];
  const items = await res.json();
  return items
    .filter((f) => !f.is_dir && f.name.endsWith(".pmtiles"))
    .map((f) => f.name)
    .sort();
}

function preferredFlavor() {
  const saved = store.get("maps.flavor", "");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function preferredLang() {
  const saved = store.get("maps.lang", "");
  const options = [...$("lang").options].map((o) => o.value);
  if (options.includes(saved)) return saved;
  const nav = (navigator.language || "en").slice(0, 2);
  return options.includes(nav) ? nav : "en";
}

function buildStyle(archive, flavor, lang) {
  const origin = window.location.origin;
  return {
    version: 8,
    glyphs: `${origin}/assets/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/assets/sprites/v4/${flavor}`,
    sources: {
      protomaps: {
        type: "vector",
        url: `pmtiles://${origin}/tiles/${encodeURIComponent(archive)}`,
        attribution: "© OpenStreetMap contributors · Protomaps",
      },
    },
    layers: window.basemaps.layers("protomaps", window.basemaps.namedFlavor(flavor), { lang }),
  };
}

async function main() {
  let maplibregl;
  try {
    maplibregl = await import("/assets/vendor/maplibre-gl.mjs");
  } catch {
    maplibregl = null;
  }
  if (!maplibregl || !window.pmtiles || !window.basemaps) {
    message("Map viewer assets are missing",
      "<p>Run <code>sudo homeisland maps assets</code> on the HomeIsland server while it is online. " +
      "The assets are stored on the data disk and work offline afterwards.</p>");
    return;
  }

  let archives = [];
  try { archives = await listArchives(); } catch { archives = []; }
  if (!archives.length) {
    message("No maps downloaded yet",
      "<p>Download a region while online, for example <code>sudo homeisland maps download cz</code>, " +
      "or copy a <code>.pmtiles</code> file into the maps folder of the data disk.</p>");
    return;
  }

  const select = $("archive");
  for (const name of archives) select.add(new Option(name.replace(/\.pmtiles$/, ""), name));
  const savedArchive = store.get("maps.archive", "");
  select.value = archives.includes(savedArchive) ? savedArchive : archives[0];
  $("lang").value = preferredLang();
  let flavor = preferredFlavor();

  // Read before creating the map: MapLibre writes its own hash immediately.
  const hadHash = Boolean(window.location.hash);
  const protocol = new window.pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  const map = new maplibregl.Map({
    container: "map",
    style: buildStyle(select.value, flavor, $("lang").value),
    hash: true,
    attributionControl: { compact: true },
  });
  map.on("error", (e) => {
    const text = String((e && e.error && e.error.message) || e.error || "unknown error");
    message("Map error", `<p>${text.replace(/[<>&]/g, "")}</p>`);
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

  async function fitArchive(name) {
    const archive = new window.pmtiles.PMTiles(`${window.location.origin}/tiles/${encodeURIComponent(name)}`);
    protocol.add(archive);
    const h = await archive.getHeader();
    map.fitBounds([[h.minLon, h.minLat], [h.maxLon, h.maxLat]], { padding: 20, animate: false });
  }

  const restyle = () => map.setStyle(buildStyle(select.value, flavor, $("lang").value));

  if (!hadHash) {
    fitArchive(select.value).catch(() => {});
  }
  select.addEventListener("change", () => {
    store.set("maps.archive", select.value);
    restyle();
    fitArchive(select.value).catch(() => {});
  });
  $("lang").addEventListener("change", () => { store.set("maps.lang", $("lang").value); restyle(); });
  $("theme").addEventListener("click", () => {
    flavor = flavor === "dark" ? "light" : "dark";
    store.set("maps.flavor", flavor);
    restyle();
  });
}

main();
