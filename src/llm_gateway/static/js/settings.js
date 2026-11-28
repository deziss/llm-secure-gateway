// settings.js — Settings page logic

async function patchSetting(key, value, toggleElem) {
  try {
    const res = await fetchWithCsrf(`/admin/settings/${key}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: String(value) }),
      credentials: "include",
    });
    if (!res.ok) throw new Error("Synchronization failure");
  } catch (e) {
    showToast("System Protocol Error", "Failed to commit setting state.", "error");
    toggleElem.checked = !value; // Revert visually
  }
}

// Admin-only toggles aren't rendered for managers; skip what isn't on the page.
function setChecked(id) {
  const el = document.getElementById(id);
  return el ? { set checked(v) { el.checked = v; } } : { set checked(v) {} };
}

async function loadSettings() {
  if (USER_ROLE !== "admin") return;
  try {
    const res = await fetchWithCsrf("/admin/settings", {
      credentials: "include",
    });
    const settings = await res.json();
    const getVal = (key) =>
      settings.find((s) => s.key === key)?.value.toLowerCase() === "true";

    setChecked("requireInviteToggle").checked =
      getVal("REQUIRE_INVITE");
    setChecked("enableFederationToggle").checked = getVal(
      "ENABLE_MODEL_FEDERATION",
    );
    setChecked("enableRoutingToggle").checked = getVal(
      "ENABLE_EXPERIMENTAL_ROUTING",
    );
    setChecked("enableRetryToggle").checked = getVal(
      "ENABLE_RETRY_BACKOFF",
    );
    setChecked("enableMissionControlToggle").checked = getVal(
      "ENABLE_MISSION_CONTROL",
    );
    setChecked("enableChatPlaygroundToggle").checked = getVal(
      "ENABLE_CHAT_PLAYGROUND",
    );
    setChecked("enableEmbedPlaygroundToggle").checked = getVal(
      "ENABLE_EMBED_PLAYGROUND",
    );

    // Compliance & Audit settings
    setChecked("enableAuditDbToggle").checked = getVal("ENABLE_AUDIT_DB");

    setChecked("enableFastPathToggle").checked = getVal(
      "ENABLE_FAST_PATH_OPTIMIZATIONS",
    );
    setChecked("enableTranslationToggle").checked = getVal(
      "ENABLE_PROTOCOL_TRANSLATION",
    );
    setChecked("enableThinkingToggle").checked = getVal(
      "ENABLE_THINKING_NORMALIZATION",
    );
    const exactCacheEl = document.getElementById("enableExactCacheToggle");
    if (exactCacheEl) exactCacheEl.checked = getVal("ENABLE_EXACT_CACHE");
    const semanticCacheEl = document.getElementById("enableSemanticCacheToggle");
    if (semanticCacheEl) semanticCacheEl.checked = getVal("ENABLE_SEMANTIC_CACHE");

    const expiryVal = settings.find(s => s.key === "KEY_EXPIRY_DAYS")?.value || "0";
    document.getElementById("keyExpiryDaysInput").value = expiryVal;

    const webhookVal = settings.find(s => s.key === "WEBHOOK_URL")?.value || "";
    document.getElementById("webhookUrlInput").value = webhookVal;
  } catch (e) {
    console.error("IO Fault: Settings payload corrupted", e);
  }
  await loadLicenseStatus();
}

async function loadInvites() {
  try {
    const res = await fetchWithCsrf("/admin/invites", {
      credentials: "include",
    });
    const invites = await res.json();
    const tbody = document.getElementById("invitesTableBody");

    if (invites.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="p-12 text-center text-ink-muted italic"><i data-lucide="inbox" class="w-10 h-10 mb-3 block opacity-20 mx-auto"></i>No pending tokens found.</td></tr>';
      return;
    }

    tbody.innerHTML = invites
      .map((i) => {
        const statusCls = i.is_used
          ? "bg-surface-inset text-ink-muted border-hairline"
          : "bg-ok/10 text-ok border-ok/25";
        const statusText = i.is_used ? "EXHAUSTED" : "AVAILABLE";
        return `
        <tr class="hover:bg-surface-inset transition-all group">
          <td class="p-4 font-mono text-accent select-all font-bold tracking-tighter">${escapeHtml(i.code)}</td>
          <td class="p-4 text-ink-muted text-xs font-medium">${escapeHtml(window.formatDateTime ? window.formatDateTime(i.created_at) : new Date(i.created_at).toLocaleString())}</td>
          <td class="p-4">
            <div class="flex items-center gap-3">
              <span class="px-2 py-0.5 rounded-full text-[9px] font-extrabold border ${statusCls}">${statusText}</span>
              <span class="text-[10px] text-ink-muted font-bold uppercase truncate max-w-[120px]">${escapeHtml(i.used_by || i.created_by || "")}</span>
            </div>
          </td>
          <td class="p-4 text-right">
            <div class="flex justify-end gap-2 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-opacity">
              <button type="button" data-action="copy" data-code="${escapeHtml(i.code)}" aria-label="Copy token" class="p-1.5 bg-surface-inset text-ink-muted hover:text-ink rounded-lg border border-hairline" title="Copy Token"><i data-lucide="copy" class="w-4 h-4"></i></button>
              <button type="button" data-action="delete" data-code="${escapeHtml(i.code)}" aria-label="Invalidate token" class="p-1.5 bg-down/10 text-down hover:bg-down hover:text-down-ink rounded-lg border border-down/25" title="Invalidate"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
            </div>
          </td>
        </tr>
      `;
      })
      .join("");
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  } catch (e) {
    console.error("DB Fault: Invites index unavailable", e);
  }
}

// Delegated handler: no data is ever interpolated into JS source.
(function () {
  const tbody = document.getElementById("invitesTableBody");
  if (!tbody) return;
  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn || !tbody.contains(btn)) return;
    const code = btn.dataset.code || "";
    if (btn.dataset.action === "copy") copyToClipboard(code);
    else if (btn.dataset.action === "delete") deleteInvite(code);
  });
})();

async function generateInvite() {
  const res = await fetchWithCsrf("/admin/invites", {
    method: "POST",
    credentials: "include",
  });
  if (res.ok) loadInvites();
}

async function deleteInvite(code) {
  const confirmed = await showConfirm("Revoke Token", `Are you sure you want to revoke token ${code}?`);
  if (!confirmed) return;
  const res = await fetchWithCsrf(`/admin/invites/${encodeURIComponent(code)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (res.ok) loadInvites();
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast("Success", "Copied to clipboard");
  }).catch(() => {
    showToast("Notice", "Failed to copy", "warning");
  });
}

// Setup Listeners — boolean toggles
[
  "requireInviteToggle",
  "enableFederationToggle",
  "enableRoutingToggle",
  "enableRetryToggle",
  "enableMissionControlToggle",
  "enableChatPlaygroundToggle",
  "enableEmbedPlaygroundToggle",
  "enableAuditDbToggle",
  "enableFastPathToggle",
  "enableTranslationToggle",
  "enableThinkingToggle",
  "enableExactCacheToggle",
  "enableSemanticCacheToggle",
].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", (e) =>
    patchSetting(e.target.dataset.key, e.target.checked, e.target),
  );
});

