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

  // Ensure escapeHtml is always defined on window for column renderers
  if (typeof window.escapeHtml !== "function") {
    window.escapeHtml = escapeText;
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

      if (instances.has(this.table)) {
        const existing = instances.get(this.table);
        if (options && Object.keys(options).length > 0) {
          existing.options = Object.assign(existing.options, options);
          if (options.data) existing.setData(options.data);
          else if (options.ajax) existing.reload();
        }
        return existing;
      }

      this.options = Object.assign({
        columns: [],
        columnDefs: [],
        ajax: null,
        data: [],
        pageLength: 50,
        searchable: true,
        sortable: true,
        emptyText: "No records found",
        loadingText: "Loading data...",
        drawCallback: null,
      }, options);

      // Apply columnDefs onto columns if provided
      if (Array.isArray(this.options.columnDefs) && Array.isArray(this.options.columns)) {
        this.options.columnDefs.forEach((def) => {
          const targets = Array.isArray(def.targets) ? def.targets : [def.targets];
          targets.forEach((t) => {
            const idx = t === -1 ? this.options.columns.length - 1 : t;
            if (this.options.columns[idx]) {
              this.options.columns[idx] = Object.assign({}, this.options.columns[idx], def);
            }
          });
        });
      }

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
      if (this.options.searchable && this.table.parentElement && !this.table.parentElement.querySelector(".gateway-table-search")) {
        const wrap = document.createElement("div");
        wrap.className = "gateway-table-search flex items-center justify-between gap-4 p-3 border-b border-hairline text-caption";
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
          if (e.key === "/" && document.activeElement && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
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

        if (this.options.ajax && typeof this.options.ajax.dataSrc === "function") {
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
          if (icon) {
            icon.innerHTML = this.sortDir === 1 ? "▲" : "▼";
            icon.classList.remove("opacity-40");
            icon.classList.add("text-accent", "opacity-100");
          }
        } else {
          th.setAttribute("aria-sort", "none");
          if (icon) {
            icon.innerHTML = "↕";
            icon.classList.add("opacity-40");
            icon.classList.remove("text-accent", "opacity-100");
          }
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
        if (typeof valA === "object" && valA !== null) valA = valA.name || valA.id || JSON.stringify(valA);
        if (typeof valB === "object" && valB !== null) valB = valB.name || valB.id || JSON.stringify(valB);
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
            ? `${this.rawData.length} record${this.rawData.length === 1 ? "" : "s"}`
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

        this.options.columns.forEach((col, colIdx) => {
          const td = document.createElement("td");
          const baseCls = "p-4 align-middle text-ink text-xs";
          td.className = col.className ? `${baseCls} ${col.className}` : baseCls;

          const val = col.data ? getByPath(row, col.data) : row;
          if (typeof col.render === "function") {
            td.innerHTML = col.render(val, "display", row, { row: rowIdx, col: colIdx });
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
        <button type="button" class="px-3 py-1 bg-surface-inset hover:bg-hairline text-ink rounded-control border border-hairline font-medium text-xs cursor-pointer">
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
  (function initJQueryShim() {
    function createWrapper(elements) {
      const arr = Array.isArray(elements) ? elements : (elements ? [elements] : []);
      const first = arr[0] || null;

      const wrapper = {
        length: arr.length,
        [Symbol.iterator]: function* () {
          for (let i = 0; i < arr.length; i++) yield arr[i];
        },
        forEach: function (callback) {
          arr.forEach((el, idx) => callback(el, idx));
          return this;
        },
        ready: function (fn) {
          if (typeof fn === "function") {
            if (document.readyState === "loading") {
              document.addEventListener("DOMContentLoaded", fn);
            } else {
              fn();
            }
          }
          return this;
        },
        DataTable: function (opts) {
          if (!first) return null;
          if (!opts) {
            return GatewayTable.getInstance(first) || new GatewayTable(first);
          }
          return new GatewayTable(first, opts);
        },
        find: function (selector) {
          if (!first) return createWrapper([]);
          try {
            const matched = first.querySelectorAll(selector);
            return createWrapper(Array.from(matched));
          } catch (_) {
            return createWrapper([]);
          }
        },
        closest: function (selector) {
          if (!first || typeof first.closest !== "function") return createWrapper([]);
          const match = first.closest(selector);
          return createWrapper(match ? [match] : []);
        },
        parent: function () {
          return createWrapper(first && first.parentElement ? [first.parentElement] : []);
        },
        children: function (selector) {
          if (!first) return createWrapper([]);
          const kids = Array.from(first.children).filter((c) => !selector || c.matches(selector));
          return createWrapper(kids);
        },
        html: function (h) {
          if (h === undefined) return first ? first.innerHTML : "";
          arr.forEach((el) => { if (el) el.innerHTML = h; });
          return this;
        },
        text: function (t) {
          if (t === undefined) return first ? first.textContent : "";
          arr.forEach((el) => { if (el) el.textContent = t; });
          return this;
        },
        val: function (v) {
          if (v === undefined) return first ? first.value : "";
          arr.forEach((el) => { if (el) el.value = v; });
          return this;
        },
        prop: function (p, v) {
          if (v === undefined) return first ? first[p] : undefined;
          arr.forEach((el) => { if (el) el[p] = v; });
          return this;
        },
        attr: function (a, v) {
          if (v === undefined) return first && typeof first.getAttribute === "function" ? first.getAttribute(a) : null;
          arr.forEach((el) => { if (el && typeof el.setAttribute === "function") el.setAttribute(a, v); });
          return this;
        },
        removeAttr: function (a) {
          arr.forEach((el) => { if (el && typeof el.removeAttribute === "function") el.removeAttribute(a); });
          return this;
        },
        css: function (prop, val) {
          if (typeof prop === "object") {
            arr.forEach((el) => { if (el && el.style) Object.assign(el.style, prop); });
            return this;
          }
          if (val === undefined) return first ? getComputedStyle(first)[prop] : "";
          arr.forEach((el) => { if (el && el.style) el.style[prop] = val; });
          return this;
        },
        addClass: function (c) {
          const classes = String(c).trim().split(/\s+/);
          arr.forEach((el) => {
            if (el && el.classList) classes.forEach((cls) => cls && el.classList.add(cls));
          });
          return this;
        },
        removeClass: function (c) {
          const classes = String(c).trim().split(/\s+/);
          arr.forEach((el) => {
            if (el && el.classList) classes.forEach((cls) => cls && el.classList.remove(cls));
          });
          return this;
        },
        toggleClass: function (c, state) {
          arr.forEach((el) => {
            if (el && el.classList) el.classList.toggle(c, state);
          });
          return this;
        },
        on: function (evt, handler) {
          const events = evt.split(/\s+/);
          arr.forEach((el) => {
            if (el && typeof el.addEventListener === "function") {
              events.forEach((e) => {
                if (e) el.addEventListener(e, handler);
              });
            }
          });
          return this;
        },
        off: function (evt, handler) {
          const events = evt.split(/\s+/);
          arr.forEach((el) => {
            if (el && typeof el.removeEventListener === "function") {
              events.forEach((e) => {
                if (e) el.removeEventListener(e, handler);
              });
            }
          });
          return this;
        },
        trigger: function (evt) {
          const e = new Event(evt, { bubbles: true, cancelable: true });
          arr.forEach((el) => {
            if (el && typeof el.dispatchEvent === "function") el.dispatchEvent(e);
          });
          return this;
        },
        focus: function () {
          if (first && typeof first.focus === "function") first.focus();
          return this;
        },
        click: function () {
          if (first && typeof first.click === "function") first.click();
          return this;
        },
        hide: function () {
          arr.forEach((el) => { if (el && el.style) el.style.display = "none"; });
          return this;
        },
        show: function () {
          arr.forEach((el) => { if (el && el.style) el.style.display = ""; });
          return this;
        },
      };

      // Assign numeric indices
      arr.forEach((el, i) => { wrapper[i] = el; });

      // Proxy fallback to delegate direct DOM properties like $(id).style, $(id).disabled, $(id).value, $(id).addEventListener
      return new Proxy(wrapper, {
        get(target, prop, receiver) {
          if (prop in target) return target[prop];
          if (first && prop in first) {
            const val = first[prop];
            if (typeof val === "function") {
              return val.bind(first);
            }
            return val;
          }
          return undefined;
        },
        set(target, prop, value, receiver) {
          if (prop in target) {
            target[prop] = value;
            return true;
          }
          if (first) {
            first[prop] = value;
            return true;
          }
          target[prop] = value;
          return true;
        },
      });
    }

    const $ = function (selector) {
      if (typeof selector === "function") {
        if (document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", selector);
        } else {
          selector();
        }
        return createWrapper([document]);
      }
      if (selector === window || selector === document) {
        return createWrapper([selector]);
      }
      if (typeof selector === "string") {
        const trimmed = selector.trim();
        if (trimmed.startsWith("<")) {
          const tag = trimmed.replace(/<|>|\//g, "").trim().split(/\s+/)[0] || "div";
          return createWrapper([document.createElement(tag)]);
        }
        try {
          const matched = document.querySelectorAll(trimmed);
          return createWrapper(Array.from(matched));
        } catch (_) {
          return createWrapper([]);
        }
      }
      if (selector instanceof Node || selector instanceof NodeList || Array.isArray(selector)) {
        return createWrapper(selector instanceof NodeList ? Array.from(selector) : selector);
      }
      return createWrapper(selector ? [selector] : []);
    };

    $.extend = function () {
      let target = arguments[0] || {};
      let i = 1;
      let deep = false;
      if (typeof target === "boolean") {
        deep = target;
        target = arguments[1] || {};
        i = 2;
      }
      for (; i < arguments.length; i++) {
        const src = arguments[i];
        if (src) {
          for (const key in src) {
            if (Object.prototype.hasOwnProperty.call(src, key)) {
              if (deep && src[key] && typeof src[key] === "object" && !Array.isArray(src[key])) {
                target[key] = $.extend(true, target[key] || {}, src[key]);
              } else {
                target[key] = src[key];
              }
            }
          }
        }
      }
      return target;
    };

    $.fn = {
      dataTable: {
        ext: { errMode: "none" },
        defaults: { columnDefs: [] },
      },
      DataTable: function (opts) {
        return $(this).DataTable(opts);
      },
    };

    $.ajax = async function (opts) {
      try {
        const fetcher = window.fetchWithCsrf || window.fetch;
        const res = await fetcher(opts.url, {
          method: opts.type || "GET",
          headers: opts.headers || {},
          body: opts.data ? JSON.stringify(opts.data) : undefined,
          credentials: opts.xhrFields?.withCredentials ? "include" : "same-origin",
        });
        const data = await res.json();
        if (typeof opts.success === "function") opts.success(data);
        return data;
      } catch (err) {
        if (typeof opts.error === "function") opts.error(err);
        throw err;
      }
    };

    if (!window.jQuery) {
      window.$ = $;
      window.jQuery = $;
    } else {
      if (!window.jQuery.fn) window.jQuery.fn = $.fn;
      if (!window.jQuery.extend) window.jQuery.extend = $.extend;
      if (!window.$) window.$ = window.jQuery;
    }
  })();
})();
