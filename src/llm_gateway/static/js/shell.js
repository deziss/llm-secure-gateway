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

  function openDrawer() {
    if (!sidebar || mdQuery.matches) return;
    lastFocus = document.activeElement;
    sidebar.classList.remove("-translate-x-full");
    if (backdrop) backdrop.classList.remove("hidden");
    if (openBtn) openBtn.setAttribute("aria-expanded", "true");
    document.documentElement.classList.add("overflow-hidden");
    var first = sidebar.querySelector("nav a");
    if (first) first.focus();
  }

  function closeDrawer(restoreFocus) {
    if (!sidebar) return;
    sidebar.classList.add("-translate-x-full");
    if (backdrop) backdrop.classList.add("hidden");
    if (openBtn) openBtn.setAttribute("aria-expanded", "false");
    document.documentElement.classList.remove("overflow-hidden");
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
  });

  // ── Theme ──────────────────────────────────────────────────────────────
  // Same storage contract as settings.js: "light" | "dark" stored, absence
  // means follow the OS.
  var THEMES = ["system", "light", "dark"];
  var LABELS = { system: "System theme", light: "Light theme", dark: "Dark theme" };
  var ICONS = { system: "monitor", light: "sun", dark: "moon" };
  var darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function currentTheme() {
    var saved = readStore("theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  }

  function applyTheme(theme) {
    var dark = theme === "dark" || (theme === "system" && darkQuery.matches);
    document.documentElement.classList.toggle("dark", dark);
  }

  function paintThemeButton(theme) {
    var btn = document.getElementById("themeCycle");
    if (!btn) return;
    var next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    btn.setAttribute("aria-label", LABELS[theme] + ". Switch to " + LABELS[next].toLowerCase());
    btn.setAttribute("title", LABELS[theme]);
    var icon = btn.querySelector("[data-lucide], svg");
    if (icon) {
      // lucide replaces <i data-lucide> with an <svg>; rebuild a fresh <i> so
      // the icon can change.
      var fresh = document.createElement("i");
      fresh.setAttribute("data-lucide", ICONS[theme]);
      fresh.setAttribute("class", "h-5 w-5");
      fresh.setAttribute("aria-hidden", "true");
      icon.replaceWith(fresh);
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }
  }

  var themeBtn = document.getElementById("themeCycle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
      writeStore("theme", next === "system" ? null : next);
      applyTheme(next);
      paintThemeButton(next);
    });
    paintThemeButton(currentTheme());
  }
  darkQuery.addEventListener("change", function () {
    if (currentTheme() === "system") applyTheme("system");
  });

  markActiveLink();
})();
