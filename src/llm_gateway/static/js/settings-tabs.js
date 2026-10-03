// settings-tabs.js — tabbed sections on the Settings page.
//
// Section cards carry data-settings-tab="<key>"; the tab buttons carry
// data-tab="<key>". The active tab lives in the URL hash so a reload or a
// shared link (/admin/view/settings#scope) opens the same section.
(function () {
  "use strict";

  var tablist = document.getElementById("settingsTabs");
  if (!tablist) return;
  var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
  var panels = Array.prototype.slice.call(document.querySelectorAll("[data-settings-tab]"));
  var keys = tabs.map(function (t) { return t.dataset.tab; });

  // aria-controls lists every card belonging to the tab.
  tabs.forEach(function (tab) {
    var ids = panels
      .filter(function (p) { return p.dataset.settingsTab === tab.dataset.tab; })
      .map(function (p) { return p.id; });
    tab.setAttribute("aria-controls", ids.join(" "));
  });

  function select(key, focus) {
    if (keys.indexOf(key) === -1) key = keys[0];
    tabs.forEach(function (tab) {
      var on = tab.dataset.tab === key;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on) {
        if (focus) tab.focus();
        // The tab bar scrolls horizontally on phones; keep the active tab visible.
        tab.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });
    panels.forEach(function (p) {
      p.hidden = p.dataset.settingsTab !== key;
    });
    if (window.location.hash.slice(1) !== key) {
      // replaceState: switching tabs shouldn't fill the back-button history.
      history.replaceState(null, "", "#" + key);
    }
  }

  tablist.addEventListener("click", function (e) {
    var tab = e.target.closest('[role="tab"]');
    if (tab) select(tab.dataset.tab);
  });

  // Standard tablist keyboard model: arrows move, Home/End jump.
  tablist.addEventListener("keydown", function (e) {
    var i = tabs.indexOf(document.activeElement);
    if (i === -1) return;
    var next = null;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next === null) return;
    e.preventDefault();
    select(keys[next], true);
  });

  window.addEventListener("hashchange", function () {
    select(window.location.hash.slice(1));
  });

  select(window.location.hash.slice(1));
})();
