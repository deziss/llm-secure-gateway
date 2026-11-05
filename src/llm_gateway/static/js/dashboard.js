// dashboard.js — Dashboard page logic
"use strict";

let trafficChart = null;
let traffic24hChart = null;
let _eventSource = null;
let _metricsTimer = null;

// Helper to safely get element
const getEl = (id) => document.getElementById(id);

function initTrafficChart() {
  const canvas = getEl("trafficChart");
  if (!canvas || !window.Chart) return;
  const ctx = canvas.getContext("2d");
  trafficChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Requests / min",
          data: [],
          borderColor: "rgb(59, 130, 246)",
          backgroundColor: "rgba(59, 130, 246, 0.08)",
          fill: true,
          tension: 0.3,
          pointRadius: 2,
          pointBackgroundColor: "rgb(59, 130, 246)",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#64748b", font: { size: 10 }, precision: 0 },
        },
        x: {
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#64748b", font: { size: 10 }, maxRotation: 0 },
        },
      },
    },
  });
}

function updateTrafficChart(traffic) {
  if (!traffic || !traffic.length || !trafficChart) return;
  trafficChart.data.labels = traffic.map((t) => t.label);
  trafficChart.data.datasets[0].data = traffic.map((t) => t.count);
  trafficChart.update();
}

