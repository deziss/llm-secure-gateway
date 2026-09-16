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
        tdIp.className = "p-4 font-mono text-blue-400 font-medium";
        tdIp.textContent = entry.ip;

        const tdTime = document.createElement("td");
        tdTime.className = "p-4 text-slate-400 text-xs";
        tdTime.textContent = entry.last_seen_seconds_ago + "s ago";

        const tdStatus = document.createElement("td");
        tdStatus.className = "p-4 text-right";
        tdStatus.innerHTML = `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><span class="w-1 h-1 rounded-full bg-emerald-400 mr-1.5 animate-pulse"></span>CONNECTED</span>`;

        tr.append(tdIp, tdTime, tdStatus);
        fragment.appendChild(tr);
      });
      ipBody.appendChild(fragment);
    }
  } catch (e) {
    // metrics load failed silently
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
      tdIp.className = "p-4 font-mono text-blue-400 font-medium";
      tdIp.textContent = entry.ip;
      const tdTime = document.createElement("td");
      tdTime.className = "p-4 text-slate-400 text-xs";
      tdTime.textContent = entry.last_seen_seconds_ago + "s ago";
      const tdStatus = document.createElement("td");
      tdStatus.className = "p-4 text-right";
      tdStatus.innerHTML = `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><span class="w-1 h-1 rounded-full bg-emerald-400 mr-1.5 animate-pulse"></span>CONNECTED</span>`;
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
