// shell.js — behaviour for the admin shell: nav drawer, active link, theme.
//
// Loaded on every authenticated page by base.html. Markup lives in
// _sidebar.html and _topbar.html; this file only wires it up.

(function () {
  "use strict";

  // ── Safe storage ───────────────────────────────────────────────────────
  // localStorage throws in private windows and when site data is blocked. The
  // old inline theme script read it unguarded.
  function readStore(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }
  function writeStore(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) {
      /* preference simply won't persist */
    }
  }

  // ── Active nav link ────────────────────────────────────────────────────
  // Longest matching prefix wins, so /admin/view/playground/chat marks "Chat"
  // and not "API tester" (whose href is a prefix of it).
  function markActiveLink() {
    var path = window.location.pathname.replace(/\/+$/, "");
    var best = null;
    var bestLen = -1;
    document.querySelectorAll("#sidebar nav a[href]").forEach(function (a) {
      var href = a.getAttribute("href").replace(/\/+$/, "");
      if ((path === href || path.indexOf(href + "/") === 0) && href.length > bestLen) {
        best = a;
        bestLen = href.length;
      }
    });
    if (best) best.setAttribute("aria-current", "page");
  }

  // ── Drawer (< md) ──────────────────────────────────────────────────────
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.getElementById("sidebarBackdrop");
  var openBtn = document.getElementById("sidebarOpen");
  var closeBtn = document.getElementById("sidebarClose");
  var mdQuery = window.matchMedia("(min-width: 768px)");
  var lastFocus = null;

  function isDrawerOpen() {
    return sidebar && !sidebar.classList.contains("-translate-x-full");
  }

  // Below md the closed drawer is only translated off-screen, so without this
  // its ~14 links stay in the Tab order and are read by screen readers.
  function syncDrawerInert() {
    if (!sidebar) return;
    sidebar.inert = !mdQuery.matches && !isDrawerOpen();
  }

  function openDrawer() {
    if (!sidebar || mdQuery.matches) return;
    lastFocus = document.activeElement;
    sidebar.classList.remove("-translate-x-full");
    syncDrawerInert();
    if (backdrop) backdrop.classList.remove("hidden");
    if (openBtn) openBtn.setAttribute("aria-expanded", "true");
    document.documentElement.classList.add("overflow-hidden");
    var first = sidebar.querySelector("nav a");
    if (first) first.focus();
  }

  function closeDrawer(restoreFocus) {
    if (!sidebar) return;
    var wasOpen = isDrawerOpen();
    sidebar.classList.add("-translate-x-full");
    syncDrawerInert();
    // Called on every resize past md too, when the drawer may never have been
    // open: only release the scroll lock the drawer itself took, and keep it if
    // a modal (which shares the same lock) is still open.
    if (!wasOpen) return;
    if (backdrop) backdrop.classList.add("hidden");
    if (openBtn) openBtn.setAttribute("aria-expanded", "false");
    var modalsOpen = window.AdminModal && window.AdminModal.openCount() > 0;
    if (!modalsOpen) document.documentElement.classList.remove("overflow-hidden");
    if (restoreFocus !== false) {
      var target = lastFocus && document.contains(lastFocus) ? lastFocus : openBtn;
      if (target && target.focus) target.focus();
    }
    lastFocus = null;
  }

  // Keep Tab inside the drawer while it is open.
  function trapFocus(e) {
    if (e.key !== "Tab" || !isDrawerOpen() || mdQuery.matches) return;
    var items = sidebar.querySelectorAll("a[href], button:not([disabled])");
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (openBtn) openBtn.addEventListener("click", openDrawer);
  if (closeBtn) closeBtn.addEventListener("click", function () { closeDrawer(); });
  if (backdrop) backdrop.addEventListener("click", function () { closeDrawer(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isDrawerOpen() && !mdQuery.matches) {
      closeDrawer();
    } else {
      trapFocus(e);
    }
  });
  // Following a link inside the drawer navigates away; resizing past md makes
  // it static. In both cases make sure scroll lock and backdrop don't linger.
  mdQuery.addEventListener("change", function (e) {
    if (e.matches) closeDrawer(false);
    syncDrawerInert();
  });
  syncDrawerInert();

  // ── Theme ──────────────────────────────────────────────────────────────
  // Same storage contract as settings.js: "light" | "dark" stored, absence
  // means follow the OS.
  var darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function currentTheme() {
    var saved = readStore("theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  }

  function applyTheme(theme) {
    var dark = theme === "dark" || (theme === "system" && darkQuery.matches);
    document.documentElement.classList.toggle("dark", dark);
  }

  var themeSwitch = document.getElementById("themeSwitch");

  function paintThemeSwitch(theme) {
    if (!themeSwitch) return;
    themeSwitch.querySelectorAll("[data-theme]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.theme === theme));
    });
    // "System" and the OS's own theme look identical, so switching between
    // them changes nothing on screen and reads as a dead click. Say what
    // System currently resolves to.
    var sys = themeSwitch.querySelector('[data-theme="system"]');
    if (sys) {
      var label = "System (" + (darkQuery.matches ? "dark" : "light") + ")";
      sys.title = label;
      sys.setAttribute("aria-label", "Use system theme, currently " + (darkQuery.matches ? "dark" : "light"));
    }
  }

  if (themeSwitch) {
    themeSwitch.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme]");
      if (!btn) return;
      var theme = btn.dataset.theme;
      writeStore("theme", theme === "system" ? null : theme);
      applyTheme(theme);
      paintThemeSwitch(theme);
      document.dispatchEvent(new CustomEvent("admin:themechange", { detail: { theme: theme, source: "topbar" } }));
    });
    paintThemeSwitch(currentTheme());
  }

  // Keep the top-bar toggle and Settings' 3-way toggle in step: both write
  // the same storage key, and each announces changes on this event.
  document.addEventListener("admin:themechange", function (e) {
    if (e.detail && e.detail.source !== "topbar") paintThemeSwitch(currentTheme());
  });
  darkQuery.addEventListener("change", function () {
    paintThemeSwitch(currentTheme());
    if (currentTheme() === "system") applyTheme("system");
  });

  markActiveLink();
})();
