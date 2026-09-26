// HomeIsland dashboard. Plain JavaScript, no dependencies, no external requests.
// Data sources (both served locally by the proxy):
//   /api/services.json  generated from the enabled modules
//   /api/status.json    written every few seconds by homeisland-collector

(function () {
  "use strict";

  var STATUS_INTERVAL = 10000;
  var SERVICES_INTERVAL = 120000;
  var FETCH_TIMEOUT = 5000;
  var STALE_AFTER = 90;

  var page = document.body.getAttribute("data-page");
  var services = null;
  var status = null;

  // -- helpers ------------------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "text") node.textContent = attrs[k];
        else if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "assets/icons.svg#" + name);
    svg.appendChild(use);
    return svg;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function fetchJSON(url) {
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, FETCH_TIMEOUT);
    return fetch(url, { cache: "no-store", signal: ctrl ? ctrl.signal : undefined })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        var date = Date.parse(res.headers.get("Date") || "");
        return res.json().then(function (data) {
          // Use the server clock: the client clock may be wrong during long offline periods.
          return { data: data, serverNow: isNaN(date) ? Date.now() / 1000 : date / 1000 };
        });
      })
      .finally(function () { clearTimeout(timer); });
  }

  function bytes(n) {
    if (n === null || n === undefined) return "–";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(n >= 100 ? 0 : 1)) + " " + units[i];
  }

  function duration(s) {
    if (s === null || s === undefined) return "–";
    s = Math.floor(s);
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return d + "d " + String(h).padStart(2, "0") + "h";
    if (h) return h + "h " + String(m).padStart(2, "0") + "m";
    return m + "m";
  }

  function ago(seconds) {
    if (seconds < 60) return Math.max(0, Math.round(seconds)) + " s ago";
    if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
    return duration(seconds) + " ago";
  }

  function num(v, digits) { return v === null || v === undefined ? "–" : Number(v).toFixed(digits || 0); }

  function level(pct, warn, bad) { return pct >= bad ? "bad" : pct >= warn ? "warn" : "ok"; }

  // -- summary --------------------------------------------------------------------

  function servicesSummary(st) {
    var states = (st && st.modules) || {};
    var names = Object.keys(states);
    var down = names.filter(function (n) { return states[n] === "down"; });
    if (!names.length) return { text: "–", cls: "" };
    if (down.length) return { text: "DEGRADED (" + down.length + " down)", cls: "state-warn" };
    return { text: "OPERATIONAL", cls: "state-ok" };
  }

  function renderHero(st, age) {
    var hero = $("hero");
    if (!hero) return;
    var wan = (st && st.internet) || {};
    var lan = (st && st.lan) || {};
    var svc = servicesSummary(st);
    var state = "unknown", value = "UNKNOWN", sub = "";

    if (wan.state === "online") {
      state = "online"; value = "ONLINE";
      sub = "Reachable" + (wan.latency_ms ? " · " + Math.round(wan.latency_ms) + " ms" : "") +
        " · " + wan.reachable + "/" + wan.total + " probes answered";
    } else if (wan.state === "offline") {
      state = "offline"; value = "OFFLINE";
      sub = svc.cls === "state-ok"
        ? "Island mode — local services keep working"
        : "Island mode — some local services need attention";
    } else if (wan.state === "disabled") {
      value = "NOT CHECKED"; sub = "Internet check disabled in configuration";
    } else {
      sub = "Waiting for the first check";
    }
    if (age > STALE_AFTER) state = "degraded";

    hero.setAttribute("data-state", state);
    $("wan-state").textContent = value;
    $("wan-sub").textContent = sub;

    var lanText = { operational: "OPERATIONAL", degraded: "DEGRADED", down: "DOWN" }[lan.state] || "–";
    var lanCls = { operational: "state-ok", degraded: "state-warn", down: "state-bad" }[lan.state] || "";
    $("lan-state").textContent = lanText;
    $("lan-state").className = lanCls;
    $("svc-state").textContent = svc.text;
    $("svc-state").className = svc.cls;
    $("uptime").textContent = duration(st && st.system && st.system.uptime_s);
    if (st && st.hostname) $("hostname").textContent = st.hostname;
  }

  function renderStale(age, failed) {
    var box = $("stale");
    if (failed) {
      box.textContent = "Cannot load monitoring data. The dashboard still works; check the homeisland-collector service on the server.";
      box.hidden = false;
    } else if (age > STALE_AFTER) {
      box.textContent = "Monitoring data was last updated " + ago(age) +
        ". Check the collector on the server: systemctl status homeisland-collector";
      box.hidden = false;
    } else {
      box.hidden = true;
    }
  }

  // -- home page ------------------------------------------------------------------------

  function renderServices() {
    var root = $("services");
    if (!root || !services) return;
    clear(root);
    var states = (status && status.modules) || {};
    var links = services.links || [];
    if (!links.length) {
      root.appendChild(el("p", { "class": "muted", text: "No services enabled." }));
      return;
    }
    links.forEach(function (link) {
      var st = states[link.module] || "unknown";
      var label = { up: "running", down: "not responding", nodata: "no data yet", unknown: "" }[st] || "";
      var card = el("a", { "class": "card", href: link.url, title: label ? link.title + ": " + label : link.title }, [
        el("span", { "class": "dot", "data-state": st, "aria-label": label || null }),
        el("span", { "class": "glyph" }, [icon(link.icon || "box")]),
        el("span", { "class": "card-body" }, [
          el("div", { "class": "card-title", text: link.title }),
          el("div", { "class": "card-desc", text: link.description || "" })
        ])
      ]);
      root.appendChild(card);
    });
  }

  function meter(opts) {
    var bar = null;
    if (opts.pct !== undefined && opts.pct !== null) {
      var fill = el("span");
      fill.style.width = Math.max(0, Math.min(100, opts.pct)) + "%";
      bar = el("div", { "class": "bar", "data-level": opts.level || "ok" }, [fill]);
    }
    return el("div", { "class": "meter" }, [
      el("div", { "class": "meter-head" }, [icon(opts.icon), opts.label]),
      el("div", { "class": "meter-value" }, [opts.value, opts.unit ? el("small", { text: opts.unit }) : null]),
      bar,
      opts.note ? el("div", { "class": "meter-note", text: opts.note }) : null
    ]);
  }

  function renderResources() {
    var root = $("resources");
    if (!root || !status) return;
    clear(root);
    var sys = status.system || {};
    var mem = sys.memory || {};
    var cpu = sys.cpu_percent;
    root.appendChild(meter({
      icon: "gauge", label: "CPU", value: num(cpu), unit: "%", pct: cpu, level: level(cpu || 0, 70, 90),
      note: sys.load ? "load " + sys.load.map(function (l) { return l.toFixed(2); }).join(" · ") : ""
    }));
    root.appendChild(meter({
      icon: "gauge", label: "Memory", value: mem.used ? bytes(mem.used) : "–",
      unit: mem.total ? "/ " + bytes(mem.total) : "", pct: mem.percent, level: level(mem.percent || 0, 80, 92)
    }));
    if (sys.cpu_temp_c !== null && sys.cpu_temp_c !== undefined) {
      root.appendChild(meter({
        icon: "thermo", label: "CPU temperature", value: num(sys.cpu_temp_c), unit: "°C",
        pct: sys.cpu_temp_c / 85 * 100, level: level(sys.cpu_temp_c, 70, 80)
      }));
    }
    (status.disks || []).forEach(function (d) {
      if (d.id === "data" && !d.present) {
        root.appendChild(meter({ icon: "disk", label: d.label, value: "MISSING", note: "Not mounted at " + d.path }));
        return;
      }
      root.appendChild(meter({
        icon: "disk", label: d.label, value: num(d.percent), unit: "%", pct: d.percent,
        level: level(d.percent || 0, 80, 92), note: d.free !== undefined ? bytes(d.free) + " free of " + bytes(d.total) : ""
      }));
    });
    var smart = status.smart || [];
    if (smart.length) {
      var temps = smart.filter(function (s) { return s.temperature_c !== undefined; });
      var hot = temps.length ? Math.max.apply(null, temps.map(function (s) { return s.temperature_c; })) : null;
      var failed = smart.filter(function (s) { return s.state === "failed"; }).length;
      var sleeping = smart.filter(function (s) { return s.standby || s.state === "standby"; }).length;
      root.appendChild(meter({
        icon: "thermo", label: "HDD", value: hot !== null ? num(hot) : (sleeping ? "standby" : "–"),
        unit: hot !== null ? "°C" : "", pct: hot !== null ? hot / 60 * 100 : null, level: level(hot || 0, 45, 55),
        note: failed ? "SMART: " + failed + " disk(s) FAILING" : "SMART: " + (smart.length - sleeping) + " checked" +
          (sleeping ? ", " + sleeping + " asleep" : "")
      }));
    }
  }

  function renderEnvironment() {
    var root = $("environment");
    var section = $("env-section");
    if (!root || !status) return;
    clear(root);
    var any = false;
    (status.sensors || []).forEach(function (s) {
      any = true;
      if (!s.ok) {
        root.appendChild(meter({ icon: "thermo", label: s.label, value: "–", note: "Sensor error: " + s.error }));
        return;
      }
      var v = s.values || {};
      if (v.temperature_c !== undefined) root.appendChild(meter({ icon: "thermo", label: s.label + " temperature", value: num(v.temperature_c, 1), unit: "°C" }));
      if (v.humidity_pct !== undefined) root.appendChild(meter({ icon: "drop", label: s.label + " humidity", value: num(v.humidity_pct), unit: "%", pct: v.humidity_pct, level: level(v.humidity_pct, 70, 85) }));
      if (v.pressure_hpa !== undefined) root.appendChild(meter({ icon: "gauge", label: "Pressure", value: num(v.pressure_hpa), unit: "hPa" }));
    });
    if (status.ups) {
      any = true;
      var u = status.ups;
      root.appendChild(meter(u.ok ? {
        icon: "battery", label: "UPS" + (u.on_battery ? " — ON BATTERY" : ""), value: num(u.charge_pct), unit: "%",
        pct: u.charge_pct, level: u.on_battery ? "warn" : "ok",
        note: (u.runtime_s ? duration(u.runtime_s) + " runtime · " : "") + (u.status || "")
      } : { icon: "battery", label: "UPS", value: "–", note: u.error }));
    }
    if (status.fan) {
      any = true;
      var f = status.fan;
      root.appendChild(meter({ icon: "gauge", label: "Fan", value: num(f.duty_pct), unit: "%", pct: f.duty_pct,
        note: f.ok ? "source: " + f.source : "error: " + f.error }));
    }
    section.hidden = !any;
  }

  // -- status page ------------------------------------------------------------------------

  function pill(text, cls) { return el("span", { "class": "pill " + (cls || ""), text: text }); }

  function rows(tableId, list) {
    var table = $(tableId);
    if (!table) return;
    clear(table);
    if (!list.length) list = [["–", "no data"]];
    list.forEach(function (r) {
      table.appendChild(el("tr", null, [
        el("td", null, [r[0]]),
        el("td", null, Array.isArray(r[1]) ? r[1] : [r[1] === null || r[1] === undefined ? "–" : r[1]])
      ]));
    });
  }

  var CHECK_CLS = { PASS: "state-ok", FAIL: "state-bad", NODATA: "state-warn" };

  function renderStatusPage() {
    if (!status) return;
    var st = status;
    rows("checks", (st.checks || []).map(function (c) {
      return [c.name, [pill(c.state, CHECK_CLS[c.state]), " ", el("span", { "class": "detail", text: c.detail })]];
    }));
    rows("containers", st.containers === null ? [["Docker", "not reachable by the collector"]] :
      (st.containers || []).map(function (c) {
        var cls = c.state === "running" ? (c.health === "unhealthy" ? "state-warn" : "state-ok") : "state-bad";
        return [c.service || c.name, [pill(c.state + (c.health ? " · " + c.health : ""), cls), " ",
          el("span", { "class": "detail", text: c.status })]];
      }));
    var sys = st.system || {}, mem = sys.memory || {}, time = st.time || {};
    rows("system", [
      ["Host", st.hostname],
      ["Architecture", sys.arch + " · " + (sys.cpu_count || "?") + " cores"],
      ["Kernel", sys.kernel],
      ["Uptime", duration(sys.uptime_s)],
      ["CPU", num(sys.cpu_percent) + " % · load " + (sys.load || []).join(" / ")],
      ["Memory", bytes(mem.used) + " / " + bytes(mem.total) + (mem.swap_used ? " · swap " + bytes(mem.swap_used) : "")],
      ["CPU temperature", sys.cpu_temp_c !== null && sys.cpu_temp_c !== undefined ? num(sys.cpu_temp_c, 1) + " °C" : "not available"],
      ["Clock", time.synchronized === true ? pill("NTP synchronized", "state-ok") :
        time.synchronized === false ? pill("not synchronized", "state-warn") : "unknown"],
      ["Hardware clock (RTC)", time.rtc ? "present" : "none — time may drift during long WAN outages"],
      ["HomeIsland", st.version]
    ]);
    var wan = st.internet || {}, lan = st.lan || {};
    rows("network", [
      ["Internet", [pill((wan.state || "unknown").toUpperCase(), wan.state === "online" ? "state-ok" : "state-island"),
        " ", el("span", { "class": "detail", text: wan.total ? wan.reachable + "/" + wan.total + " probes" : "" })]],
      ["Local network", [pill((lan.state || "unknown").toUpperCase(),
        lan.state === "operational" ? "state-ok" : lan.state === "down" ? "state-bad" : "state-warn")]],
      ["Addresses", (lan.addresses || []).join(", ")],
      ["Gateway", lan.gateway ? lan.gateway + (lan.gateway_reachable === true ? " (reachable)" :
        lan.gateway_reachable === false ? " (NOT reachable)" : "") : "none"],
      ["Local DNS", lan.local_dns ? pill("answering", "state-ok") : pill("failing: " + (lan.local_dns_rcode || "?"), "state-bad")]
    ]);
    var storage = (st.disks || []).map(function (d) {
      if (d.id === "data" && !d.present) return [d.label, pill("MISSING at " + d.path, "state-bad")];
      return [d.label, num(d.percent) + " % used · " + bytes(d.free) + " free" +
        (d.id === "data" && !d.separate_device ? " · on the system disk!" : "")];
    });
    (st.smart || []).forEach(function (s) {
      var cls = s.state === "passed" ? "state-ok" : s.state === "failed" ? "state-bad" : "state-warn";
      storage.push([s.device + (s.model ? " (" + s.model + ")" : ""), [pill("SMART " + s.state + (s.standby ? " · asleep" : ""), cls),
        " ", el("span", { "class": "detail", text: (s.temperature_c !== undefined ? s.temperature_c + " °C " : "") +
          (s.warnings ? s.warnings.join(", ") : s.detail || "") })]]);
    });
    rows("storage", storage);
    var hw = [];
    (st.sensors || []).forEach(function (s) {
      var v = s.values || {};
      hw.push([s.label + " (" + s.type + ")", s.ok ? num(v.temperature_c, 1) + " °C · " + num(v.humidity_pct) + " % · " +
        num(v.pressure_hpa) + " hPa" : pill("error: " + s.error, "state-bad")]);
    });
    if (st.ups) hw.push(["UPS", st.ups.ok ? st.ups.status + " · " + num(st.ups.charge_pct) + " % · load " + num(st.ups.load_pct) + " %" : st.ups.error]);
    if (st.fan) hw.push(["Fan", num(st.fan.duty_pct) + " % " + (st.fan.ok ? "" : "(" + st.fan.error + ")")]);
    if (!hw.length) hw.push(["Optional hardware", "none enabled (see docs/HARDWARE.md)"]);
    rows("hardware", hw);
  }

  // -- refresh loop ---------------------------------------------------------------------------

  function refreshStatus() {
    return fetchJSON("/api/status.json").then(function (res) {
      status = res.data;
      var age = res.serverNow - (status.generated_at || 0);
      renderHero(status, age);
      renderStale(age, false);
      $("updated").textContent = "Updated " + ago(age);
      if (page === "home") { renderServices(); renderResources(); renderEnvironment(); }
      if (page === "status") renderStatusPage();
    }).catch(function () {
      renderStale(0, true);
      renderHero(status, Infinity);
    });
  }

  function refreshServices() {
    return fetchJSON("/api/services.json").then(function (res) {
      services = res.data;
      renderServices();
    }).catch(function () {
      var root = $("services");
      if (root && !services) {
        clear(root);
        root.appendChild(el("p", { "class": "muted", text: "Service list unavailable. Run `homeisland apply` on the server." }));
      }
    });
  }

  if (page === "home") {
    refreshServices();
    setInterval(refreshServices, SERVICES_INTERVAL);
  }
  refreshStatus();
  setInterval(function () {
    if (!document.hidden) refreshStatus();
  }, STATUS_INTERVAL);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshStatus(); });
})();
