// users.js — Users management page logic

let usersTable = null;

$(document).ready(function () {
  usersTable = $("#usersTable").DataTable({
    ajax: {
      url: "/admin/users",
      dataSrc: function (json) {
        if (Array.isArray(json)) {
          json.forEach((item) => (usersData[item.id] = item));
        }
        return json || [];
      },
      xhrFields: { withCredentials: true },
    },
    columns: [
      {
        data: null,
        render: function (data) {
          const [bg, fg] = getAvatarColor(data.email);
          const initials = getInitials(data.email);
          return `
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm shadow-lg shadow-indigo-500/10" style="background:${bg}">
                ${initials}
              </div>
              <div class="flex flex-col">
                <span class="text-white font-bold text-base tracking-tight">${escapeHtml(data.email)}</span>
                <span class="text-[10px] font-mono text-slate-500 uppercase tracking-widest">${escapeHtml(data.id)}</span>
              </div>
            </div>
          `;
        },
      },
      {
        data: "is_active",
        render: (data, type, row) => {
          const statusCls = data
            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
            : "bg-rose-500/10 text-rose-400 border-rose-500/20";
          const statusText = data ? "ACTIVE" : "SUSPENDED";

          let roleVal =
            row.role && row.role.value ? row.role.value : row.role || "VIEWER";
          roleVal = roleVal.toUpperCase();

          const roleCls =
            {
              ADMIN: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
              MANAGER: "bg-blue-500/10 text-blue-400 border-blue-500/20",
              DEVELOPER: "bg-teal-500/10 text-teal-400 border-teal-500/20",
              VIEWER: "bg-slate-500/10 text-slate-400 border-slate-500/20",
            }[roleVal] ||
            "bg-slate-500/10 text-slate-400 border-slate-500/20";

          return `
            <div class="flex flex-col gap-2">
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border ${statusCls} w-fit">
                <span class="w-1 h-1 rounded-full ${data ? "bg-emerald-400" : "bg-rose-400"} mr-1.5 ${data ? "animate-pulse" : ""}"></span>
                ${statusText}
              </span>
              <span class="inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-extrabold border ${roleCls} w-fit uppercase">
                ${escapeHtml(roleVal)}
              </span>
            </div>
          `;
        },
      },
      {
        data: "created_at",
        render: function (data, type, row) {
          const created = data ? new Date(data).toLocaleDateString() : "-";
          const lastLogin = row.last_login
            ? new Date(row.last_login).toLocaleString()
            : "Never";
          return `
            <div class="flex flex-col text-[11px] gap-1">
              <div class="flex items-center gap-2 text-slate-400">
                <i data-lucide="calendar" class="w-3.5 h-3.5 text-slate-500"></i> <span>Joined: ${created}</span>
              </div>
              <div class="flex items-center gap-2 text-slate-500 font-medium">
                <i data-lucide="clock" class="w-3.5 h-3.5 text-slate-600"></i> <span>Last Login: ${lastLogin}</span>
              </div>
            </div>
          `;
        },
      },
      {
        data: null,
        className: "text-right",
        render: function (data) {
          const safeId = data.id.replace(/'/g, "\'");
          if (USER_ROLE === "admin" || USER_ROLE === "manager") {
            let actions = `
              <button type="button" onclick="openEditModal('${safeId}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700 cursor-pointer" title="Edit Permissions">
                <i data-lucide="edit-3" class="w-4 h-4"></i>
              </button>`;

            if (USER_ROLE === "admin") {
              actions += `
                <button type="button" onclick="openResetPasswordModal('${safeId}')" class="p-2 bg-amber-500/10 text-amber-500 hover:bg-amber-500 hover:text-white rounded-lg transition-all border border-amber-500/20 cursor-pointer" title="Reset Password">
                  <i data-lucide="key" class="w-4 h-4"></i>
                </button>
                <button type="button" onclick="deleteUser('${safeId}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20 cursor-pointer" title="Delete Account">
                  <i data-lucide="trash-2" class="w-4 h-4"></i>
                </button>
              `;
            }
            return `<div class="flex justify-end gap-2">${actions}</div>`;
          }
          return `<span class="text-slate-600 text-[10px] font-bold uppercase tracking-widest italic">Protected</span>`;
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

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Synchronizing...');
    if (window.lucide) window.lucide.createIcons();

    const id = document.getElementById("editId").value;
    const isActive = document.getElementById("editActive").checked;
    const role = document.getElementById("editRole").value;

    try {
      const res = await fetchWithCsrf("/admin/users/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: isActive, role: role }),
      });

      if (res.ok) {
        showToast("Success", "User updated successfully");
        closeEditModal();
        usersTable.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Update failed");
        showToast("Synchronization Error", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service.", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide) window.lucide.createIcons();
    }
  });

  $("#resetPasswordForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Securing...');
    if (window.lucide) window.lucide.createIcons();

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const id = data.id;

    try {
      const res = await fetchWithCsrf(
        "/admin/users/" + encodeURIComponent(id) + "/reset-password",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: data.password }),
        },
      );

      if (res.ok) {
        closeResetPasswordModal();
        showToast("Success", "Password reset successfully.", "success");
        this.reset();
      } else {
        const err = await res.json().catch(() => ({ detail: "Reset failed" }));
        const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Could not reset password");
        showToast("Security Error", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Transmission failed.", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide) window.lucide.createIcons();
    }
  });

  $("#addUserForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn.prop("disabled", true).html('<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>Registering...');
    if (window.lucide) window.lucide.createIcons();

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    data.email = (data.email || "").trim();
    data.is_active = !!data.is_active;
    data.is_superuser = data.role === 'ADMIN';

    try {
      const res = await fetchWithCsrf("/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (res.ok) {
        showToast("Success", `User '${data.email}' registered successfully`);
        closeAddUserModal();
        usersTable.ajax.reload(null, false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Registration failed" }));
        const msg = window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Registration failed");
        showToast("Registration Error", msg, "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service.", "error");
    } finally {
      btn.prop("disabled", false).html(originalText);
      if (window.lucide) window.lucide.createIcons();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeAddUserModal();
      closeEditModal();
      closeResetPasswordModal();
    }
  });
});

function openAddUserModal() {
  const form = document.getElementById("addUserForm");
  if (form) form.reset();
  const modal = document.getElementById("addUserModal");
  if (modal) modal.classList.remove("hidden");
  const input = document.getElementById("addUserEmail");
  if (input) setTimeout(() => input.focus(), 50);
  if (window.lucide) window.lucide.createIcons();
}
window.openAddUserModal = openAddUserModal;

function closeAddUserModal() {
  const modal = document.getElementById("addUserModal");
  if (modal) modal.classList.add("hidden");
}
window.closeAddUserModal = closeAddUserModal;

function openEditModal(id) {
  const data = usersData[id];
  if (!data) return;
  document.getElementById("editId").value = data.id;
  document.getElementById("editEmail").value = data.email;
  document.getElementById("editActive").checked = !!data.is_active;
  let roleVal =
    data.role && data.role.value ? data.role.value : data.role || "VIEWER";
  document.getElementById("editRole").value = roleVal.toUpperCase();
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

function openResetPasswordModal(id) {
  const data = usersData[id];
  if (!data) return;
  document.getElementById("resetId").value = data.id;
  document.getElementById("resetEmail").innerText = data.email;
  const modal = document.getElementById("resetPasswordModal");
  if (modal) modal.classList.remove("hidden");
  if (window.lucide) window.lucide.createIcons();
}
window.openResetPasswordModal = openResetPasswordModal;

function closeResetPasswordModal() {
  const modal = document.getElementById("resetPasswordModal");
  if (modal) modal.classList.add("hidden");
}
window.closeResetPasswordModal = closeResetPasswordModal;

async function deleteUser(userId) {
  const user = usersData[userId];
  const confirmed = await showConfirm(
    "Confirm Deletion",
    `Are you sure you want to permanently delete user account '${user?.email || userId}'? This action is IRREVERSIBLE.`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf("/admin/users/" + encodeURIComponent(userId), {
      method: "DELETE",
    });
    if (res.ok) {
      showToast("Success", "User deleted successfully");
      delete usersData[userId];
      if (usersTable) usersTable.ajax.reload(null, false);
    } else {
      const err = await res.json().catch(() => ({ detail: "Deletion failed" }));
      showToast("Deletion Failed", window.formatErrorMessage ? window.formatErrorMessage(err) : (err.detail || "Unauthorized or invalid request."), "error");
    }
  } catch (e) {
    showToast("Error", "Failed to communicate with deletion service.", "error");
  }
}