// Key rotation days — number input (debounced save on change)
const expiryInput = document.getElementById("keyExpiryDaysInput");
if (expiryInput) {
  expiryInput.addEventListener("change", async () => {
    const days = Math.max(0, Math.min(365, parseInt(expiryInput.value, 10) || 0));
    expiryInput.value = days;
    try {
      const res = await fetchWithCsrf(`/admin/settings/KEY_EXPIRY_DAYS`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: String(days) }),
        credentials: "include",
      });
      if (!res.ok) throw new Error();
      showToast("Policy Updated", days > 0 ? `New keys will expire in ${days} days` : "Key auto-rotation disabled");
    } catch (e) {
      showToast("Error", "Failed to save key rotation policy", "error");
    }
  });
}

// Webhook URL — text input (save on blur)
const webhookInput = document.getElementById("webhookUrlInput");
if (webhookInput) {
  webhookInput.addEventListener("change", async () => {
    const url = webhookInput.value.trim();
    try {
      const res = await fetchWithCsrf(`/admin/settings/WEBHOOK_URL`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: url }),
        credentials: "include",
      });
      if (!res.ok) throw new Error();
      showToast("Webhooks", url ? "Webhook URL configured" : "Webhooks disabled");
    } catch (e) {
      showToast("Error", "Failed to save webhook URL", "error");
    }
  });
}

