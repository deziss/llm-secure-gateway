// owners.js — Owners page logic

let ownersTable = null;

$(document).ready(function () {
  ownersTable = $("#ownersTable").DataTable({
    ajax: {
      url: "/admin/owners",
      dataSrc: function (json) {
        if (Array.isArray(json)) {
          json.forEach((item) => (ownersData[item.id] = item));
        }
        return json || [];
      },
      xhrFields: { withCredentials: true },
    },
    columns: [
      {
        data: null,
        render: function (d) {
          const name = d.name || d.id;
          const [bg, fg] = getAvatarColor(d.id);
          const initials = getInitials(name);
          return `
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm shadow-lg shadow-indigo-500/10" style="background:${bg}">
                ${initials}
              </div>
              <div class="flex flex-col">
                <span class="text-white font-bold text-base tracking-tight">${escapeHtml(name)}</span>
                <span class="text-[10px] font-mono text-slate-500 uppercase tracking-widest">${escapeHtml(d.id)}</span>
              </div>
            </div>
          `;
        },
      },
      {
        data: "type",
        render: (data) => {
          const cls =
            data === "project"
              ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
              : "bg-blue-500/10 text-blue-400 border-blue-500/20";
          const icon =
            data === "project" ? "layers" : "user";
          return `<span class="px-2.5 py-1 rounded-lg text-[10px] font-bold border ${cls} uppercase flex items-center gap-1.5 w-fit"><i data-lucide="${icon}" class="w-3.5 h-3.5"></i> ${escapeHtml(data)}</span>`;
        },
      },
      {
        data: "description",
        render: (data) => {
          return `<div class="text-xs text-slate-400 max-w-[200px] truncate" title="${escapeHtml(data || '')}">${data ? escapeHtml(data) : '<span class="italic opacity-50">No description</span>'}</div>`;
        },
      },
      {
        data: "block_endpoints",
        render: (data, type, row) => {
          const statusCls = data
            ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
            : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
          const statusText = data ? "HARDENED" : "UNRESTRICTED";
          const isActive = row.is_active !== false;
          const activeCls = isActive
            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
            : "bg-slate-500/10 text-slate-400 border-slate-500/20";
          const activeText = isActive ? "ACTIVE" : "INACTIVE";
          return `
            <div class="flex flex-col gap-1">
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-bold border ${statusCls} w-fit">
                <span class="w-1 h-1 rounded-full ${data ? "bg-rose-400" : "bg-emerald-400"} mr-1.5"></span>
                ${statusText}
              </span>
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-bold border ${activeCls} w-fit">
                <span class="w-1 h-1 rounded-full ${isActive ? "bg-emerald-400" : "bg-slate-400"} mr-1.5"></span>
                ${activeText}
              </span>
            </div>
          `;
        },
      },
      {
        data: "id",
        className: "text-right",
        render: function (data) {
          const safeId = data.replace(/'/g, "\'");
          let btns = `
            <button type="button" onclick="openKeysModal('${safeId}')" class="p-2 bg-amber-500/10 text-amber-500 hover:bg-amber-500 hover:text-white rounded-lg transition-all border border-amber-500/20 cursor-pointer" title="Manage API Keys"><i data-lucide="key" class="w-4 h-4"></i></button>
            <button type="button" onclick="openPermissionsModal('${safeId}')" class="p-2 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500 hover:text-white rounded-lg transition-all border border-emerald-500/20 cursor-pointer" title="Access Grants"><i data-lucide="shield-check" class="w-4 h-4"></i></button>
            <button type="button" onclick="openEditModal('${safeId}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700 cursor-pointer" title="Edit Profile"><i data-lucide="edit-3" class="w-4 h-4"></i></button>
          `;
          if (["admin", "manager"].includes(USER_ROLE)) {
            btns += `<button type="button" onclick="deleteOwner('${safeId}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20 cursor-pointer" title="Revoke Owner"><i data-lucide="trash-2" class="w-4 h-4"></i></button>`;
          }
          return `<div class="flex justify-end gap-2">${btns}</div>`;
        },
      },
    ],
    dom: "t",
    pageLength: 50,
    retrieve: true,
    drawCallback: function () {
      if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
      }
    }
  });

  $("#addForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Registering...');
    if (window.lucide) window.lucide.createIcons();

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    data.id = (data.id || "").trim();
    data.name = (data.name || "").trim();
    data.block_endpoints = !!document.getElementById("blockEndpointsAdd")?.checked;
    if (data.max_keys) data.max_keys = parseInt(data.max_keys, 10);

    try {
      const res = await fetchWithCsrf("/admin/owners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        credentials: "include",
      });

      if (res.ok) {
        showToast("Success", `Owner '${data.name || data.id}' registered successfully`);
        closeAddModal();
        ownersTable.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Invalid request" }));
        const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "ID already exists or invalid data.");
        showToast("Registration Failed", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide) window.lucide.createIcons();
    }
  });

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Syncing...');
    if (window.lucide) window.lucide.createIcons();

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const id = data.id;
    delete data.id;
    data.name = (data.name || "").trim();
    data.block_endpoints = !!document.getElementById("blockEndpointsEdit")?.checked;
    data.is_active = !!document.getElementById("isActiveEdit")?.checked;
    if (data.max_keys) data.max_keys = parseInt(data.max_keys, 10);

    try {
      const res = await fetchWithCsrf("/admin/owners/" + encodeURIComponent(id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        credentials: "include",
      });

      if (res.ok) {
        showToast("Success", `Owner '${id}' updated successfully`);
        closeEditModal();
        ownersTable.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Update failed" }));
        const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Update failed.");
        showToast("Update Failed", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide) window.lucide.createIcons();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeAddModal();
      closeEditModal();
      closePermissionsModal();
      closeKeysModal();
    }
  });
});

