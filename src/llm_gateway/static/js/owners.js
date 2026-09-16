// owners.js — Owners page logic

$(document).ready(function () {
  const table = $("#ownersTable").DataTable({
    ajax: {
      url: BASE_URL + "/admin/owners",
      dataSrc: function (json) {
        json.forEach((item) => (ownersData[item.id] = item));
        return json;
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
            data === "project" ? "fas fa-project-diagram" : "fas fa-user-tie";
          return `<span class="px-2.5 py-1 rounded-lg text-[10px] font-bold border ${cls} uppercase flex items-center gap-1.5 w-fit"><i class="${icon}"></i> ${data}</span>`;
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
          let btns = `
            <button onclick="openKeysModal('${data}')" class="p-2 bg-amber-500/10 text-amber-500 hover:bg-amber-500 hover:text-white rounded-lg transition-all border border-amber-500/20" title="Manage API Keys"><i class="fas fa-key"></i></button>
            <button onclick="openPermissionsModal('${data}')" class="p-2 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500 hover:text-white rounded-lg transition-all border border-emerald-500/20" title="Access Grants"><i class="fas fa-lock"></i></button>
            <button onclick="openEditModal('${data}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700" title="Edit Profile"><i class="fas fa-user-edit"></i></button>
          `;
          if (["admin", "manager"].includes(USER_ROLE)) {
            btns += `<button onclick="deleteOwner('${data}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20" title="Revoke Owner"><i class="fas fa-trash-alt"></i></button>`;
          }
          return `<div class="flex justify-end gap-2">${btns}</div>`;
        },
      },
    ],
    dom: "t",
    pageLength: 50,
    retrieve: true,
  });

  $("#addForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Registering...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    data.block_endpoints =
      document.getElementById("blockEndpointsAdd").checked;
    if (data.max_keys) data.max_keys = parseInt(data.max_keys);

    const res = await fetchWithCsrf(BASE_URL + "/admin/owners", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      credentials: "include",
    });

    if (res.ok) {
      location.reload();
    } else {
      const err = await res.json();
      showToast("Registration Failed", err.detail || "ID already exists or invalid data.", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Syncing...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const id = data.id;
    delete data.id;
    data.block_endpoints =
      document.getElementById("blockEndpointsEdit").checked;
    data.is_active =
      document.getElementById("isActiveEdit").checked;
    if (data.max_keys) data.max_keys = parseInt(data.max_keys);

    const res = await fetchWithCsrf(BASE_URL + "/admin/owners/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      credentials: "include",
    });

    if (res.ok) {
      location.reload();
    } else {
      const err = await res.json();
      showToast("Update Failed", err.detail || "Unknown error", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });
});

async function openEditModal(ownerId) {
  const data = ownersData[ownerId];
  if (!data) return;
  document.getElementById("editId").value = data.id;
  document.getElementById("editName").value = data.name;
  document.getElementById("editEmail").value = data.email || "";
  document.getElementById("editType").value = data.type;
  document.getElementById("blockEndpointsEdit").checked = data.block_endpoints;
  document.getElementById("isActiveEdit").checked = data.is_active !== false;
  document.getElementById("editDescription").value = data.description || "";
  document.getElementById("editMaxKeys").value = data.max_keys || 5;
  document.getElementById("editModal").classList.remove("hidden");
}

