// gateway-table.js — Lightweight, zero-dependency, accessible data table component.
// Replaces jQuery & DataTables across LLM Secure Gateway.
// Features: Instant search, column sort, async ajax fetch, responsive card support,
// session expiry interception, and full keyboard navigation.

(function () {
  "use strict";

  const instances = new Map();

  function escapeText(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getByPath(obj, path) {
    if (!path || !obj) return obj;
    const parts = path.split(".");
    let curr = obj;
    for (let i = 0; i < parts.length; i++) {
      if (curr === null || curr === undefined) return undefined;
      curr = curr[parts[i]];
    }
    return curr;
  }

  class GatewayTable {
    constructor(elementOrSelector, options = {}) {
      this.table = typeof elementOrSelector === "string"
        ? document.querySelector(elementOrSelector)
        : elementOrSelector;

      if (!this.table) {
        console.warn("GatewayTable: target table not found", elementOrSelector);
        return;
      }

      this.options = Object.assign({
        columns: [],
        ajax: null,
        data: [],
        pageLength: 50,
        searchable: true,
        sortable: true,
        emptyText: "No records found",
        loadingText: "Loading data...",
        drawCallback: null,
      }, options);

      this.rawData = [];
      this.filteredData = [];
      this.sortCol = -1;
      this.sortDir = 1; // 1 = asc, -1 = desc
      this.searchTerm = "";
      this.page = 0;

      this._initDOM();
      instances.set(this.table, this);

      if (this.options.ajax) {
        this.reload();
      } else if (Array.isArray(this.options.data) && this.options.data.length > 0) {
        this.setData(this.options.data);
      }
    }

    _initDOM() {
      this.table.classList.add("gateway-table");
      const thead = this.table.querySelector("thead");
      if (thead) {
        const ths = thead.querySelectorAll("th");
        ths.forEach((th, idx) => {
          const colDef = this.options.columns[idx] || {};
          if (colDef.orderable !== false && this.options.sortable) {
            th.classList.add("cursor-pointer", "select-none", "hover:text-ink");
            th.setAttribute("tabindex", "0");
            th.setAttribute("role", "columnheader");
            th.setAttribute("aria-sort", "none");

            const iconSpan = document.createElement("span");
            iconSpan.className = "inline-block ml-1 opacity-40 sort-icon text-micro";
            iconSpan.innerHTML = "↕";
            th.appendChild(iconSpan);

            th.addEventListener("click", () => this.sortBy(idx));
            th.addEventListener("keydown", (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                this.sortBy(idx);
              }
            });
          }
        });
      }

      // Add a clean search toolbar if enabled and not already present
      if (this.options.searchable && !this.table.parentElement.querySelector(".gateway-table-search")) {
        const wrap = document.createElement("div");
        wrap.className = "gateway-table-search flex items-center justify-between gap-4 mb-3 text-caption";
        wrap.innerHTML = `
          <div class="relative flex-1 max-w-sm">
            <input type="search" placeholder="Filter rows... (Press / to focus)"
                   class="w-full pl-8 pr-3 py-1.5 rounded-control bg-surface-inset border border-hairline text-ink text-xs focus:border-accent focus:outline-none transition-colors" />
            <span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted pointer-events-none">
              <i data-lucide="search" class="w-3.5 h-3.5"></i>
            </span>
          </div>
          <div class="gateway-table-count text-micro text-ink-muted font-medium"></div>
        `;

        const input = wrap.querySelector("input");
        input.addEventListener("input", (e) => {
          this.searchTerm = e.target.value.toLowerCase().trim();
          this.applyFilter();
        });

        // Keyboard shortcut: '/' to focus search
        document.addEventListener("keydown", (e) => {
          if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
            e.preventDefault();
            input.focus();
          }
        });

        this.table.parentNode.insertBefore(wrap, this.table);
        this.searchWrap = wrap;
        if (window.lucide && typeof window.lucide.createIcons === "function") {
          window.lucide.createIcons({ root: wrap });
        }
      }
    }

    async reload(callback, resetPaging) {
      if (!this.options.ajax) return;
      this._renderLoading();

      const url = typeof this.options.ajax === "string" ? this.options.ajax : this.options.ajax.url;
      try {
        const fetcher = window.fetchWithCsrf || window.fetch;
        const res = await fetcher(url, { credentials: "include" });
        if (res.status === 401 || res.status === 403) {
          if (typeof window.handleSessionExpiry === "function") {
            window.handleSessionExpiry();
          }
          return;
        }

        if (!res.ok) {
          throw new Error("HTTP " + res.status + " " + res.statusText);
        }

        let json = await res.json();
        this._lastAjaxJson = json;

        if (this.options.ajax.dataSrc && typeof this.options.ajax.dataSrc === "function") {
          json = this.options.ajax.dataSrc(json);
        }

        this.setData(json || []);
        if (typeof callback === "function") callback(json);
      } catch (err) {
        console.error("GatewayTable error:", err);
        this._renderError(err.message || "Failed to load data");
      }
    }

    setData(data) {
      this.rawData = Array.isArray(data) ? data : [];
      this.applyFilter();
    }

    applyFilter() {
      if (!this.searchTerm) {
        this.filteredData = this.rawData.slice();
      } else {
        const term = this.searchTerm;
        this.filteredData = this.rawData.filter((row) => {
          return Object.values(row).some((val) => {
            if (val === null || val === undefined) return false;
            if (typeof val === "object") return JSON.stringify(val).toLowerCase().includes(term);
            return String(val).toLowerCase().includes(term);
          });
        });
      }

      if (this.sortCol >= 0) {
        this._sortData();
      }

      this.render();
    }

    sortBy(colIdx) {
      if (this.sortCol === colIdx) {
        this.sortDir = -this.sortDir;
      } else {
        this.sortCol = colIdx;
        this.sortDir = 1;
      }

      // Update header ARIA and icons
      const ths = this.table.querySelectorAll("thead th");
      ths.forEach((th, idx) => {
        const icon = th.querySelector(".sort-icon");
        if (idx === colIdx) {
          th.setAttribute("aria-sort", this.sortDir === 1 ? "ascending" : "descending");
          if (icon) icon.innerHTML = this.sortDir === 1 ? "▲" : "▼";
          if (icon) icon.classList.remove("opacity-40");
          if (icon) icon.classList.add("text-accent", "opacity-100");
        } else {
          th.setAttribute("aria-sort", "none");
          if (icon) icon.innerHTML = "↕";
          if (icon) icon.classList.add("opacity-40");
          if (icon) icon.classList.remove("text-accent", "opacity-100");
        }
      });

      this._sortData();
      this.render();
    }

    _sortData() {
      const colDef = this.options.columns[this.sortCol];
      if (!colDef) return;
      const key = colDef.data;
      const dir = this.sortDir;

      this.filteredData.sort((a, b) => {
        let valA = key ? getByPath(a, key) : a;
        let valB = key ? getByPath(b, key) : b;
        if (valA === valB) return 0;
        if (valA === null || valA === undefined) return 1;
        if (valB === null || valB === undefined) return -1;
        if (typeof valA === "string") return dir * valA.localeCompare(String(valB));
        return dir * (valA > valB ? 1 : -1);
      });
    }

    render() {
      const tbody = this.table.querySelector("tbody") || this.table.appendChild(document.createElement("tbody"));
      tbody.innerHTML = "";

      if (this.searchWrap) {
        const countEl = this.searchWrap.querySelector(".gateway-table-count");
        if (countEl) {
          countEl.textContent = this.filteredData.length === this.rawData.length
            ? `${this.rawData.length} records`
            : `Showing ${this.filteredData.length} of ${this.rawData.length}`;
        }
      }

      if (this.filteredData.length === 0) {
        const colCount = Math.max(1, this.options.columns.length || this.table.querySelectorAll("thead th").length);
        const tr = document.createElement("tr");
        tr.innerHTML = `<td colspan="${colCount}" class="text-center py-10 text-ink-muted text-xs">
          <div class="font-medium text-ink mb-1">${escapeText(this.options.emptyText)}</div>
          ${this.searchTerm ? '<div class="text-micro text-ink-muted">No rows match your search filter.</div>' : ''}
        </td>`;
        tbody.appendChild(tr);
        this._afterDraw();
        return;
      }

      const frag = document.createDocumentFragment();
      this.filteredData.forEach((row, rowIdx) => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-surface-inset/50 transition-colors border-b border-hairline/60";

        this.options.columns.forEach((col) => {
          const td = document.createElement("td");
          if (col.className) td.className = col.className;
          else td.className = "py-3 px-3 align-middle text-ink text-xs";

          const val = col.data ? getByPath(row, col.data) : row;
          if (typeof col.render === "function") {
            td.innerHTML = col.render(val, "display", row, { row: rowIdx, col: col });
          } else {
            td.textContent = val !== undefined && val !== null ? String(val) : "—";
          }
          tr.appendChild(td);
        });

        frag.appendChild(tr);
      });

      tbody.appendChild(frag);
      this._afterDraw();
    }

    _afterDraw() {
      if (window.AdminTable && typeof window.AdminTable.refresh === "function") {
        window.AdminTable.refresh();
      }
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons({ root: this.table });
      }
      if (typeof this.options.drawCallback === "function") {
        this.options.drawCallback.call(this);
      }
    }

    _renderLoading() {
      const tbody = this.table.querySelector("tbody") || this.table.appendChild(document.createElement("tbody"));
      const colCount = Math.max(1, this.options.columns.length || this.table.querySelectorAll("thead th").length);
      tbody.innerHTML = `<tr><td colspan="${colCount}" class="text-center py-12 text-ink-muted text-xs">
        <div class="inline-flex items-center gap-2">
          <span class="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin"></span>
          <span>${escapeText(this.options.loadingText)}</span>
        </div>
      </td></tr>`;
    }

    _renderError(msg) {
      const tbody = this.table.querySelector("tbody") || this.table.appendChild(document.createElement("tbody"));
      const colCount = Math.max(1, this.options.columns.length || this.table.querySelectorAll("thead th").length);
      tbody.innerHTML = `<tr><td colspan="${colCount}" class="text-center py-8 text-rose-500 text-xs">
        <div class="font-bold mb-1">Failed to load data</div>
        <div class="text-micro text-ink-muted mb-3">${escapeText(msg)}</div>
        <button type="button" class="px-3 py-1 bg-surface-inset hover:bg-hairline text-ink rounded-control border border-hairline font-medium text-xs">
          Retry
        </button>
      </td></tr>`;
      const btn = tbody.querySelector("button");
      if (btn) btn.addEventListener("click", () => this.reload());
    }

    // DataTables compatibility layer
    get ajax() {
      return {
        reload: (callback, resetPaging) => this.reload(callback, resetPaging),
        json: () => this._lastAjaxJson || this.rawData,
      };
    }

    rows() {
      return {
        invalidate: () => ({ draw: () => this.render() }),
        data: () => ({ toArray: () => this.rawData }),
      };
    }

    draw() {
      this.render();
      return this;
    }

    data() {
      return this.rawData;
    }

    api() {
      return this;
    }
  }

  window.GatewayTable = GatewayTable;
  GatewayTable.init = (selector, options) => new GatewayTable(selector, options);
  GatewayTable.getInstance = (selectorOrEl) => {
    const el = typeof selectorOrEl === "string" ? document.querySelector(selectorOrEl) : selectorOrEl;
    return instances.get(el);
  };

  // Zero-dependency jQuery / DataTables compatibility shim
  if (!window.jQuery) {
    const $ = function (selector) {
      if (typeof selector === "function") {
        if (document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", selector);
        } else {
          selector();
        }
        return;
      }
      const el = typeof selector === "string" ? document.querySelector(selector) : selector;
      return {
        DataTable: function (opts) {
          if (!opts) {
            return GatewayTable.getInstance(el) || new GatewayTable(el);
          }
          return new GatewayTable(el, opts);
        },
        find: function (s) { return el ? $(el.querySelector(s)) : null; },
        html: function (h) {
          if (!el) return "";
          if (h === undefined) return el.innerHTML;
          el.innerHTML = h;
          return this;
        },
        prop: function (p, v) {
          if (!el) return;
          if (v === undefined) return el[p];
          el[p] = v;
          return this;
        },
        val: function (v) {
          if (!el) return "";
          if (v === undefined) return el.value;
          el.value = v;
          return this;
        },
        on: function (evt, handler) {
          if (el) el.addEventListener(evt, handler);
          return this;
        },
        addClass: function (c) { if (el) el.classList.add(c); return this; },
        removeClass: function (c) { if (el) el.classList.remove(c); return this; },
      };
    };
    window.$ = $;
    window.jQuery = $;
  }
})();
