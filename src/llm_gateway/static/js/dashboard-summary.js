// dashboard-summary.js — the dashboard's summary cards (spend, tokens, top
// models, alerts, cache, security, backend health).
//
// One request to /admin/dashboard/summary every 30 s, paused while the tab is
// hidden. Live traffic stays on dashboard.js's SSE stream. All values are
// written with textContent / DOM APIs: owner names, key prefixes and audit
// identities are user-controlled.

(function () {
  "use strict";

  const REFRESH_MS = 30000;
  const $ = (id) => document.getElementById(id);
  if (!$("dashSummary")) return;

  const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const integer = new Intl.NumberFormat();

  function money(v) {
    v = Number(v) || 0;
    if (v === 0) return "$0";
    if (Math.abs(v) < 0.01) return "<$0.01";
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: "USD", maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0,
    }).format(v);
  }

  function relTime(iso) {
    const s = Math.round((Date.parse(iso) - Date.now()) / 1000);
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    const a = Math.abs(s);
    if (a < 60) return rtf.format(s, "second");
    if (a < 3600) return rtf.format(Math.round(s / 60), "minute");
    if (a < 86400) return rtf.format(Math.round(s / 3600), "hour");
    return rtf.format(Math.round(s / 86400), "day");
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function empty(list, text) {
    list.replaceChildren(el("li", "text-sm text-ink-muted py-1", text));
  }

  function tokenColor(name, alpha) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v ? `rgb(${v.split(/\s+/).join(", ")}${alpha !== undefined ? ", " + alpha : ""})` : "#6366f1";
  }

  // ── KPI cards ─────────────────────────────────────────────────────────
  function renderKpis(spend) {
    const m = spend.month, t = spend.today, prev = spend.prev_month_to_date;
    $("stat-spend-month").textContent = money(m.cost_usd);
    $("stat-spend-today").textContent = money(t.cost_usd);

    const delta = $("stat-spend-delta");
    if (prev.cost_usd > 0) {
      const pct = Math.round(((m.cost_usd - prev.cost_usd) / prev.cost_usd) * 100);
      delta.textContent = `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct)}% vs last month`;
      // Spending more is the thing to watch, so up is the warning colour.
      delta.className = "tabular-nums " + (pct > 0 ? "text-warn" : "text-ok");
    } else {
      delta.textContent = "";
    }

    const total = t.input_tokens + t.output_tokens;
    $("stat-tokens-today").textContent = compact.format(total);
    $("stat-tokens-in").textContent = compact.format(t.input_tokens);
    $("stat-tokens-out").textContent = compact.format(t.output_tokens);
    $("stat-tokens-in-bar").style.width = total ? (t.input_tokens / total) * 100 + "%" : "0%";
    $("stat-tokens-out-bar").style.width = total ? (t.output_tokens / total) * 100 + "%" : "0%";
    $("stat-requests-today").textContent = `${integer.format(t.requests)} request${t.requests === 1 ? "" : "s"}`;
  }

  // ── Spend chart ───────────────────────────────────────────────────────
  let spendChart = null;
  function renderSpendChart(daily) {
    const canvas = $("spendChart");
    if (!canvas || typeof Chart === "undefined") return;
    const labels = daily.map((d) =>
      new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })
    );
    const values = daily.map((d) => d.cost_usd);
    if (spendChart) {
      spendChart.data.labels = labels;
      spendChart.data.datasets[0].data = values;
      spendChart.update("none");
      return;
    }
    const muted = tokenColor("--ink-muted");
    spendChart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Spend (USD)", data: values, borderRadius: 3, maxBarThickness: 28,
          backgroundColor: tokenColor("--accent", 0.75), hoverBackgroundColor: tokenColor("--accent"),
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => `${money(c.parsed.y)} · ${integer.format(daily[c.dataIndex].requests)} req` } },
        },
        scales: {
          y: { beginAtZero: true, grid: { color: tokenColor("--hairline", 0.6) }, ticks: { color: muted, font: { size: 10 }, callback: (v) => money(v) } },
          x: { grid: { display: false }, ticks: { color: muted, font: { size: 10 }, maxRotation: 0, autoSkip: true } },
        },
      },
    });
  }
  // Chart colours come from CSS tokens; rebuild when the theme flips.
  document.addEventListener("admin:themechange", () => {
    if (spendChart) { spendChart.destroy(); spendChart = null; }
    if (lastData) requestAnimationFrame(() => renderSpendChart(lastData.spend.daily));
  });

  // ── Top models ────────────────────────────────────────────────────────
  function renderTopModels(rows) {
    const list = $("dashTopModels");
    if (!rows.length) return empty(list, "No requests this month yet.");
    const max = rows[0].requests || 1;
    list.replaceChildren(...rows.map((r) => {
      const li = el("li", "min-w-0");
      const top = el("div", "flex items-baseline justify-between gap-3 text-sm");
      const name = el("span", "truncate font-medium text-ink font-mono", r.model);
      name.title = r.model;
      top.append(name, el("span", "shrink-0 text-caption text-ink-muted tabular-nums",
        `${integer.format(r.requests)} req · ${compact.format(r.tokens)} tok · ${money(r.cost_usd)}`));
      const bar = el("div", "mt-1 h-1.5 rounded-full bg-surface-inset overflow-hidden");
      const fill = el("div", "h-full rounded-full bg-accent");
      fill.style.width = Math.max(2, (r.requests / max) * 100) + "%";
      bar.append(fill);
      li.append(top, bar);
      return li;
    }));
  }

  // ── Needs attention ───────────────────────────────────────────────────
  function renderAlerts(alerts) {
    const list = $("dashAlerts");
    const items = [];
    alerts.budgets.forEach((b) => {
      const over = b.ratio >= 1;
      const li = el("li", "py-2 first:pt-0 last:pb-0");
      const row = el("div", "flex items-center justify-between gap-2 text-sm");
      const label = el("span", "min-w-0 truncate text-ink");
      label.append(el("span", "text-ink-muted", b.kind === "key" ? "Key " : "Project "), el("span", "font-medium", b.label));
      row.append(label, el("span", `shrink-0 text-caption font-semibold tabular-nums ${over ? "text-down" : "text-warn"}`,
        `${Math.round(b.ratio * 100)}%`));
      const bar = el("div", "mt-1 h-1.5 rounded-full bg-surface-inset overflow-hidden");
      const fill = el("div", `h-full rounded-full ${over ? "bg-down" : "bg-warn"}`);
      fill.style.width = Math.min(100, b.ratio * 100) + "%";
      bar.append(fill);
      li.append(row, bar, el("p", "mt-1 text-caption text-ink-muted tabular-nums",
        `${money(b.spent_usd)} of ${money(b.budget_usd)} monthly budget`));
      items.push(li);
    });
    alerts.expiring_keys.forEach((k) => {
      const li = el("li", "py-2 first:pt-0 last:pb-0 flex items-center justify-between gap-2 text-sm");
      const label = el("span", "min-w-0 truncate text-ink");
      label.append(el("span", "text-ink-muted", "Key "), el("span", "font-medium font-mono", k.label),
        el("span", "text-ink-muted", " · " + k.owner_id));
      li.append(label, el("span", "shrink-0 text-caption font-semibold text-warn", "expires " + relTime(k.expires_at)));
      items.push(li);
    });
    $("dashAlertCount").textContent = items.length ? String(items.length) : "";
    if (!items.length) return empty(list, "All clear: no budgets above 80% and no keys expiring within 7 days.");
    list.replaceChildren(...items);
  }

  // ── Cache ─────────────────────────────────────────────────────────────
  function renderCache(c) {
    $("dashCacheHits").textContent = compact.format(c.hits);
    $("dashCacheEntries").textContent = compact.format(c.entries);
    $("dashCacheSaved").textContent = compact.format(c.tokens_saved);
  }

  // ── Security ──────────────────────────────────────────────────────────
  function renderSecurity(s) {
    $("dashSecDenied").textContent = integer.format(s.denied_24h);
    $("dashSecErrors").textContent = integer.format(s.errors_24h);
    $("dashSecDenied").classList.toggle("text-down", s.denied_24h > 0);
    $("dashSecErrors").classList.toggle("text-warn", s.errors_24h > 0);
    const list = $("dashSecRecent");
    if (!s.audit_enabled) {
      return empty(list, "Audit logging is off. Turn on ENABLE_AUDIT_DB in Settings › Compliance to record events.");
    }
    if (!s.recent.length) return empty(list, "No denied or failed events in the last 24 hours.");
    list.replaceChildren(...s.recent.map((r) => {
      const li = el("li", "py-1.5 first:pt-0 last:pb-0 flex items-center gap-2 min-w-0");
      li.append(
        el("span", `shrink-0 rounded-control px-1.5 text-micro font-semibold ${r.decision === "deny" ? "bg-down/10 text-down" : "bg-warn/10 text-warn"}`, r.decision),
        el("span", "min-w-0 truncate text-ink", `${r.identity} → ${r.resource}`),
        el("span", "ml-auto shrink-0 text-caption text-ink-muted", relTime(r.timestamp))
      );
      li.title = `${r.event_type}: ${r.identity} → ${r.resource}`;
      return li;
    }));
  }

  // ── Backend health ────────────────────────────────────────────────────
  const STATE = {
    closed: { label: "Healthy", dot: "bg-ok", text: "text-ok" },
    half_open: { label: "Recovering", dot: "bg-warn", text: "text-warn" },
    open: { label: "Down", dot: "bg-down", text: "text-down" },
    idle: { label: "No traffic yet", dot: "bg-ink-muted/40", text: "text-ink-muted" },
  };
  function renderBackends(rows) {
    const list = $("dashBackends");
    if (!rows.length) return empty(list, "No model servers configured.");
    list.replaceChildren(...rows.map((b) => {
      const st = STATE[b.state] || STATE.idle;
      const li = el("li");
      const a = el("a", "flex items-center gap-3 rounded-control border border-hairline px-3 py-2 hover:bg-surface-inset min-w-0");
      a.href = "/admin/view/servers";
      const dot = el("span", `h-2.5 w-2.5 shrink-0 rounded-full ${st.dot}`);
      dot.setAttribute("aria-hidden", "true");
      const body = el("span", "min-w-0 flex-1");
      body.append(el("span", "block truncate text-sm font-medium text-ink", b.name),
        el("span", "block text-caption text-ink-muted", `${b.backend_type} · ${b.models} model${b.models === 1 ? "" : "s"}`));
      const right = el("span", "shrink-0 text-right");
      right.append(el("span", `block text-caption font-semibold ${st.text}`, st.label),
        el("span", "block text-caption text-ink-muted tabular-nums", b.avg_latency_ms != null ? `${integer.format(b.avg_latency_ms)} ms` : ""));
      a.append(dot, body, right);
      li.append(a);
      return li;
    }));
  }

  // ── Load loop ─────────────────────────────────────────────────────────
  let lastData = null;
  let timer = null;
  let inflight = false;

  async function load() {
    if (inflight) return;
    inflight = true;
    try {
      const res = await fetch(BASE_URL + "/admin/dashboard/summary", { credentials: "include" });
      if (res.status === 401) return typeof handleSessionExpiry === "function" ? handleSessionExpiry() : undefined;
      if (!res.ok) throw new Error("HTTP " + res.status);
      const d = await res.json();
      lastData = d;
      renderKpis(d.spend);
      renderSpendChart(d.spend.daily);
      renderTopModels(d.top_models);
      renderAlerts(d.alerts);
      renderCache(d.cache);
      renderSecurity(d.security);
      renderBackends(d.backends);
      $("dashUpdated").textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (e) {
      $("dashUpdated").textContent = "Couldn't refresh summary (" + e.message + "). Retrying in 30 s.";
    } finally {
      inflight = false;
    }
  }

  function start() { if (!timer) { load(); timer = setInterval(load, REFRESH_MS); } }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  start();
})();
