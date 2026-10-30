// observability.js — the /admin/observability page.
//
// Data comes from one LivePayload (see observability/api.py). It arrives over
// SSE (/admin/observability/stream, every 3 s); if the stream fails we fall
// back to polling /admin/observability/live every 5 s. Both pause while the tab
// is hidden. Every value is written with textContent / DOM APIs: model names,
// server names, process names and sink endpoints are operator-controlled.
// Null or absent values render as an em dash.

(function () {
  "use strict";

  const root = document.getElementById("obsRoot");
  if (!root) return;

  const IS_ADMIN = root.dataset.admin === "true";
  const POLL_MS = 5000;
  const SPARK_LEN = 60;
  const DASH = "—";

  const $ = (id) => document.getElementById(id);
  const integer = new Intl.NumberFormat();

  const state = {
    tab: "models",
    payload: null,
    es: null,
    pollTimer: null,
    failures: 0,
    history: new Map(), // "server|model" -> number[] (rps_10s ring buffer)
    memHistory: [],     // {t, rss, inflight} samples for the gateway chart
    memChart: null,
    hostsLoaded: false,
    gpuHosts: [],
    overridesLoaded: false,
    tracemallocOn: null,
  };

  // ── Formatting helpers ──────────────────────────────────────────────
  function isNum(v) { return typeof v === "number" && Number.isFinite(v); }

  function fmt(v, digits = 1, suffix = "") {
    if (!isNum(v)) return DASH;
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) + suffix;
  }

  function fmtInt(v) { return isNum(v) ? integer.format(Math.round(v)) : DASH; }

  function fmtPct(v, digits = 0) { return isNum(v) ? fmt(v, digits, "%") : DASH; }

  // Input MiB -> "21.4 GiB".
  function fmtGiB(mib) { return isNum(mib) ? fmt(mib / 1024, 1, " GiB") : DASH; }

  function fmtMiB(mib) { return isNum(mib) ? fmtInt(mib) + " MiB" : DASH; }

  function fmtBytes(b) {
    if (!isNum(b)) return DASH;
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let v = b, i = 0;
    while (Math.abs(v) >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return fmt(v, i === 0 ? 0 : 1, " " + units[i]);
  }

  function fmtMs(ms) {
    if (!isNum(ms)) return DASH;
    return ms >= 1000 ? fmt(ms / 1000, 2, " s") : fmt(ms, 0, " ms");
  }

  function ageSeconds(iso) {
    if (!iso) return null;
    const t = Date.parse(iso);
    return Number.isNaN(t) ? null : Math.max(0, Math.round((Date.now() - t) / 1000));
  }

  function fmtAge(iso) {
    const s = ageSeconds(iso);
    if (s === null) return DASH;
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    return Math.round(s / 3600) + "h ago";
  }

  // ── DOM helpers (textContent only) ──────────────────────────────────
  function el(tag, opts, children) {
    const node = document.createElement(tag);
    if (opts) {
      if (opts.cls) node.className = opts.cls;
      if (opts.text !== undefined) node.textContent = opts.text;
      if (opts.attrs) for (const k of Object.keys(opts.attrs)) node.setAttribute(k, opts.attrs[k]);
      if (opts.data) for (const k of Object.keys(opts.data)) node.dataset[k] = opts.data[k];
    }
    (children || []).forEach((c) => { if (c) node.appendChild(c); });
    return node;
  }

  function td(text, cls) {
    return el("td", { cls: "p-3 " + (cls || ""), text: text === null || text === undefined ? DASH : String(text) });
  }

  function badge(text, tone) {
    const tones = {
      ok: "text-ok border-hairline",
      warn: "text-warn border-hairline",
      down: "text-down border-hairline",
      muted: "text-ink-muted border-hairline",
    };
    return el("span", { cls: "inline-flex items-center rounded-control border px-2 py-0.5 text-caption font-medium " + (tones[tone] || tones.muted), text });
  }

  function bar(pct, tone) {
    const track = el("div", { cls: "h-2 w-full overflow-hidden rounded-control bg-surface-inset" });
    const fill = el("div", { cls: "h-2 rounded-control " + (tone === "warn" ? "bg-warn" : "bg-accent") });
    fill.style.width = (isNum(pct) ? Math.min(100, Math.max(0, pct)) : 0) + "%";
    track.appendChild(fill);
    return track;
  }

  // Tiny inline sparkline as an SVG polyline (no canvas, so no theme rebuild).
  function sparkline(values) {
    const w = 96, h = 24;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "text-accent");
    if (!values || values.length < 2) return svg;
    const max = Math.max(...values, 1e-9);
    const step = w / (SPARK_LEN - 1);
    const pts = values.map((v, i) => {
      const x = (i + (SPARK_LEN - values.length)) * step;
      const y = h - 2 - (v / max) * (h - 4);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", pts);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "currentColor");
    line.setAttribute("stroke-width", "1.5");
    svg.appendChild(line);
    return svg;
  }

  function emptyRow(cols, text) {
    return el("tr", null, [el("td", { cls: "p-4 text-ink-muted", text, attrs: { colspan: String(cols) } })]);
  }

  function replaceRows(tbody, rows) {
    tbody.replaceChildren(...rows);
  }

  // ── Connection (SSE with polling fallback, paused when hidden) ──────
  function setLive(text, tone) {
    const node = $("obsLive");
    node.textContent = text;
    node.className = "rounded-control px-2 py-1 font-medium " + (tone === "ok" ? "text-ok" : tone === "down" ? "text-down" : "text-ink-muted");
  }

  function onPayload(p) {
    state.failures = 0;
    state.payload = p;
    recordHistory(p);
    render();
  }

  function startStream() {
    stopAll();
    if (document.hidden) { setLive("paused (tab hidden)", "muted"); return; }
    if (!window.EventSource) { startPolling(); return; }
    setLive("connecting…");
    const es = new EventSource("/admin/observability/stream");
    state.es = es;
    es.onopen = () => setLive("live · SSE", "ok");
    es.onmessage = (ev) => {
      try { onPayload(JSON.parse(ev.data)); } catch (e) { /* ignore a malformed frame */ }
    };
    es.onerror = () => {
      es.close();
      state.es = null;
      state.failures++;
      // Fall back to polling for good; the SSE endpoint is not essential.
      startPolling();
    };
  }

  async function pollOnce() {
    try {
      const res = await fetch("/admin/observability/live", { credentials: "include", cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      onPayload(await res.json());
      setLive("live · polling", "ok");
    } catch (e) {
      setLive("unavailable — retrying", "down");
    }
  }

  function startPolling() {
    stopPolling();
    pollOnce();
    state.pollTimer = setInterval(() => { if (!document.hidden) pollOnce(); }, POLL_MS);
  }

  function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  function stopAll() {
    if (state.es) { state.es.close(); state.es = null; }
    stopPolling();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopAll();
      setLive("paused (tab hidden)", "muted");
    } else {
      startStream();
    }
  });

  function recordHistory(p) {
    for (const m of p.models || []) {
      const key = m.server + "|" + m.model;
      let arr = state.history.get(key);
      if (!arr) { arr = []; state.history.set(key, arr); }
      arr.push(m.gateway_traffic || !isNum(m.server_rps) ? (isNum(m.rps_10s) ? m.rps_10s : 0) : m.server_rps);
      if (arr.length > SPARK_LEN) arr.splice(0, arr.length - SPARK_LEN);
    }
    const inflight = (p.models || []).reduce((s, m) => s + (isNum(m.inflight_bytes) ? m.inflight_bytes : 0), 0);
    const rss = p.process && isNum(p.process.rss_bytes) ? p.process.rss_bytes : null;
    if (rss !== null) {
      state.memHistory.push({ rss, inflight });
      if (state.memHistory.length > SPARK_LEN) state.memHistory.splice(0, state.memHistory.length - SPARK_LEN);
    }
  }

  // ── Render dispatcher ───────────────────────────────────────────────
  function render() {
    const p = state.payload;
    if (!p) return;
    $("obsScope").textContent = "scope: " + (p.scope || DASH);
    $("obsInstance").textContent = "instance: " + (p.instance || DASH);
    const reps = $("obsReplicas");
    if (Array.isArray(p.replicas) && p.replicas.length > 1) {
      reps.textContent = p.replicas.length + " replicas";
      reps.classList.remove("hidden");
    } else {
      reps.classList.add("hidden");
    }
    if (state.tab === "models") renderModels(p);
    if (state.tab === "groups") renderGroups(p);
    if (state.tab === "gpus") renderGpus(p);
    if (state.tab === "gateway") renderGateway(p);
    if (state.tab === "integrations") renderSinks(p);
  }

  // ── Tabs ────────────────────────────────────────────────────────────
  function setTab(name) {
    state.tab = name;
    document.querySelectorAll("[data-panel]").forEach((s) => s.classList.toggle("hidden", s.dataset.panel !== name));
    document.querySelectorAll('[data-action="tab"]').forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.classList.toggle("bg-accent/10", on);
      b.classList.toggle("text-accent", on);
      b.classList.toggle("text-ink-muted", !on);
    });
    if (name === "gpus" && IS_ADMIN && !state.hostsLoaded) loadGpuHosts();
    if (name === "gateway" && IS_ADMIN && !state.overridesLoaded) loadOverrides();
    if (state.payload) render();
  }

  // ── Models tab ──────────────────────────────────────────────────────
  function renderModels(p) {
    const servers = new Map((p.servers || []).map((s) => [s.name, s]));
    const rows = (p.models || []).map((m) => {
      const tr = el("tr", { cls: "hover:bg-surface-inset" });
      tr.appendChild(td(m.server));
      const modelCell = td(m.model, "font-mono text-xs");
      if (m.tokens_estimated) modelCell.appendChild(el("span", { cls: "ml-2", text: "≈ est. tokens" }));
      tr.appendChild(modelCell);
      tr.appendChild(td(fmt(m.rps_10s, 2), "text-right tabular-nums"));
      tr.appendChild(td(fmt(m.rps_60s, 2), "text-right tabular-nums"));
      tr.appendChild(td(fmtInt(m.waiting), "text-right tabular-nums"));
      tr.appendChild(td(fmtInt(m.generating), "text-right tabular-nums"));
      tr.appendChild(td(fmt(m.tok_s_decode, 1), "text-right tabular-nums"));
      tr.appendChild(td(fmt(m.tok_s_throughput, 1), "text-right tabular-nums"));
      tr.appendChild(td(fmtMs(m.ttft_p50_ms), "text-right tabular-nums"));
      tr.appendChild(td(fmt(m.server_rps, 2), "text-right tabular-nums"));
      tr.appendChild(td(fmt(m.server_tok_s, 1), "text-right tabular-nums"));
      tr.appendChild(td(fmtMs(m.itl_ms), "text-right tabular-nums"));
      const run = isNum(m.server_running) || isNum(m.server_waiting)
        ? fmtInt(m.server_running) + " / " + fmtInt(m.server_waiting) : DASH;
      tr.appendChild(td(run, "text-right tabular-nums"));
      tr.appendChild(td(fmtPct(m.kv_cache_pct, 0), "text-right tabular-nums"));
      const spark = el("td", { cls: "p-3" }, [sparkline(state.history.get(m.server + "|" + m.model))]);
      tr.appendChild(spark);
      return tr;
    });
    replaceRows($("obsModelsBody"), rows);
    $("obsModelsEmpty").classList.toggle("hidden", rows.length > 0);
  }

  // ── Groups tab (collage) ────────────────────────────────────────────
  function renderGroups(p) {
    const host = $("obsGroups");
    const cards = (p.groups || []).map(groupCard);
    host.replaceChildren(...cards);
    $("obsGroupsEmpty").classList.toggle("hidden", cards.length > 0);
  }

  function statTile(label, value) {
    return el("div", { cls: "rounded-control bg-surface-inset p-3" }, [
      el("p", { cls: "text-caption text-ink-muted", text: label }),
      el("p", { cls: "mt-1 text-sm font-semibold text-ink tabular-nums", text: value }),
    ]);
  }

  function groupCard(g) {
    const t = g.totals || {};
    const vramPct = isNum(t.vram_pct) ? t.vram_pct : (isNum(t.vram_used_mib) && isNum(t.vram_total_mib) && t.vram_total_mib > 0 ? (t.vram_used_mib / t.vram_total_mib) * 100 : null);
    const vramText = isNum(t.vram_total_mib) && t.vram_total_mib > 0
      ? `${fmtGiB(t.vram_used_mib).replace(" GiB", "")} / ${fmtGiB(t.vram_total_mib)} (${fmtPct(vramPct, 0)})`
      : DASH;
    const gpuText = isNum(t.gpu_util_avg_pct)
      ? `${fmtPct(t.gpu_util_avg_pct, 0)} avg · ${fmtInt(t.gpu_count)} GPU${t.gpu_count === 1 ? "" : "s"}`
      : DASH;

    const header = el("div", { cls: "flex items-start justify-between gap-3" }, [
      el("div", null, [
        el("h2", { cls: "text-sm font-bold text-ink break-all", text: g.group }),
        el("p", { cls: "text-caption text-ink-muted", text: `${fmtInt(t.instance_count)} instance${t.instance_count === 1 ? "" : "s"}` }),
      ]),
    ]);

    const stats = el("div", { cls: "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2" }, [
      statTile("VRAM", vramText),
      statTile("GPU util", gpuText),
      statTile("Σ RPS 60s", fmt(t.rps_60s, 2)),
      statTile("Σ tok/s", fmt(t.tok_s, 1)),
      statTile("Σ queue", fmtInt(t.waiting)),
    ]);

    const vramBar = el("div", { cls: "space-y-1" }, [
      el("div", { cls: "flex justify-between text-caption text-ink-muted", text: "VRAM " + vramText }),
      bar(vramPct, vramPct !== null && vramPct >= 90 ? "warn" : "ok"),
    ]);

    const list = el("ul", { cls: "divide-y divide-hairline" });
    (g.instances || []).forEach((inst) => list.appendChild(instanceRow(inst)));
    if (!g.instances || g.instances.length === 0) list.appendChild(el("li", { cls: "py-2 text-sm text-ink-muted", text: "No instances" }));

    return el("article", { cls: "rounded-panel border border-hairline bg-surface-raised shadow-rest p-4 space-y-4" }, [
      header, stats, vramBar, list,
    ]);
  }

  const ATTRIBUTION = {
    pinned: ["Pinned", "ok"],
    process: ["Per-process", "ok"],
    host: ["Host total", "warn"],
    none: ["Not attributed", "muted"],
  };

  function instanceRow(inst) {
    const gpuParts = (inst.gpus || []).map((g) => {
      const used = isNum(g.mem_used_mib) && isNum(g.mem_total_mib)
        ? `${fmtGiB(g.mem_used_mib).replace(" GiB", "")}/${fmtGiB(g.mem_total_mib)}`
        : fmtGiB(g.mem_used_mib);
      return `GPU${g.index ?? "?"} ${fmtPct(g.util_pct, 0)} · ${used}`;
    });
    const attr = ATTRIBUTION[inst.gpu_attribution] || ATTRIBUTION.none;
    const top = el("div", { cls: "flex flex-wrap items-center justify-between gap-2" }, [
      el("p", { cls: "text-sm font-medium text-ink break-all", text: `${inst.server} · ${inst.model}` }),
      badge(attr[0], attr[1]),
    ]);
    const gpuLine = el("p", { cls: "text-caption text-ink-muted tabular-nums", text: gpuParts.length ? gpuParts.join("  ·  ") : "No GPU data" });
    const stats = el("p", { cls: "text-caption text-ink-muted tabular-nums",
      text: `server ${fmt(inst.server_rps, 2)} rps · ${fmt(inst.server_tok_s, 1)} tok/s · running ${fmtInt(inst.server_running)}`
        + ` · gateway ${fmt(inst.rps_60s, 2)} rps · queue ${fmtInt(inst.waiting)}` });
    return el("li", { cls: "py-3 space-y-1" }, [top, gpuLine, stats]);
  }

  // ── GPUs tab ────────────────────────────────────────────────────────
  function stalenessBadge(h) {
    const age = ageSeconds(h.fetched_at);
    if (h.status && h.status !== "ok") return badge(h.status, "down");
    if (age === null) return badge("no data yet", "muted");
    if (age > 30) return badge("stale · " + fmtAge(h.fetched_at), "warn");
    return badge("fresh · " + fmtAge(h.fetched_at), "ok");
  }

  function renderGpus(p) {
    // Re-rendering every 3 s would collapse open process lists; remember them.
    const open = new Set(Array.from(document.querySelectorAll("#obsGpuHosts details[open]")).map((d) => d.dataset.key));
    const hosts = (p.gpus || []).map(gpuHostCard);
    if (!hosts.length) hosts.push(el("p", { cls: "text-sm text-ink-muted", text: "No GPU hosts configured yet." }));
    $("obsGpuHosts").replaceChildren(...hosts);
    $("obsGpuHosts").querySelectorAll("details[data-key]").forEach((d) => { if (open.has(d.dataset.key)) d.open = true; });
    if (IS_ADMIN) fillLinkServers(p);
  }

  function gpuHostCard(h) {
    const head = el("div", { cls: "flex flex-wrap items-center justify-between gap-2" }, [
      el("div", null, [
        el("h2", { cls: "text-sm font-bold text-ink break-all", text: h.name }),
        el("p", { cls: "text-caption text-ink-muted", text: `${h.source} · ${h.url}` }),
      ]),
      el("div", { cls: "flex items-center gap-2" }, [stalenessBadge(h), h.enabled === false ? badge("disabled", "muted") : null]),
    ]);
    if (h.error) head.appendChild(el("p", { cls: "w-full text-caption text-down break-all", text: "Error: " + h.error }));

    const gpus = h.gpus || [];
    const table = el("table", { cls: "w-full text-left text-sm border-separate border-spacing-0", attrs: { "aria-label": "GPUs on " + h.name } });
    table.appendChild(el("thead", { cls: "bg-surface-inset text-ink-muted text-micro font-bold uppercase tracking-widest" }, [
      el("tr", null, ["GPU", "Name", "Util", "VRAM used / total", "Temp", "Power"].map((c, i) =>
        el("th", { cls: "p-2 border-b border-hairline " + (i > 1 ? "text-right" : ""), text: c, attrs: { scope: "col" } }))),
    ]));
    const tbody = el("tbody", { cls: "divide-y divide-hairline text-ink" });
    gpus.forEach((g) => {
      const pct = isNum(g.mem_used_mib) && isNum(g.mem_total_mib) && g.mem_total_mib > 0 ? (g.mem_used_mib / g.mem_total_mib) * 100 : null;
      const vram = el("td", { cls: "p-2 text-right tabular-nums" }, [
        el("div", { text: `${fmtMiB(g.mem_used_mib)} / ${fmtMiB(g.mem_total_mib)}` }),
        el("div", { cls: "text-caption text-ink-muted", text: `${fmtPct(pct, 0)} · ${fmtGiB(g.mem_used_mib)} / ${fmtGiB(g.mem_total_mib)}` }),
      ]);
      const power = isNum(g.power_w) || isNum(g.power_limit_w)
        ? `${fmt(g.power_w, 0, " W")} / ${fmt(g.power_limit_w, 0, " W")}` : DASH;
      tbody.appendChild(el("tr", null, [
        td(isNum(g.index) ? "GPU" + g.index : DASH),
        td(g.name),
        el("td", { cls: "p-2 tabular-nums" }, [el("div", { text: fmtPct(g.util_pct, 0) }), bar(g.util_pct, "ok")]),
        vram,
        td(isNum(g.temp_c) ? fmt(g.temp_c, 0, " °C") : DASH, "text-right tabular-nums"),
        td(power, "text-right tabular-nums"),
      ]));
    });
    if (!gpus.length) tbody.appendChild(emptyRow(6, "No GPU samples yet."));
    table.appendChild(tbody);

    const procs = gpus.map((g) => {
      const label = `GPU${isNum(g.index) ? g.index : "?"} processes`;
      if (g.processes === null || g.processes === undefined) {
        return el("details", { cls: "text-sm", data: { key: `${h.name}#${g.index}` } }, [
          el("summary", { cls: "cursor-pointer py-1 text-ink-muted", text: label }),
          el("p", { cls: "ml-3 py-1 text-caption text-ink-muted", text: "n/a — exporter has no per-process data" }),
        ]);
      }
      const rows = g.processes.map((pr) => el("tr", null, [
        td(pr.pid), td(pr.name), td(fmtMiB(pr.used_mib), "text-right tabular-nums"),
        td(pr.model_hint), td(pr.server),
      ]));
      const pt = el("table", { cls: "w-full text-left text-caption border-separate border-spacing-0" }, [
        el("thead", { cls: "text-ink-muted" }, [el("tr", null, ["PID", "Process", "MiB", "Model", "Server"].map((c) =>
          el("th", { cls: "p-1 border-b border-hairline", text: c, attrs: { scope: "col" } })))]),
        el("tbody", { cls: "divide-y divide-hairline" }, rows.length ? rows : [emptyRow(5, "No processes on this GPU")]),
      ]);
      return el("details", { cls: "text-sm", data: { key: `${h.name}#${g.index}` } }, [
        el("summary", { cls: "cursor-pointer py-1 text-ink", text: `${label} (${g.processes.length})` }),
        el("div", { cls: "overflow-x-auto ml-3" }, [pt]),
      ]);
    });

    return el("section", { cls: "rounded-panel border border-hairline bg-surface-raised shadow-rest p-4 space-y-3" }, [
      head,
      el("div", { cls: "overflow-x-auto" }, [table]),
      el("div", { cls: "space-y-1" }, procs),
    ]);
  }

  function fillLinkServers(p) {
    const sel = $("obsLinkServer");
    const hostSel = $("obsLinkHost");
    if (!sel || !hostSel) return;
    // Only rebuild when the option set changes, so an open dropdown survives refreshes.
    const servers = (p.servers || []).map((s) => s.name);
    const hosts = state.gpuHosts.length ? state.gpuHosts.map((h) => h.name) : (p.gpus || []).map((h) => h.name);
    const sig = JSON.stringify([servers, hosts]);
    if (sel.dataset.sig === sig) return;
    sel.dataset.sig = sig;
    const keepServer = sel.value;
    const keepHost = hostSel.value;
    sel.replaceChildren(...servers.map((n) => el("option", { text: n, attrs: { value: n } })));
    if (keepServer && servers.includes(keepServer)) sel.value = keepServer;
    hostSel.replaceChildren(el("option", { text: "— none (unlink)", attrs: { value: "" } }),
      ...hosts.map((n) => el("option", { text: n, attrs: { value: n } })));
    if (keepHost && hosts.includes(keepHost)) hostSel.value = keepHost;
  }

  async function loadGpuHosts() {
    try {
      const res = await fetch("/admin/observability/gpu-hosts", { credentials: "include", cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      state.gpuHosts = await res.json();
      state.hostsLoaded = true;
      renderHostList();
    } catch (e) {
      showToast("GPU hosts", "Could not load GPU hosts", "error");
    }
  }

  function renderHostList() {
    const list = $("obsHostList");
    if (!list) return;
    const items = state.gpuHosts.map((h) => {
      const label = el("div", { cls: "min-w-0 flex-1" }, [
        el("p", { cls: "font-medium text-ink break-all", text: h.name }),
        el("p", { cls: "text-caption text-ink-muted break-all", text: `${h.source} · ${h.url}${h.enabled ? "" : " · disabled"}` }),
      ]);
      const actions = el("div", { cls: "flex shrink-0 gap-2" }, [
        el("button", { cls: "min-h-touch px-2 text-accent", text: "Edit", attrs: { type: "button" }, data: { action: "host-edit", name: h.name } }),
        el("button", { cls: "min-h-touch px-2 text-down", text: "Delete", attrs: { type: "button" }, data: { action: "host-delete", name: h.name } }),
      ]);
      return el("li", { cls: "flex items-start gap-3 py-2" }, [label, actions]);
    });
    if (!items.length) items.push(el("li", { cls: "py-2 text-ink-muted", text: "No GPU hosts yet." }));
    list.replaceChildren(...items);
    if (state.payload) fillLinkServers(state.payload);
  }

  // ── Gateway tab ─────────────────────────────────────────────────────
  function renderGateway(p) {
    const pr = p.process || {};
    const dl = $("obsProcess");
    const items = [
      ["RSS", fmtBytes(pr.rss_bytes)],
      ["USS", pr.uss_bytes === null || pr.uss_bytes === undefined ? DASH : fmtBytes(pr.uss_bytes)],
      ["Threads", fmtInt(pr.num_threads)],
      ["asyncio tasks", fmtInt(pr.asyncio_tasks)],
      ["Open fds", fmtInt(pr.num_fds)],
      ["CPU", fmtPct(pr.cpu_percent, 1)],
      ["GC counts", Array.isArray(pr.gc_counts) ? pr.gc_counts.join(" / ") : DASH],
      ["PID", fmtInt(pr.pid)],
    ];
    dl.replaceChildren(...items.flatMap(([k, v]) => [
      el("dt", { cls: "text-ink-muted", text: k }),
      el("dd", { cls: "text-ink text-right tabular-nums", text: v }),
    ]));

    const threads = (pr.threads || []).map((t) => el("tr", null, [
      td(t.id), td(fmt(t.user_s, 2), "text-right tabular-nums"), td(fmt(t.system_s, 2), "text-right tabular-nums"),
    ]));
    replaceRows($("obsThreadsBody"), threads.length ? threads : [emptyRow(3, "No thread data")]);

    const inflight = (p.models || []).filter((m) => isNum(m.inflight_bytes)).map((m) => el("tr", null, [
      td(m.server), td(m.model, "font-mono text-xs"), td(fmtBytes(m.inflight_bytes), "text-right tabular-nums"),
    ]));
    replaceRows($("obsInflightBody"), inflight.length ? inflight : [emptyRow(3, "No in-flight buffers")]);

    const tm = pr.tracemalloc || {};
    state.tracemallocOn = !!tm.enabled;
    const btn = $("obsTraceBtn");
    if (btn) {
      btn.setAttribute("aria-pressed", tm.enabled ? "true" : "false");
      btn.textContent = tm.enabled ? "Disable tracemalloc" : "Enable tracemalloc (adds CPU overhead)";
    }
    const top = Array.isArray(tm.top) ? tm.top.slice(0, 20) : [];
    const trRows = top.map((r) => el("tr", null, [
      td(r.site, "font-mono text-xs break-all"), td(fmtBytes(r.size_bytes), "text-right tabular-nums"), td(fmtInt(r.count), "text-right tabular-nums"),
    ]));
    replaceRows($("obsTraceBody"), trRows);
    $("obsTraceEmpty").textContent = tm.enabled
      ? (top.length ? "Top 20 allocation sites" + (tm.taken_at ? " · taken " + fmtAge(tm.taken_at) : "") : "tracemalloc is on; no snapshot yet.")
      : "tracemalloc is off.";
    $("obsTraceEmpty").classList.toggle("hidden", top.length > 0 && tm.enabled);

    drawMemChart();
  }

  function drawMemChart() {
    const canvas = $("obsMemChart");
    if (!canvas || typeof Chart === "undefined") return;
    const labels = state.memHistory.map((_, i) => String(i - state.memHistory.length + 1));
    const rss = state.memHistory.map((s) => s.rss / 1048576);
    const inflight = state.memHistory.map((s) => (isNum(s.inflight) ? s.inflight : 0) / 1048576);
    if (state.memChart) {
      state.memChart.data.labels = labels;
      state.memChart.data.datasets[0].data = rss;
      state.memChart.data.datasets[1].data = inflight;
      state.memChart.update("none");
      return;
    }
    const muted = tokenColor("--ink-muted");
    state.memChart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "RSS (MiB)", data: rss, borderColor: tokenColor("--accent"), backgroundColor: tokenColor("--accent", 0.15), fill: true, pointRadius: 0, tension: 0.3 },
          { label: "In-flight (MiB)", data: inflight, borderColor: tokenColor("--warn"), pointRadius: 0, tension: 0.3 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: muted, boxWidth: 10, font: { size: 10 } } } },
        scales: {
          y: { beginAtZero: true, grid: { color: tokenColor("--hairline", 0.6) }, ticks: { color: muted, font: { size: 10 } } },
          x: { display: false },
        },
      },
    });
  }

  // Chart colours come from CSS tokens; rebuild when the theme flips.
  function tokenColor(name, alpha) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v ? `rgb(${v.split(/\s+/).join(", ")}${alpha !== undefined ? ", " + alpha : ""})` : "#6366f1";
  }

  document.addEventListener("admin:themechange", () => {
    if (state.memChart) { state.memChart.destroy(); state.memChart = null; }
    if (state.tab === "gateway" && state.payload) requestAnimationFrame(() => drawMemChart());
  });

  async function toggleTracemalloc() {
    const want = !state.tracemallocOn;
    if (want) {
      const ok = await showConfirm(
        "Enable tracemalloc?",
        "tracemalloc adds roughly 10-30% CPU overhead while it is on. Turn it off again when you are done.",
        false
      );
      if (!ok) return;
    }
    try {
      const res = await fetchWithCsrf("/admin/observability/tracemalloc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable: want }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "HTTP " + res.status);
      state.tracemallocOn = !!(await res.json()).enabled;
      showToast("tracemalloc", state.tracemallocOn ? "Enabled" : "Disabled");
      if (state.payload) render();
    } catch (e) {
      showToast("tracemalloc", String(e.message || e), "error");
    }
  }

  async function loadOverrides() {
    try {
      const res = await fetch("/admin/observability/groups", { credentials: "include", cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const body = await res.json();
      const ov = body.overrides || {};
      $("obsOverrides").value = Object.keys(ov).map((g) => `${g}: ${(ov[g] || []).join(", ")}`).join("\n");
      state.overridesLoaded = true;
    } catch (e) {
      showToast("Group overrides", "Could not load overrides", "error");
    }
  }

  function parseOverrides(text) {
    const out = {};
    text.split("\n").forEach((line) => {
      const i = line.indexOf(":");
      if (i < 0) return;
      const group = line.slice(0, i).trim();
      const globs = line.slice(i + 1).split(",").map((s) => s.trim()).filter(Boolean);
      if (group && globs.length) out[group] = globs;
    });
    return out;
  }

  async function saveOverrides() {
    const overrides = parseOverrides($("obsOverrides").value);
    try {
      const res = await fetchWithCsrf("/admin/observability/groups", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(window.formatErrorMessage ? window.formatErrorMessage(err, "Save failed") : "Save failed");
      }
      showToast("Group overrides", "Saved");
      state.overridesLoaded = false;
      loadOverrides();
    } catch (e) {
      showToast("Group overrides", String(e.message || e), "error");
    }
  }

  // ── GPU host CRUD and server link (admin) ───────────────────────────
  function hostFormReset() {
    const f = $("obsHostForm");
    if (!f) return;
    f.reset();
    f.elements.original_name.value = "";
    f.elements.name.disabled = false;
  }

  function hostFormFill(name) {
    const h = state.gpuHosts.find((x) => x.name === name);
    const f = $("obsHostForm");
    if (!h || !f) return;
    f.elements.original_name.value = h.name;
    f.elements.name.value = h.name;
    f.elements.url.value = h.url;
    f.elements.source.value = h.source;
    f.elements.enabled.checked = !!h.enabled;
    f.elements.name.focus();
  }

  async function onHostSubmit(e) {
    e.preventDefault();
    const f = e.currentTarget;
    const original = f.elements.original_name.value;
    const body = {
      name: f.elements.name.value.trim(),
      url: f.elements.url.value.trim(),
      source: f.elements.source.value,
      enabled: f.elements.enabled.checked,
    };
    const url = original ? "/admin/observability/gpu-hosts/" + encodeURIComponent(original) : "/admin/observability/gpu-hosts";
    try {
      const res = await fetchWithCsrf(url, {
        method: original ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(window.formatErrorMessage ? window.formatErrorMessage(err, "Save failed") : "Save failed");
      }
      showToast("GPU host", "Saved");
      hostFormReset();
      await loadGpuHosts();
      if (state.payload) fillLinkServers(state.payload);
    } catch (err) {
      showToast("GPU host", String(err.message || err), "error");
    }
  }

  async function deleteHost(name) {
    const ok = await showConfirm("Delete GPU host", `Remove GPU host '${name}'? Servers linked to it lose their GPU attribution.`);
    if (!ok) return;
    try {
      const res = await fetchWithCsrf("/admin/observability/gpu-hosts/" + encodeURIComponent(name), { method: "DELETE" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      showToast("GPU host", "Deleted");
      await loadGpuHosts();
    } catch (err) {
      showToast("GPU host", String(err.message || err), "error");
    }
  }

  async function onLinkSubmit(e) {
    e.preventDefault();
    const f = e.currentTarget;
    const server = f.elements.server.value;
    const host = f.elements.gpu_host.value || null;
    const raw = f.elements.gpu_indices.value.trim();
    let indices = null;
    if (raw) {
      indices = raw.split(",").map((s) => s.trim()).filter(Boolean).map(Number);
      if (indices.some((n) => !Number.isInteger(n) || n < 0)) {
        showToast("GPU link", "GPU indices must be non-negative integers", "error");
        return;
      }
    }
    try {
      const res = await fetchWithCsrf("/admin/observability/servers/" + encodeURIComponent(server) + "/gpu", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gpu_host: host, gpu_indices: indices }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(window.formatErrorMessage ? window.formatErrorMessage(err, "Save failed") : "Save failed");
      }
      showToast("GPU link", `${server} linked to ${host || "no host"}`);
    } catch (err) {
      showToast("GPU link", String(err.message || err), "error");
    }
  }

  // ── Integrations tab ────────────────────────────────────────────────
  function renderSinks(p) {
    const sinks = p.sinks || [];
    const rows = sinks.map((s) => el("tr", null, [
      td(s.name, "font-medium"),
      td((s.signals || []).join(", ")),
      td(s.endpoint, "font-mono text-xs break-all"),
      td(s.last_ok ? fmtAge(s.last_ok) : DASH),
      td(s.last_error, "text-down break-all"),
      td(fmtInt(s.exported), "text-right tabular-nums"),
      td(fmtInt(s.failed), "text-right tabular-nums"),
    ]));
    replaceRows($("obsSinksBody"), rows);
    $("obsSinksEmpty").classList.toggle("hidden", rows.length > 0);
  }

  // ── Event delegation (no inline handlers) ───────────────────────────
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn || !root.contains(btn)) return;
    switch (btn.dataset.action) {
      case "tab": setTab(btn.dataset.tab); break;
      case "tracemalloc": toggleTracemalloc(); break;
      case "save-overrides": saveOverrides(); break;
      case "host-reset": hostFormReset(); break;
      case "host-edit": hostFormFill(btn.dataset.name); break;
      case "host-delete": deleteHost(btn.dataset.name); break;
      default: break;
    }
  });

  const hostForm = $("obsHostForm");
  if (hostForm) hostForm.addEventListener("submit", onHostSubmit);
  const linkForm = $("obsLinkForm");
  if (linkForm) linkForm.addEventListener("submit", onLinkSubmit);

  // ── Boot ────────────────────────────────────────────────────────────
  setTab("models");
  startStream();
})();
