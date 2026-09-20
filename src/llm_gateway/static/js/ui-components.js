// ui-components.js — Shared UI utility functions

function showConfirm(title, message, isDangerous = true) {
  return new Promise((resolve) => {
    const modal = document.getElementById('globalConfirmModal');
    const content = document.getElementById('globalConfirmContent');
    const titleEl = document.getElementById('globalConfirmTitle');
    const msgEl = document.getElementById('globalConfirmMessage');
    const cancelBtn = document.getElementById('globalConfirmCancel');
    const actionBtn = document.getElementById('globalConfirmAction');

    if (titleEl) titleEl.innerText = title;
    if (msgEl) msgEl.innerText = message;

    if (isDangerous) {
      actionBtn.classList.remove('bg-indigo-600', 'hover:bg-indigo-500', 'shadow-indigo-600/20');
      actionBtn.classList.add('bg-red-600', 'hover:bg-red-500', 'shadow-red-600/20');
    } else {
      actionBtn.classList.remove('bg-red-600', 'hover:bg-red-500', 'shadow-red-600/20');
      actionBtn.classList.add('bg-indigo-600', 'hover:bg-indigo-500', 'shadow-indigo-600/20');
    }

    const previousFocus = document.activeElement;

    if (modal) modal.classList.remove('hidden');
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
    setTimeout(() => {
      if (content) {
        content.classList.remove('scale-95', 'opacity-0');
        content.classList.add('scale-100', 'opacity-100');
      }
      if (cancelBtn) cancelBtn.focus();
    }, 10);

    const focusableEls = [cancelBtn, actionBtn].filter(Boolean);
    const trapFocus = (e) => {
      if (e.key === 'Tab') {
        const idx = focusableEls.indexOf(document.activeElement);
        if (e.shiftKey) {
          focusableEls[idx <= 0 ? focusableEls.length - 1 : idx - 1]?.focus();
        } else {
          focusableEls[(idx + 1) % focusableEls.length]?.focus();
        }
        e.preventDefault();
      }
    };
    if (content) content.addEventListener('keydown', trapFocus);

    const cleanup = () => {
      if (content) {
        content.removeEventListener('keydown', trapFocus);
        content.classList.remove('scale-100', 'opacity-100');
        content.classList.add('scale-95', 'opacity-0');
      }
      setTimeout(() => {
        if (modal) modal.classList.add('hidden');
        if (previousFocus) previousFocus.focus();
      }, 200);
    };

    const escHandler = (e) => {
      if (e.key === 'Escape') { cleanup(); resolve(false); }
    };
    if (modal) modal.addEventListener('keydown', escHandler, { once: true });

    if (cancelBtn) {
      cancelBtn.onclick = () => {
        cleanup();
        resolve(false);
      };
    }

    if (actionBtn) {
      actionBtn.onclick = () => {
        cleanup();
        resolve(true);
      };
    }
  });
}

function showToast(titleOrMsg, msgOrType = '', type = 'success') {
    const toast = document.getElementById('globalToast');
    const icon = document.getElementById('toastIcon');
    const titleEl = document.getElementById('toastTitle');
    const msgEl = document.getElementById('toastMessage');

    let title = 'Notice';
    let message = '';
    let actualType = type;

    if (arguments.length === 1) {
        message = titleOrMsg;
        title = 'Notice';
        actualType = 'success';
    } else if (arguments.length === 2) {
        if (msgOrType === 'success' || msgOrType === 'error' || msgOrType === 'warning') {
            actualType = msgOrType;
            title = actualType === 'error' ? 'Error' : (actualType === 'warning' ? 'Warning' : 'Success');
            message = titleOrMsg;
        } else {
            title = titleOrMsg;
            message = msgOrType;
            actualType = 'success';
        }
    } else {
        title = titleOrMsg;
        message = msgOrType;
        actualType = type;
    }

    // Format error detail if passed as object
    if (typeof message === 'object') {
        message = window.formatErrorMessage ? window.formatErrorMessage(message) : JSON.stringify(message);
    }
    if (typeof title === 'object') {
        title = window.formatErrorMessage ? window.formatErrorMessage(title) : 'Error';
    }

    if (titleEl) titleEl.innerText = title;
    if (msgEl) msgEl.innerText = message;

    if (icon) {
        if (actualType === 'success') {
            icon.className = 'w-10 h-10 rounded-xl flex items-center justify-center text-lg bg-emerald-500/10 text-emerald-500 border border-emerald-500/20';
            icon.innerHTML = '<i data-lucide="check-circle-2" class="w-5 h-5"></i>';
        } else if (actualType === 'warning') {
            icon.className = 'w-10 h-10 rounded-xl flex items-center justify-center text-lg bg-amber-500/10 text-amber-500 border border-amber-500/20';
            icon.innerHTML = '<i data-lucide="alert-triangle" class="w-5 h-5"></i>';
        } else {
            icon.className = 'w-10 h-10 rounded-xl flex items-center justify-center text-lg bg-red-500/10 text-red-500 border border-red-500/20';
            icon.innerHTML = '<i data-lucide="alert-circle" class="w-5 h-5"></i>';
        }
    }

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }

    if (toast) {
        toast.classList.remove('translate-y-20', 'opacity-0');
        toast.classList.add('translate-y-0', 'opacity-100');

        setTimeout(() => {
            toast.classList.remove('translate-y-0', 'opacity-100');
            toast.classList.add('translate-y-20', 'opacity-0');
        }, 4000);
    }
}

function getInitials(name) {
  if (!name) return "??";
  return name
    .trim()
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

const AVATAR_COLORS = [
  ["#6366f1", "#fff"],
  ["#8b5cf6", "#fff"],
  ["#06b6d4", "#fff"],
  ["#10b981", "#fff"],
  ["#f59e0b", "#fff"],
  ["#ef4444", "#fff"],
  ["#ec4899", "#fff"],
  ["#3b82f6", "#fff"],
];

function getAvatarColor(str) {
  let hash = 0;
  for (let c of str) hash = (hash * 31 + c.charCodeAt(0)) & 0xffffffff;
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
