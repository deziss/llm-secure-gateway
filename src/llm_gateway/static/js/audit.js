// audit.js — Audit history page (/admin/view/audit), backed by GET /admin/audit.
(function () {
  "use strict";
  const PAGE = 50;
  let skip = 0;
  let debounce = null;
  const $ = (id) => document.getElementById(id);

  const BADGE = {
    allow: "bg-ok/10 text-ok border-ok/30",
    deny: "bg-down/10 text-down border-down/30",
    error: "bg-warn/10 text-warn border-warn/30",
  };

  function when(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return `<time datetime="${escapeHtml(d.toISOString())}" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleString([], { dateStyle: "short", timeStyle: "medium" }))}</time>`;
  }

  function details(meta) {
    const keys = Object.keys(meta || {});
    if (!keys.length) return '<span class="text-ink-muted">—</span>';
    const summary = keys.slice(0, 2).map((k) => `${escapeHtml(k)}: ${escapeHtml(String(meta[k]).slice(0, 40))}`).join(" · ");
    return `<details><summary class="cursor-pointer text-ink-muted">${summary}${keys.length > 2 ? " …" : ""}</summary><pre class="mt-1 max-w-md whitespace-pre-wrap break-words text-caption text-ink-muted">${escapeHtml(JSON.stringify(meta, null, 2))}</pre></details>`;
  }

  function params() {
    const p = new URLSearchParams({ skip: String(skip), limit: String(PAGE) });
    const q = $("auditSearch").value.trim();
    if (q) p.set("q", q);
    if ($("auditEvent").value) p.set("event_type", $("auditEvent").value);
    if ($("auditDecision").value) p.set("decision", $("auditDecision").value);
    if ($("auditRange").value) p.set("hours", $("auditRange").value);
    p.set("hide_allowed_requests", String(!$("auditShowRequests").checked));
    return p;
  }

  async function load() {
    const body = $("auditBody");
    try {
      const res = await fetch("/admin/audit?" + params().toString(), { credentials: "include" });
      if (res.status === 401) return handleSessionExpiry();
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.items.length) {
        body.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-ink-muted">No audit events match these filters.</td></tr>`;
      } else {
        body.innerHTML = data.items.map((r) => `
          <tr class="hover:bg-surface-inset">
            <td class="p-3 whitespace-nowrap text-ink-muted">${when(r.timestamp)}</td>
            <td class="p-3 font-mono text-caption">${escapeHtml(r.event_type)}</td>
            <td class="p-3 font-mono text-caption break-all">${escapeHtml(r.identity)}${r.ip_address ? `<div class="text-ink-muted">${escapeHtml(r.ip_address)}</div>` : ""}</td>
            <td class="p-3 font-mono text-caption break-all">${escapeHtml(r.resource)}</td>
            <td class="p-3"><span class="inline-flex rounded-full border px-2 py-0.5 text-caption font-semibold ${BADGE[r.decision] || "border-hairline text-ink-muted"}">${escapeHtml(r.decision)}</span></td>
            <td class="p-3">${details(r.metadata)}</td>
          </tr>`).join("");
      }
      const end = Math.min(skip + data.items.length, data.total);
      $("auditCount").textContent = data.total ? `${(skip + 1).toLocaleString()}–${end.toLocaleString()} of ${data.total.toLocaleString()} events` : "";
      $("auditPrev").disabled = skip === 0;
      $("auditNext").disabled = end >= data.total;
    } catch (e) {
      body.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-down">Couldn't load audit events (${escapeHtml(e.message)}). Reload the page or sign in again.</td></tr>`;
    }
  }

  async function loadEventTypes() {
    try {
      const res = await fetch("/admin/audit/event-types", { credentials: "include" });
      if (!res.ok) return;
      const sel = $("auditEvent");
      (await res.json()).forEach((t) => sel.add(new Option(t, t)));
    } catch (e) { /* filter just stays at "All events" */ }
  }

  async function checkEnabled() {
    try {
      const res = await fetch("/admin/settings", { credentials: "include" });
      if (!res.ok) return;
      const rows = await res.json();
      const row = Array.isArray(rows) && rows.find((s) => s.key === "ENABLE_AUDIT_DB");
      $("auditDisabled").classList.toggle("hidden", !!(row && String(row.value).toLowerCase() === "true"));
    } catch (e) {}
  }

  const reset = () => { skip = 0; load(); };
  ["auditEvent", "auditDecision", "auditRange", "auditShowRequests"].forEach((id) => $(id).addEventListener("change", reset));
  $("auditSearch").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(reset, 300); });
  $("auditPrev").addEventListener("click", () => { skip = Math.max(0, skip - PAGE); load(); });
  $("auditNext").addEventListener("click", () => { skip += PAGE; load(); });
  $("auditRefresh").addEventListener("click", load);

  loadEventTypes();
  checkEnabled();
  load();
})();
