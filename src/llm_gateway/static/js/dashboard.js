// dashboard.js — below-the-fold live widgets: 15-minute live-traffic stream,
// edge connectivity table, and the range-scoped telemetry timeline.
//
// Transport: one SSE connection while the tab is visible; if SSE fails (e.g.
// an HTTP/1.1 connection-limit squeeze or a proxy that buffers) we fall back
// to /admin/metrics/live through the shared DashCache scheduler and retry SSE
// periodically. The scheduler also pauses for hidden/offline and the user's
// Live pause preference — this file never owns a setInterval of its own.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DC = window.DashCache;
  const DCW = () => window.DashWidgets;

  const fmtInt = new Intl.NumberFormat();
  const tokenColor = (name, alpha) => {
    const W = DCW();
    if (W) return W.getTokenColor(name, alpha);
    return "rgb(99, 102, 241)";
  };

  // ── Live traffic chart (last 15 minutes) ──────────────────────────────
  let trafficChart = null;

  function initTrafficChart() {
    const canvas = $("trafficChart");
    if (!canvas || !window.Chart) return;
    trafficChart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "Requests / min",
          data: [],
          borderColor: tokenColor("--accent"),
          backgroundColor: tokenColor("--accent", 0.08),
          fill: true,
          tension: 0.3,
          pointRadius: 2,
          pointBackgroundColor: tokenColor("--accent"),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: DC && DC.reducedMotion() ? false : { duration: 300 },
        plugins: { legend: { display: false } },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: tokenColor("--hairline", 0.6) },
            ticks: { color: tokenColor("--ink-muted"), font: { size: 10 }, precision: 0 },
          },
          x: {
            grid: { display: false },
            ticks: { color: tokenColor("--ink-muted"), font: { size: 10 }, maxRotation: 0 },
          },
        },
      },
    });
  }

  function updateTrafficChart(traffic) {
    if (!trafficChart || !Array.isArray(traffic)) return;
    trafficChart.data.labels = traffic.map((t) => t.label);
    trafficChart.data.datasets[0].data = traffic.map((t) => t.count || 0);
    trafficChart.update("none"); // live tick: repaint, don't animate
  }

  // ── Edge connectivity table (keyed rows; never innerHTML with data) ───
  let ipRowTemplate = null;
  const ipRows = new Map(); // ip -> {tr, time}

  function ipEmpty(msg) {
    const row = $("ipEmptyRow");
    if (row) {
      const msgEl = row.querySelector("[data-ip-empty-msg]") || row.querySelector("td");
      if (msgEl) msgEl.textContent = (msg || "No external clients connected yet");
      return row;
    }
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "p-5 text-center text-ink-muted text-xs";
    td.textContent = " " + (msg || "No external clients connected yet");
    tr.append(td);
    return tr;
  }

  function makeIpRow(ip) {
    if (!ipRowTemplate) {
      ipRowTemplate = $("ipRowTemplate");
    }
    const tr = ipRowTemplate ? ipRowTemplate.content.firstElementChild.cloneNode(true) : document.createElement("tr");
    const addr = tr.querySelector("[data-ip-address]");
    const time = tr.querySelector("[data-ip-time]");
    if (addr) addr.textContent = ip; // data-free of innerHTML; textContent only
    return { tr, addr, time };
  }

  function renderActiveIps(activeIps) {
    const body = $("ipTableBody");
    if (!body) return;
    if (window.dashEdgeSkeleton) { window.dashEdgeSkeleton(); window.dashEdgeSkeleton = null; }
    const list = Array.isArray(activeIps) ? activeIps : [];
    const seen = new Set();
    for (const entry of list) {
      if (!entry || typeof entry.ip !== "string") continue;
      seen.add(entry.ip);
      let row = ipRows.get(entry.ip);
      if (!row) {
        row = makeIpRow(entry.ip);
        ipRows.set(entry.ip, row);
        const emptyRow = $("ipEmptyRow");
        if (emptyRow && emptyRow.parentNode === body) body.removeChild(emptyRow);
        body.append(row.tr);
      }
      if (row.time) row.time.textContent = (Number(entry.last_seen_seconds_ago) || 0) + "s ago";
    }
    for (const [ip, row] of ipRows) {
      if (!seen.has(ip)) { row.tr.remove(); ipRows.delete(ip); }
    }
    if (!ipRows.size && !$("ipEmptyRow")) body.append(ipEmpty());
    const statIps = $("stat-ips");
    if (statIps) statIps.textContent = list.length ? String(list.length) : "0";
  }

  function showEdgeEmpty(msg) {
    const body = $("ipTableBody");
    if (!body || ipRows.size) return;
    body.replaceChildren(ipEmpty(msg));
  }

  // ── KPI tiles fed by the richer metrics payload ───────────────────────
  function renderStats(data) {
    if (data.total_active_ips !== undefined) $("stat-ips") && ($("stat-ips").textContent = data.total_active_ips);
    if (data.active_backends !== undefined) $("stat-active-backends") && ($("stat-active-backends").textContent = data.active_backends);
    if (data.total_backends !== undefined) $("stat-total-backends") && ($("stat-total-backends").textContent = data.total_backends);
    if (data.total_models_available !== undefined) $("stat-total-models") && ($("stat-total-models").textContent = data.total_models_available);
    if (data.traffic) updateTrafficChart(data.traffic);
    if (data.active_ips) renderActiveIps(data.active_ips);
  }

  // ── Transport: SSE primary, scheduler-driven poll fallback ────────────
  let es = null;
  let usePoll = false;
  let esErrors = 0;
  let transportMode = "off"; // "sse" | "poll" | "off"
  let statsTask = null;

  async function pollLive() {
    const res = await fetch("/admin/metrics/live", { credentials: "include" });
    if (res.status === 401) { if (window.handleSessionExpiry) window.handleSessionExpiry(); throw new Error("session"); }
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderStats(await res.json());
  }

  async function pollFullStats() {
    const res = await fetch("/admin/metrics", { credentials: "include" });
    if (res.status === 401) { if (window.handleSessionExpiry) window.handleSessionExpiry(); throw new Error("session"); }
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderStats(await res.json());
    if (window.dashEdgeSkeleton) { window.dashEdgeSkeleton(); window.dashEdgeSkeleton = null; }
  }

  function openTransport() {
    const W = DCW();
    if (!W || !W.scheduler || W.prefs.live.enabled === false) return;
    if (transportMode === "sse" && es && es.readyState !== 2) return;
    if (usePoll) {
      if (transportMode !== "poll") { transportMode = "poll"; pollLive().catch(() => {}); }
      return;
    }
    try {
      // withCredentials so the session cookie rides along on same-origin SSE.
      es = new EventSource("/admin/metrics/stream", { withCredentials: true });
      transportMode = "sse";
      es.onmessage = (e) => {
        esErrors = 0;
        try { renderStats(JSON.parse(e.data)); } catch (err) { /* ignore malformed frame */ }
      };
      es.onerror = () => {
        esErrors++;
        if (es) { es.close(); es = null; }
        transportMode = "off";
        if (esErrors >= 2) {
          usePoll = true; // proxy buffers the stream / connection cap reached
          transportMode = "poll";
          pollLive().catch(() => {});
        } else {
          // transient blip: retry via SSE soon; don't hammer
          setTimeout(() => { if (!es && !usePoll) openTransport(); }, 4000);
        }
      };
    } catch (e) {
      usePoll = true;
      transportMode = "poll";
      pollLive().catch(() => {});
    }
  }

  function closeTransport() {
    if (es) { es.close(); es = null; }
    transportMode = "off";
  }

  // A slow scheduler task keeps transport decisions aligned with Live/pause
  // state; SSE pushes arrive on their own.
  function setupTransport(W) {
    W.scheduler.add(async () => {
      if (W.prefs.live.enabled === false) { closeTransport(); return; }
      if (usePoll && transportMode !== "poll") { usePoll = false; esErrors = 0; } // give SSE another chance
      if (transportMode === "off" || (transportMode === "sse" && (!es || es.readyState === 2))) {
        openTransport();
        if (!ipRows.size) await pollLive().catch(() => {});
      }
    }, 30000, { firstDelayMs: 100, backoff: true });

    // First load + occasional richer snapshot (backend/model counts when
    // Mission Control is enabled; the live endpoint omits them).
    statsTask = W.scheduler.add(async () => {
      try { await pollFullStats(); }
      catch (e) {
        if (e && e.message === "session") throw e;
        showEdgeEmpty("Listening for edge connections…");
        throw e;
      }
    }, 60000, { firstDelayMs: 300, backoff: true });
  }

  // ── Telemetry timeline (range-scoped, DB-backed, cached) ──────────────
  let chart24 = null;

  function init24hChart() {
    const canvas = $("traffic24hChart");
    if (!canvas || !window.Chart) return;
    chart24 = new Chart(canvas.getContext("2d"), {
      data: {
        labels: [],
        datasets: [
          {
            type: "bar",
            label: "Requests",
            data: [],
            backgroundColor: tokenColor("--accent", 0.75),
            borderColor: tokenColor("--accent"),
            borderWidth: 1,
            borderRadius: 3,
            yAxisID: "y",
          },
          {
            type: "line",
            label: "RPS",
            data: [],
            borderColor: "rgb(56, 189, 248)",
            backgroundColor: "rgba(56, 189, 248, 0.1)",
            tension: 0.3,
            pointRadius: 2,
            yAxisID: "yRps",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: DC && DC.reducedMotion() ? false : { duration: 400 },
        plugins: {
          legend: { display: true, position: "top", align: "end", labels: { color: tokenColor("--ink-muted"), font: { size: 11 }, boxWidth: 10 } },
          tooltip: { mode: "index", intersect: false },
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: tokenColor("--hairline", 0.6) },
            ticks: { color: tokenColor("--ink-muted"), font: { size: 10 }, precision: 0 },
            title: { display: true, text: "Requests", color: tokenColor("--ink-muted"), font: { size: 10 } },
          },
          yRps: {
            beginAtZero: true,
            position: "right",
            grid: { drawOnChartArea: false },
            ticks: { color: tokenColor("--ink-muted"), font: { size: 10 } },
            title: { display: true, text: "RPS", color: tokenColor("--ink-muted"), font: { size: 10 } },
          },
          x: {
            grid: { display: false },
            ticks: { color: tokenColor("--ink-muted"), font: { size: 10 }, maxRotation: 0, autoSkip: true },
          },
        },
      },
    });
  }

  function bucketLabel(b, granularity) {
    const raw = b.timestamp || b.hour;
    const d = window.parseIsoDate ? window.parseIsoDate(raw) : new Date(raw);
    if (!d || Number.isNaN(d.getTime())) return String(raw || "");
    if (granularity === "day") return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
    return window.formatTime ? window.formatTime(d) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function render24hTraffic(d) {
    if (window.dashTimelineSkeleton) { window.dashTimelineSkeleton(); window.dashTimelineSkeleton = null; }
    if (!d || !Array.isArray(d.hourly)) return;
    const set = (id, txt) => { const n = $(id); if (n) n.textContent = txt; };
    set("dash24hPeakRps", (Number(d.peak_rps) || 0).toFixed(2) + " RPS");
    const p50 = d.ttft_p50_ms != null ? d.ttft_p50_ms
      : (d.hourly.find((b) => b.ttft_p50_ms != null) || {}).ttft_p50_ms;
    set("dash24hP50Ttft", p50 != null ? Math.round(p50) + " ms" : "—");
    set("dash24hTotalTokens", fmtInt.format(Number(d.total_tokens) || 0));

    if (!chart24) init24hChart();
    if (!chart24) return;
    const g = d.granularity || "hour";
    chart24.data.labels = d.hourly.map((b) => bucketLabel(b, g));
    chart24.data.datasets[0].data = d.hourly.map((b) => Number(b.requests) || 0);
    const div = g === "minute" ? 60 : g === "day" ? 86400 : 3600;
    chart24.data.datasets[1].data = d.hourly.map((b) => +((Number(b.requests) || 0) / div).toFixed(3));
    chart24.update(painted24 ? "none" : undefined);
    painted24 = true;
  }
  let painted24 = false;

  // Public: called by dashboard-summary.js after its render and by the slow
  // scheduler tick. opts.force bypasses the cache TTL (range switch).
  window.dashTick24h = async function (opts) {
    opts = opts || {};
    if (!DC || typeof DC.swr !== "function") return;
    const W = DCW();
    const range = ((W && W.prefs.timeRange) || "24h");
    const canvas = $("traffic24hChart");
    if (!canvas) return;
    if (!painted24) DC.skeletonOn(canvas.closest("section") || canvas, 250);
    try {
      const ttl = opts.force ? 0 : 60000;
      const r = await DC.swr("/admin/dashboard/traffic-24h?range=" + encodeURIComponent(range),
        { ttlMs: ttl, user: String(window.dashboardUser || "").toLowerCase() });
      if (r && r.data) render24hTraffic(r.data);
    } catch (e) {
      if (e && e.message === "session") return;
      console.debug("telemetry timeline refresh deferred:", e);
    }
  };

  // dashboard-summary.js hands us the summary payload; the timeline needs the
  // dedicated traffic endpoint, so piggyback a (cheap, cached) tick.
  window.dashRenderSecondary = function () {
    return window.dashTick24h();
  };

  // ── Theme: rebuild charts so colours follow tokens ────────────────────
  document.addEventListener("dash:themerebuild", () => {
    if (trafficChart) { trafficChart.destroy(); trafficChart = null; }
    if (chart24) { chart24.destroy(); chart24 = null; painted24 = false; }
    initTrafficChart();
    init24hChart();
    pollLive().catch(() => {});
    window.dashTick24h({ force: true });
  });

  // ── Boot ──────────────────────────────────────────────────────────────
  if (!$("trafficChart")) return;
  if (!DC) { console.warn("dash-cache.js must load before dashboard.js"); return; }
  initTrafficChart();
  init24hChart();

  const ready = () => {
    const W = DCW();
    if (!W || !W.scheduler) { setTimeout(ready, 50); return; }
    setupTransport(W);
    // Skeletons for below-the-fold widgets until their first render.
    const edgeSection = $("ipTableBody") && $("ipTableBody").closest("section");
    if (edgeSection) {
      window.dashEdgeSkeleton = () => DC.skeletonOff(edgeSection);
      DC.skeletonOn(edgeSection, 250);
    }
    const tl = $("traffic24hChart") && $("traffic24hChart").closest("section");
    if (tl) {
      window.dashTimelineSkeleton = () => DC.skeletonOff(tl);
      DC.skeletonOn(tl, 250);
    }
  };
  if (window.DashWidgets) ready();
  else document.addEventListener("DOMContentLoaded", ready);
})();
