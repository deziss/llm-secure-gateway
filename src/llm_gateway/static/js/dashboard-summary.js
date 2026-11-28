// dashboard-summary.js — the dashboard's summary cards (spend, tokens, top
// models, alerts, cache, security, backend health) plus the widget layer:
// time range, live cadence, customise (show/hide/reorder), density, and
// cache-first paint via DashCache (see dash-cache.js).
//
// One request to /admin/dashboard/summary drives most widgets; live traffic
// stays on dashboard.js's SSE stream. All values are written with textContent
// / DOM APIs: owner names, key prefixes and audit identities are user-controlled.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  if (!$("dashSummary")) return;
  const DC = window.DashCache;
  if (!DC) { console.warn("dash-cache.js must load before dashboard-summary.js"); return; }

  const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const integer = new Intl.NumberFormat();

  const RANGES = [
    { id: "1h", label: "1h" },
    { id: "24h", label: "24h" },
    { id: "7d", label: "7d" },
    { id: "30d", label: "30d" },
  ];
  const USER = String(window.dashboardUser || "").toLowerCase();

  function money(v) {
    v = Number(v) || 0;
    if (v === 0) return "$0";
    if (Math.abs(v) < 0.01) return "<$0.01";
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency: "USD", maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0,
    }).format(v);
  }

  function pct(v) {
    return (Number(v) * 100).toFixed(Number(v) < 0.1 ? 1 : 0) + "%";
  }

  function relTime(iso) {
    if (window.formatRelTime) return window.formatRelTime(iso);
    const d = window.parseIsoDate ? window.parseIsoDate(iso) : new Date(iso);
    if (!d || Number.isNaN(d.getTime())) return "—";
    const s = Math.round((d.getTime() - Date.now()) / 1000);
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

  function icon(name, cls) {
    const i = el("i", cls || "h-3.5 w-3.5");
    i.setAttribute("data-lucide", name);
    return i;
  }

  function empty(list, text) {
    list.replaceChildren(el("li", "text-sm text-ink-muted py-1", text));
  }

  function tokenColor(name, alpha) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v ? `rgb(${v.split(/\s+/).join(", ")}${alpha !== undefined ? ", " + alpha : ""})` : "#D97706";
  }

  const prefs = DC.loadPrefs(USER);

  // ── Freshness header ("as of", stale badge, screen-reader announcements) ─
  let lastUpdate = null;
  let isStale = false;
  const announce = $("dashAnnounce");

  function setUpdated() {
    const node = $("dashUpdated");
    if (!node) return;
    if (!lastUpdate) { node.textContent = ""; return; }
    const t = window.formatTime ? window.formatTime(lastUpdate) : lastUpdate.toLocaleTimeString();
    node.textContent = isStale ? "Showing saved data · updates failing" : "Updated " + t;
    node.classList.toggle("text-warn", isStale);
    node.classList.toggle("text-ink-muted", !isStale);
  }
  // markUpdated(stale) — stale=true keeps the "saved data" badge until a live
  // response arrives; a successful paint clears it and resets the clock.
  function markUpdated(stale) {
    if (typeof stale === "boolean") isStale = stale;
    lastUpdate = new Date();
    setUpdated();
  }
  setInterval(() => { if (lastUpdate && document.visibilityState === "visible") setUpdated(); }, 5000);

  function announceOnce(msg) { if (announce) announce.textContent = msg; }

  // ── KPI cards ─────────────────────────────────────────────────────────
  function setText(id, v) {
    const node = $(id);
    if (node) node.textContent = v;
  }

  // Count-up for the big numbers; only after the very first paint has
  // happened, so the initial "–" → value swap stays instant.
  let painted = false;
  function setNum(id, to, format) {
    const node = $(id);
    if (!node) return;
    if (painted) DC.countTo(node, Number(to) || 0, format, { animate: true });
    else { node.dataset.v = String(to); node.textContent = format(Number(to) || 0); }
  }

  function renderKpis(spend, headline) {
    const m = spend.month, t = spend.today, prev = spend.prev_month_to_date;
    setText("stat-spend-month", money(m.cost_usd));
    setText("stat-spend-today", money(t.cost_usd));

    const delta = $("stat-spend-delta");
    if (delta) {
      if (prev.cost_usd > 0) {
        const p = Math.round(((m.cost_usd - prev.cost_usd) / prev.cost_usd) * 100);
        delta.textContent = `${p >= 0 ? "▲" : "▼"} ${Math.abs(p)}% vs last month`;
        // Spending more is the thing to watch, so up is the warning colour.
        delta.className = "tabular-nums " + (p > 0 ? "text-warn" : "text-ok");
      } else {
        delta.textContent = "";
      }
    }

    const total = t.input_tokens + t.output_tokens;
    setText("stat-tokens-today", compact.format(total));
    setText("stat-tokens-in", compact.format(t.input_tokens));
    setText("stat-tokens-out", compact.format(t.output_tokens));
    const inBar = $("stat-tokens-in-bar"), outBar = $("stat-tokens-out-bar");
    if (inBar) inBar.style.width = total ? (t.input_tokens / total) * 100 + "%" : "0%";
    if (outBar) outBar.style.width = total ? (t.output_tokens / total) * 100 + "%" : "0%";
    setText("stat-requests-today", `${integer.format(t.requests)} request${t.requests === 1 ? "" : "s"}`);

    // Range-scoped headline KPIs (requests / errors+rate / TTFT percentiles).
    if (headline) {
      const rangeLabel = (RANGES.find((r) => r.id === (headline.range || prefs.timeRange)) || RANGES[1]).label;
      setNum("stat-req-range", headline.requests, integer.format);
      setText("stat-req-range-label", "Requests · last " + rangeLabel);
      const rd = $("stat-req-delta");
      if (rd) {
        if (headline.requests_delta_pct != null) {
          const up = headline.requests_delta_pct >= 0;
          rd.textContent = `${up ? "▲" : "▼"} ${Math.abs(headline.requests_delta_pct)}% vs prev ${rangeLabel}`;
          rd.className = "text-micro font-medium flex items-center gap-1 tabular-nums " + (up ? "text-ok" : "text-warn");
        } else {
          rd.textContent = "";
        }
      }

      setText("stat-error-rate", headline.requests ? pct(headline.error_rate) : "—");
      const er = $("stat-error-rate");
      if (er) {
        const bad = (headline.error_rate || 0) >= 0.05;
        er.textContent = headline.requests ? pct(headline.error_rate) : "—";
        er.classList.toggle("text-down", bad);
        er.classList.toggle("text-ink", !bad);
      }
      setText("stat-error-detail", `${integer.format(headline.errors)} of ${integer.format(headline.requests)} ${rangeLabel}`);

      const p90 = $("stat-p90-ttft");
      if (p90) p90.textContent = headline.ttft_p90_ms != null ? integer.format(Math.round(headline.ttft_p90_ms)) : "—";
      const p50 = headline.ttft_p50_ms != null ? integer.format(Math.round(headline.ttft_p50_ms)) + " ms" : "—";
      const avg = headline.avg_ttft_ms != null ? integer.format(Math.round(headline.avg_ttft_ms)) + " ms" : "—";
      setText("stat-ttft-detail", "p50 ~" + p50 + " · avg " + avg);
    }
  }

  // ── Spend chart ───────────────────────────────────────────────────────
  let spendChart = null;
  function renderSpendChart(daily) {
    const canvas = $("spendChart");
    if (!canvas || typeof Chart === "undefined") return;
    const labels = daily.map((d) =>
      window.formatDate ? window.formatDate(d.date, { month: "short", day: "numeric" }) : d.date
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
        responsive: true, maintainAspectRatio: false,
        animation: DC.reducedMotion() ? false : { duration: 400 },
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
    document.dispatchEvent(new CustomEvent("dash:themerebuild"));
  });

  // ── Top models ────────────────────────────────────────────────────────
  function renderTopModels(rows) {
    const list = $("dashTopModels");
    if (!list) return;
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
    if (!list) return;
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
    setText("dashAlertCount", items.length ? String(items.length) : "");
    if (!items.length) return empty(list, "All clear: no budgets above 80% and no keys expiring within 7 days.");
    list.replaceChildren(...items);
  }

  // ── Cache ─────────────────────────────────────────────────────────────
  function renderCache(c) {
    setText("dashCacheHits", compact.format(c.hits));
    setText("dashCacheEntries", compact.format(c.entries));
    setText("dashCacheSaved", compact.format(c.tokens_saved));
  }

  // ── Security ──────────────────────────────────────────────────────────
  function renderSecurity(s) {
    setText("dashSecDenied", integer.format(s.denied_24h));
    setText("dashSecErrors", integer.format(s.errors_24h));
    const dn = $("dashSecDenied"), er = $("dashSecErrors");
    if (dn) dn.classList.toggle("text-down", s.denied_24h > 0);
    if (er) er.classList.toggle("text-warn", s.errors_24h > 0);
    const list = $("dashSecRecent");
    if (!list) return;
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
    if (!list) return;
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

  // ── Render-all ────────────────────────────────────────────────────────
  let lastData = null;
  function renderAll(d, opts) {
    opts = opts || {};
    const first = !lastData;
    lastData = d;
    try { renderKpis(d.spend, d.headline); } catch (e) { console.debug("headline render", e); }
    renderSpendChart(d.spend.daily);
    renderTopModels(d.top_models);
    renderAlerts(d.alerts);
    renderCache(d.cache);
    renderSecurity(d.security);
    renderBackends(d.backends);
    markUpdated(!!opts.stale);
    if (first) {
      painted = true;
      // One polite announcement per page life, not per tick (WCAG/MDN:
      // tickers should not be live regions).
      announceOnce("Dashboard data loaded" + (opts.fromCache ? " from cache; refreshing." : "."));
      document.querySelectorAll("[data-dash-skeleton]").forEach((n) => DC.skeletonOff(n));
    }
    if (typeof window.dashRenderSecondary === "function") {
      try { window.dashRenderSecondary(d); } catch (e) { console.debug("secondary render", e); }
    }
  }

  // ── Load loop (single scheduler for the whole page) ───────────────────
  const scheduler = DC.makeScheduler();
  window.DashWidgets = {
    scheduler, prefs, ranges: RANGES,
    getTokenColor: tokenColor, fmt: { money, compact, integer, pct },
    setUpdated, markUpdated, announce: announceOnce,
  };

  const summaryUrl = () => "/admin/dashboard/summary?range=" + encodeURIComponent(prefs.timeRange);

  async function loadSummary() {
    DC.skeletonOn($("dashSummary"), 250);
    try {
      const r = await DC.swr(summaryUrl(), { ttlMs: 15000, user: USER });
      if (!r) return;
      renderAll(r.data, { fromCache: r.fromCache, stale: !!r.stale });
      DC.skeletonOff($("dashSummary"));
    } catch (e) {
      if (e && e.message === "session") return;
      setUpdated(true);
      const node = $("dashUpdated");
      if (node) node.textContent = "Couldn't refresh summary. Retrying with backoff.";
      throw e; // let the scheduler's backoff see the failure
    }
  }

  scheduler.add(() => loadSummary(), () => Math.max(10, prefs.live.everySeconds) * 1000, {
    firstDelayMs: 50, backoff: true,
  });
  // Below-the-fold charts refresh slowly; the 24h widget runs in dashboard.js.
  scheduler.add(() => (window.dashTick24h ? window.dashTick24h() : Promise.resolve()), 120000, {
    firstDelayMs: 1500, backoff: true,
  });
  scheduler.setLive(prefs.live.enabled !== false);
  scheduler.start();

  // Cross-tab: another tab fetched fresh summary data → paint from cache now.
  if (DC.broadcast) {
    DC.broadcast.onmessage = (ev) => {
      if (ev.data && ev.data.invalidate && ev.data.invalidate.indexOf("/admin/dashboard/summary") === 0) {
        const rec = DC.cacheGet(ev.data.invalidate);
        if (rec && (rec.user || null) === (USER || null)) renderAll(rec.data, { fromCache: true });
      }
    };
  }

  // ── Header controls: range · live cadence · customise ─────────────────
  function segBtn(r, active) {
    const b = el("button", "px-2.5 py-1 text-micro font-semibold rounded-[0.3rem] transition-colors " +
      (active ? "bg-accent text-accent-ink" : "text-ink-muted hover:text-ink"));
    b.type = "button";
    b.textContent = r.label;
    b.setAttribute("aria-pressed", String(active));
    return b;
  }

  function buildRangeControl() {
    const host = $("dashControls");
    if (!host) return;
    const seg = el("div", "inline-flex rounded-control border border-hairline bg-surface-inset p-0.5 gap-0.5");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "Time range");
    RANGES.forEach((r) => {
      const b = segBtn(r, r.id === prefs.timeRange);
      b.addEventListener("click", () => {
        if (prefs.timeRange === r.id) return;
        prefs.timeRange = r.id;
        DC.savePrefs(prefs);
        seg.querySelectorAll("button").forEach((x, i) => {
          const on = RANGES[i].id === r.id;
          x.setAttribute("aria-pressed", String(on));
          x.className = segBtn(RANGES[i], on).className;
        });
        isStale = false;
        lastUpdate = null;
        setUpdated();
        loadSummary();
        if (window.dashTick24h) window.dashTick24h({ force: true });
        announceOnce("Time range changed to " + r.label + ".");
      });
      seg.append(b);
    });
    host.append(seg);
  }

  function buildLiveControl() {
    const host = $("dashControls");
    if (!host) return;
    const wrap = el("div", "inline-flex items-center gap-1.5");
    wrap.append(el("span", "text-micro text-ink-muted font-medium", "Live"));
    const sel = document.createElement("select");
    sel.className = "rounded-control border border-hairline bg-surface-inset px-1.5 py-1 text-micro font-semibold text-ink";
    sel.setAttribute("aria-label", "Auto-refresh frequency (pause control for live updates)");
    [{ v: "off", label: "Off" }, { v: "30", label: "30s" }, { v: "60", label: "1m" }, { v: "300", label: "5m" }]
      .forEach((o) => {
        const op = document.createElement("option");
        op.value = o.v; op.textContent = o.label;
        if (o.v === (prefs.live.enabled === false ? "off" : String(prefs.live.everySeconds))) op.selected = true;
        sel.append(op);
      });
    sel.addEventListener("change", () => {
      if (sel.value === "off") { prefs.live.enabled = false; scheduler.setLive(false); }
      else { prefs.live.enabled = true; prefs.live.everySeconds = Number(sel.value); scheduler.setLive(true); }
      DC.savePrefs(prefs);
      announceOnce(sel.value === "off" ? "Live updates paused." : "Live updates every " + sel.selectedOptions[0].textContent + ".");
    });
    wrap.append(sel);
    host.append(wrap);
  }

  function applyDensity() {
    document.documentElement.classList.toggle("dash-compact", prefs.density === "compact");
  }

  // ── Customise popover: widget visibility/order, density, animations ────
  const WIDGETS = [
    { id: "traffic24h", title: "Telemetry timeline" },
    { id: "live", title: "Live traffic (15m)" },
    { id: "edge", title: "Edge connectivity" },
    { id: "spend", title: "Spend trajectory" },
    { id: "models", title: "Top models" },
    { id: "alerts", title: "Attention needed" },
    { id: "cache", title: "Response cache" },
    { id: "security", title: "Security events" },
    { id: "health", title: "Server health" },
  ];

  function widgetPref(id) {
    if (!prefs.widgets[id]) prefs.widgets[id] = { visible: true, order: WIDGETS.findIndex((w) => w.id === id) + 1 };
    return prefs.widgets[id];
  }

  function applyWidgetPrefs() {
    WIDGETS.forEach((w) => {
      const node = document.querySelector('[data-widget="' + w.id + '"]');
      if (!node) return;
      const p = widgetPref(w.id);
      node.classList.toggle("hidden", p.visible === false);
      node.style.order = String(p.order != null ? p.order : 99);
    });
    // Sections container needs flex/grid ordering to take effect.
    const host = $("dashSummary");
    if (host) {
      host.classList.add("dash-ordered");
    }
  }

  function moveWidget(id, dir) {
    const p = widgetPref(id);
    p.order = Math.max(1, (p.order || 5) + dir * 2);
    DC.savePrefs(prefs);
    applyWidgetPrefs();
    syncCustomizeChecks();
  }

  let syncCustomizeChecks = () => {};
  function buildCustomize(rebuild) {
    const host = $("dashControls");
    if (!host) return;
    if (rebuild) { const old = $("dashCustomDetails"); if (old) old.remove(); }
    const details = el("details", "relative");
    details.id = "dashCustomDetails";
    const summary = el("summary", "inline-flex items-center gap-1.5 rounded-control border border-hairline bg-surface-inset px-2.5 py-1.5 text-micro font-semibold text-ink cursor-pointer list-none hover:border-accent transition-colors");
    summary.append(icon("sliders-horizontal"), el("span", "", "Customise"));
    const panel = el("div", "absolute right-0 z-30 mt-1.5 w-72 rounded-panel border border-hairline bg-surface-raised shadow-lg p-3 space-y-3");

    // Density
    const dens = el("div", "flex items-center justify-between gap-2");
    dens.append(el("span", "text-xs font-medium text-ink", "Density"));
    const dSel = document.createElement("select");
    dSel.className = "rounded-control border border-hairline bg-surface-inset px-1.5 py-1 text-micro font-semibold text-ink";
    dSel.setAttribute("aria-label", "Card density");
    [["comfortable", "Comfortable"], ["compact", "Compact"]].forEach(([v, l]) => {
      const op = document.createElement("option"); op.value = v; op.textContent = l;
      if (prefs.density === v) op.selected = true;
      dSel.append(op);
    });
    dSel.addEventListener("change", () => { prefs.density = dSel.value; DC.savePrefs(prefs); applyDensity(); });
    dens.append(dSel);

    // Animations
    const anim = el("label", "flex items-center justify-between gap-2 text-xs font-medium text-ink cursor-pointer");
    anim.append(el("span", "", "Chart & count animations"));
    const animBox = document.createElement("input");
    animBox.type = "checkbox";
    animBox.checked = prefs.animations !== false;
    animBox.addEventListener("change", () => { prefs.animations = animBox.checked; DC.savePrefs(prefs); });
    anim.append(animBox);

    // Widgets
    const wHead = el("div", "flex items-center justify-between border-t border-hairline pt-2.5");
    wHead.append(el("span", "text-xs font-semibold text-ink", "Widgets"));
    const reset = el("button", "text-micro text-accent font-medium hover:underline");
    reset.type = "button";
    reset.textContent = "Reset layout";
    reset.addEventListener("click", () => {
      prefs.widgets = {};
      DC.savePrefs(prefs);
      applyWidgetPrefs();
      buildCustomize(true);
      announceOnce("Widget layout reset.");
    });
    wHead.append(reset);

    const wList = el("ul", "space-y-0.5 max-h-64 overflow-y-auto custom-scrollbar pr-1");
    const rows = [];
    WIDGETS.forEach((w) => {
      const p = widgetPref(w.id);
      const li = el("li", "flex items-center gap-1.5 rounded-control px-1 py-0.5 hover:bg-surface-inset");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = p.visible !== false;
      box.id = "dashW" + w.id;
      box.addEventListener("change", () => { p.visible = box.checked; DC.savePrefs(prefs); applyWidgetPrefs(); });
      const label = el("label", "flex-1 text-xs text-ink cursor-pointer truncate");
      label.htmlFor = box.id;
      label.textContent = w.title;
      const mkBtn = (name, aria, dir) => {
        const b = el("button", "p-1 rounded-target text-ink-muted hover:text-ink hover:bg-surface-inset");
        b.type = "button";
        b.append(icon(name));
        b.setAttribute("aria-label", aria);
        b.addEventListener("click", () => moveWidget(w.id, dir));
        return b;
      };
      li.append(box, label, mkBtn("chevron-up", "Move " + w.title + " up", -1), mkBtn("chevron-down", "Move " + w.title + " down", 1));
      wList.append(li);
      rows.push({ w, box });
    });
    syncCustomizeChecks = () => rows.forEach(({ w, box }) => { box.checked = widgetPref(w.id).visible !== false; });

    panel.append(dens, anim, wHead, wList);
    details.append(summary, panel);
    host.append(details);
    if (window.lucide) window.lucide.createIcons();
  }

  buildRangeControl();
  buildLiveControl();
  buildCustomize();
  applyDensity();
  applyWidgetPrefs();
})();
