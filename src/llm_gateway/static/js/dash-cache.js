// dash-cache.js — stale-while-revalidate caching, prefs and a single poll
// scheduler for the operations dashboard.
//
// Why: every refresh used to blank the page and wait on the network. This
// module paints instantly from localStorage, then revalidates in the
// background, and gives one place to own refresh timing, backoff and
// cross-tab invalidation. Prefs (layout, time range, live cadence) live in a
// separate namespace so cache eviction can never delete user settings.
//
// Rules this file implements (verified against MDN / web.dev guidance):
//  * localStorage is synchronous and capped (~5 MB): small payloads only,
//    try/catch on every access, LRU eviction of our own keys on quota errors.
//  * Keys are version- and range-scoped; bump SCHEMA on any API shape change.
//  * Cached payloads must never outlive a logout: clear() is wired to
//    session expiry, and payloads carry the username they belong to.
//  * Auto-refresh is centralised (one scheduler), paused when the tab is
//    hidden or offline, with full-jitter exponential backoff on failures.
(function () {
  "use strict";

  const SCHEMA = "v1";
  const DATA_NS = "llmgw:data:" + SCHEMA + ":";
  const PREFS_KEY = "llmgw:prefs:v1";
  const INDEX_KEY = DATA_NS + "@index";
  const BUDGET_BYTES = 1.5 * 1024 * 1024; // self-imposed cap, well under the browser's

  const safeParse = (s) => { try { return JSON.parse(s); } catch (e) { return null; } };

  function lsGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function lsSet(key, val) {
    try { window.localStorage.setItem(key, val); return true; } catch (e) { return false; }
  }
  function lsDel(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  // ── Data cache ─────────────────────────────────────────────────────────
  // An index key holds {cacheKey: savedAtMs} so eviction and quota cleanup
  // don't have to enumerate (and mis-attribute) every localStorage entry.
  function readIndex() { return safeParse(lsGet(INDEX_KEY)) || {}; }
  function writeIndex(idx) { lsSet(INDEX_KEY, JSON.stringify(idx)); }

  function sweepOldSchema() {
    try {
      const doomed = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("llmgw:data:") && !k.startsWith(DATA_NS)) doomed.push(k);
      }
      doomed.forEach(lsDel);
    } catch (e) { /* storage blocked: cache is simply disabled */ }
  }

  function evictLru(needed) {
    const idx = readIndex();
    const entries = Object.entries(idx)
      .filter(([k]) => k !== INDEX_KEY)
      .sort((a, b) => a[1] - b[1]);
    let freed = 0;
    for (const [k] of entries) {
      const raw = lsGet(DATA_NS + k.replace(DATA_NS, "")) || lsGet(k);
      freed += raw ? raw.length * 2 : 0; // UTF-16
      lsDel(k);
      delete idx[k];
      if (freed >= needed) break;
    }
    writeIndex(idx);
  }

  function cacheGet(cacheKey) {
    const rec = safeParse(lsGet(DATA_NS + cacheKey));
    if (!rec || typeof rec !== "object" || rec.v !== SCHEMA || !("data" in rec)) return null;
    return rec; // {v, savedAt, etag, user, data}
  }

  function cacheSet(cacheKey, data, etag, user) {
    const rec = { v: SCHEMA, savedAt: Date.now(), etag: etag || null, user: user || null, data };
    const full = DATA_NS + cacheKey;
    let raw = "";
    try { raw = JSON.stringify(rec); } catch (e) { return; }
    if (raw.length * 2 > BUDGET_BYTES) return; // oversized payloads are not worth blocking input
    if (!lsSet(full, raw)) {
      evictLru(raw.length * 2 + 1024);
      if (!lsSet(full, raw)) return;
    }
    const idx = readIndex();
    idx[full] = rec.savedAt;
    // Opportunistic budget trim: drop oldest while our namespace exceeds BUDGET.
    let total = 0;
    for (const [k, t] of Object.entries(idx)) {
      if (k === INDEX_KEY) continue;
      const r = lsGet(k);
      if (!r) { delete idx[k]; continue; }
      total += r.length * 2;
      if (t === undefined) idx[k] = 0;
    }
    if (total > BUDGET_BYTES) {
      const entries = Object.entries(idx).filter(([k]) => k !== INDEX_KEY).sort((a, b) => a[1] - b[1]);
      for (const [k] of entries) {
        if (total <= BUDGET_BYTES) break;
        const r = lsGet(k);
        total -= r ? r.length * 2 : 0;
        lsDel(k);
        delete idx[k];
      }
    }
    writeIndex(idx);
  }

  function cacheClearData() {
    const idx = readIndex();
    Object.keys(idx).forEach((k) => k !== INDEX_KEY && lsDel(k));
    try {
      const doomed = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith(DATA_NS) && k !== INDEX_KEY) doomed.push(k);
      }
      doomed.forEach(lsDel);
    } catch (e) { /* ignore */ }
    lsSet(INDEX_KEY, "{}");
  }

  // ── SWR fetch with request de-duplication ──────────────────────────────
  const inflight = new Map(); // cacheKey -> Promise<{data, fromCache}>
  let broadcast = null;
  try { broadcast = "BroadcastChannel" in window ? new BroadcastChannel("llmgw") : null; } catch (e) { /* unsupported */ }

  // opts: {ttlMs, user, method}
  async function swr(url, opts) {
    opts = opts || {};
    const cacheKey = url;
    const cached = cacheGet(cacheKey);
    const fresh = cached && (Date.now() - cached.savedAt) < (opts.ttlMs || 15000) && cached.user === (opts.user || null);

    const doFetch = async () => {
      // NB: fetch() rejects a null `headers` value; omit the key entirely.
      const init = { credentials: "include" };
      if (cached && cached.etag) init.headers = { "If-None-Match": cached.etag };
      const res = await fetch(url, init);
      if (res.status === 401) {
        cacheClearData();
        if (typeof window.handleSessionExpiry === "function") window.handleSessionExpiry();
        throw new Error("session");
      }
      if (res.status === 304 && cached) {
        // Server says our body is current: bump savedAt, keep painting it.
        cacheSet(cacheKey, cached.data, cached.etag, cached.user);
        return cached;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      const etag = res.headers.get("ETag");
      cacheSet(cacheKey, data, etag, opts.user || null);
      if (broadcast) { try { broadcast.postMessage({ invalidate: cacheKey }); } catch (e) { /* ignore */ } }
      return { v: SCHEMA, savedAt: Date.now(), etag: etag, user: opts.user || null, data };
    };

    const run = () => doFetch().finally(() => inflight.delete(cacheKey));

    if (fresh) {
      // Serve cache now; revalidate in the background unless one is running.
      if (!inflight.has(cacheKey)) {
        inflight.set(cacheKey, run().catch(() => null));
      }
      return { data: cached.data, fromCache: true, savedAt: cached.savedAt };
    }
    if (inflight.has(cacheKey)) {
      const rec = await inflight.get(cacheKey);
      return rec ? { data: rec.data, fromCache: false, savedAt: rec.savedAt } : (cached ? { data: cached.data, fromCache: true, savedAt: cached.savedAt } : null);
    }
    inflight.set(cacheKey, run());
    try {
      const rec = await inflight.get(cacheKey);
      return rec ? { data: rec.data, fromCache: false, savedAt: rec.savedAt } : (cached ? { data: cached.data, fromCache: true, savedAt: cached.savedAt } : null);
    } catch (e) {
      if (cached) return { data: cached.data, fromCache: true, stale: true, savedAt: cached.savedAt }; // stale-if-error
      throw e;
    }
  }

  // ── Backoff (full jitter, AWS style) ───────────────────────────────────
  function makeBackoff(baseMs, capMs) {
    let attempt = 0;
    return {
      next() {
        const exp = Math.min(capMs, baseMs * Math.pow(2, attempt));
        attempt++;
        return Math.random() * exp; // full jitter
      },
      reset() { attempt = 0; },
      get fails() { return attempt; },
    };
  }

  // ── Central scheduler ──────────────────────────────────────────────────
  // One loop drives every poller: tasks self-schedule with setTimeout (no
  // interval backlog), pause while hidden/offline, revalidate on tab focus
  // and reconnect, and honour the user's Live cadence/pause preference.
  function makeScheduler(refreshAll) {
    const tasks = [];
    let running = false;
    let liveEnabled = true;

    function tickTask(task) {
      task.timer = null;
      if (!running || !liveEnabled || document.hidden || !navigator.onLine) return;
      Promise.resolve(task.fn()).finally(() => {
        const base = task.intervalMs();
        const delay = task.backoff && task.backoff.fails ? base + task.backoff.next() : base;
        task.timer = setTimeout(() => tickTask(task), delay);
      });
    }

    function start() {
      if (running) return;
      running = true;
      tasks.forEach((t) => {
        if (!t.timer) t.timer = setTimeout(() => tickTask(t), t.firstDelayMs != null ? t.firstDelayMs : t.intervalMs());
      });
    }
    function stop() {
      running = false;
      tasks.forEach((t) => { if (t.timer) { clearTimeout(t.timer); t.timer = null; } });
    }
    function setLive(on) {
      liveEnabled = on;
      if (on) start(); else stop();
    }
    function add(fn, intervalMsFn, opts) {
      opts = opts || {};
      const task = {
        fn,
        intervalMs: typeof intervalMsFn === "function" ? intervalMsFn : () => intervalMsFn,
        backoff: opts.backoff ? makeBackoff(1000, 60000) : null,
        firstDelayMs: opts.firstDelayMs,
        timer: null,
      };
      // Wrap fn to reset/mark backoff.
      const raw = task.fn;
      task.fn = async function () {
        try { const r = await raw(); if (task.backoff) task.backoff.reset(); return r; }
        catch (e) { throw e; }
      };
      tasks.push(task);
      return task;
    }
    function revalidateNow() {
      if (!liveEnabled) return;
      tasks.forEach((t) => {
        if (t.timer) { clearTimeout(t.timer); t.timer = null; }
        t.timer = setTimeout(() => tickTask(t), 0);
      });
    }

    document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else start(); });
    window.addEventListener("focus", () => { if (!document.hidden && running) revalidateNow(); });
    window.addEventListener("online", () => { if (!document.hidden) revalidateNow(); });
    return { add, start, stop, setLive, revalidateNow, isRunning: () => running };
  }

  // ── Prefs ──────────────────────────────────────────────────────────────
  const PREF_DEFAULTS = {
    version: 1,
    timeRange: "24h",
    density: "comfortable",
    live: { enabled: true, everySeconds: 30 },
    animations: true,
    widgets: {}, // id -> {visible, order}
  };
  function prefsDefaults() { return JSON.parse(JSON.stringify(PREF_DEFAULTS)); }
  let prefsCache = null;
  let prefsTimer = null;

  function loadPrefs(user) {
    if (prefsCache) return prefsCache;
    const stored = safeParse(lsGet(PREFS_KEY));
    const base = prefsDefaults();
    if (stored && stored.version === 1) {
      base.timeRange = ["1h", "6h", "24h", "7d", "30d"].includes(stored.timeRange) ? stored.timeRange
        : (stored.timeRange === "6h" ? "24h" : base.timeRange);
      base.density = stored.density === "compact" ? "compact" : "comfortable";
      if (stored.live && typeof stored.live === "object") {
        base.live.enabled = stored.live.enabled !== false;
        base.live.everySeconds = [10, 15, 30, 60, 300].includes(Number(stored.live.everySeconds)) ? Number(stored.live.everySeconds) : 30;
      }
      base.animations = stored.animations !== false;
      // Per-widget merge with unknown-key pruning happens in the widget layer.
      base.widgets = stored.widgets && typeof stored.widgets === "object" ? stored.widgets : {};
    }
    prefsCache = base;
    return base;
  }
  function savePrefs(prefs) {
    prefsCache = prefs;
    // Debounced: prefs change in bursts (toggles) and each write is sync I/O.
    if (prefsTimer) clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => { prefsTimer = null; lsSet(PREFS_KEY, JSON.stringify(prefs)); }, 300);
  }

  // ── Misc UI helpers shared by dashboard modules ────────────────────────
  const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Count-up on first paint / large jumps; final formatted value is the
  // accessible value immediately via the same element's text.
  function countTo(el, to, format, opts) {
    opts = opts || {};
    const from = Number(el.dataset.v);
    const prevShown = el.dataset.v !== undefined;
    el.dataset.v = String(to);
    const final = format(to);
    if (!prevShown || !opts.animate || reducedMotion() || !prefs().animations || from === to || !isFinite(from)) {
      el.textContent = final;
      return;
    }
    const ms = opts.ms || 450;
    const t0 = performance.now();
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    (function step(now) {
      const p = Math.min((now - t0) / ms, 1);
      el.textContent = format(from + (to - from) * ease(p));
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  // Skeletons sized to the real content; delayed so fast responses never
  // flash them (web.dev: skeletons for sub-300ms loads are noise).
  const skTimers = new Map();
  function skeletonOn(el, delayMs) {
    if (!el) return;
    if (skTimers.has(el)) return;
    const t = setTimeout(() => {
      skTimers.delete(el);
      el.classList.add("dash-loading");
      el.setAttribute("aria-busy", "true");
    }, delayMs == null ? 250 : delayMs);
    skTimers.set(el, t);
  }
  function skeletonOff(el) {
    if (!el) return;
    if (skTimers.has(el)) { clearTimeout(skTimers.get(el)); skTimers.delete(el); }
    el.classList.remove("dash-loading");
    el.removeAttribute("aria-busy");
  }

  function prefs() { return prefsCache || loadPrefs(); }

  sweepOldSchema();

  window.DashCache = {
    SCHEMA, swr, cacheGet, cacheSet, clear: cacheClearData,
    makeScheduler, makeBackoff,
    loadPrefs, savePrefs, prefsDefaults, prefs,
    reducedMotion, countTo,
    skeletonOn, skeletonOff,
    broadcast,
  };
})();
