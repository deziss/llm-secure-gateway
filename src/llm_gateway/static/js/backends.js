// backends.js — Backends page logic and modal management

$(document).ready(function () {
  const table = $("#backendsTable").DataTable({
    ajax: {
      url: "/admin/backends",
      dataSrc: function (json) {
        json.forEach((item) => (backendsData[item.name] = item));
        return json;
      },
      xhrFields: { withCredentials: true },
    },
    columns: [
      {
        data: "name",
        render: (data, type, row) => `
          <div class="flex flex-col">
            <span class="text-white font-bold text-base tracking-tight">${escapeHtml(data)}</span>
            <span class="text-[11px] font-mono text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap max-w-[220px]">${escapeHtml(row.base_url)}</span>
          </div>
        `,
      },
      {
        data: "backend_type",
        render: (data, type, row) => {
          const types = {
            ollama: "bg-orange-500/10 text-orange-400 border-orange-500/20",
            vllm: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
            llamacpp: "bg-teal-500/10 text-teal-400 border-teal-500/20",
            openai: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
            groq: "bg-pink-500/10 text-pink-400 border-pink-500/20",
            anthropic: "bg-amber-500/10 text-amber-400 border-amber-500/20",
            google: "bg-blue-500/10 text-blue-400 border-blue-500/20",
          };
          const cls =
            types[data] ||
            "bg-slate-500/10 text-slate-400 border-slate-500/20";
          return `
            <div class="flex items-center gap-3">
              <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${cls} uppercase">${escapeHtml(data)}</span>
              <div id="health-${escapeHtml(row.name)}" class="flex items-center gap-1.5 text-[10px] font-bold text-slate-500">
                <span class="w-1.5 h-1.5 rounded-full bg-slate-600"></span> CHECKING...
              </div>
            </div>
          `;
        },
      },
      {
        data: "models",
        render: (data) => `
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 bg-slate-800 rounded-lg text-white font-bold text-xs border border-slate-700">${data ? data.length : 0}</span>
            <span class="text-slate-500 text-[10px] font-bold uppercase tracking-widest">Models</span>
          </div>
        `,
      },
      {
        data: "name",
        className: "text-right",
        render: function (data, type, row) {
          if (USER_ROLE === "admin" || USER_ROLE === "manager") {
            const escaped = escapeHtml(data);
            const safeName = data.replace(/'/g, "\'");
            const manageBtn =
              row.backend_type === "ollama"
                ? `<button type="button" onclick="openManageModelsModal('${safeName}')" class="p-2 bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500 hover:text-white rounded-lg transition-all border border-indigo-500/20 cursor-pointer" title="Manage Models"><i data-lucide="boxes" class="w-4 h-4"></i></button>`
                : `<button type="button" onclick="syncBackendModels('${safeName}')" class="p-2 bg-blue-500/10 text-blue-400 hover:bg-blue-500 hover:text-white rounded-lg transition-all border border-blue-500/20 cursor-pointer" title="Sync Models"><i data-lucide="refresh-cw" class="w-4 h-4"></i></button>`;

            return `
              <div class="flex justify-end gap-2">
                ${manageBtn}
                <button type="button" onclick="openEditModal('${safeName}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700 cursor-pointer" title="Edit"><i data-lucide="edit-3" class="w-4 h-4"></i></button>
                <button type="button" onclick="deleteBackend('${safeName}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20 cursor-pointer" title="Delete"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
              </div>
            `;
          }
          return `<span class="text-slate-600 text-[10px] font-bold uppercase tracking-widest">Read Only</span>`;
        },
      },
    ],
    dom: "t",
    pageLength: 100,
    drawCallback: function () {
      const json = this.api().ajax.json();
      if (json && Array.isArray(json)) {
        json.forEach((b) => checkBackendHealth(b.name));
      }
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    },
  });

  $("#addForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Saving...');
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }

    const fd = new FormData(this);
    const rawData = Object.fromEntries(fd.entries());
    
    // Sanitize values
    const name = (rawData.name || "").trim();
    let baseUrl = (rawData.base_url || "").trim();
    if (!baseUrl.startsWith("http://") && !baseUrl.startsWith("https://")) {
      baseUrl = "http://" + baseUrl;
    }
    baseUrl = baseUrl.replace(/\/+$/, "");

    const payload = {
      name: name,
      backend_type: rawData.backend_type,
      base_url: baseUrl,
      models: [],
      allowed_endpoints: ["*"],
      normalize_thinking: !!this.querySelector('input[name="normalize_thinking"]')?.checked,
    };

    const apiKey = (rawData.api_key || "").trim();
    if (apiKey) {
      payload.api_key = apiKey;
    }

    try {
      const res = await fetchWithCsrf("/admin/backends", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        credentials: "include",
      });

      if (res.ok) {
        showToast("Success", `Backend '${name}' registered successfully`);
        closeAddModal();
        table.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Invalid response from server" }));
        const msg = window.formatErrorMessage
          ? window.formatErrorMessage(err, "Backend registration failed")
          : (err.detail || "Validation failed");
        showToast("Registration Failed", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Failed to connect to gateway server", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }
  });

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Updating...');
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const name = data.name;

    let baseUrl = (data.base_url || "").trim();
    if (!baseUrl.startsWith("http://") && !baseUrl.startsWith("https://")) {
      baseUrl = "http://" + baseUrl;
    }
    baseUrl = baseUrl.replace(/\/+$/, "");

    const payload = {
      base_url: baseUrl,
      translation_mode: data.translation_mode || "none",
      normalize_thinking: !!this.querySelector('input[name="normalize_thinking"]')?.checked,
    };

    const apiKey = (data.api_key || "").trim();
    if (apiKey) {
      payload.api_key = apiKey;
    }

    const rawEndpoints = (data.allowed_endpoints || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    payload.allowed_endpoints = rawEndpoints.length > 0 ? rawEndpoints : ["*"];

    try {
      const res = await fetchWithCsrf("/admin/backends/" + encodeURIComponent(name), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        credentials: "include",
      });

      if (res.ok) {
        showToast("Success", `Backend '${name}' updated successfully`);
        closeEditModal();
        table.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Invalid response from server" }));
        const msg = window.formatErrorMessage
          ? window.formatErrorMessage(err, "Backend update failed")
          : (err.detail || "Validation failed");
        showToast("Update Failed", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Failed to update backend", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }
  });

  // Global modal escape key listener
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeAddModal();
      closeEditModal();
      closeManageModelsModal();
    }
  });
});

