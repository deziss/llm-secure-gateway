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
    view: "cards", // Groups tab: "cards" | "list"
  };

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: view just won't persist */ } },
  };
  state.view = store.get("obs.groupsView", "cards") === "list" ? "list" : "cards";

  const val = (id) => { const n = $(id); return n ? n.value : ""; };
  const norm = (t) => String(t || "").toLowerCase();

  // ── Sorting (click a column header; click again to reverse) ─────────
  const sorts = { models: { key: "server", dir: "asc" }, groups: { key: "name", dir: "asc" }, gpu: { key: "index", dir: "asc" } };
  try { Object.assign(sorts, JSON.parse(store.get("obs.sorts", "{}"))); } catch (e) { /* keep defaults */ }
  const TEXT_KEYS = new Set(["server", "model", "name"]);

  function cmpValues(a, b, dir) {
    const an = a === null || a === undefined || (typeof a === "number" && !Number.isFinite(a));
    const bn = b === null || b === undefined || (typeof b === "number" && !Number.isFinite(b));
    if (an && bn) return 0;
    if (an) return 1;           // empty values always sort last, in either direction
    if (bn) return -1;
    const r = (typeof a === "string" || typeof b === "string")
      ? String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" }) : a - b;
    return dir === "asc" ? r : -r;
  }

  function sortBy(rows, scope, getter) {
    const { key, dir } = sorts[scope];
    return rows.slice().sort((x, y) => cmpValues(getter(x, key), getter(y, key), dir));
  }

  function sortButton(scope, key, label) {
    return el("button", { cls: "inline-flex items-center gap-1 font-bold uppercase tracking-widest hover:text-ink focus:outline-none focus:ring-2 focus:ring-accent/50 rounded",
      text: label, attrs: { type: "button" }, data: { action: "sort", scope, key, label } });
  }

  function applySortIndicators(scope) {
    const { key, dir } = sorts[scope];
    document.querySelectorAll(`button[data-action="sort"][data-scope="${scope}"]`).forEach((b) => {
      const on = b.dataset.key === key;
      b.textContent = b.dataset.label + (on ? (dir === "asc" ? " ▲" : " ▼") : "");
      const th = b.closest("th");
      if (th) th.setAttribute("aria-sort", on ? (dir === "asc" ? "ascending" : "descending") : "none");
    });
  }

  function setSort(scope, key) {
    const cur = sorts[scope];
    sorts[scope] = cur.key === key ? { key, dir: cur.dir === "asc" ? "desc" : "asc" } : { key, dir: TEXT_KEYS.has(key) || key === "index" ? "asc" : "desc" };
    store.set("obs.sorts", JSON.stringify(sorts));
  }

  function initSortHeaders() {
    document.querySelectorAll("th[data-key][data-scope]").forEach((th) => {
      const label = th.textContent.trim();
      th.textContent = "";
      th.appendChild(sortButton(th.dataset.scope, th.dataset.key, label));
    });
  }

  // Keep a <select>'s options in sync with the data while preserving the user's choice.
  function syncOptions(select, first, values) {
    if (!select) return;
    const want = [""].concat(values);
    const have = Array.from(select.options).map((o) => o.value);
    if (want.length === have.length && want.every((v, i) => v === have[i])) return;
    const keep = select.value;
    select.replaceChildren(el("option", { text: first, attrs: { value: "" } }),
      ...values.map((v) => el("option", { text: v, attrs: { value: v } })));
    select.value = want.includes(keep) ? keep : "";
  }

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
    return el("td", { cls: "px-3 py-2 " + (cls || ""), text: text === null || text === undefined ? DASH : String(text) });
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
  function modelIsActive(m) {
    return (m.generating || 0) > 0 || (m.waiting || 0) > 0 || (m.server_running || 0) > 0
      || (m.server_waiting || 0) > 0 || (m.rps_60s || 0) > 0;
  }

  function renderModels(p) {
    const all = p.models || [];
    syncOptions($("fModelsServer"), "All servers", Array.from(new Set(all.map((m) => m.server))).sort());
    const q = norm(val("fModelsQ")), server = val("fModelsServer"), status = val("fModelsStatus");
    const shown = all.filter((m) => {
      if (server && m.server !== server) return false;
      if (q && !(norm(m.server) + " " + norm(m.model) + " " + norm(m.group)).includes(q)) return false;
      if (status === "active" && !modelIsActive(m)) return false;
      if (status === "idle" && modelIsActive(m)) return false;
      if (status === "errors" && !((m.errors_60s || 0) > 0 || (m.server_status && m.server_status !== "ok"))) return false;
      return true;
    });
    shown.splice(0, shown.length, ...sortBy(shown, "models", (m, k) => m[k]));
    applySortIndicators("models");
    $("fModelsCount").textContent = shown.length === all.length ? `${all.length} models` : `${shown.length} of ${all.length} models`;
    const rows = shown.map((m) => {
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
      tr.appendChild(el("td", { cls: "px-3 py-1" }, [sparkline(state.history.get(m.server + "|" + m.model))]));
      return tr;
    });
    replaceRows($("obsModelsBody"), rows);
    $("obsModelsEmpty").textContent = all.length ? "No models match the filters." : "No model servers configured yet. Add one under Model servers.";
    $("obsModelsEmpty").classList.toggle("hidden", rows.length > 0);
  }

  // ── Groups tab (collage) ────────────────────────────────────────────
  const ATTRIBUTION = {
    pinned: ["Pinned", "ok"],
    process: ["Per-process", "ok"],
    host: ["Host total", "warn"],
    none: ["No GPU data", "muted"],
  };

  function groupVram(t) {
    const pct = isNum(t.vram_pct) ? t.vram_pct
      : (isNum(t.vram_used_mib) && isNum(t.vram_total_mib) && t.vram_total_mib > 0 ? (t.vram_used_mib / t.vram_total_mib) * 100 : null);
    const text = isNum(t.vram_total_mib) && t.vram_total_mib > 0
      ? `${fmtGiB(t.vram_used_mib).replace(" GiB", "")} / ${fmtGiB(t.vram_total_mib)} (${fmtPct(pct, 0)})`
      : DASH;
    return { pct, text };
  }

  function groupHasGpu(g) { return (g.instances || []).some((i) => (i.gpus || []).length > 0); }
  function groupShared(g) { return (g.instances || []).some((i) => i.gpu_attribution === "host"); }
  function groupActive(g) { return (g.instances || []).some((i) => i.has_traffic); }

  function filterGroups(p) {
    const q = norm(val("fGroupsQ")), gpu = val("fGroupsGpu"), traffic = val("fGroupsTraffic");
    const out = (p.groups || []).filter((g) => {
      if (q) {
        const hay = norm(g.group) + " " + (g.instances || []).map((i) => norm(i.server) + " " + norm(i.model)).join(" ");
        if (!hay.includes(q)) return false;
      }
      if (gpu === "with" && !groupHasGpu(g)) return false;
      if (gpu === "without" && groupHasGpu(g)) return false;
      if (gpu === "shared" && !groupShared(g)) return false;
      if (traffic === "active" && !groupActive(g)) return false;
      if (traffic === "idle" && groupActive(g)) return false;
      return true;
    });
    const GET = {
      name: (g) => g.group, vram: (g) => (g.totals || {}).vram_used_mib, util: (g) => (g.totals || {}).gpu_util_avg_pct,
      tok: (g) => (g.totals || {}).tok_s, rps: (g) => (g.totals || {}).rps_60s, gpus: (g) => (g.totals || {}).gpu_count,
      queue: (g) => ((g.totals || {}).waiting || 0) + ((g.totals || {}).server_waiting || 0),
      data: (g) => (groupHasGpu(g) ? 1 : 0),
    };
    return sortBy(out, "groups", (g, k) => (GET[k] || GET.name)(g));
  }

  function renderGroups(p) {
    const groups = filterGroups(p);
    const total = (p.groups || []).length;
    if ($("fGroupsSort")) $("fGroupsSort").value = Array.from($("fGroupsSort").options).some((o) => o.value === sorts.groups.key) ? sorts.groups.key : "name";
    $("fGroupsCount").textContent = groups.length === total ? `${total} groups` : `${groups.length} of ${total} groups`;
    document.querySelectorAll('[data-action="view"]').forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.view === state.view ? "true" : "false");
      b.classList.toggle("bg-accent/10", b.dataset.view === state.view);
      b.classList.toggle("text-accent", b.dataset.view === state.view);
    });
    const cardsHost = $("obsGroups"), listHost = $("obsGroupsList");
    cardsHost.classList.toggle("hidden", state.view !== "cards");
    listHost.classList.toggle("hidden", state.view !== "list");
    if (state.view === "cards") cardsHost.replaceChildren(...groups.map(groupCard));
    else { listHost.replaceChildren(groupTable(groups)); applySortIndicators("groups"); }
    $("obsGroupsEmpty").textContent = total ? "No groups match the filters." : "No model groups yet.";
    $("obsGroupsEmpty").classList.toggle("hidden", groups.length > 0);
  }

  function miniStat(label, value) {
    return el("div", { cls: "min-w-0" }, [
      el("p", { cls: "text-micro uppercase tracking-wide text-ink-muted truncate", text: label }),
      el("p", { cls: "text-sm font-semibold text-ink tabular-nums truncate", text: value, attrs: { title: value } }),
    ]);
  }

  function gpuChips(inst) {
    return (inst.gpus || []).map((g) => {
      const used = isNum(g.mem_used_mib) && isNum(g.mem_total_mib)
        ? `${fmtGiB(g.mem_used_mib).replace(" GiB", "")}/${fmtGiB(g.mem_total_mib)}` : fmtGiB(g.mem_used_mib);
      return `GPU${g.index ?? "?"} ${fmtPct(g.util_pct, 0)} · ${used}`;
    });
  }

  function instVramText(inst) {
    return isNum(inst.model_vram_mib) ? fmtGiB(inst.model_vram_mib) : DASH;
  }

  function groupCard(g) {
    const t = g.totals || {};
    const v = groupVram(t);
    const gpuText = isNum(t.gpu_util_avg_pct)
      ? `${fmtPct(t.gpu_util_avg_pct, 0)} · ${fmtInt(t.gpu_count)} GPU${t.gpu_count === 1 ? "" : "s"}` : DASH;
    const header = el("div", { cls: "flex items-baseline justify-between gap-2" }, [
      el("h2", { cls: "text-sm font-bold text-ink break-all", text: g.group }),
      el("span", { cls: "shrink-0 text-caption text-ink-muted", text: `${fmtInt(t.instance_count)} inst.` }),
    ]);
    const stats = el("div", { cls: "grid grid-cols-5 gap-2" }, [
      miniStat("VRAM", v.text), miniStat("GPU util", gpuText), miniStat("RPS", fmt(t.rps_60s, 2)),
      miniStat("tok/s", fmt(t.tok_s, 1)), miniStat("Queue", fmtInt(t.waiting)),
    ]);
    const list = el("ul", { cls: "divide-y divide-hairline" });
    (g.instances || []).forEach((inst) => list.appendChild(instanceRow(inst)));
    return el("article", { cls: "rounded-panel border border-hairline bg-surface-raised shadow-rest p-3 space-y-2" }, [
      header, stats, bar(v.pct, v.pct !== null && v.pct >= 90 ? "warn" : "ok"), list,
    ]);
  }

  function instanceRow(inst) {
    const attr = ATTRIBUTION[inst.gpu_attribution] || ATTRIBUTION.none;
    const chips = gpuChips(inst);
    const top = el("div", { cls: "flex items-center justify-between gap-2" }, [
      el("p", { cls: "text-xs font-medium text-ink break-all", text: `${inst.server} · ${inst.model}` }),
      badge(attr[0], attr[1]),
    ]);
    const gpuText = chips.length
      ? `VRAM ${instVramText(inst)} · ${chips.join("  ·  ")}`
      : (inst.gpu_note || "No GPU data");
    const gpuLine = el("p", { cls: "text-caption tabular-nums " + (chips.length ? "text-ink-muted" : "text-warn"), text: gpuText });
    const stats = el("p", { cls: "text-caption text-ink-muted tabular-nums",
      text: `${fmt(inst.server_rps ?? inst.rps_60s, 2)} rps · ${fmt(inst.server_tok_s ?? inst.tok_s_throughput, 1)} tok/s · running ${fmtInt(inst.server_running)} · queue ${fmtInt(inst.waiting)}` });
    return el("li", { cls: "py-1.5 space-y-0.5" }, [top, gpuLine, stats]);
  }

  // List view: one fixed-width table, group rows with their instances beneath.
  const LIST_COLS = [
    ["Group / instance", "w-[22%]", "", "name"], ["VRAM", "w-[19%]", "text-right", "vram"], ["GPU util", "w-[10%]", "text-right", "util"],
    ["GPUs", "w-[16%]", "", "gpus"], ["RPS", "w-[7%]", "text-right", "rps"], ["tok/s", "w-[8%]", "text-right", "tok"],
    ["Queue", "w-[6%]", "text-right", "queue"], ["GPU data", "w-[12%]", "", "data"],
  ];

  function groupTable(groups) {
    const table = el("table", { cls: "w-full table-fixed text-left text-sm border-separate border-spacing-0", attrs: { "aria-label": "Model groups" } });
    table.appendChild(el("thead", { cls: "bg-surface-inset text-ink-muted text-micro font-bold uppercase tracking-widest" }, [
      el("tr", null, LIST_COLS.map(([c, w, al, key]) => el("th", { cls: `${w} px-3 py-2 border-b border-hairline ${al}`, attrs: { scope: "col" } }, [sortButton("groups", key, c)]))),
    ]));
    groups.forEach((g) => {
      const t = g.totals || {}, v = groupVram(t);
      const body = el("tbody", { cls: "border-b border-hairline" });
      const gpuText = isNum(t.gpu_util_avg_pct) ? fmtPct(t.gpu_util_avg_pct, 0) : DASH;
      const cell = (text, cls) => el("td", { cls: "px-3 py-1.5 tabular-nums " + (cls || ""), text });
      const vramCell = el("td", { cls: "px-3 py-1.5 text-right tabular-nums" }, [
        el("div", { text: v.text }), el("div", { cls: "mt-0.5" }, [bar(v.pct, v.pct !== null && v.pct >= 90 ? "warn" : "ok")]),
      ]);
      body.appendChild(el("tr", { cls: "bg-surface-inset/50 font-semibold text-ink" }, [
        el("td", { cls: "px-3 py-1.5 break-all", text: `${g.group}  ·  ${fmtInt(t.instance_count)}` }),
        vramCell, cell(gpuText, "text-right"), cell(`${fmtInt(t.gpu_count)} GPU${t.gpu_count === 1 ? "" : "s"}`),
        cell(fmt(t.rps_60s, 2), "text-right"), cell(fmt(t.tok_s, 1), "text-right"), cell(fmtInt(t.waiting), "text-right"),
        cell(groupHasGpu(g) ? "" : "No GPU data", "text-ink-muted font-normal"),
      ]));
      (g.instances || []).forEach((inst) => {
        const attr = ATTRIBUTION[inst.gpu_attribution] || ATTRIBUTION.none;
        const gp = inst.gpus || [];
        const util = gp.length ? fmtPct(gp.reduce((a, x) => a + (x.util_pct || 0), 0) / gp.length, 0) : DASH;
        const own = isNum(inst.model_vram_mib) ? fmtGiB(inst.model_vram_mib) : DASH;
        const cap = gp.length && gp.every((x) => isNum(x.mem_total_mib)) ? fmtGiB(gp.reduce((a, x) => a + x.mem_total_mib, 0)) : DASH;
        body.appendChild(el("tr", { cls: "text-ink-muted" }, [
          el("td", { cls: "px-3 py-1 pl-6 text-xs break-all", text: `${inst.server} · ${inst.model}`, attrs: { title: inst.gpu_note || "" } }),
          cell(gp.length ? `${own} of ${cap}` : DASH, "text-right text-xs"), cell(util, "text-right text-xs"),
          cell(gp.length ? gp.map((x) => "GPU" + x.index).join(", ") : DASH, "text-xs"),
          cell(fmt(inst.server_rps ?? inst.rps_60s, 2), "text-right text-xs"),
          cell(fmt(inst.server_tok_s ?? inst.tok_s_throughput, 1), "text-right text-xs"),
          cell(fmtInt(inst.waiting), "text-right text-xs"),
          el("td", { cls: "px-3 py-1", attrs: { title: inst.gpu_note || "" } }, [badge(attr[0], attr[1])]),
        ]));
      });
      table.appendChild(body);
    });
    return table;
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
    applySortIndicators("gpu");
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

    const gpus = sortBy(h.gpus || [], "gpu", (g, k) => g[k]);
    // Fixed layout + one width/alignment list shared by header and cells keeps every
    // GPU row, and every host's table, lined up.
    const GPU_COLS = [["GPU", "w-[8%]", "", "index"], ["Name", "w-[24%]", "", "name"], ["Util", "w-[22%]", "", "util_pct"],
                      ["VRAM used / total", "w-[26%]", "text-right", "mem_used_mib"], ["Temp", "w-[8%]", "text-right", "temp_c"], ["Power", "w-[12%]", "text-right", "power_w"]];
    const table = el("table", { cls: "w-full table-fixed text-left text-sm border-separate border-spacing-0", attrs: { "aria-label": "GPUs on " + h.name } });
    table.appendChild(el("thead", { cls: "bg-surface-inset text-ink-muted text-micro font-bold uppercase tracking-widest" }, [
      el("tr", null, GPU_COLS.map(([c, w, al, key]) => el("th", { cls: `${w} px-3 py-2 border-b border-hairline ${al}`, attrs: { scope: "col" } }, [sortButton("gpu", key, c)]))),
    ]));
    const tbody = el("tbody", { cls: "divide-y divide-hairline text-ink" });
    gpus.forEach((g) => {
      const pct = isNum(g.mem_used_mib) && isNum(g.mem_total_mib) && g.mem_total_mib > 0 ? (g.mem_used_mib / g.mem_total_mib) * 100 : null;
      const vram = el("td", { cls: "px-3 py-1.5 text-right tabular-nums" }, [
        el("div", { text: `${fmtMiB(g.mem_used_mib)} / ${fmtMiB(g.mem_total_mib)}` }),
        el("div", { cls: "text-caption text-ink-muted", text: `${fmtPct(pct, 0)} · ${fmtGiB(g.mem_used_mib)} / ${fmtGiB(g.mem_total_mib)}` }),
      ]);
      const power = isNum(g.power_w) || isNum(g.power_limit_w)
        ? `${fmt(g.power_w, 0, " W")} / ${fmt(g.power_limit_w, 0, " W")}` : DASH;
      tbody.appendChild(el("tr", null, [
        td(isNum(g.index) ? "GPU" + g.index : DASH, "font-medium"),
        td(g.name, "truncate"),
        el("td", { cls: "px-3 py-1.5 tabular-nums" }, [el("div", { text: fmtPct(g.util_pct, 0) }), bar(g.util_pct, "ok")]),
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
      const pc = (text, cls) => el("td", { cls: "px-2 py-1 truncate " + (cls || ""), text: text === null || text === undefined ? DASH : String(text), attrs: { title: text || "" } });
      const rows = g.processes.map((pr) => el("tr", null, [
        pc(pr.pid, "tabular-nums"), pc(pr.name), pc(fmtMiB(pr.used_mib), "text-right tabular-nums"),
        pc(pr.model_hint), pc(pr.server),
      ]));
      const PROC_COLS = [["PID", "w-[11%]", ""], ["Process", "w-[30%]", ""], ["MiB", "w-[14%]", "text-right"], ["Model", "w-[27%]", ""], ["Server", "w-[18%]", ""]];
      const pt = el("table", { cls: "w-full table-fixed text-left text-caption border-separate border-spacing-0" }, [
        el("thead", { cls: "text-ink-muted" }, [el("tr", null, PROC_COLS.map(([c, w, al]) =>
          el("th", { cls: `${w} px-2 py-1 border-b border-hairline ${al}`, text: c, attrs: { scope: "col" } })))]),
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
  const intState = { list: null, loadedAt: 0, loading: false, open: null, results: {} };

  async function loadIntegrations(force) {
    if (intState.loading || (!force && Date.now() - intState.loadedAt < 6000)) return;
    intState.loading = true;
    try {
      const res = await fetch("/admin/observability/integrations", { credentials: "include", cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      intState.list = (await res.json()).integrations || [];
      intState.loadedAt = Date.now();
    } catch (e) {
      intState.list = intState.list || [];
    } finally {
      intState.loading = false;
    }
    if (state.tab === "integrations") renderIntegrations();
  }

  function integrationState(it) {
    if (it.builtin) return it.enabled ? "active" : "disabled";
    if (it.configured && it.enabled && it.error) return "failing";
    if (it.active && it.status && it.status.last_error && !it.status.last_ok) return "failing";
    if (it.active && it.status && it.status.failed > 0 && (!it.status.last_ok || it.status.last_error)) return "failing";
    if (it.active) return "active";
    if (it.configured) return "disabled";
    return "unconfigured";
  }

  const INT_STATE_BADGE = {
    active: ["Active", "ok"], failing: ["Failing", "down"], disabled: ["Off", "muted"], unconfigured: ["Not configured", "muted"],
  };

  function statusLine(it, st) {
    const s = it.status;
    if (it.builtin) {
      return s ? `Scraped ${fmtInt(s.exported)} times · last ${s.last_ok ? fmtAge(s.last_ok) : DASH}` + (s.last_error ? ` · ${s.last_error}` : "") : "Disabled (OBS_PROMETHEUS=false)";
    }
    if (st === "unconfigured") return it.env_configured ? "Configured through the environment (OBS_EXPORTERS)." : "Not configured.";
    if (it.error) return "Error: " + it.error;
    if (!s) return it.enabled ? "Starting…" : "Configured, turned off.";
    const parts = [`${fmtInt(s.exported)} sent`, `${fmtInt(s.failed)} failed`, "last ok " + (s.last_ok ? fmtAge(s.last_ok) : DASH)];
    return parts.join(" · ") + (s.last_error ? ` · last error: ${s.last_error}` : "");
  }

  function renderIntegrations() {
    if (!intState.list) { loadIntegrations(true); return; }
    const q = norm(val("fIntQ")), want = val("fIntState");
    const items = intState.list.filter((it) => {
      const st = integrationState(it);
      if (want && st !== want) return false;
      return !q || (norm(it.title) + " " + norm(it.name) + " " + norm(it.description)).includes(q);
    });
    $("fIntCount").textContent = items.length === intState.list.length ? `${items.length} tools` : `${items.length} of ${intState.list.length} tools`;
    const host = $("obsIntegrations");
    const keep = intState.open ? host.querySelector(`[data-card="${intState.open}"]`) : null;
    const cards = items.map((it) => (keep && it.name === intState.open ? keep : integrationCard(it)));
    host.replaceChildren(...cards);
    if (keep) updateCardStatus(keep, intState.list.find((x) => x.name === intState.open));
    $("obsIntegrationsEmpty").classList.toggle("hidden", cards.length > 0);
  }

  // Refresh only the status line/badge of an expanded card so typing in its form is never lost.
  function updateCardStatus(card, it) {
    if (!it) return;
    const st = integrationState(it);
    const b = card.querySelector("[data-badge]");
    if (b) { b.replaceWith(statusBadge(st)); }
    const line = card.querySelector("[data-status-line]");
    if (line) line.textContent = statusLine(it, st);
  }

  function statusBadge(st) {
    const bd = INT_STATE_BADGE[st] || INT_STATE_BADGE.unconfigured;
    const node = badge(bd[0], bd[1]);
    node.setAttribute("data-badge", "1");
    return node;
  }

  function integrationCard(it) {
    const st = integrationState(it);
    const open = intState.open === it.name;
    const head = el("div", { cls: "flex items-start justify-between gap-2" }, [
      el("div", { cls: "min-w-0" }, [
        el("h3", { cls: "text-sm font-bold text-ink", text: it.title }),
        el("p", { cls: "text-caption text-ink-muted", text: (it.signals || ["traces", "metrics"]).join(" · ") + (it.mode ? ` · ${it.mode}` : "") }),
      ]),
      statusBadge(st),
    ]);
    const line = el("p", { cls: "text-caption " + (st === "failing" ? "text-down" : "text-ink-muted") + " break-words", text: statusLine(it, st), attrs: { "data-status-line": "1" } });
    const actions = el("div", { cls: "flex flex-wrap items-center gap-2" }, []);
    const btn = (text, action, extra, tone) => el("button", {
      cls: "rounded-control px-3 py-1 text-sm font-medium " + (tone === "accent" ? "bg-accent text-accent-ink" : "border border-hairline text-ink"),
      text, attrs: { type: "button" }, data: Object.assign({ action, name: it.name }, extra || {}),
    });
    actions.appendChild(btn(open ? "Close" : (it.builtin ? "How to connect" : (IS_ADMIN ? "Configure" : "Details")), "int-open"));
    if (IS_ADMIN && !it.builtin && it.configured) {
      actions.appendChild(btn(it.enabled ? "Turn off" : "Turn on", "int-toggle", { enabled: String(!it.enabled) }));
      actions.appendChild(btn("Test", "int-test"));
    }
    const card = el("article", { cls: "rounded-panel border border-hairline bg-surface-raised shadow-rest p-3 space-y-2", data: { card: it.name } }, [head, el("p", { cls: "text-caption text-ink-muted", text: it.description }), line, actions]);
    if (it.env_configured && !it.builtin) {
      card.appendChild(el("p", { cls: "text-caption text-warn", text: "Also set in the environment (OBS_EXPORTERS). Turning it on here exports twice; remove it from OBS_EXPORTERS." }));
    }
    if (open) card.appendChild(integrationBody(it));
    return card;
  }

  function integrationBody(it) {
    const body = el("div", { cls: "space-y-3 border-t border-hairline pt-3" }, []);
    body.appendChild(el("div", null, [
      el("p", { cls: "text-caption font-bold text-ink", text: "How to connect" }),
      el("ol", { cls: "ml-5 list-decimal space-y-1 text-caption text-ink-muted" }, (it.howto || []).map((t) => el("li", { text: t }))),
    ]));
    if (it.docs) {
      body.appendChild(el("a", { cls: "text-caption text-accent underline", text: "Vendor documentation", attrs: { href: it.docs, target: "_blank", rel: "noopener noreferrer" } }));
    }
    if (it.builtin) {
      body.appendChild(el("pre", { cls: "overflow-x-auto rounded-control bg-surface-inset p-2 text-caption text-ink", text: it.scrape_config || "" }));
      body.appendChild(el("button", { cls: "rounded-control border border-hairline px-3 py-1 text-sm text-ink", text: "Copy scrape config",
        attrs: { type: "button" }, data: { action: "int-copy", text: it.scrape_config || "" } }));
      return body;
    }
    if (!IS_ADMIN) { body.appendChild(el("p", { cls: "text-caption text-ink-muted", text: "Only administrators can change integrations." })); return body; }

    const form = el("form", { cls: "grid grid-cols-1 md:grid-cols-2 gap-3 text-sm", attrs: { autocomplete: "off", "data-int-form": it.name } }, []);
    (it.fields || []).forEach((f) => {
      const input = el("input", { cls: "w-full rounded-control border border-hairline bg-surface-inset px-3 py-2 text-ink",
        attrs: { type: f.secret ? "password" : "text", name: f.key, maxlength: "2048", autocomplete: "off", spellcheck: "false",
                 placeholder: f.secret && f.set ? "•••••• stored — leave blank to keep" : (f.placeholder || "") } });
      if (!f.secret) input.value = f.value || "";
      const label = el("label", { cls: "block" }, [
        el("span", { cls: "block text-caption font-medium text-ink-muted mb-1", text: f.label + (f.required ? " *" : "") }),
        input,
      ]);
      if (f.help) label.appendChild(el("span", { cls: "block text-micro text-ink-muted mt-0.5", text: f.help }));
      if (f.secret && f.set) {
        const clr = el("input", { attrs: { type: "checkbox", name: "__clear__" + f.key }, cls: "h-4 w-4" });
        label.appendChild(el("label", { cls: "mt-1 flex items-center gap-2 text-micro text-ink-muted" }, [clr, el("span", { text: "Remove stored value" })]));
      }
      form.appendChild(label);
    });
    const enabled = el("input", { attrs: { type: "checkbox", name: "__enabled" }, cls: "h-4 w-4" });
    enabled.checked = it.configured ? !!it.enabled : true;
    form.appendChild(el("label", { cls: "flex items-center gap-2 md:col-span-2 text-ink-muted" }, [enabled, el("span", { text: "Enabled (start exporting after saving)" })]));
    const bar2 = el("div", { cls: "md:col-span-2 flex flex-wrap items-center gap-2" }, [
      el("button", { cls: "min-h-touch rounded-control bg-accent px-4 py-2 font-medium text-accent-ink", text: "Save", attrs: { type: "submit" } }),
      el("button", { cls: "min-h-touch rounded-control border border-hairline px-4 py-2 font-medium text-ink", text: "Test with these values",
        attrs: { type: "button" }, data: { action: "int-test", name: it.name, form: "1" } }),
    ]);
    if (it.configured) {
      bar2.appendChild(el("button", { cls: "min-h-touch rounded-control border border-hairline px-4 py-2 font-medium text-down ml-auto", text: "Remove",
        attrs: { type: "button" }, data: { action: "int-remove", name: it.name } }));
    }
    form.appendChild(bar2);
    form.appendChild(el("p", { cls: "md:col-span-2 text-caption", attrs: { "data-result": it.name, role: "status" }, text: "" }));
    form.addEventListener("submit", (e) => { e.preventDefault(); saveIntegration(it.name, form); });
    body.appendChild(form);
    const r = intState.results[it.name];
    if (r) showResult(form.querySelector("[data-result]"), r);
    return body;
  }

  function formPayload(form, fields) {
    const config = {}, secrets = {}, clear = [];
    (fields || []).forEach((f) => {
      const v = form.elements[f.key] ? form.elements[f.key].value.trim() : "";
      if (f.secret) {
        if (v) secrets[f.key] = v;
        const c = form.elements["__clear__" + f.key];
        if (c && c.checked) clear.push(f.key);
      } else {
        config[f.key] = v;
      }
    });
    return { enabled: !!form.elements.__enabled.checked, config, secrets, clear_secrets: clear };
  }

  function showResult(node, r) {
    if (!node) return;
    node.className = "md:col-span-2 text-caption " + (r.ok ? "text-ok" : "text-down");
    node.textContent = r.ok
      ? `✓ ${r.message} (HTTP ${r.status_code}, ${r.latency_ms} ms)`
      : `✗ ${r.message}${r.status_code ? ` (HTTP ${r.status_code})` : ""}${r.stage === "network" ? " — could not reach the endpoint from the gateway" : ""}`;
  }

  async function api(method, path, body) {
    const res = await fetchWithCsrf("/admin/observability/integrations" + path, {
      method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || "HTTP " + res.status);
    return data;
  }

  function toggleIntegration(name) {
    intState.open = intState.open === name ? null : name;
    renderIntegrationsFresh();
  }

  function renderIntegrationsFresh() {
    $("obsIntegrations").replaceChildren();   // drop the kept card so it is rebuilt
    renderIntegrations();
  }

  async function saveIntegration(name, form) {
    const it = intState.list.find((x) => x.name === name);
    try {
      await api("PUT", "/" + encodeURIComponent(name), formPayload(form, it.fields));
      showToast("Saved", it.title + " updated", "success");
      intState.results[name] = null;
      await loadIntegrations(true);
      renderIntegrationsFresh();
    } catch (e) { showToast("Error", e.message, "error"); }
  }

  async function setIntegrationEnabled(name, enabled) {
    try {
      await api("POST", "/" + encodeURIComponent(name) + "/enabled", { enabled });
      await loadIntegrations(true);
      renderIntegrationsFresh();
    } catch (e) { showToast("Error", e.message, "error"); }
  }

  async function testIntegration(name) {
    const it = intState.list.find((x) => x.name === name);
    const form = document.querySelector(`[data-int-form="${name}"]`);
    const slot = form ? form.querySelector("[data-result]") : null;
    if (slot) { slot.className = "md:col-span-2 text-caption text-ink-muted"; slot.textContent = "Sending a test span…"; }
    try {
      const r = await api("POST", "/" + encodeURIComponent(name) + "/test", form ? formPayload(form, it.fields) : null);
      intState.results[name] = r;
      if (slot) showResult(slot, r);
      else showToast(r.ok ? "Test passed" : "Test failed", `${it.title}: ${r.message}`, r.ok ? "success" : "error");
    } catch (e) { showToast("Error", e.message, "error"); }
  }

  async function removeIntegration(name) {
    const it = intState.list.find((x) => x.name === name);
    if (!window.confirm(`Remove ${it ? it.title : name}? Exporting stops and the stored credentials are deleted.`)) return;
    try {
      await api("DELETE", "/" + encodeURIComponent(name));
      intState.open = null;
      await loadIntegrations(true);
      renderIntegrationsFresh();
    } catch (e) { showToast("Error", e.message, "error"); }
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); showToast("Copied", "Scrape config copied", "success"); }
    catch (e) { showToast("Copy failed", "Select the text and copy it manually.", "error"); }
  }

  function renderSinks(p) {
    loadIntegrations(false);
    renderIntegrations();
    const sinks = p.sinks || [];
    const rows = sinks.map((s) => el("tr", null, [
      td(s.name, "font-medium"),
      td(s.source || DASH),
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
      case "sort": setSort(btn.dataset.scope, btn.dataset.key); render(); break;
      case "int-open": toggleIntegration(btn.dataset.name); break;
      case "int-toggle": setIntegrationEnabled(btn.dataset.name, btn.dataset.enabled === "true"); break;
      case "int-test": testIntegration(btn.dataset.name); break;
      case "int-remove": removeIntegration(btn.dataset.name); break;
      case "int-copy": copyText(btn.dataset.text || ""); break;
      case "view":
        state.view = btn.dataset.view === "list" ? "list" : "cards";
        store.set("obs.groupsView", state.view);
        render();
        break;
      case "filters-clear":
        (btn.dataset.scope === "groups" ? ["fGroupsQ", "fGroupsGpu", "fGroupsTraffic"] : ["fModelsQ", "fModelsServer", "fModelsStatus"])
          .forEach((id) => { const n = $(id); if (n) n.value = ""; });
        if (btn.dataset.scope === "groups") sorts.groups = { key: "name", dir: "asc" };
        render();
        break;
      case "tracemalloc": toggleTracemalloc(); break;
      case "save-overrides": saveOverrides(); break;
      case "host-reset": hostFormReset(); break;
      case "host-edit": hostFormFill(btn.dataset.name); break;
      case "host-delete": deleteHost(btn.dataset.name); break;
      default: break;
    }
  });

  const groupSort = $("fGroupsSort");
  if (groupSort) groupSort.addEventListener("change", () => {
    sorts.groups = { key: groupSort.value, dir: TEXT_KEYS.has(groupSort.value) ? "asc" : "desc" };
    store.set("obs.sorts", JSON.stringify(sorts));
  });
  ["fModelsQ", "fModelsServer", "fModelsStatus", "fGroupsQ", "fGroupsGpu", "fGroupsTraffic", "fGroupsSort", "fIntQ", "fIntState"].forEach((id) => {
    const n = $(id);
    if (n) n.addEventListener(n.tagName === "SELECT" ? "change" : "input", render);
  });

  const hostForm = $("obsHostForm");
  if (hostForm) hostForm.addEventListener("submit", onHostSubmit);
  const linkForm = $("obsLinkForm");
  if (linkForm) linkForm.addEventListener("submit", onLinkSubmit);

  // ── Boot ────────────────────────────────────────────────────────────
  initSortHeaders();
  applySortIndicators("models");
  setTab("models");
  startStream();
})();
