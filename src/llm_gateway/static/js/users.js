// users.js — Users page logic

// Override getInitials for email-based initials on this page
function getInitials(email) {
  if (!email) return "??";
  const parts = email.split("@")[0].split(/[._-]/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return email.substring(0, 2).toUpperCase();
}

$(document).ready(function () {
  const table = $("#usersTable").DataTable({
    ajax: {
      url: BASE_URL + "/admin/users",
      dataSrc: function (json) {
        json.forEach((item) => (usersData[item.id] = item));
        return json;
      },
      xhrFields: { withCredentials: true },
    },
    columns: [
      {
        data: "email",
        render: (data) => `
          <div class="flex items-center gap-4">
            <div class="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm shadow-lg shadow-indigo-500/20">
              ${getInitials(data)}
            </div>
            <div class="flex flex-col">
              <span class="text-white font-bold text-base tracking-tight">${escapeHtml(data)}</span>
              <span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Permanent ID</span>
            </div>
          </div>
        `,
      },
      {
        data: "is_active",
        render: function (data, type, row) {
          const statusCls = data
            ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
            : "bg-rose-500/10 text-rose-400 border-rose-500/20";
          const statusText = data ? "ACTIVE" : "INACTIVE";

          let roleVal = row.role ? row.role.value || row.role : "viewer";
          if (typeof roleVal !== "string") roleVal = "viewer";
          roleVal = roleVal.toLowerCase();

          const roleCls =
            {
              admin: "bg-purple-500/10 text-purple-400 border-purple-500/20",
              manager: "bg-blue-500/10 text-blue-400 border-blue-500/20",
              developer:
                "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
              viewer: "bg-slate-500/10 text-slate-400 border-slate-500/20",
            }[roleVal] ||
            "bg-slate-500/10 text-slate-400 border-slate-500/20";

          return `
            <div class="flex flex-col gap-2">
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border ${statusCls} w-fit">
                <span class="w-1 h-1 rounded-full ${data ? "bg-emerald-400" : "bg-rose-400"} mr-1.5 ${data ? "animate-pulse" : ""}"></span>
                ${statusText}
              </span>
              <span class="inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-extrabold border ${roleCls} w-fit uppercase">
                ${roleVal}
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
                <i class="fas fa-calendar-plus w-3"></i> <span>Joined: ${created}</span>
              </div>
              <div class="flex items-center gap-2 text-slate-500 font-medium">
                <i class="fas fa-history w-3"></i> <span>Last Login: ${lastLogin}</span>
              </div>
            </div>
          `;
        },
      },
      {
        data: null,
        className: "text-right",
        render: function (data) {
          if (USER_ROLE === "admin" || USER_ROLE === "manager") {
            let actions = `
              <button onclick="openEditModal('${data.id}')" class="p-2 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white rounded-lg transition-all border border-slate-700" title="Edit Permissions">
                <i class="fas fa-user-edit"></i>
              </button>`;

            if (USER_ROLE === "admin") {
              actions += `
                <button onclick="openResetPasswordModal('${data.id}')" class="p-2 bg-amber-500/10 text-amber-500 hover:bg-amber-500 hover:text-white rounded-lg transition-all border border-amber-500/20" title="Reset Password">
                  <i class="fas fa-key"></i>
                </button>
                <button onclick="deleteUser('${data.id}')" class="p-2 bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white rounded-lg transition-all border border-red-500/20" title="Delete Account">
                  <i class="fas fa-trash-alt"></i>
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
  });

  $("#editForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Synchronizing...');

    const id = document.getElementById("editId").value;
    const isActive = document.getElementById("editActive").checked;
    const role = document.getElementById("editRole").value;

    try {
      const res = await fetchWithCsrf(BASE_URL + "/admin/users/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: isActive, role: role }),
        credentials: "include",
      });

      if (res.ok) {
        location.reload();
      } else {
        const err = await res.json();
        showToast("Synchronization Error", err.detail || "Unknown error", "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service.", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });

  $("#resetPasswordForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn
      .prop("disabled", true)
      .html('<i class="fas fa-spinner fa-spin mr-2"></i>Securing...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    const id = data.id;

    try {
      const res = await fetchWithCsrf(
        BASE_URL + "/admin/users/" + id + "/reset-password",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: data.password }),
          credentials: "include",
        },
      );

      if (res.ok) {
        document.getElementById("resetPasswordModal").classList.add("hidden");
        showToast("Success", "Security override successful. User password has been reset.", "success");
        this.reset();
      } else {
        const err = await res.json();
        showToast("Security Error", err.detail || "Could not reset password", "error");
      }
    } catch (e) {
      showToast("Network Error", "Transmission failed.", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });

  $("#addUserForm").on("submit", async function (e) {
    e.preventDefault();
    const btn = $(this).find('button[type="submit"]');
    const originalText = btn.html();
    btn.prop("disabled", true).html('<i class="fas fa-spinner fa-spin mr-2"></i>Registering...');

    const fd = new FormData(this);
    const data = Object.fromEntries(fd.entries());
    data.is_active = !!data.is_active;
    data.is_superuser = data.role === 'ADMIN';

    try {
      const res = await fetchWithCsrf(BASE_URL + "/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        credentials: "include",
      });
      if (res.ok) {
        location.reload();
      } else {
        const err = await res.json();
        showToast("Registration Error", err.detail || "Unknown error", "error");
      }
    } catch (e) {
      showToast("Network Error", "Could not reach the administration service.", "error");
    }
    btn.prop("disabled", false).html(originalText);
  });
});

function openEditModal(id) {
  const data = usersData[id];
  if (!data) return;
  document.getElementById("editId").value = data.id;
  document.getElementById("editEmail").value = data.email;
  document.getElementById("editActive").checked = data.is_active;
  let roleVal =
    data.role && data.role.value ? data.role.value : data.role || "VIEWER";
  document.getElementById("editRole").value = roleVal.toUpperCase();
  document.getElementById("editModal").classList.remove("hidden");
}

function openResetPasswordModal(id) {
  const data = usersData[id];
  if (!data) return;
  document.getElementById("resetId").value = data.id;
  document.getElementById("resetEmail").innerText = data.email;
  document.getElementById("resetPasswordModal").classList.remove("hidden");
}

async function deleteUser(userId) {
  const user = usersData[userId];
  const confirmed = await showConfirm(
    "Confirm Deletion",
    `Are you sure you want to permanently delete user account '${user?.email}'? This action is IRREVERSIBLE.`
  );
  if (!confirmed) return;

  try {
    const res = await fetchWithCsrf(BASE_URL + "/admin/users/" + userId, {
      method: "DELETE",
      credentials: "include",
    });
    if (res.ok) {
      showToast("Success", "User deleted successfully");
      setTimeout(() => location.reload(), 1000);
    } else {
      const err = await res.json();
      showToast("Deletion Failed", err.detail || "Unauthorized or invalid request.", "error");
    }
  } catch (e) {
    showToast("Error", "Failed to communicate with deletion service.", "error");
  }
}
