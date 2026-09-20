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

async function loadSettings() {
  if (USER_ROLE !== "admin") return;
  try {
    const res = await fetchWithCsrf("/admin/settings", {
      credentials: "include",
    });
    const settings = await res.json();
    const getVal = (key) =>
      settings.find((s) => s.key === key)?.value.toLowerCase() === "true";

    document.getElementById("requireInviteToggle").checked =
      getVal("REQUIRE_INVITE");
    document.getElementById("enableFederationToggle").checked = getVal(
      "ENABLE_MODEL_FEDERATION",
    );
    document.getElementById("enableRoutingToggle").checked = getVal(
      "ENABLE_EXPERIMENTAL_ROUTING",
    );
    document.getElementById("enableRetryToggle").checked = getVal(
      "ENABLE_RETRY_BACKOFF",
    );
    document.getElementById("enableMissionControlToggle").checked = getVal(
      "ENABLE_MISSION_CONTROL",
    );
    document.getElementById("enableChatPlaygroundToggle").checked = getVal(
      "ENABLE_CHAT_PLAYGROUND",
    );
    document.getElementById("enableEmbedPlaygroundToggle").checked = getVal(
      "ENABLE_EMBED_PLAYGROUND",
    );

    // Compliance & Audit settings
    document.getElementById("enableAuditDbToggle").checked = getVal("ENABLE_AUDIT_DB");

    document.getElementById("enableFastPathToggle").checked = getVal(
      "ENABLE_FAST_PATH_OPTIMIZATIONS",
    );
    document.getElementById("enableTranslationToggle").checked = getVal(
      "ENABLE_PROTOCOL_TRANSLATION",
    );
    document.getElementById("enableThinkingToggle").checked = getVal(
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
        '<tr><td colspan="4" class="p-12 text-center text-slate-500 italic"><i data-lucide="inbox" class="w-10 h-10 mb-3 block opacity-20 mx-auto"></i>No pending tokens found.</td></tr>';
      return;
    }

    tbody.innerHTML = invites
      .map((i) => {
        const statusCls = i.is_used
          ? "bg-slate-500/10 text-slate-500 border-slate-500/20"
          : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
        const statusText = i.is_used ? "EXHAUSTED" : "AVAILABLE";
        return `
        <tr class="hover:bg-slate-800/30 transition-all group">
          <td class="p-4 font-mono text-blue-400 select-all font-bold tracking-tighter">${i.code}</td>
          <td class="p-4 text-slate-500 text-xs font-medium">${new Date(i.created_at).toLocaleString()}</td>
          <td class="p-4">
            <div class="flex items-center gap-3">
              <span class="px-2 py-0.5 rounded-full text-[9px] font-extrabold border ${statusCls}">${statusText}</span>
              <span class="text-[10px] text-slate-500 font-bold uppercase truncate max-w-[120px]">${i.used_by || i.created_by}</span>
            </div>
          </td>
          <td class="p-4 text-right">
            <div class="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <button onclick="copyToClipboard('${i.code}')" class="p-1.5 bg-slate-800 text-slate-400 hover:text-white rounded-lg border border-slate-700" title="Copy Token"><i data-lucide="copy" class="w-4 h-4"></i></button>
              <button onclick="deleteInvite('${i.code}')" class="p-1.5 bg-rose-500/10 text-rose-500 hover:bg-rose-500 hover:text-white rounded-lg border border-rose-500/20" title="Invalidate"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
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
  const res = await fetchWithCsrf(`/admin/invites/${code}`, {
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

  tbody.innerHTML = "";

  // Render custom rules first (editable)
  _customScopeRules.forEach((rule, idx) => {
    tbody.innerHTML += `
      <tr class="hover:bg-slate-800/20 transition-colors">
        <td class="p-4 font-mono text-violet-400 font-bold">${escapeHtml(rule[0])}</td>
        <td class="p-4"><span class="px-2 py-0.5 bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded text-[10px] font-bold">${escapeHtml(rule[1])}</span></td>
        <td class="p-4"><span class="px-2 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded text-[9px] font-bold uppercase">Custom</span></td>
        <td class="p-4 text-right">
          <button onclick="removeScopeRule(${idx})" class="text-slate-500 hover:text-red-500 transition-colors p-1" title="Remove rule">
            <i data-lucide="x-circle" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>`;
  });

  // Render built-in rules (read-only)
  BUILTIN_RULES.forEach(rule => {
    tbody.innerHTML += `
      <tr class="hover:bg-slate-800/20 transition-colors opacity-60">
        <td class="p-4 font-mono text-slate-400">${escapeHtml(rule[0])}</td>
        <td class="p-4"><span class="px-2 py-0.5 bg-slate-500/10 text-slate-400 border border-slate-500/20 rounded text-[10px] font-bold">${escapeHtml(rule[1])}</span></td>
        <td class="p-4"><span class="px-2 py-0.5 bg-slate-500/10 text-slate-500 border border-slate-500/20 rounded text-[9px] font-bold uppercase">Built-in</span></td>
        <td class="p-4 text-right text-slate-600 text-[10px]">read-only</td>
      </tr>`;
  });
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}

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
  const confirmed = await showConfirm("Remove Rule", `Remove custom rule: ${rule[0]} → ${rule[1]}?`);
  if (!confirmed) return;
  _customScopeRules.splice(idx, 1);
  await _saveScopeRules();
  showToast("Policy Updated", "Rule removed. Built-in defaults will apply.");
  await loadScopeRules();
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
    btn.classList.toggle("bg-white", isActive);
    btn.classList.toggle("dark:bg-slate-600", isActive);
    btn.classList.toggle("text-slate-900", isActive);
    btn.classList.toggle("dark:text-white", isActive);
    btn.classList.toggle("shadow-sm", isActive);
    btn.classList.toggle("text-slate-500", !isActive);
    btn.classList.toggle("dark:text-slate-400", !isActive);
  });
}

function initThemeToggle() {
  const toggle = document.getElementById("themeToggle");
  if (!toggle) return;

  // Read saved preference (or default to "system")
  const saved = localStorage.getItem("theme") || "system";
  applyTheme(saved);
  updateThemeButtons(saved);

  // Listen for clicks on the 3-way toggle
  toggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".theme-btn");
    if (!btn) return;
    const theme = btn.dataset.theme;
    if (theme === "system") {
      localStorage.removeItem("theme");
    } else {
      localStorage.setItem("theme", theme);
    }
    applyTheme(theme);
    updateThemeButtons(theme);
  });

  // Listen for OS theme changes when set to "system"
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = localStorage.getItem("theme") || "system";
    if (current === "system") {
      applyTheme("system");
    }
  });
}

loadSettings();
loadInvites();
loadScopeRules();
initThemeToggle();