// ─── Scope Policy Rules Management ──────────────────────────────────────

// Built-in default rules (same as policy.py DEFAULT_RULES)
const BUILTIN_RULES = [
  ["/api/chat", "llm:chat"],
  ["/v1/chat/completions", "llm:chat"],
  ["/v1/completions", "llm:chat"],
  ["/api/generate", "llm:chat"],
  ["/api/embeddings", "llm:embed"],
  ["/v1/embeddings", "llm:embed"],
  ["/api/tags", "llm:read"],
  ["/v1/models", "llm:read"],
  ["/api/ps", "llm:read"],
];

let _customScopeRules = []; // DB overrides

async function loadScopeRules() {
  const tbody = document.getElementById("scopeRulesBody");
  if (!tbody) return;

  // Load current SCOPE_RULES from settings
  try {
    const res = await fetchWithCsrf("/admin/settings", { credentials: "include" });
    const settings = await res.json();
    const scopeSetting = settings.find(s => s.key === "SCOPE_RULES");
    _customScopeRules = [];
    if (scopeSetting && scopeSetting.value) {
      try { _customScopeRules = JSON.parse(scopeSetting.value); } catch(e) {}
    }
  } catch(e) {}

  // Build the markup once, then assign once.
  const customHtml = _customScopeRules
    .map(
      (rule, idx) => `
      <tr class="hover:bg-surface-inset transition-colors">
        <td class="p-4 font-mono text-accent font-bold">${escapeHtml(rule[0])}</td>
        <td class="p-4"><span class="px-2 py-0.5 bg-accent/10 text-accent border border-accent/25 rounded text-micro font-bold">${escapeHtml(rule[1])}</span></td>
        <td class="p-4"><span class="px-2 py-0.5 bg-warn/10 text-warn border border-warn/25 rounded text-micro font-bold">Custom</span></td>
        <td class="p-4 text-right">
          <button type="button" data-action="remove-rule" data-idx="${idx}" aria-label="Remove rule ${escapeHtml(rule[0])}" class="text-ink-muted hover:text-down transition-colors p-2 min-h-touch min-w-touch inline-flex items-center justify-center" title="Remove rule">
            <i data-lucide="x-circle" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>`,
    )
    .join("");

  const builtinHtml = BUILTIN_RULES.map(
    (rule) => `
      <tr class="hover:bg-surface-inset transition-colors ">
        <td class="p-4 font-mono text-ink-muted">${escapeHtml(rule[0])}</td>
        <td class="p-4"><span class="px-2 py-0.5 bg-surface-inset text-ink-muted border border-hairline rounded text-micro font-bold">${escapeHtml(rule[1])}</span></td>
        <td class="p-4"><span class="px-2 py-0.5 bg-surface-inset text-ink-muted border border-hairline rounded text-micro font-bold">Built-in</span></td>
        <td class="p-4 text-right text-ink-muted text-micro">read-only</td>
      </tr>`,
  ).join("");

  tbody.innerHTML = customHtml + builtinHtml;
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}

(function () {
  const tbody = document.getElementById("scopeRulesBody");
  if (!tbody) return;
  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest('button[data-action="remove-rule"]');
    if (!btn || !tbody.contains(btn)) return;
    removeScopeRule(parseInt(btn.dataset.idx, 10));
  });
})();

async function _saveScopeRules() {
  const value = _customScopeRules.length > 0 ? JSON.stringify(_customScopeRules) : "";
  await patchSetting("SCOPE_RULES", value, { checked: false });
}