async function deleteOwner(ownerId) {
  const confirmed = await showConfirm(
    "Revoke Owner",
    `Are you sure you want to permanently delete API Owner '${ownerId}'? This action is IRREVERSIBLE and will revoke all associated API keys.`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf(BASE_URL + "/admin/owners/" + ownerId, {
      method: "DELETE",
      credentials: "include",
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Deletion failed");
    }
    showToast("Success", "Owner deleted successfully");
    setTimeout(() => location.reload(), 1000);
  } catch (e) {
    showToast("Error", e.message, "error");
  }
}

let currentOwnerIdForKeys = null;

async function openKeysModal(ownerId) {
  currentOwnerIdForKeys = ownerId;
  document.getElementById("keysOwnerIdDisplay").innerText = ownerId.toUpperCase();
  await loadKeys(ownerId);
  document.getElementById("keysModal").classList.remove("hidden");
}

async function loadKeys(ownerId) {
  const res = await fetch(BASE_URL + `/admin/owners/${ownerId}/keys`, { credentials: "include" });
  const keys = await res.json();
  const tbody = document.getElementById("keysList");
  if (keys.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="p-8 text-center text-slate-500 italic">No active API keys found.</td></tr>`;
    return;
  }
  const scopeColors = {
    "llm:chat": "bg-blue-500/10 text-blue-400 border-blue-500/20",
    "llm:embed": "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    "llm:read": "bg-slate-500/10 text-slate-400 border-slate-500/20",
    "llm:*": "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
    "admin:*": "bg-purple-500/10 text-purple-400 border-purple-500/20",
    "*": "bg-rose-500/10 text-rose-400 border-rose-500/20",
    "chat": "bg-blue-500/10 text-blue-400 border-blue-500/20",
    "embeddings": "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    "admin": "bg-purple-500/10 text-purple-400 border-purple-500/20",
    "read_only": "bg-slate-500/10 text-slate-400 border-slate-500/20",
  };
  const expiryBadge = (k) => {
    if (!k.expires_at) return '<span class="text-slate-600 text-[10px] italic">No expiry</span>';
    const ms = new Date(k.expires_at) - new Date();
    const days = Math.ceil(ms / 86400000);
    if (days <= 0) return '<span class="text-rose-400 text-[10px] font-bold">EXPIRED</span>';
    if (days <= 7) return `<span class="text-amber-400 text-[10px] font-bold">${days}d left</span>`;
    return `<span class="text-slate-400 text-[10px]">${days}d left</span>`;
  };
  tbody.innerHTML = keys.filter(k => k.is_active).map(k => {
    const created = new Date(k.created_at).toLocaleDateString();
    const scopes = (k.scopes || []).map(s => {
      const cls = scopeColors[s] || "bg-slate-500/10 text-slate-400 border-slate-500/20";
      return `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold border ${cls}">${escapeHtml(s)}</span>`;
    }).join(" ");
    return `
    <tr class="hover:bg-slate-800/20 transition-colors">
      <td class="p-4 font-mono text-amber-400 font-bold">${escapeHtml(k.prefix)}</td>
      <td class="p-4"><div class="flex flex-wrap gap-1">${scopes || '<span class="text-slate-600 text-[10px] italic">none</span>'}</div></td>
      <td class="p-4 text-slate-400 text-xs">${created}</td>
      <td class="p-4">${expiryBadge(k)}</td>
      <td class="p-4 text-right">
        <button onclick="revokeKey('${escapeHtml(k.prefix)}')" class="text-slate-500 hover:text-red-500 transition-colors p-1" title="Revoke Key">
          <i class="fas fa-trash"></i>
        </button>
      </td>
    </tr>`;
  }).join("");
  if (tbody.innerHTML === "") {
      tbody.innerHTML = `<tr><td colspan="5" class="p-8 text-center text-slate-500 italic">No active API keys found.</td></tr>`;
  }
}

async function revokeKey(prefix) {
  const confirmed = await showConfirm(
    "Revoke Key",
    `Warning: This will permanently revoke the key starting with '${prefix}'. Applications using it will immediately lose access.`
  );
  if (!confirmed) return;
  try {
    const res = await fetchWithCsrf(BASE_URL + `/admin/keys/${prefix}`, { method: "DELETE", credentials: "include" });
    if (!res.ok) throw new Error("Revocation failed");
    showToast("Success", "API Key revoked");
    await loadKeys(currentOwnerIdForKeys);
  } catch (e) {
    showToast("Error", e.message, "error");
  }
}

async function createKeyFromModal() {
  const ownerId = currentOwnerIdForKeys;
  // Read selected scopes from checkboxes
  const selectedScopes = Array.from(
    document.querySelectorAll('input[name="newKeyScope"]:checked')
  ).map(cb => cb.value);
  if (selectedScopes.length === 0) {
    showToast("Validation Error", "Select at least one scope for the new key.", "error");
    return;
  }
  // Check if auto-rotation is enabled
  let expiryNote = "";
  try {
    const sRes = await fetch(BASE_URL + "/admin/settings", { credentials: "include" });
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
    const res = await fetchWithCsrf(BASE_URL + "/admin/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: ownerId, scopes: selectedScopes }),
      credentials: "include",
    });
    if (!res.ok) throw new Error(`Status ${res.status}`);
    const data = await res.json();
    await loadKeys(ownerId);

    const modal = document.createElement("div");
    modal.className =
      "fixed inset-0 bg-black/90 backdrop-blur-xl flex justify-center items-center z-[100] p-4";
    modal.innerHTML = `
      <div class="bg-slate-900 border border-slate-800 p-8 rounded-3xl w-full max-w-lg shadow-2xl text-center">
        <div class="w-16 h-16 bg-emerald-500/20 text-emerald-500 rounded-2xl flex items-center justify-center mx-auto mb-6 text-2xl shadow-lg shadow-emerald-500/20">
          <i class="fas fa-key"></i>
        </div>
        <h3 class="text-2xl font-bold text-white mb-2">New API Key Issued</h3>
        <p class="text-slate-400 text-sm mb-6">Store this key securely. It will never be shown again.</p>
        <div class="relative group mb-8">
          <div class="absolute -inset-0.5 bg-gradient-to-r from-emerald-500 to-teal-500 rounded-xl blur opacity-20 transition"></div>
          <div class="relative bg-black border border-slate-700 p-4 rounded-xl font-mono text-sm text-emerald-400 break-all select-all">
            ${data.api_key}
          </div>
        </div>
        <button onclick="this.closest('.fixed').remove(); loadKeys('${ownerId}');" class="w-full py-3 bg-white text-black font-bold rounded-xl hover:bg-slate-200 transition">
          Acknowledged & Saved
        </button>
      </div>
    `;
    document.body.appendChild(modal);
  } catch (e) {
    showToast("Encryption Error", "Failed to issue key. Check system logs.", "error");
  }
}

