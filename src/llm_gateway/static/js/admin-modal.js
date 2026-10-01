// admin-modal.js — accessibility behaviour for every page-level modal.
//
// The ten existing modals are opened and closed by toggling Tailwind's
// `hidden` class (see backends.js openAddModal / closeAddModal). None had a
// focus trap, initial focus, focus restore, scroll lock or an accessible name.
//
// Rather than rewrite 19 open/close functions, this module *observes* the
// `hidden` class on each dialog and adds the missing behaviour when it flips.
// Existing pages gain it with no changes, and new code can call
// AdminModal.open(id) / AdminModal.close(id) directly.
//
//   AdminModal.open("addModal")    show + focus
//   AdminModal.close("addModal")   hide + restore focus
//
// A dialog is anything matching DIALOG_SELECTOR. #globalConfirmModal is
// excluded: showConfirm() in ui-components.js already traps focus for it.

(function () {
  "use strict";

  var DIALOG_SELECTOR =
    '[role="dialog"], [role="alertdialog"], .fixed.inset-0[id$="Modal"]';
  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  var stack = []; // open dialogs, top-most last
  var returnFocus = new WeakMap(); // dialog -> element focused before opening
  var observed = new WeakSet();

  function isOpen(dialog) {
    return !dialog.classList.contains("hidden");
  }

  function visibleFocusable(dialog) {
    return Array.prototype.filter.call(dialog.querySelectorAll(FOCUSABLE), function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  // Give every dialog an accessible name from its first heading, and make sure
  // it announces itself as a modal dialog. aliases.html's modals had no role.
  function ensureSemantics(dialog) {
    if (!dialog.getAttribute("role")) dialog.setAttribute("role", "dialog");
    if (!dialog.getAttribute("aria-modal")) dialog.setAttribute("aria-modal", "true");
    if (dialog.getAttribute("aria-labelledby") || dialog.getAttribute("aria-label")) return;
    var heading = dialog.querySelector("h1, h2, h3, h4");
    if (heading) {
      if (!heading.id) heading.id = (dialog.id || "dialog") + "-title";
      dialog.setAttribute("aria-labelledby", heading.id);
    }
  }

  // ── Scroll lock ────────────────────────────────────────────────────────
  // Counted, so nested/stacked dialogs don't unlock the page early.
  function syncScrollLock() {
    document.documentElement.classList.toggle("overflow-hidden", stack.length > 0);
  }

  function handleOpen(dialog) {
    if (stack.indexOf(dialog) !== -1) return;
    stack.push(dialog);
    returnFocus.set(dialog, document.activeElement);
    ensureSemantics(dialog);
    syncScrollLock();

    // Prefer an explicit autofocus target, then the first field, then the
    // first control. Runs synchronously: this fires from a MutationObserver
    // microtask, by which point the `hidden` class is already gone, and
    // focus() forces layout. (Pages used setTimeout(..., 50) for this.)
    var target =
      dialog.querySelector("[autofocus]") ||
      dialog.querySelector("input:not([type=hidden]):not([disabled]), select, textarea") ||
      visibleFocusable(dialog)[0];
    if (target && !dialog.contains(document.activeElement)) target.focus();
  }

  function handleClose(dialog) {
    var i = stack.indexOf(dialog);
    if (i === -1) return;
    stack.splice(i, 1);
    syncScrollLock();
    var back = returnFocus.get(dialog);
    returnFocus.delete(dialog);
    if (back && document.contains(back) && typeof back.focus === "function") back.focus();
  }

  function sync(dialog) {
    if (isOpen(dialog)) handleOpen(dialog);
    else handleClose(dialog);
  }

  function observe(dialog) {
    if (observed.has(dialog) || dialog.id === "globalConfirmModal") return;
    observed.add(dialog);
    new MutationObserver(function () { sync(dialog); }).observe(dialog, {
      attributes: true,
      attributeFilter: ["class"],
    });
    ensureSemantics(dialog);
    sync(dialog); // in case it is already open
  }

  function scan(root) {
    (root || document).querySelectorAll(DIALOG_SELECTOR).forEach(observe);
  }

  // ── Keyboard: Escape closes the top dialog, Tab stays inside it ────────
  document.addEventListener("keydown", function (e) {
    var top = stack[stack.length - 1];
    if (!top) return;

    if (e.key === "Escape") {
      e.preventDefault();
      // Prefer the dialog's own close control so page-specific cleanup (form
      // reset, state clearing) still runs; fall back to hiding it directly.
      var closer = top.querySelector(
        '[aria-label="Close modal"], [aria-label="Close"], [data-modal-close]'
      );
      if (closer) closer.click();
      else top.classList.add("hidden");
      return;
    }

    if (e.key === "Tab") {
      var items = visibleFocusable(top);
      if (!items.length) {
        e.preventDefault();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      if (!top.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  // ── Public API ─────────────────────────────────────────────────────────
  window.AdminModal = {
    open: function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      observe(el);
      el.classList.remove("hidden");
    },
    close: function (id) {
      var el = document.getElementById(id);
      if (el) el.classList.add("hidden");
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { scan(); });
  } else {
    scan();
  }
})();