// Modal controller functions
function openAddModal() {
  const form = document.getElementById("addForm");
  if (form) form.reset();
  const modal = document.getElementById("addModal");
  if (modal) {
    modal.classList.remove("hidden");
    const input = document.getElementById("addBackendName");
    if (input) setTimeout(() => input.focus(), 50);
  }
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}
window.openAddModal = openAddModal;

function closeAddModal() {
  const modal = document.getElementById("addModal");
  if (modal) modal.classList.add("hidden");
}
window.closeAddModal = closeAddModal;

function openEditModal(name) {
  const data = backendsData[name];
  if (!data) return;
  const nameEl = document.getElementById("editName");
  if (nameEl) nameEl.value = data.name;
  const urlEl = document.getElementById("editBaseUrl");
  if (urlEl) urlEl.value = data.base_url;
  const keyEl = document.getElementById("editBackendApiKey");
  if (keyEl) keyEl.value = "";

  const endpoints = Array.isArray(data.allowed_endpoints) ? data.allowed_endpoints : [];
  const endpEl = document.getElementById("editAllowedEndpoints");
  if (endpEl) endpEl.value = endpoints.join("\n");

  const modeEl = document.getElementById("editTranslationMode");
  if (modeEl) modeEl.value = data.translation_mode || "none";

  const normEl = document.getElementById("editNormalizeThinking");
  if (normEl) normEl.checked = !!data.normalize_thinking;

  const modal = document.getElementById("editModal");
  if (modal) {
    modal.classList.remove("hidden");
    if (urlEl) setTimeout(() => urlEl.focus(), 50);
  }
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}
window.openEditModal = openEditModal;

function closeEditModal() {
  const modal = document.getElementById("editModal");
  if (modal) modal.classList.add("hidden");
}
window.closeEditModal = closeEditModal;

