// backends.js — Backends page logic

$(document).ready(function () {
  const table = $("#backendsTable").DataTable({
    ajax: {
      url: BASE_URL + "/admin/backends",
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
            <span class="text-[11px] font-mono text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap max-w-[200px]">${escapeHtml(row.base_url)}</span>
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
            openai:
              "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
            groq: "bg-pink-500/10 text-pink-400 border-pink-500/20",
            anthropic: "bg-amber-500/10 text-amber-400 border-amber-500/20",
            google: "bg-blue-500/10 text-blue-400 border-blue-500/20",
          };
          const cls =
            types[data] ||
            "bg-slate-500/10 text-slate-400 border-slate-500/20";
          return `
            <div class="flex items-center gap-3">
              <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${cls} uppercase">${data}</span>
              <div id="health-${row.name}" class="flex items-center gap-1.5 text-[10px] font-bold text-slate-500">
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
          if (USER_ROLE === "admin") {
            const manageBtn =
              row.backend_type === "ollama"
                ? `<button onclick="openManageModelsModal('${data}')" class="p-2 bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500 hover:text-white rounded-lg transition-all border border-indigo-500/20" title="Manage Models"><i class="fas fa-cubes"></i></button>`
                : `<button onclick="syncBackendModels('${data}')" class="p-2 bg-blue-500/10 text-blue-400 hover:bg-blue-500 hover:text-white rounded-lg transition-all border border-blue-500/20" title="Sync Models"><i class="fas fa-sync-alt"></i></button>`;

            return `
              <div class="flex justify-end gap-2">
                ${manageBtn}
                <button onclick="openEditModal('${data}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700" title="Edit"><i class="fas fa-edit"></i></button>
                <button onclick="deleteBackend('${data}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20" title="Delete"><i class="fas fa-trash"></i></button>
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
      if (json) {
        json.forEach((b) => checkBackendHealth(b.name));
      }
    },
  });

  $("#addForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Processing...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    data.models = [];
    data.allowed_endpoints = ["*"];
    data.normalize_thinking = this.querySelector('input[name="normalize_thinking"]').checked;

    try {
      const res = await fetchWithCsrf(BASE_URL + "/admin/backends", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        credentials: "include",
      });

      if (res.ok) {
        showToast("Success", "Backend registered successfully");
        setTimeout(() => location.reload(), 1000);
      } else {
        const err = await res.json();
        showToast("Registration Failed", err.detail || "Validation failed", "error");
      }
    } catch (e) {
      showToast("Error", "Failed to connect to gateway", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Updating...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const name = data.name;
    const payload = { base_url: data.base_url };
    if (data.api_key) payload.api_key = data.api_key;
    const rawEndpoints = (data.allowed_endpoints || "").split("\n").map(s => s.trim()).filter(Boolean);
    payload.allowed_endpoints = rawEndpoints.length > 0 ? rawEndpoints : ["*"];
    payload.translation_mode = data.translation_mode;
    payload.normalize_thinking = this.querySelector('input[name="normalize_thinking"]').checked;

    const res = await fetchWithCsrf(BASE_URL + "/admin/backends/" + name, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "include",
    });

    if (res.ok) {
      showToast("Success", "Backend updated successfully");
      setTimeout(() => location.reload(), 1000);
    } else {
      const err = await res.json();
      showToast("Update Failed", err.detail || "Unknown error", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });
});

async function checkBackendHealth(name) {
  const el = document.getElementById(`health-${name}`);
  if (!el) return;
  try {
    const res = await fetch(`${BASE_URL}/admin/backends/${name}/health`, {
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
  const res = await fetchWithCsrf(`${BASE_URL}/admin/backends/${name}/sync-models`, {
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

function openEditModal(name) {
  const data = backendsData[name];
  if (!data) return;
  document.getElementById("editName").value = data.name;
  document.getElementById("editBaseUrl").value = data.base_url;
  const endpoints = Array.isArray(data.allowed_endpoints) ? data.allowed_endpoints : [];
  document.getElementById("editAllowedEndpoints").value = endpoints.join("\n");
  document.getElementById("editTranslationMode").value = data.translation_mode || "none";
  document.getElementById("editNormalizeThinking").checked = !!data.normalize_thinking;
  document.getElementById("editModal").classList.remove("hidden");
}

async function deleteBackend(name) {
  const confirmed = await showConfirm(
    "Revoke Backend",
    "Are you sure you want to permanently remove backend " + name + "? This will affect all associated models."
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf(BASE_URL + "/admin/backends/" + name, {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) {
      showToast("Success", "Backend deleted successfully");
      setTimeout(() => location.reload(), 1000);
    } else {
      const err = await res.json();
      showToast("Deletion Failed", err.detail || "Failed to delete backend", "error");
    }
  } catch (e) {
    showToast("Error", "Network error occurred", "error");
  }
}

function openManageModelsModal(name) {
  const data = backendsData[name];
  if (!data) return;
  document.getElementById("manageBackendName").innerText = data.name;
  document.getElementById("pullModelName").value = "";
  const pullOut = document.getElementById("pullOutput");
  pullOut.classList.add("hidden");
  pullOut.innerText = "";

  const list = document.getElementById("manageModelsList");
  list.innerHTML = "";

  if (data.models && data.models.length > 0) {
    data.models.forEach((model) => {
      const li = document.createElement("li");
      li.className =
        "flex justify-between items-center p-4 hover:bg-slate-800/50 transition-colors group";
      li.innerHTML = `
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400 font-bold text-xs">
            <i class="fas fa-cube"></i>
          </div>
          <span class="text-white font-medium">${model}</span>
        </div>
        <button onclick="deleteRemoteModel('${name}', '${model}')" class="p-2 text-slate-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100">
          <i class="fas fa-trash-alt"></i>
        </button>
      `;
      list.appendChild(li);
    });
  } else {
    list.innerHTML = `<li class="p-8 text-center text-slate-500 italic text-sm">No models registered for this backend.</li>`;
  }
  document.getElementById("manageModelsModal").classList.remove("hidden");
}

async function deleteRemoteModel(backendName, modelName) {
  const confirmed = await showConfirm(
    "Delete Model",
    `Are you sure you want to delete '${modelName}' from remote backend?`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf(
      `${BASE_URL}/admin/backends/${backendName}/models/${encodeURIComponent(modelName)}`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );
    if (res.ok) {
      if (backendsData[backendName]) {
        backendsData[backendName].models = backendsData[
          backendName
        ].models.filter((m) => m !== modelName);
      }
      showToast("Success", "Model deleted");
      $("#backendsTable").DataTable().ajax.reload(null, false);
      openManageModelsModal(backendName);
    } else {
      const err = await res.json();
      showToast("Deletion Failed", err.detail || "Failed to delete model", "error");
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
  btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Pulling...';
  out.classList.remove("hidden");
  out.innerText = "Connection initialized...\n";

  try {
    const response = await fetchWithCsrf(
      `${BASE_URL}/admin/backends/${backendName}/pull`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: modelName }),
        credentials: "include",
      },
    );

    if (!response.ok) {
      const err = await response.json();
      out.innerText += `\nError: ${err.detail || "Failed to contact backend"}`;
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-download mr-1"></i> Start Pull';
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
            out.innerText += `\n> ${data.status} ${data.progress || ""}`;
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
  btn.innerHTML = '<i class="fas fa-download mr-1"></i> Start Pull';
}