async function addScopeRule() {
  const prefix = document.getElementById("newRulePrefix").value.trim();
  const scope = document.getElementById("newRuleScope").value.trim();
  if (!prefix || !scope) {
    showToast("Validation", "Both endpoint prefix and scope are required.", "error");
    return;
  }
  if (!prefix.startsWith("/")) {
    showToast("Validation", "Endpoint prefix must start with /", "error");
    return;
  }
  _customScopeRules.push([prefix, scope]);
  await _saveScopeRules();
  document.getElementById("newRulePrefix").value = "";
  document.getElementById("newRuleScope").value = "";
  showToast("Policy Updated", `Rule added: ${prefix} → ${scope}`);
  await loadScopeRules();
}

async function removeScopeRule(idx) {
  const rule = _customScopeRules[idx];
  if (!rule) return;
  const confirmed = await showConfirm("Remove Rule", `Remove custom rule: ${rule[0]} → ${rule[1]}?`);
  if (!confirmed) return;
  _customScopeRules.splice(idx, 1);
  await _saveScopeRules();
  showToast("Policy Updated", "Rule removed. Built-in defaults will apply.");
  await loadScopeRules();
}

function readStoredTheme() {
  try {
    return localStorage.getItem("theme") || "system";
  } catch (e) {
    return "system";
  }
}

function storeTheme(theme) {
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch (e) {
    /* storage unavailable (private window): theme applies for this page only */
  }
}

// ─── Theme Toggle ──────────────────────────────────────────────────────

function applyTheme(theme) {
  if (theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}

function updateThemeButtons(active) {
  document.querySelectorAll("#themeToggle .theme-btn").forEach((btn) => {
    const isActive = btn.dataset.theme === active;
    btn.classList.toggle("bg-surface-raised", isActive);
    btn.classList.toggle("text-ink", isActive);
    btn.classList.toggle("shadow-sm", isActive);
    btn.classList.toggle("text-ink-muted", !isActive);
  });
}

function initThemeToggle() {
  const toggle = document.getElementById("themeToggle");
  if (!toggle) return;

  // Read saved preference (or default to "system")
  const saved = readStoredTheme();
  applyTheme(saved);
  updateThemeButtons(saved);

  // Listen for clicks on the 3-way toggle
  toggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".theme-btn");
    if (!btn) return;
    const theme = btn.dataset.theme;
    storeTheme(theme);
    applyTheme(theme);
    updateThemeButtons(theme);
    document.dispatchEvent(new CustomEvent("admin:themechange", { detail: { theme: theme, source: "settings" } }));
  });

  // The top-bar theme button (shell.js) writes the same setting.
  document.addEventListener("admin:themechange", (e) => {
    if (e.detail && e.detail.source !== "settings") updateThemeButtons(readStoredTheme());
  });

  // Listen for OS theme changes when set to "system"
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = readStoredTheme();
    if (current === "system") {
      applyTheme("system");
    }
  });
}

// ─── License & Subscription ────────────────────────────────────────────