function openAddModal() {
  const form = document.getElementById("addForm");
  if (form) form.reset();
  const modal = document.getElementById("addModal");
  if (modal) modal.classList.remove("hidden");
  const input = document.getElementById("addOwnerId");
  if (input) setTimeout(() => input.focus(), 50);
  if (window.lucide) window.lucide.createIcons();
}
window.openAddModal = openAddModal;

function closeAddModal() {
  const modal = document.getElementById("addModal");
  if (modal) modal.classList.add("hidden");
}
window.closeAddModal = closeAddModal;

function openEditModal(id) {
  const data = ownersData[id];
  if (!data) return;
  document.getElementById("editId").value = data.id;
  document.getElementById("editName").value = data.name || "";
  document.getElementById("editEmail").value = data.email || "";
  document.getElementById("editType").value = data.type || "individual";
  document.getElementById("editDescription").value = data.description || "";
  document.getElementById("editMaxKeys").value = data.max_keys || 5;
  document.getElementById("blockEndpointsEdit").checked = !!data.block_endpoints;
  document.getElementById("isActiveEdit").checked = data.is_active !== false;

  const modal = document.getElementById("editModal");
  if (modal) modal.classList.remove("hidden");
  if (window.lucide) window.lucide.createIcons();
}
window.openEditModal = openEditModal;

function closeEditModal() {
  const modal = document.getElementById("editModal");
  if (modal) modal.classList.add("hidden");
}
window.closeEditModal = closeEditModal;

function closePermissionsModal() {
  const modal = document.getElementById("permissionsModal");
  if (modal) modal.classList.add("hidden");
}
window.closePermissionsModal = closePermissionsModal;

function closeKeysModal() {
  const modal = document.getElementById("keysModal");
  if (modal) modal.classList.add("hidden");
}
window.closeKeysModal = closeKeysModal;

