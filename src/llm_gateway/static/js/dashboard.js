// dashboard.js — Dashboard page logic

async function loadMetrics() {
  try {
    const response = await fetch(BASE_URL + "/admin/metrics", {
      credentials: "include",
    });
    if (!response.ok) {
      if (response.status === 401) window.location.href = "/auth/login";
      return;
    }
    const data = await response.json();

    document.getElementById("stat-ips").textContent = data.total_active_ips;
    document.getElementById("stat-active-backends").textContent =
      data.active_backends || 0;
    document.getElementById("stat-total-backends").textContent =
      data.total_backends;

    // Update traffic chart with real per-minute data
    if (data.traffic) updateTrafficChart(data.traffic);

    if (data.total_models_available !== undefined) {
      document.getElementById("stat-total-models").textContent =
        data.total_models_available;
    }


    const ipBody = document.getElementById("ipTableBody");
    ipBody.innerHTML = "";
    if (data.active_ips.length === 0) {
      ipBody.innerHTML = `<tr><td colspan="3" class="p-8 text-center text-slate-500 italic">No active connections</td></tr>`;
    } else {
      const fragment = document.createDocumentFragment();
      data.active_ips.forEach((entry) => {
        const tr = document.createElement("tr");
        tr.className = "group hover:bg-slate-800/30 transition-colors";

        const tdIp = document.createElement("td");
        tdIp.className = "px-3 py-1.5 font-mono text-blue-400 font-medium text-xs";
        tdIp.textContent = entry.ip;

        const tdTime = document.createElement("td");
        tdTime.className = "px-3 py-1.5 text-slate-400 text-xs";
        tdTime.textContent = entry.last_seen_seconds_ago + "s ago";

        const tdStatus = document.createElement("td");
        tdStatus.className = "px-3 py-1.5 text-right text-xs";
        tdStatus.innerHTML = `<span class="inline-flex items-center px-1.5 py-0.5 rounded-full text-micro font-bold bg-emerald-500/10 text-ok border border-emerald-500/20"><span class="w-1 h-1 rounded-full bg-emerald-400 mr-1 animate-pulse"></span>CONNECTED</span>`;

        tr.append(tdIp, tdTime, tdStatus);
        fragment.appendChild(tr);
      });
      ipBody.appendChild(fragment);
    }
  } catch (e) {
    // Without this, a failed load left the tiles at 0 and the table on
    // "Syncing edge data..." indefinitely.
    const ipBody = document.getElementById("ipTableBody");
    if (ipBody) ipBody.innerHTML = `<tr><td colspan="3" class="p-8 text-center text-down text-sm">Couldn't load live metrics. Check the gateway is running, then reload.</td></tr>`;
  }
}

// --- SSE with polling fallback ---
// Try Server-Sent Events first (3s push, no HTTP overhead).
// Fall back to 5s polling if SSE fails (e.g., auth issue, old browser).
let _eventSource = null;
let _metricsTimer = null;

function updateFromSSE(data) {
  document.getElementById("stat-ips").textContent = data.total_active_ips || 0;
  if (data.traffic) updateTrafficChart(data.traffic);

  const ipBody = document.getElementById("ipTableBody");
  if (data.active_ips && data.active_ips.length > 0) {
    ipBody.innerHTML = "";
    const fragment = document.createDocumentFragment();
    data.active_ips.forEach((entry) => {
      const tr = document.createElement("tr");
      tr.className = "group hover:bg-slate-800/30 transition-colors";
      const tdIp = document.createElement("td");
      tdIp.className = "px-3 py-1.5 font-mono text-blue-400 font-medium text-xs";
      tdIp.textContent = entry.ip;
      const tdTime = document.createElement("td");
      tdTime.className = "px-3 py-1.5 text-slate-400 text-xs";
      tdTime.textContent = entry.last_seen_seconds_ago + "s ago";
      const tdStatus = document.createElement("td");
      tdStatus.className = "px-3 py-1.5 text-right text-xs";
      tdStatus.innerHTML = `<span class="inline-flex items-center px-1.5 py-0.5 rounded-full text-micro font-bold bg-emerald-500/10 text-ok border border-emerald-500/20"><span class="w-1 h-1 rounded-full bg-emerald-400 mr-1 animate-pulse"></span>CONNECTED</span>`;
      tr.append(tdIp, tdTime, tdStatus);
      fragment.appendChild(tr);
    });
    ipBody.appendChild(fragment);
  }
}