let currentOwnerIdForPerms = null;

async function loadPermissions(ownerId) {
  const res = await fetch(BASE_URL + `/admin/owners/${ownerId}/permissions`, {
    credentials: "include",
  });
  const perms = await res.json();
  const tbody = document.getElementById("permissionsList");

  if (perms.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="3" class="p-12 text-center text-slate-500 italic">
          <i class="fas fa-ghost text-4xl mb-4 block opacity-10"></i>
          No permissions active. Access will be rejected.
        </td>
      </tr>`;
    return;
  }

  tbody.innerHTML = perms
    .map(
      (p) => `
    <tr class="hover:bg-slate-800/20 transition-colors">
      <td class="p-4">
        <div class="flex items-center gap-2">
          <div class="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500"></div>
          <span class="font-bold text-white tracking-wide">${p.backend_name}</span>
        </div>
      </td>
      <td class="p-4">
        <div class="flex flex-wrap gap-1">
          ${p.allowed_models.map((m) => `<span class="px-1.5 py-0.5 bg-slate-900 border border-slate-700 rounded text-[10px] text-slate-400 font-mono">${escapeHtml(m)}</span>`).join("")}
        </div>
      </td>
      <td class="p-4 text-right">
        ${
          USER_ROLE === "admin"
            ? `
          <button onclick="deletePermission(${p.id})" class="text-slate-500 hover:text-red-500 transition-colors p-1">
            <i class="fas fa-times-circle"></i>
          </button>`
            : ""
        }
      </td>
    </tr>
  `,
    )
    .join("");
}

async function fetchBackendsForSelect() {
  try {
    const res = await fetch(BASE_URL + "/admin/backends", { credentials: "include" });
    if (!res.ok) return;
    const backends = await res.json();
    const select = document.getElementById("permBackend");
    select.innerHTML = backends.map(b => `<option value="${b.name}">${b.name} (${b.backend_type})</option>`).join("");
  } catch(e) {
    console.error("Failed to fetch backends for permissions select");
  }
}

async function openPermissionsModal(ownerId) {
  currentOwnerIdForPerms = ownerId;
  document.getElementById("permOwnerIdDisplay").innerText =
    ownerId.toUpperCase();
  document.getElementById("permFormOwnerId").value = ownerId;

  if (USER_ROLE !== "admin") {
    document.getElementById("addPermissionContainer").classList.add("hidden");
  }

  await fetchBackendsForSelect();
  await loadPermissions(ownerId);
  document.getElementById("permissionsModal").classList.remove("hidden");
}

$("#addPermissionForm").on("submit", async function (e) {
  e.preventDefault();
  const btn = $(this).find('button[type="submit"]');
  btn
    .prop("disabled", true)
    .html('<i class="fas fa-spinner fa-spin mr-2"></i>Adding...');

  const data = {
    backend_name: document.getElementById("permBackend").value,
    allowed_models: document
      .getElementById("permModels")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    allowed_endpoints: ["*"],  // Endpoint auth handled by API key scopes
  };

  const res = await fetchWithCsrf(
    BASE_URL + `/admin/owners/${currentOwnerIdForPerms}/permissions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      credentials: "include",
    },
  );

  if (res.ok) {
    document.getElementById("permModels").value = "*";
    document.getElementById("permEndpoints").value = "*";
    await loadPermissions(currentOwnerIdForPerms);
  } else {
    const err = await res.json();
    showToast("Authorization Error", err.detail || "Check backend availability.", "error");
  }
  btn.prop("disabled", false).html("Add Permission Grant");
});

async function deletePermission(permId) {
  const confirmed = await showConfirm(
    "Remove Permission",
    "Are you sure you want to remove this access grant permanently?"
  );
  if (!confirmed) return;
  try {
    const res = await fetchWithCsrf(
      BASE_URL +
        `/admin/owners/${currentOwnerIdForPerms}/permissions/${permId}`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );
    if (!res.ok) throw new Error("Failed to delete permission");
    showToast("Success", "Permission removed");
    await loadPermissions(currentOwnerIdForPerms);
  } catch (e) {
    showToast("Error", e.message, "error");
  }
}