function init24hTrafficChart() {
  const canvas = getEl("traffic24hChart");
  if (!canvas || !window.Chart) return;
  const ctx24 = canvas.getContext("2d");
  traffic24hChart = new Chart(ctx24, {
    data: {
      labels: [],
      datasets: [
        {
          type: "bar",
          label: "Requests",
          data: [],
          backgroundColor: "rgba(99, 102, 241, 0.75)",
          borderColor: "rgb(99, 102, 241)",
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
      animation: { duration: 400 },
      plugins: {
        legend: {
          display: true,
          position: "top",
          align: "end",
          labels: { color: "#94a3b8", font: { size: 11 }, boxWidth: 10 },
        },
        tooltip: {
          backgroundColor: "#0f172a",
          titleColor: "#f8fafc",
          bodyColor: "#e2e8f0",
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderWidth: 1,
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#64748b", font: { size: 10 }, precision: 0 },
          title: { display: true, text: "Requests", color: "#64748b", font: { size: 10 } },
        },
        yRps: {
          beginAtZero: true,
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#64748b", font: { size: 10 } },
          title: { display: true, text: "RPS", color: "#64748b", font: { size: 10 } },
        },
        x: {
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#64748b", font: { size: 10 }, maxRotation: 0 },
        },
      },
    },
  });
}

function renderActiveIps(activeIps) {
  const ipBody = getEl("ipTableBody");
  if (!ipBody) return;
  ipBody.innerHTML = "";

  if (!activeIps || activeIps.length === 0) {
    ipBody.innerHTML = `<tr><td colspan="3" class="p-5 text-center text-ink-muted text-xs"><i class="fas fa-satellite-dish mr-1 text-slate-500"></i> No external clients connected yet</td></tr>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  activeIps.forEach((entry) => {
    const tr = document.createElement("tr");
    tr.className = "group hover:bg-surface-inset transition-colors";

    const tdIp = document.createElement("td");
    tdIp.className = "px-3.5 py-2 font-mono text-accent font-medium text-xs";
    tdIp.textContent = entry.ip;

    const tdTime = document.createElement("td");
    tdTime.className = "px-3.5 py-2 text-ink-muted text-xs";
    tdTime.textContent = (entry.last_seen_seconds_ago || 0) + "s ago";

    const tdStatus = document.createElement("td");
    tdStatus.className = "px-3.5 py-2 text-right text-xs";
    tdStatus.innerHTML = `<span class="inline-flex items-center px-1.5 py-0.5 rounded-full text-micro font-bold bg-ok/10 text-ok border border-ok/20"><span class="w-1.5 h-1.5 rounded-full bg-ok mr-1 animate-pulse"></span>ACTIVE</span>`;

    tr.append(tdIp, tdTime, tdStatus);
    fragment.appendChild(tr);
  });
  ipBody.appendChild(fragment);
}

async function loadMetrics() {
  try {
    const response = await fetch("/admin/metrics", {
      credentials: "include",
    });
    if (!response.ok) {
      if (response.status === 401) {
        window.location.href = "/auth/login";
        return;
      }
      return;
    }
    const data = await response.json();

    const statIps = getEl("stat-ips");
    if (statIps && data.total_active_ips !== undefined) {
      statIps.textContent = data.total_active_ips;
    }

    const statActiveB = getEl("stat-active-backends");
    if (statActiveB && data.active_backends !== undefined) {
      statActiveB.textContent = data.active_backends;
    }

    const statTotalB = getEl("stat-total-backends");
    if (statTotalB && data.total_backends !== undefined) {
      statTotalB.textContent = data.total_backends;
    }

    if (data.total_models_available !== undefined) {
      const statTotalM = getEl("stat-total-models");
      if (statTotalM) statTotalM.textContent = data.total_models_available;
    }

    if (data.traffic) updateTrafficChart(data.traffic);
    renderActiveIps(data.active_ips);
  } catch (e) {
    console.warn("Telemetry metrics sync deferred:", e);
    const ipBody = getEl("ipTableBody");
    if (ipBody && (!ipBody.children.length || ipBody.innerText.includes("Syncing"))) {
      ipBody.innerHTML = `<tr><td colspan="3" class="p-5 text-center text-ink-muted text-xs"><i class="fas fa-satellite-dish mr-1 text-slate-500"></i> Listening for edge connections…</td></tr>`;
    }
  }
}

function updateFromSSE(data) {
  if (!data) return;
  const statIps = getEl("stat-ips");
  if (statIps && data.total_active_ips !== undefined) {
    statIps.textContent = data.total_active_ips;
  }
  if (data.traffic) updateTrafficChart(data.traffic);
  if (data.active_ips) renderActiveIps(data.active_ips);
}

function startSSE() {
  if (_eventSource) return;
  try {
    _eventSource = new EventSource("/admin/metrics/stream");
    _eventSource.onmessage = (e) => {
      try { updateFromSSE(JSON.parse(e.data)); } catch (err) {}
    };
    _eventSource.onerror = () => {
      if (_eventSource) {
        _eventSource.close();
        _eventSource = null;
      }
      startPolling();
    };
  } catch (e) {
    startPolling();
  }
}

function stopSSE() {
  if (_eventSource) {
    _eventSource.close();
    _eventSource = null;
  }
}

function startPolling() {
  if (_metricsTimer) return;
  loadMetrics();
  _metricsTimer = setInterval(loadMetrics, 5000);
}

function stopPolling() {
  if (_metricsTimer) {
    clearInterval(_metricsTimer);
    _metricsTimer = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopSSE();
    stopPolling();
  } else {
    startSSE();
  }
});

async function load24hTraffic() {
  const canvas = getEl("traffic24hChart");
  if (!canvas) return;
  try {
    let resp = await fetch("/admin/dashboard/traffic-24h", { credentials: "include" });
    if (!resp.ok) {
      resp = await fetch("/admin/observability/history?timeframe=24h", { credentials: "include" });
    }
    if (!resp.ok) return;
    const data = await resp.json();
    render24hTraffic(data);
  } catch (e) {
    console.debug("24h traffic telemetry unavailable", e);
  }
}

function render24hTraffic(data) {
  if (!data) return;
  const peakRpsEl = getEl("dash24hPeakRps");
  const p50TtftEl = getEl("dash24hP50Ttft");
  const totalTokensEl = getEl("dash24hTotalTokens");

  let buckets = [];
  if (Array.isArray(data)) {
    buckets = data;
  } else if (Array.isArray(data.hourly)) {
    buckets = data.hourly;
  } else if (Array.isArray(data.traffic)) {
    buckets = data.traffic;
  } else if (Array.isArray(data.series)) {
    buckets = data.series;
  } else if (Array.isArray(data.buckets)) {
    buckets = data.buckets;
  } else if (Array.isArray(data.models)) {
    const bucketMap = new Map();
    for (const m of data.models) {
      for (const pt of (m.series || m.timeseries || m.buckets || [])) {
        const timeKey = pt.bucket_time || pt.time || pt.label || pt.hour || pt.timestamp;
        if (!timeKey) continue;
        const cur = bucketMap.get(timeKey) || { label: timeKey, count: 0, rps: 0, tokens: 0, ttfts: [] };
        cur.count += (Number(pt.requests) || 0);
        cur.rps += (Number(pt.rps) || 0);
        cur.tokens += (Number(pt.total_tokens) || ((Number(pt.prompt_tokens) || 0) + (Number(pt.completion_tokens) || 0)));
        if (pt.ttft_p50_ms !== undefined && pt.ttft_p50_ms !== null) cur.ttfts.push(Number(pt.ttft_p50_ms));
        bucketMap.set(timeKey, cur);
      }
    }
    buckets = Array.from(bucketMap.values());
  }

  // Calculate metrics
  let peakRps = data.peak_rps ?? data.summary?.peak_rps;
  if (peakRps === undefined && buckets.length) {
    peakRps = Math.max(...buckets.map(b => Number(b.rps || ((b.requests || b.count) ? (b.requests || b.count) / 3600 : 0))), 0);
  }

  let p50Ttft = data.avg_ttft_ms ?? data.p50_ttft_ms ?? data.summary?.p50_ttft_ms;
  if (p50Ttft === undefined && buckets.length) {
    const allTtfts = buckets.flatMap(b => b.ttfts || (b.ttft_p50_ms !== undefined ? [Number(b.ttft_p50_ms)] : (b.avg_ttft_ms !== undefined ? [Number(b.avg_ttft_ms)] : [])));
    if (allTtfts.length) {
      allTtfts.sort((a, b) => a - b);
      p50Ttft = allTtfts[Math.floor(allTtfts.length / 2)];
    }
  }

  let totalTokens = data.total_tokens ?? data.summary?.total_tokens;
  if (totalTokens === undefined && buckets.length) {
    totalTokens = buckets.reduce((s, b) => s + (Number(b.tokens || b.total_tokens || 0)), 0);
  } else if (totalTokens === undefined && Array.isArray(data.models)) {
    totalTokens = data.models.reduce((s, m) => s + (Number(m.total_tokens || 0)), 0);
  }

  if (peakRpsEl) peakRpsEl.textContent = (typeof peakRps === "number" && Number.isFinite(peakRps)) ? peakRps.toFixed(2) + " RPS" : "0.00 RPS";
  if (p50TtftEl) p50TtftEl.textContent = (typeof p50Ttft === "number" && Number.isFinite(p50Ttft)) ? p50Ttft.toFixed(1) + " ms" : "—";
  if (totalTokensEl) totalTokensEl.textContent = (typeof totalTokens === "number" && Number.isFinite(totalTokens)) ? new Intl.NumberFormat().format(totalTokens) : "0";

  if (traffic24hChart && buckets.length) {
    const labels = buckets.map(b => {
      const lbl = b.label || b.time || b.bucket_time || b.hour || b.timestamp || "";
      if (lbl.includes("T")) {
        try {
          const d = new Date(lbl);
          return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch (e) {
          return lbl;
        }
      }
      return lbl;
    });
    const reqCounts = buckets.map(b => Number(b.requests ?? b.count ?? 0));
    const rpsValues = buckets.map(b => Number(b.rps ?? ((b.requests || b.count) ? (b.requests || b.count) / 3600 : 0)));

    traffic24hChart.data.labels = labels;
    traffic24hChart.data.datasets[0].data = reqCounts;
    traffic24hChart.data.datasets[1].data = rpsValues;
    traffic24hChart.update();
  }
}

// Initialise charts and start live listeners
initTrafficChart();
init24hTrafficChart();
loadMetrics();
startSSE();
load24hTraffic();

