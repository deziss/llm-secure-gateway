// admin-table.js — makes every data table responsive.
//
// Below the md breakpoint the CSS in input.css (`table.responsive-table`)
// turns each row into a labelled card. That CSS needs two things from script:
//
//   1. the `responsive-table` class on each table, and
//   2. each <td> carrying `data-label` = its column header text.
//
// Rows are rendered later by DataTables, by fetch().then(innerHTML = ...) and
// by hand-rolled loops, so labels are (re)applied whenever a tbody changes.
// This keeps all those render paths untouched.
//
// Also gives wide tables a horizontal scroll container at >= md: they sat in
// `div.p-1` inside an `overflow-hidden` card, so overflow was clipped.
//
// Opt a table out with the data-no-cards attribute.

(function () {
  "use strict";

  function headerLabels(table) {
    var row = table.querySelector("thead tr");
    if (!row) return [];
    return Array.prototype.map.call(row.children, function (th) {
      return (th.textContent || "").replace(/\s+/g, " ").trim();
    });
  }

  function labelCells(table) {
    var labels = headerLabels(table);
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      // Skip DataTables' / our "loading" and empty-state rows (a single
      // td spanning every column).
      if (tr.children.length === 1 && tr.children[0].hasAttribute("colspan")) return;
      Array.prototype.forEach.call(tr.children, function (td, i) {
        var label = labels[i] || "";
        if (td.getAttribute("data-label") !== label) td.setAttribute("data-label", label);
      });
    });
  }

  function enhance(table) {
    if (table.hasAttribute("data-no-cards") || table.__adminTable) return;
    table.__adminTable = true;
    table.classList.add("responsive-table");

    // Scroll, don't clip, when a table is wider than its card at >= md.
    var wrap = table.parentElement;
    if (wrap && !wrap.classList.contains("overflow-x-auto")) {
      wrap.classList.add("overflow-x-auto", "custom-scrollbar");
    }
    // The wrappers carried `p-1`, leaving a 4px gutter between the card's
    // border and the table's header/rows. Let the table run edge to edge;
    // the card's overflow-hidden + radius clips the corners.
    if (wrap) wrap.classList.remove("p-1");

    // On phones each row is its own card, so the page-level card around the
    // table would draw a second border and waste ~24px of width. Drop its
    // chrome below md. (Written as full class names so Tailwind's scan of
    // static/js picks them up.)
    var panel = wrap && wrap.parentElement;
    if (panel && panel.classList.contains("overflow-hidden")) {
      panel.classList.add(
        "max-md:border-0",
        "max-md:bg-transparent",
        "max-md:shadow-none",
        "max-md:backdrop-blur-none"
      );
    }

    labelCells(table);

    // Pin the first/last columns only while the table is wider than its
    // scroll container (see .is-overflowing in input.css).
    if (wrap && typeof ResizeObserver !== "undefined") {
      var check = function () {
        table.classList.toggle("is-overflowing", wrap.scrollWidth > wrap.clientWidth + 1);
      };
      var ro = new ResizeObserver(check);
      ro.observe(wrap);
      ro.observe(table);
      check();
    }

    var tbody = table.querySelector("tbody");
    if (tbody) {
      // Mutations we cause (setAttribute) are attribute changes, which this
      // childList observer ignores, so there is no feedback loop.
      new MutationObserver(function () { labelCells(table); }).observe(tbody, {
        childList: true,
      });
    }
  }

  function scan() {
    document.querySelectorAll("main table").forEach(enhance);
  }

  window.AdminTable = { refresh: scan };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
})();