function startSSE() {
  if (_eventSource) return;
  try {
    _eventSource = new EventSource(BASE_URL + "/admin/metrics/stream");
    _eventSource.onmessage = (e) => {
      try { updateFromSSE(JSON.parse(e.data)); } catch(err) {}
    };
    _eventSource.onerror = () => {
      // SSE failed — fall back to polling
      _eventSource.close();
      _eventSource = null;
      startPolling();
    };
  } catch(e) {
    startPolling();
  }
}

function stopSSE() {
  if (_eventSource) { _eventSource.close(); _eventSource = null; }
}

function startPolling() {
  if (_metricsTimer) return;
  loadMetrics();
  _metricsTimer = setInterval(loadMetrics, 5000);
}

function stopPolling() {
  if (_metricsTimer) { clearInterval(_metricsTimer); _metricsTimer = null; }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) { stopSSE(); stopPolling(); }
  else { startSSE(); }
});

// Initial load via polling (gets full metrics including backends/models),
// then upgrade to SSE for live updates.
loadMetrics();
startSSE();
init24hTrafficChart();
load24hTraffic();

// Traffic chart — updated with real data from /admin/metrics
const ctx = document.getElementById("trafficChart").getContext("2d");
const trafficChart = new Chart(ctx, {
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
        ticks: { color: "#64748b", font: { size: 10 } },
      },
      x: {
        grid: { color: "rgba(255, 255, 255, 0.05)" },
        ticks: { color: "#64748b", font: { size: 10 }, maxRotation: 0 },
      },
    },
  },
});

function updateTrafficChart(traffic) {
  if (!traffic || !traffic.length) return;
  trafficChart.data.labels = traffic.map(t => t.label);
  trafficChart.data.datasets[0].data = traffic.map(t => t.count);
  trafficChart.update();
}

// --- 24-Hour Traffic Activity & Observability ---
let traffic24hChart = null;

function init24hTrafficChart() {
  const canvas = document.getElementById("traffic24hChart");
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
          ticks: { color: "#64748b", font: { size: 10 } },
          title: { display: true, text: "Requests", color: "#64748b" },
        },
        yRps: {
          beginAtZero: true,
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#64748b", font: { size: 10 } },
          title: { display: true, text: "RPS", color: "#64748b" },
        },
        x: {
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#64748b", font: { size: 10 }, maxRotation: 0 },
        },
      },
    },
  });
}

async function load24hTraffic() {
  const canvas = document.getElementById("traffic24hChart");
  if (!canvas) return;
  try {
    let resp = await fetch(BASE_URL + "/admin/dashboard/traffic-24h", { credentials: "include" });
    if (!resp.ok) {
      resp = await fetch(BASE_URL + "/admin/observability/history?timeframe=24h", { credentials: "include" });
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
  const peakRpsEl = document.getElementById("dash24hPeakRps");
  const p50TtftEl = document.getElementById("dash24hP50Ttft");
  const totalTokensEl = document.getElementById("dash24hTotalTokens");

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
        const timeKey = pt.bucket_time || pt.time || pt.label;
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

  if (peakRpsEl) peakRpsEl.textContent = (typeof peakRps === "number" && Number.isFinite(peakRps)) ? peakRps.toFixed(2) + " RPS" : "—";
  if (p50TtftEl) p50TtftEl.textContent = (typeof p50Ttft === "number" && Number.isFinite(p50Ttft)) ? p50Ttft.toFixed(1) + " ms" : "—";
  if (totalTokensEl) totalTokensEl.textContent = (typeof totalTokens === "number" && Number.isFinite(totalTokens)) ? new Intl.NumberFormat().format(totalTokens) : "—";

  if (traffic24hChart && buckets.length) {
    const labels = buckets.map(b => {
      const lbl = b.label || b.time || b.bucket_time || "";
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