function openManageModelsModal(name) {
  const data = backendsData[name];
  if (!data) return;
  const titleEl = document.getElementById("manageBackendName");
  if (titleEl) titleEl.innerText = data.name;
  const pullInput = document.getElementById("pullModelName");
  if (pullInput) pullInput.value = "";
  const pullOut = document.getElementById("pullOutput");
  if (pullOut) {
    pullOut.classList.add("hidden");
    pullOut.innerText = "";
  }

  const list = document.getElementById("manageModelsList");
  if (list) {
    list.innerHTML = "";
    if (data.models && data.models.length > 0) {
      data.models.forEach((model) => {
        const li = document.createElement("li");
        li.className =
          "flex justify-between items-center p-4 hover:bg-slate-800/50 transition-colors group";
        const escapedModel = escapeHtml(model);
        const safeModel = model.replace(/'/g, "\'");
        const safeBackend = name.replace(/'/g, "\'");
        li.innerHTML = `
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400 font-bold text-xs">
              <i data-lucide="box" class="w-4 h-4"></i>
            </div>
            <span class="text-white font-medium text-sm">${escapedModel}</span>
          </div>
          <button type="button" onclick="deleteRemoteModel('${safeBackend}', '${safeModel}')" class="p-2 text-slate-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 cursor-pointer" title="Delete model">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        `;
        list.appendChild(li);
      });
    } else {
      list.innerHTML = `<li class="p-8 text-center text-slate-500 italic text-sm">No models registered for this backend.</li>`;
    }
  }

  const modal = document.getElementById("manageModelsModal");
  if (modal) modal.classList.remove("hidden");
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}
window.openManageModelsModal = openManageModelsModal;

function closeManageModelsModal() {
  const modal = document.getElementById("manageModelsModal");
  if (modal) modal.classList.add("hidden");
}
window.closeManageModelsModal = closeManageModelsModal;

async function checkBackendHealth(name) {
  const el = document.getElementById(`health-${name}`);
  if (!el) return;
  try {
    const res = await fetch(`/admin/backends/${encodeURIComponent(name)}/health`, {
      credentials: "include",
    });
    const data = await res.json();
    if (data.status === "healthy") {
      el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> <span class="text-emerald-400">ONLINE</span>`;
    } else {
      el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-red-500"></span> <span class="text-red-400">OFFLINE</span>`;
    }
  } catch (e) {
    el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-rose-500"></span> <span class="text-rose-400">ERROR</span>`;
  }
}

async function syncBackendModels(name) {
  const res = await fetchWithCsrf(`/admin/backends/${encodeURIComponent(name)}/sync-models`, {
    method: "POST",
    credentials: "include",
  });
  if (res.ok) {
    showToast("Success", "Models synced for " + name);
    $("#backendsTable").DataTable().ajax.reload(null, false);
  } else {
    showToast("Sync Failed", "Failed to sync models for " + name, "error");
  }
}

async function syncAllBackends() {
  const names = Object.keys(backendsData);
  for (const name of names) {
    checkBackendHealth(name);
    await syncBackendModels(name);
  }
}

async function deleteBackend(name) {
  const confirmed = await showConfirm(
    "Revoke Backend",
    "Are you sure you want to permanently remove backend '" + name + "'? This will affect all associated models."
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf("/admin/backends/" + encodeURIComponent(name), {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) {
      showToast("Success", "Backend deleted successfully");
      delete backendsData[name];
      $("#backendsTable").DataTable().ajax.reload(null, false);
    } else {
      const err = await res.json().catch(() => ({ detail: "Failed to delete backend" }));
      const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Failed to delete backend");
      showToast("Deletion Failed", msg, "error");
    }
  } catch (e) {
    showToast("Error", "Network error occurred while deleting backend", "error");
  }
}

async function deleteRemoteModel(backendName, modelName) {
  const confirmed = await showConfirm(
    "Delete Model",
    `Are you sure you want to delete '${modelName}' from backend '${backendName}'?`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf(
      `/admin/backends/${encodeURIComponent(backendName)}/models/${encodeURIComponent(modelName)}`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );
    if (res.ok) {
      if (backendsData[backendName] && Array.isArray(backendsData[backendName].models)) {
        backendsData[backendName].models = backendsData[backendName].models.filter((m) => m !== modelName);
      }
      showToast("Success", "Model deleted");
      $("#backendsTable").DataTable().ajax.reload(null, false);
      openManageModelsModal(backendName);
    } else {
      const err = await res.json().catch(() => ({ detail: "Failed to delete model" }));
      const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Failed to delete model");
      showToast("Deletion Failed", msg, "error");
    }
  } catch (e) {
    showToast("Error", "Network error occurred", "error");
  }
}

async function pullModel() {
  const backendName = document.getElementById("manageBackendName").innerText;
  const modelName = document.getElementById("pullModelName").value.trim();
  if (!modelName) return;

  const btn = document.getElementById("pullBtn");
  const out = document.getElementById("pullOutput");
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Pulling...';
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
  out.classList.remove("hidden");
  out.innerText = "Connection initialized...\n";

  try {
    const response = await fetchWithCsrf(
      `/admin/backends/${encodeURIComponent(backendName)}/pull`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: modelName }),
        credentials: "include",
      },
    );

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: "Failed to contact backend" }));
      const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Failed to contact backend");
      out.innerText += `
Error: ${msg}`;
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="download" class="w-4 h-4 inline-block mr-1"></i> Start Pull';
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split("\n");
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const data = JSON.parse(line);
          if (data.status) {
            out.innerText += `
> ${data.status} ${data.progress || ""}`;
            out.scrollTop = out.scrollHeight;
            if (data.status === "success") {
              syncBackendModels(backendName);
              setTimeout(() => openManageModelsModal(backendName), 1500);
            }
          }
        } catch (e) {
          out.innerText += chunk;
        }
      }
    }
  } catch (e) {
    out.innerText += "\n[CRITICAL ERROR] Network timeout or connection lost.";
  }
  btn.disabled = false;
  btn.innerHTML = '<i data-lucide="download" class="w-4 h-4 inline-block mr-1"></i> Start Pull';
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}