async function deleteOwner(id) {
  const confirmed = await showConfirm(
    "Revoke Owner",
    `Are you sure you want to permanently revoke owner '${id}'? This will delete all associated API keys immediately.`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf("/admin/owners/" + encodeURIComponent(id), {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) {
      showToast("Success", "Owner revoked successfully");
      delete ownersData[id];
      if (ownersTable) ownersTable.ajax.reload(null, false);
    } else {
      const err = await res.json().catch(() => ({ detail: "Deletion failed" }));
      showToast("Revocation Failed", window.formatErrorMessage ? window.formatErrorMessage(err) : err.detail, "error");
    }
  } catch (e) {
    showToast("Network Error", "Failed to communicate with server", "error");
  }
}

let currentOwnerIdForKeys = null;

async function openKeysModal(ownerId) {
  currentOwnerIdForKeys = ownerId;
  const ownerEl = document.getElementById("keysModalOwnerId");
  if (ownerEl) ownerEl.innerText = ownerId.toUpperCase();
  await loadKeys(ownerId);
  const modal = document.getElementById("keysModal");
  if (modal) modal.classList.remove("hidden");
  if (window.lucide) window.lucide.createIcons();
}
window.openKeysModal = openKeysModal;

async function loadKeys(ownerId) {
  const tbody = document.getElementById("keysList");
  if (!tbody) return;
  try {
    const res = await fetchWithCsrf(`/admin/owners/${encodeURIComponent(ownerId)}/keys`);
    if (!res.ok) throw new Error("Failed to load keys");
    const keys = await res.json();

    if (keys.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" class="p-8 text-center text-slate-500 italic text-sm">No active keys. Click 'Issue New Key' below.</td></tr>`;
      return;
    }

    tbody.innerHTML = keys
      .map(
        (k) => `
      <tr class="hover:bg-slate-800/20 transition-colors">
        <td class="p-4 font-mono font-bold text-white tracking-wider flex items-center gap-2">
          <i data-lucide="key" class="w-3.5 h-3.5 text-amber-500"></i> ${escapeHtml(k.prefix)}••••••••
        </td>
        <td class="p-4">
          <div class="flex flex-wrap gap-1">
            ${(k.scopes || []).map(s => `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">${escapeHtml(s)}</span>`).join('')}
          </div>
        </td>
        <td class="p-4 text-xs text-slate-400">
          ${k.expires_at ? new Date(k.expires_at).toLocaleDateString() : '<span class="text-slate-500 font-mono">Never</span>'}
        </td>
        <td class="p-4 text-right">
          <button type="button" onclick="revokeKey('${escapeHtml(k.prefix)}')" class="text-red-400 hover:text-red-300 transition-colors p-1.5 rounded-lg hover:bg-red-500/10 cursor-pointer" title="Revoke Key">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>
    `
      )
      .join("");
    if (window.lucide) window.lucide.createIcons();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="p-4 text-center text-red-400">Failed to load keys: ${e.message}</td></tr>`;
  }
}

async function revokeKey(prefix) {
  const confirmed = await showConfirm(
    "Revoke Key",
    `Warning: This will permanently revoke the key starting with '${prefix}'. Applications using it will immediately lose access.`
  );
  if (!confirmed) return;
  try {
    const res = await fetchWithCsrf(`/admin/keys/${encodeURIComponent(prefix)}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Revocation failed");
    showToast("Success", "API Key revoked");
    await loadKeys(currentOwnerIdForKeys);
  } catch (e) {
    showToast("Error", e.message, "error");
  }
}

async function createKeyFromModal() {
  const ownerId = currentOwnerIdForKeys;
  const selectedScopes = Array.from(
    document.querySelectorAll('input[name="newKeyScope"]:checked')
  ).map(cb => cb.value);
  if (selectedScopes.length === 0) {
    showToast("Validation Error", "Select at least one scope for the new key.", "error");
    return;
  }

  let expiryNote = "";
  try {
    const sRes = await fetchWithCsrf("/admin/settings");
    const sList = await sRes.json();
    const days = parseInt(sList.find(s => s.key === "KEY_EXPIRY_DAYS")?.value || "0", 10);
    if (days > 0) expiryNote = `\n\nAuto-rotation active: this key will expire in ${days} days.`;
  } catch(e) {}

  const confirmed = await showConfirm(
    "Issue New Key",
    `Issuing key with scopes: ${selectedScopes.join(", ")}. Existing keys for '${ownerId}' will NOT be revoked.${expiryNote} Proceed?`,
    false
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf("/admin/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: ownerId, scopes: selectedScopes }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to issue key" }));
      throw new Error(window.formatErrorMessage ? window.formatErrorMessage(err) : "Failed to issue key");
    }
    const data = await res.json();
    await loadKeys(ownerId);

    const modal = document.createElement("div");
    modal.className =
      "fixed inset-0 bg-black/90 backdrop-blur-xl flex justify-center items-center z-[100] p-4";
    modal.innerHTML = `
      <div class="bg-slate-900 border border-slate-800 p-8 rounded-3xl w-full max-w-lg shadow-2xl text-center">
        <div class="w-16 h-16 bg-emerald-500/20 text-emerald-500 rounded-2xl flex items-center justify-center mx-auto mb-6 text-2xl shadow-lg shadow-emerald-500/20">
          <i data-lucide="key" class="w-8 h-8"></i>
        </div>
        <h3 class="text-2xl font-bold text-white mb-2">New API Key Issued</h3>
        <p class="text-slate-400 text-sm mb-6">Store this key securely. It will never be shown again.</p>
        <div class="relative group mb-8">
          <div class="absolute -inset-0.5 bg-gradient-to-r from-emerald-500 to-teal-500 rounded-xl blur opacity-20 transition"></div>
          <div class="relative bg-black border border-slate-700 p-4 rounded-xl font-mono text-sm text-emerald-400 break-all select-all">
            ${escapeHtml(data.api_key)}
          </div>
        </div>
        <button onclick="this.closest('.fixed').remove(); loadKeys('${ownerId.replace(/'/g, "\\'")}');" class="w-full py-3 bg-white text-black font-bold rounded-xl hover:bg-slate-200 transition cursor-pointer">
          Acknowledged & Saved
        </button>
      </div>
    `;
    document.body.appendChild(modal);
    if (window.lucide) window.lucide.createIcons();
  } catch (e) {
    showToast("Encryption Error", e.message || "Failed to issue key.", "error");
  }
}

let currentOwnerIdForPerms = null;

async function loadPermissions(ownerId) {
  const res = await fetchWithCsrf(`/admin/owners/${encodeURIComponent(ownerId)}/permissions`);
  const perms = await res.json().catch(() => []);
  const tbody = document.getElementById("permissionsList");
  if (!tbody) return;

  if (perms.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="3" class="p-12 text-center text-slate-500 italic">
          <i data-lucide="shield-alert" class="w-8 h-8 mb-4 block mx-auto opacity-20"></i>
          No permissions active. Access will be rejected.
        </td>
      </tr>`;
    if (window.lucide) window.lucide.createIcons();
    return;
  }

  tbody.innerHTML = perms
    .map(
      (p) => `
    <tr class="hover:bg-slate-800/20 transition-colors">
      <td class="p-4">
        <div class="flex items-center gap-2">
          <div class="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500"></div>
          <span class="font-bold text-white tracking-wide">${escapeHtml(p.backend_name)}</span>
        </div>
      </td>
      <td class="p-4">
        <div class="flex flex-wrap gap-1">
          ${(p.allowed_models || []).map((m) => `<span class="px-1.5 py-0.5 bg-slate-900 border border-slate-700 rounded text-[10px] text-slate-400 font-mono">${escapeHtml(m)}</span>`).join("")}
        </div>
      </td>
      <td class="p-4 text-right">
        ${
          USER_ROLE === "admin" || USER_ROLE === "manager"
            ? `
          <button type="button" onclick="deletePermission(${p.id})" class="text-slate-500 hover:text-red-500 transition-colors p-1 cursor-pointer" title="Remove Grant">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>`
            : ""
        }
      </td>
    </tr>
  `,
    )
    .join("");
  if (window.lucide) window.lucide.createIcons();
}

async function fetchBackendsForSelect() {
  try {
    const res = await fetchWithCsrf("/admin/backends");
    if (!res.ok) return;
    const backends = await res.json();
    const select = document.getElementById("permBackend");
    if (select) {
      select.innerHTML = backends.map(b => `<option value="${escapeHtml(b.name)}">${escapeHtml(b.name)} (${escapeHtml(b.backend_type)})</option>`).join("");
    }
  } catch(e) {
    console.error("Failed to fetch backends for permissions select");
  }
}

async function openPermissionsModal(ownerId) {
  currentOwnerIdForPerms = ownerId;
  const disp = document.getElementById("permOwnerIdDisplay");
  if (disp) disp.innerText = ownerId.toUpperCase();
  const formOwner = document.getElementById("permFormOwnerId");
  if (formOwner) formOwner.value = ownerId;

  if (USER_ROLE !== "admin" && USER_ROLE !== "manager") {
    const permContainer = document.getElementById("addPermissionContainer");
    if (permContainer) permContainer.classList.add("hidden");
  }

  await fetchBackendsForSelect();
  await loadPermissions(ownerId);
  const modal = document.getElementById("permissionsModal");
  if (modal) modal.classList.remove("hidden");
  if (window.lucide) window.lucide.createIcons();
}
window.openPermissionsModal = openPermissionsModal;

$("#addPermissionForm").on("submit", async function (e) {
  e.preventDefault();
  const btn = $(this).find('button[type="submit"]');
  const originalText = btn.html();
  btn
    .prop("disabled", true)
    .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Adding...');
  if (window.lucide) window.lucide.createIcons();

  const data = {
    backend_name: document.getElementById("permBackend").value,
    allowed_models: document
      .getElementById("permModels")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    allowed_endpoints: ["*"],
  };

  try {
    const res = await fetchWithCsrf(
      `/admin/owners/${encodeURIComponent(currentOwnerIdForPerms)}/permissions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      },
    );

    if (res.ok) {
      document.getElementById("permModels").value = "*";
      showToast("Success", "Permission grant registered");
      await loadPermissions(currentOwnerIdForPerms);
    } else {
      const err = await res.json().catch(() => ({ detail: "Authorization Error" }));
      showToast("Authorization Error", window.formatErrorMessage ? window.formatErrorMessage(err) : err.detail, "error");
    }
  } catch (err) {
    showToast("Network Error", "Could not reach the server", "error");
  } finally {
    btn.prop("disabled", false).html(originalText);
    if (window.lucide) window.lucide.createIcons();
  }
});

async function deletePermission(permId) {
  const confirmed = await showConfirm(
    "Remove Permission",
    "Are you sure you want to remove this access grant permanently?"
  );
  if (!confirmed) return;
  try {
    const res = await fetchWithCsrf(
      `/admin/owners/${encodeURIComponent(currentOwnerIdForPerms)}/permissions/${permId}`,
      {
        method: "DELETE",
      },
    );
    if (!res.ok) throw new Error("Failed to delete permission");
    showToast("Success", "Permission removed");
    await loadPermissions(currentOwnerIdForPerms);
  } catch (e) {
    showToast("Error", e.message, "error");
  }
}