async function loadLicenseStatus() {
  if (USER_ROLE !== "admin") return;
  try {
    const res = await fetchWithCsrf("/admin/license/status", {
      credentials: "include",
    });
    if (!res.ok) return;
    const data = await res.json();

    // 1. Tier Badge
    const badge = document.getElementById("licTierBadge");
    if (badge) {
      const tier = (data.tier || "community").toLowerCase();
      badge.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
      badge.className = "px-2.5 py-1 text-xs font-semibold rounded-control inline-flex items-center";
      if (tier === "enterprise") {
        badge.classList.add("bg-ok/10", "text-ok", "border", "border-ok/25");
      } else if (tier === "pro") {
        badge.classList.add("bg-accent/10", "text-accent", "border", "border-accent/25");
      } else {
        badge.classList.add("bg-surface-base", "text-ink-muted", "border", "border-hairline");
      }
    }

    // 2. Masked Key
    const keyEl = document.getElementById("licKeyMasked");
    if (keyEl) {
      keyEl.textContent = data.key_masked || "COMMUNITY-EDITION";
    }

    // 3. Hardware Fingerprint
    const hwEl = document.getElementById("licHardwareId");
    if (hwEl) {
      const hwId = data.hardware_id || "localhost";
      hwEl.textContent = hwId;
      hwEl.title = hwId;
      hwEl.setAttribute("data-full-id", hwId);
    }

    // 4. Lease Expiry
    const leaseEl = document.getElementById("licLeaseExpiry");
    if (leaseEl) {
      if (data.lease_expires_at) {
        try {
          const expStr = window.formatDateTime ? window.formatDateTime(data.lease_expires_at) : new Date(data.lease_expires_at).toLocaleString();
          leaseEl.textContent = `Expires ${expStr}`;
        } catch (_) {
          leaseEl.textContent = data.lease_expires_at;
        }
      } else {
        leaseEl.textContent = data.is_community ? "Perpetual / Offline" : "Active Offline Lease";
      }
    }

    // 5. Quotas
    const limits = data.limits || {};
    const backendsQuotaEl = document.getElementById("licBackendsQuota");
    if (backendsQuotaEl) {
      backendsQuotaEl.textContent = limits.max_backends !== undefined ? `${limits.max_backends} max` : "Unlimited";
    }

    const keysQuotaEl = document.getElementById("licKeysQuota");
    if (keysQuotaEl) {
      keysQuotaEl.textContent = limits.max_keys !== undefined ? `${limits.max_keys} max` : "Unlimited";
    }

    // 6. Features Checklist
    const features = data.features || {};
    const featChecks = [
      { id: "licFeatSemanticCache", enabled: Boolean(features.semantic_cache) },
      { id: "licFeatComplexityRouting", enabled: Boolean(features.complexity_routing) },
      { id: "licFeatLiveTraces", enabled: Boolean(features.live_traces) },
      { id: "licFeatAuditLogs", enabled: Boolean(features.audit_logs) },
    ];

    featChecks.forEach(({ id, enabled }) => {
      const container = document.getElementById(id);
      if (!container) return;
      const icon = container.querySelector(".feature-icon");
      if (icon) {
        if (enabled) {
          icon.setAttribute("data-lucide", "check-circle-2");
          icon.className = "w-4 h-4 text-ok feature-icon";
        } else {
          icon.setAttribute("data-lucide", "x-circle");
          icon.className = "w-4 h-4 text-ink-muted feature-icon";
        }
      }
    });

    if (window.lucide && typeof lucide.createIcons === "function") {
      lucide.createIcons();
    }
  } catch (err) {
    console.error("Failed to load license status:", err);
  }
}

function initLicenseControls() {
  const refreshBtn = document.getElementById("btnRefreshLicense");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      try {
        const res = await fetchWithCsrf("/admin/license/refresh", {
          method: "POST",
          credentials: "include",
        });
        if (res.ok) {
          showToast("License Lease", "License lease refreshed successfully.");
          await loadLicenseStatus();
        } else {
          const body = await res.json().catch(() => ({}));
          showToast("License Error", body.detail || "Failed to refresh license lease.", "error");
        }
      } catch (err) {
        showToast("License Error", "Network error while refreshing license.", "error");
      } finally {
        refreshBtn.disabled = false;
      }
    });
  }

  const deactivateBtn = document.getElementById("btnDeactivateLicense");
  if (deactivateBtn) {
    deactivateBtn.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to deactivate this gateway license seat? The gateway will revert to Community mode.")) {
        return;
      }
      deactivateBtn.disabled = true;
      try {
        const res = await fetchWithCsrf("/admin/license/deactivate", {
          method: "POST",
          credentials: "include",
        });
        if (res.ok) {
          showToast("Seat Deactivated", "Gateway license has been deactivated and reverted to Community tier.");
          await loadLicenseStatus();
        } else {
          const body = await res.json().catch(() => ({}));
          showToast("Deactivation Error", body.detail || "Failed to deactivate license.", "error");
        }
      } catch (err) {
        showToast("Deactivation Error", "Network error during deactivation.", "error");
      } finally {
        deactivateBtn.disabled = false;
      }
    });
  }

  const copyBtn = document.getElementById("btnCopyHardwareId");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const hwEl = document.getElementById("licHardwareId");
      if (!hwEl) return;
      const text = hwEl.getAttribute("data-full-id") || hwEl.textContent || "";
      if (!text || text === "...") return;
      navigator.clipboard.writeText(text).then(
        () => showToast("Copied", "Hardware fingerprint copied to clipboard."),
        () => showToast("Notice", "Failed to copy hardware fingerprint.", "warning")
      );
    });
  }
}

function openUpgradeLicenseModal() {
  const modal = document.getElementById("upgradeLicenseModal");
  if (!modal) return;
  // Mark the currently active plan card.
  const tier = (document.getElementById("licTierBadge")?.textContent || "").trim().toLowerCase();
  const badgeMap = { community: "upgradePlanCommunityBadge", pro: "upgradePlanProBadge", enterprise: "upgradePlanEnterpriseBadge" };
  for (const [, badgeId] of Object.entries(badgeMap)) {
    const el = document.getElementById(badgeId);
    if (el) el.classList.add("hidden");
  }
  const currentBadge = document.getElementById(badgeMap[tier] || "");
  if (currentBadge) currentBadge.classList.remove("hidden");
  // Mirror the hardware fingerprint from the license panel into the modal.
  const hwEl = document.getElementById("licHardwareId");
  const modalHwEl = document.getElementById("upgradeHardwareId");
  if (modalHwEl && hwEl) {
    modalHwEl.textContent = hwEl.getAttribute("data-full-id") || hwEl.textContent || "...";
  }
  const keyInput = document.getElementById("upgradeLicenseKey");
  if (keyInput) keyInput.value = "";
  modal.classList.remove("hidden");
  if (window.lucide && typeof lucide.createIcons === "function") lucide.createIcons();
  if (keyInput) keyInput.focus();
}

function closeUpgradeLicenseModal() {
  const modal = document.getElementById("upgradeLicenseModal");
  if (modal) modal.classList.add("hidden");
}

function initUpgradeLicenseModal() {
  const openBtn = document.getElementById("btnUpgradeLicense");
  if (openBtn) {
    openBtn.addEventListener("click", openUpgradeLicenseModal);
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeUpgradeLicenseModal();
  });

  const copyHwBtn = document.getElementById("btnCopyUpgradeHardwareId");
  if (copyHwBtn) {
    copyHwBtn.addEventListener("click", () => {
      const text = document.getElementById("upgradeHardwareId")?.textContent || "";
      if (!text || text === "...") return;
      navigator.clipboard.writeText(text).then(
        () => showToast("Copied", "Hardware fingerprint copied to clipboard."),
        () => showToast("Notice", "Failed to copy hardware fingerprint.", "warning")
      );
    });
  }

  const applyBtn = document.getElementById("btnApplyUpgradeKey");
  const keyInput = document.getElementById("upgradeLicenseKey");
  if (!applyBtn || !keyInput) return;

  const submitUpgrade = async () => {
    const licenseKey = keyInput.value.trim();
    if (!licenseKey) {
      showToast("License Upgrade", "Enter a license key first.", "warning");
      return;
    }
    applyBtn.disabled = true;
    const originalHtml = applyBtn.innerHTML;
    applyBtn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>Validating...';
    if (window.lucide && typeof lucide.createIcons === "function") lucide.createIcons();
    try {
      const res = await fetchWithCsrf("/admin/license/upgrade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_key: licenseKey }),
        credentials: "include",
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        const tierName = (body.tier || "").toUpperCase();
        showToast("License Upgraded", `Welcome to the ${tierName || "new"} tier! Entitlements are now active. This key persists across restarts.`);
        closeUpgradeLicenseModal();
        await loadLicenseStatus();
      } else {
        showToast("Upgrade Failed", body.detail || "Could not validate the new license key.", "error");
      }
    } catch (err) {
      showToast("Upgrade Failed", "Network error while contacting the licensing server.", "error");
    } finally {
      applyBtn.disabled = false;
      applyBtn.innerHTML = originalHtml;
      if (window.lucide && typeof lucide.createIcons === "function") lucide.createIcons();
    }
  };

  applyBtn.addEventListener("click", submitUpgrade);
  keyInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitUpgrade();
  });

  window.closeUpgradeLicenseModal = closeUpgradeLicenseModal;
}

loadSettings();
loadInvites();
loadScopeRules();
initThemeToggle();
initLicenseControls();
initUpgradeLicenseModal();
