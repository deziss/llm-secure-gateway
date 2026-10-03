// embedding-playground.js — Embedding Playground: embed texts through the
// gateway, lay them out in 2D, and make similarity explicit (neighbour scores,
// similarity matrix, semantic search, vector math).
//
// Heavy work (similarity matrices, PCA, t-SNE, UMAP, k-means) runs in
// embedding-worker.js; this file handles requests, state, drawing and UI.
//
// Requests go to the gateway's /provider/{provider}/... routes. With no API
// key the signed-in session is used (fetchWithCsrf adds the CSRF header);
// with a key it is sent as a Bearer token and that key's owner rules apply.
//
// Vectors are cached in IndexedDB by provider + model + text, so re-adding a
// text, reloading a sample or reloading the page costs no API call, and the
// current session (points and settings, never the key) survives a reload.

(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const ASSET_V = (document.currentScript && document.currentScript.dataset.assetV) || "";
  const WORKER_URL = "/static/js/embedding-worker.js" + (ASSET_V ? "?v=" + ASSET_V : "");
  const PALETTE = ["#60A5FA", "#F472B6", "#FBBF24", "#34D399", "#C084FC", "#F87171", "#22D3EE", "#FB923C", "#A3E635", "#818CF8", "#2DD4BF", "#E879F9"];
  const NEUTRAL = "#94A3B8";
  const EDGE_LIMIT = 20000;
  const BATCH = 64;
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const settings = {
    provider: "vllm", model: "", apiKey: "",
    method: "pca", perplexity: 15, nNeighbors: 10, metric: "cosine",
    edgeMode: "knn", k: 2, threshold: 0.6, edgeScores: false,
    colorBy: "point", clusterK: 4, pointSize: 6, labelMode: "auto",
    splitLines: true, hmOrder: "added", searchMode: "meaning", topN: 10,
  };

  const state = {
    points: [], byId: new Map(), nextId: 1, nextColor: 0, labelColors: new Map(),
    dims: 0, modelKey: null, modelName: "",
    layout: null, layoutVersion: 0, pos: new Map(), edges: [], edgeRange: { min: 0, max: 1 }, edgeCapped: false,
    layoutInfo: "", pinned: null, hover: null, highlight: null,
    view: { s: 1, x: 0, y: 0 }, tab: "map", busy: false,
  };

  // ── Theme colours (canvas can't read CSS variables directly) ────────────
  let theme = {};
  function readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const rgb = (n, a) => {
      const v = (cs.getPropertyValue(n).trim() || "128 128 128").split(/\s+/).map(Number);
      return a === undefined ? `rgb(${v.join(",")})` : `rgba(${v.join(",")},${a})`;
    };
    const tri = (n) => (cs.getPropertyValue(n).trim() || "128 128 128").split(/\s+/).map(Number);
    const dark = document.documentElement.classList.contains("dark");
    theme = {
      dark, bg: rgb("--surface-raised"), ink: rgb("--ink"), muted: rgb("--ink-muted"),
      grid: rgb("--ink-muted", dark ? 0.06 : 0.09), line: (a) => rgb("--ink-muted", a),
      halo: rgb("--surface-raised"), bgTri: tri("--surface-raised"),
    };
    // Matrix colour ramp: page surface → teal → emerald → pale lime.
    STOPS = [theme.bgTri, dark ? [23, 64, 92] : [167, 214, 228], [20, 184, 138], dark ? [230, 245, 160] : [6, 95, 70]];
    const g = $("#epHmGrad");
    if (g) g.style.background = `linear-gradient(90deg, ${STOPS.map((s) => `rgb(${s.join(",")})`).join(",")})`;
  }
  let STOPS = [];

  // ── Storage (IndexedDB; falls back to memory) ───────────────────────────
  let dbPromise = null;
  function openDb() {
    if (!dbPromise) dbPromise = new Promise((res, rej) => {
      if (!("indexedDB" in window)) return rej(new Error("IndexedDB unavailable"));
      const r = indexedDB.open("llm-gateway-embeddings", 2);
      r.onupgradeneeded = () => {
        const db = r.result;
        if (db.objectStoreNames.contains("vec")) db.deleteObjectStore("vec");
        if (!db.objectStoreNames.contains("cache")) db.createObjectStore("cache");
        if (!db.objectStoreNames.contains("session")) db.createObjectStore("session");
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbPromise;
  }
  async function tx(store, mode, fn) {
    try {
      const db = await openDb();
      return await new Promise((res, rej) => {
        const t = db.transaction(store, mode), out = fn(t.objectStore(store));
        t.oncomplete = () => res(out && out.result);
        t.onerror = () => rej(t.error); t.onabort = () => rej(t.error);
      });
    } catch (e) { return undefined; }
  }
  async function cacheGetMany(keys) {
    try {
      const db = await openDb();
      return await new Promise((res, rej) => {
        const t = db.transaction("cache", "readonly"), st = t.objectStore("cache"), out = new Array(keys.length);
        keys.forEach((k, i) => { const r = st.get(k); r.onsuccess = () => { out[i] = r.result; }; });
        t.oncomplete = () => res(out); t.onerror = () => rej(t.error);
      });
    } catch (e) { return new Array(keys.length); }
  }
  const cachePutMany = (entries) => tx("cache", "readwrite", (st) => { for (const [k, v] of entries) st.put(v, k); return null; });
  const memCache = new Map();

  let saveTimer = 0;
  function saveSession() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      tx("session", "readwrite", (st) => st.put({
        settings: { ...settings, apiKey: "" }, modelKey: state.modelKey, modelName: state.modelName, dims: state.dims, nextColor: state.nextColor,
        points: state.points.map((p) => ({ id: p.id, text: p.text, label: p.label, color: p.color, raw: p.raw })),
      }, "current"));
    }, 400);
  }
  async function loadSession() {
    const s = await tx("session", "readonly", (st) => st.get("current"));
    if (!s) return;
    Object.assign(settings, s.settings || {}, { apiKey: "" });
    state.points = (s.points || []).filter((p) => p && p.raw instanceof Float32Array).map((p) => ({ ...p, vec: normalize(p.raw) }));
    state.modelKey = s.modelKey || null; state.modelName = s.modelName || "";
    state.dims = s.dims || (state.points[0] ? state.points[0].raw.length : 0);
    state.nextColor = s.nextColor || state.points.length;
    state.nextId = state.points.reduce((m, p) => Math.max(m, p.id), 0) + 1;
  }

  // ── Embedding requests ─────────────────────────────────────────────────
  const connKey = () => (settings.provider === "demo" ? "demo" : `${settings.provider}|${settings.model.trim()}`);
  const modelLabel = () => (settings.provider === "demo" ? "Demo vectors" : settings.model.trim() || "no model");

  async function post(path, body) {
    const headers = { "Content-Type": "application/json" };
    if (settings.apiKey.trim()) headers.Authorization = "Bearer " + settings.apiKey.trim();
    let res;
    try {
      res = await fetchWithCsrf(BASE_URL + path, { method: "POST", headers, body: JSON.stringify(body) });
    } catch (e) {
      throw new Error("Couldn't reach the gateway. Check it is running, then try again.");
    }
    if (!res.ok) {
      let msg = "";
      try { msg = await res.text(); } catch (e) { /* ignore */ }
      try { const j = JSON.parse(msg); msg = j.detail || (j.error && (j.error.message || j.error)) || j.message || msg; } catch (e) { /* not JSON */ }
      if (typeof msg !== "string") msg = JSON.stringify(msg);
      const hint = res.status === 401 ? " Sign in again, or add an API key."
        : res.status === 403 ? " This key's project may not have access to this server or endpoint."
        : res.status === 404 ? " Check the provider and model name." : "";
      const err = new Error(`Gateway answered ${res.status}: ${msg.slice(0, 220) || res.statusText}.${hint}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  async function apiEmbed(texts) {
    const model = settings.model.trim();
    if (!model) throw new Error("Enter a model name first, or switch Provider to Demo.");
    const p = settings.provider;
    if (p === "ollama") {
      try {
        const j = await post("/provider/ollama/api/embed", { model, input: texts });
        if (Array.isArray(j.embeddings) && j.embeddings.length === texts.length) return j.embeddings.map((v) => Float32Array.from(v));
      } catch (e) {
        if (![400, 404, 405].includes(e.status)) throw e;
      }
      const out = []; // older Ollama: one prompt per request
      for (const t of texts) out.push(Float32Array.from((await post("/provider/ollama/api/embeddings", { model, prompt: t })).embedding));
      return out;
    }
    const j = await post(`/provider/${p}/v1/embeddings`, { model, input: texts, encoding_format: "float" });
    const data = (j.data || []).slice().sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
    if (data.length !== texts.length) throw new Error(`Asked for ${texts.length} embeddings and received ${data.length}.`);
    return data.map((d) => Float32Array.from(d.embedding));
  }

  function fnv(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function demoEmbed(text) {
    const D = 384, v = new Float32Array(D), t = " " + text.toLowerCase().normalize("NFKC") + " ";
    const add = (f, w) => { const h = fnv(f); v[h % D] += (h & 0x80000000 ? -1 : 1) * w; };
    for (let n = 2; n <= 4; n++) for (let i = 0; i + n <= t.length; i++) add(n + ":" + t.slice(i, i + n), 1 / n);
    for (const w of t.split(/[^\p{L}\p{N}]+/u)) if (w) add("w:" + w, 1.5);
    return v;
  }
  function normalize(v) {
    let s = 0; for (let i = 0; i < v.length; i++) s += v[i] * v[i];
    s = Math.sqrt(s); if (!s) return v;
    const o = new Float32Array(v.length); for (let i = 0; i < v.length; i++) o[i] = v[i] / s; return o;
  }

  // Returns raw vectors aligned with `texts`, from cache where possible.
  async function embedTexts(texts, onProgress) {
    const key = connKey(), keys = texts.map((t) => key + "␟" + t), out = new Array(texts.length);
    const stored = await cacheGetMany(keys), miss = [];
    texts.forEach((t, i) => { const c = memCache.get(keys[i]) || stored[i]; if (c instanceof Float32Array) out[i] = c; else miss.push(i); });
    let done = texts.length - miss.length;
    onProgress && onProgress(done, texts.length);
    for (let s = 0; s < miss.length; s += BATCH) {
      const idx = miss.slice(s, s + BATCH), batch = idx.map((i) => texts[i]);
      const vecs = settings.provider === "demo" ? batch.map(demoEmbed) : await apiEmbed(batch);
      const puts = [];
      idx.forEach((i, k) => { out[i] = vecs[k]; memCache.set(keys[i], vecs[k]); puts.push([keys[i], vecs[k]]); });
      cachePutMany(puts);
      done += idx.length; onProgress && onProgress(done, texts.length);
    }
    return { vecs: out, cached: texts.length - miss.length };
  }

  // ── Status & toasts ────────────────────────────────────────────────────
  function setStatus(kind, text) { const el = $("#epStatus"); el.dataset.state = kind; el.lastElementChild.textContent = text; }
  function toast(msg, kind = "info") {
    if (typeof showToast !== "function") return;
    const type = kind === "error" ? "error" : kind === "warn" ? "warning" : "success";
    showToast(kind === "error" ? "Error" : kind === "warn" ? "Heads up" : "Embedding Playground", msg, type);
  }

  // ── Points ─────────────────────────────────────────────────────────────
  function parseInput(raw, split) {
    if (!split) { const t = raw.trim(); return t ? [{ text: t, label: "" }] : []; }
    return raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((l) => {
      const i = l.lastIndexOf("|");
      return i > 0 ? { text: l.slice(0, i).trim(), label: l.slice(i + 1).trim() } : { text: l, label: "" };
    }).filter((x) => x.text);
  }

  async function addItems(items) {
    if (state.busy) return false;
    const have = new Set(state.points.map((p) => p.text)), fresh = [], seen = new Set();
    for (const it of items) { if (have.has(it.text) || seen.has(it.text)) continue; seen.add(it.text); fresh.push(it); }
    const skipped = items.length - fresh.length;
    if (!fresh.length) { toast(skipped ? "Those points are already on the map." : "Type some text to embed.", "warn"); return false; }
    if (state.points.length && state.modelKey && state.modelKey !== connKey()) {
      toast(`Your points were embedded with ${state.modelName}. Re-embed all points with the current model, or switch back, before adding more.`, "error");
      return false;
    }
    state.busy = true; $("#epEmbedBtn").disabled = true;
    try {
      const { vecs, cached } = await embedTexts(fresh.map((f) => f.text), (d, t) => setStatus("busy", `Embedding ${d} of ${t}…`));
      const dim = vecs[0].length;
      if (state.dims && state.points.length && dim !== state.dims) throw new Error(`The model returned ${dim} dimensions, but your points have ${state.dims}. Re-embed all points or clear the map.`);
      fresh.forEach((f, i) => state.points.push({ id: state.nextId++, text: f.text, label: f.label, color: state.nextColor++, raw: vecs[i], vec: normalize(vecs[i]) }));
      state.dims = dim; state.modelKey = connKey(); state.modelName = modelLabel();
      if (fresh.some((f) => f.label) && settings.colorBy === "point") setSetting("colorBy", "label");
      onPointsChanged();
      const bits = [];
      if (cached) bits.push(`${cached} from cache`);
      if (skipped) bits.push(`skipped ${skipped} already on the map`);
      if (bits.length) toast(`Added ${fresh.length} (${bits.join(", ")}).`);
      return true;
    } catch (e) {
      setStatus("error", "Embedding failed"); toast(e.message, "error"); return false;
    } finally { state.busy = false; $("#epEmbedBtn").disabled = false; }
  }

  async function reembedAll() {
    if (!state.points.length) { toast("There are no points to re-embed.", "warn"); return; }
    if (state.busy) return;
    state.busy = true; $("#epReembedBtn").disabled = true;
    try {
      const { vecs } = await embedTexts(state.points.map((p) => p.text), (d, t) => setStatus("busy", `Re-embedding ${d} of ${t}…`));
      state.points.forEach((p, i) => { p.raw = vecs[i]; p.vec = normalize(vecs[i]); });
      state.dims = vecs[0].length; state.modelKey = connKey(); state.modelName = modelLabel();
      onPointsChanged();
      toast(`Re-embedded ${state.points.length} points with ${state.modelName}.`);
    } catch (e) { setStatus("error", "Embedding failed"); toast(e.message, "error"); }
    finally { state.busy = false; $("#epReembedBtn").disabled = false; }
  }

  function removePoint(id) {
    state.points = state.points.filter((p) => p.id !== id);
    if (state.pinned === id) state.pinned = null;
    if (state.highlight) state.highlight.delete(id);
    state.pos.delete(id);
    if (!state.points.length) { state.modelKey = null; state.dims = 0; }
    onPointsChanged();
  }

  function clearAll() {
    if (worker) { worker.terminate(); worker = null; }
    Object.assign(state, { points: [], dims: 0, modelKey: null, modelName: "", layout: null, edges: [], pinned: null, hover: null, highlight: null, layoutInfo: "", view: { s: 1, x: 0, y: 0 }, nextColor: 0 });
    state.pos.clear();
    $("#epResults").innerHTML = "";
    onPointsChanged();
  }

  function onPointsChanged() {
    state.byId = new Map(state.points.map((p) => [p.id, p]));
    state.labelColors = new Map();
    for (const p of state.points) if (p.label && !state.labelColors.has(p.label)) state.labelColors.set(p.label, state.labelColors.size);
    renderList(); updateMeta(); updateModelWarn(); renderDetail();
    $("#epEmpty").hidden = state.points.length > 0;
    if (!state.points.length) setStatus("ready", "Ready");
    scheduleLayout(); saveSession(); requestDraw();
  }

  // ── Layout worker ──────────────────────────────────────────────────────
  let worker = null, jobId = 0, pendingIds = null, layoutTimer = 0, warnedSlow = false;
  function scheduleLayout(delay = 120) { clearTimeout(layoutTimer); layoutTimer = setTimeout(runLayout, delay); }
  const effectivePerplexity = (n) => Math.max(1, Math.min(settings.perplexity, Math.floor(((n - 1) / 3) * 10) / 10));
  const effectiveNeighbors = (n) => Math.max(2, Math.min(settings.nNeighbors, n - 1));

  function runLayout() {
    if (worker) { worker.terminate(); worker = null; }
    const n = state.points.length;
    if (!n) {
      state.layout = null; state.edges = []; state.layoutInfo = "";
      updateLayoutInfo(); updateEdgeNote(); requestDraw();
      if (state.tab === "heatmap") drawHeatmap();
      return;
    }
    const d = state.dims, unit = new Float32Array(n * d), raw = new Float32Array(n * d);
    state.points.forEach((p, i) => { unit.set(p.vec, i * d); raw.set(p.raw, i * d); });
    pendingIds = state.points.map((p) => p.id);
    const id = ++jobId;
    if (settings.method === "tsne" && n > 2500 && !warnedSlow) { warnedSlow = true; toast("t-SNE gets slow above a few thousand points. UMAP or PCA will be faster.", "warn"); }
    try { worker = new Worker(WORKER_URL); }
    catch (e) { toast("This browser blocked the background worker, so the layout cannot run: " + e.message, "error"); return; }
    worker.onmessage = onWorkerMessage;
    worker.onerror = (e) => { setStatus("error", "Layout failed"); toast("Layout failed: " + (e.message || "unknown error"), "error"); };
    setStatus("busy", "Arranging points…"); setProgress(0.03);
    worker.postMessage({
      type: "layout", jobId: id, n, d, unit, raw, metric: settings.metric, method: settings.method,
      perplexity: effectivePerplexity(n), nNeighbors: effectiveNeighbors(n),
      iters: n <= 200 ? 1000 : n <= 1000 ? 750 : 500, clusterK: settings.clusterK,
    }, [unit.buffer, raw.buffer]);
  }

  function onWorkerMessage(e) {
    const m = e.data;
    if (m.jobId !== jobId) return;
    if (m.type === "sim") {
      const ids = pendingIds;
      state.layout = { ids, index: new Map(ids.map((id, i) => [id, i])), sim: m.sim, m: null, metric: null, clusters: null };
      state.layoutVersion++;
      recomputeEdges(); renderDetail(); requestDraw();
    } else if (m.type === "metric") {
      if (!state.layout) return;
      state.layout.m = m.m; state.layout.metric = m.metric; state.layoutVersion++;
      recomputeEdges(); renderDetail(); requestDraw();
      if (state.tab === "heatmap") drawHeatmap();
    } else if (m.type === "clusters") {
      if (!state.layout) return;
      state.layout.clusters = m.labels; state.layoutVersion++;
      if (settings.colorBy === "cluster") renderList();
      renderDetail(); requestDraw();
      if (state.tab === "heatmap") drawHeatmap();
    } else if (m.type === "progress") {
      applyCoords(m.coords, false); setProgress(m.frac);
    } else if (m.type === "done") {
      applyCoords(m.coords, true); setProgress(1);
      state.layoutInfo = describeLayout(m.info); updateLayoutInfo();
      setStatus("ready", "Ready");
    } else if (m.type === "error") {
      setStatus("error", "Layout failed"); setProgress(1); toast("Layout failed: " + m.message, "error");
    }
  }
  const requestClusters = () => { if (worker && state.layout) worker.postMessage({ type: "cluster", jobId, k: settings.clusterK }); };
  const requestMetric = () => { if (worker && state.layout) worker.postMessage({ type: "metric", jobId, metric: settings.metric }); else scheduleLayout(0); };

  let progressTimer = 0;
  function setProgress(f) {
    const el = $("#epProgress"); el.classList.add("on"); el.style.width = clamp(f, 0, 1) * 100 + "%";
    clearTimeout(progressTimer);
    if (f >= 1) progressTimer = setTimeout(() => { el.classList.remove("on"); el.style.width = "0"; }, 400);
  }

  function describeLayout(info) {
    const n = state.points.length;
    let s = "";
    if (info.fallback) s = "UMAP could not load, so PCA is shown. ";
    if (info.small) s = "PCA is shown because t-SNE and UMAP need at least 4 points. ";
    if (info.explained && info.explained.length) {
      const [a, b] = info.explained.map((x) => Math.round(x * 100));
      s += `PCA: the horizontal axis captures ${a}% of the variation and the vertical axis ${b || 0}%.`;
    } else if (info.method === "tsne") {
      const p = effectivePerplexity(n);
      s += `t-SNE with perplexity ${p}${p < settings.perplexity ? `, lowered to suit ${n} points` : ""}.`;
    } else if (info.method === "umap") {
      s += `UMAP with ${effectiveNeighbors(n)} neighbors.`;
    }
    if (n < 30 && info.method !== "pca" && !info.small && !info.fallback) s += " With this few points, distances on the map are rough. The similarity matrix shows exact scores.";
    return s;
  }
  const updateLayoutInfo = () => { $("#epLayoutInfo").textContent = state.points.length ? state.layoutInfo : ""; };

  // ── Similarity helpers ─────────────────────────────────────────────────
  const higherIsCloser = () => settings.metric !== "euclidean";
  const metricLabel = () => ({ cosine: "cosine similarity", dot: "dot product", euclidean: "euclidean distance" }[settings.metric]);
  const fmt = (s) => (settings.metric === "dot" && Math.abs(s) >= 10 ? s.toFixed(1) : s.toFixed(3));
  const simIndex = (id) => (state.layout ? state.layout.index.get(id) : undefined);
  // Score in the selected metric; falls back to cosine until the metric matrix arrives.
  function scoreAt(i, j) {
    const L = state.layout, n = L.ids.length;
    return L.m && L.metric === settings.metric ? L.m[i * n + j] : L.sim[i * n + j];
  }
  const closer = (a, b) => (higherIsCloser() ? b.s - a.s : a.s - b.s);
  // Bar width for a score, relative to the spread being shown.
  function barFrac(s, lo, hi) {
    if (settings.metric === "cosine") return clamp(s, 0, 1);
    if (!(hi > lo)) return 1;
    return higherIsCloser() ? (s - lo) / (hi - lo) : (hi - s) / (hi - lo);
  }

  let nbMemo = { key: "", val: [] };
  function neighbors(id, k) {
    const L = state.layout, i = simIndex(id);
    if (!L || i == null) return [];
    const key = `${id}|${k}|${state.layoutVersion}|${state.points.length}|${settings.metric}`;
    if (nbMemo.key === key) return nbMemo.val;
    const n = L.ids.length, arr = [];
    for (let j = 0; j < n; j++) { if (j === i) continue; const jid = L.ids[j]; if (state.byId.has(jid)) arr.push({ id: jid, s: scoreAt(i, j), cos: L.sim[i * n + j] }); }
    arr.sort(closer);
    nbMemo = { key, val: arr.slice(0, k) };
    return nbMemo.val;
  }

  function recomputeEdges() {
    const L = state.layout; state.edges = []; state.edgeCapped = false;
    if (!L || settings.edgeMode === "off") { updateEdgeNote(); return; }
    const n = L.ids.length, S = L.sim, alive = L.ids.map((id) => state.byId.has(id)), out = [];
    if (settings.edgeMode === "knn") {
      const seen = new Set(), order = [];
      for (let i = 0; i < n; i++) {
        if (!alive[i]) continue;
        order.length = 0;
        for (let j = 0; j < n; j++) if (j !== i && alive[j]) order.push({ j, s: scoreAt(i, j) });
        order.sort(closer);
        for (let t = 0; t < Math.min(settings.k, order.length); t++) {
          const j = order[t].j, a = Math.min(i, j), b = Math.max(i, j), key = a * n + b;
          if (!seen.has(key)) { seen.add(key); out.push({ a, b, s: S[a * n + b], m: scoreAt(a, b) }); }
        }
      }
    } else {
      const t = settings.threshold;
      for (let i = 0; i < n; i++) { if (!alive[i]) continue; for (let j = i + 1; j < n; j++) if (alive[j] && S[i * n + j] >= t) out.push({ a: i, b: j, s: S[i * n + j], m: scoreAt(i, j) }); }
      if (out.length > EDGE_LIMIT) { out.sort((x, y) => y.s - x.s); out.length = EDGE_LIMIT; state.edgeCapped = true; }
    }
    let min = Infinity, max = -Infinity;
    for (const e of out) { if (e.s < min) min = e.s; if (e.s > max) max = e.s; }
    state.edges = out; state.edgeRange = { min, max };
    updateEdgeNote();
  }
  function updateEdgeNote() {
    const el = $("#epEdgeNote"), L = state.layout;
    if (!L || !state.points.length || settings.edgeMode === "off") { el.textContent = ""; return; }
    const touched = new Set(); for (const e of state.edges) { touched.add(e.a); touched.add(e.b); }
    const alone = L.ids.filter((id, i) => state.byId.has(id) && !touched.has(i)).length;
    let s = `${state.edges.length.toLocaleString()} line${state.edges.length === 1 ? "" : "s"}.`;
    if (settings.edgeMode === "threshold" && alone) s += ` ${alone} point${alone === 1 ? " has" : "s have"} no match at ${settings.threshold.toFixed(2)} or above. Click one to see its best scores.`;
    if (state.edgeCapped) s += ` Showing the ${EDGE_LIMIT.toLocaleString()} strongest.`;
    el.textContent = s;
  }

  function pointColor(p) {
    if (settings.colorBy === "label") return p.label ? PALETTE[state.labelColors.get(p.label) % PALETTE.length] : NEUTRAL;
    if (settings.colorBy === "cluster") {
      const L = state.layout, i = simIndex(p.id);
      return L && L.clusters && i != null ? PALETTE[L.clusters[i] % PALETTE.length] : NEUTRAL;
    }
    return PALETTE[p.color % PALETTE.length];
  }
  function clusterOf(id) { const L = state.layout, i = simIndex(id); return L && L.clusters && i != null ? L.clusters[i] : null; }

  // ── Map rendering ──────────────────────────────────────────────────────
  const map = $("#epMap"), mctx = map.getContext("2d");
  let MW = 0, MH = 0, DPR = 1, raf = 0;
  const SCR = new Map();

  new ResizeObserver(() => {
    const r = map.parentElement.getBoundingClientRect();
    DPR = window.devicePixelRatio || 1; MW = r.width; MH = r.height;
    map.width = Math.max(1, Math.round(MW * DPR)); map.height = Math.max(1, Math.round(MH * DPR));
    requestDraw();
  }).observe(map.parentElement);

  const baseScale = () => (Math.min(MW, MH) / 2) * 0.82;
  function toScreen(wx, wy) { const b = baseScale() * state.view.s; return [MW / 2 + state.view.x + wx * b, MH / 2 + state.view.y - wy * b]; }
  function toWorld(sx, sy) { const b = baseScale() * state.view.s; return [(sx - MW / 2 - state.view.x) / b, -(sy - MH / 2 - state.view.y) / b]; }

  // New coordinates are centred and scaled into [-1, 1]; the final frame of a
  // layout eases from where points were, so switching methods animates.
  function applyCoords(coords, final) {
    const ids = pendingIds, n = ids.length;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < n; i++) { const x = coords[2 * i], y = coords[2 * i + 1]; if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, ext = Math.max(maxX - minX, maxY - minY) / 2 || 1, now = performance.now();
    for (let i = 0; i < n; i++) {
      const id = ids[i]; if (!state.byId.has(id)) continue;
      const tx = (coords[2 * i] - cx) / ext, ty = (coords[2 * i + 1] - cy) / ext;
      const p = state.pos.get(id);
      if (!p) { state.pos.set(id, { x: tx, y: ty, fx: tx, fy: ty, tx, ty, t0: 0 }); continue; }
      if (final && !reduceMotion) { p.fx = p.x; p.fy = p.y; p.tx = tx; p.ty = ty; p.t0 = now; }
      else { p.x = p.tx = tx; p.y = p.ty = ty; p.t0 = 0; }
    }
    requestDraw();
  }

  function requestDraw() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame(now) {
    raf = 0; let anim = false;
    for (const p of state.pos.values()) {
      if (!p.t0) continue;
      const t = Math.min(1, (now - p.t0) / 450), e = 1 - Math.pow(1 - t, 3);
      p.x = p.fx + (p.tx - p.fx) * e; p.y = p.fy + (p.ty - p.fy) * e;
      if (t >= 1) p.t0 = 0; else anim = true;
    }
    if (state.tab === "map") drawMap();
    if (anim) requestDraw();
  }

  function drawMap() {
    const c = mctx;
    c.setTransform(DPR, 0, 0, DPR, 0, 0);
    c.fillStyle = theme.bg; c.fillRect(0, 0, MW, MH);

    let g = 64 * state.view.s; while (g < 32) g *= 2; while (g > 128) g /= 2;
    const ox = (((MW / 2 + state.view.x) % g) + g) % g, oy = (((MH / 2 + state.view.y) % g) + g) % g;
    c.strokeStyle = theme.grid; c.lineWidth = 1; c.beginPath();
    for (let x = ox; x < MW; x += g) { c.moveTo(Math.round(x) + 0.5, 0); c.lineTo(Math.round(x) + 0.5, MH); }
    for (let y = oy; y < MH; y += g) { c.moveTo(0, Math.round(y) + 0.5); c.lineTo(MW, Math.round(y) + 0.5); }
    c.stroke();

    SCR.clear();
    for (const p of state.points) { const q = state.pos.get(p.id); if (q) SCR.set(p.id, toScreen(q.x, q.y)); }

    const L = state.layout, pinned = state.pinned, hl = state.highlight;
    const focus = pinned != null ? new Set([pinned, ...neighbors(pinned, 5).map((n) => n.id)]) : null;
    if (focus && L) for (const e of state.edges) { const a = L.ids[e.a], b = L.ids[e.b]; if (a === pinned) focus.add(b); else if (b === pinned) focus.add(a); }
    const dimOthers = focus || hl;

    const scoreLabels = [];
    if (L && state.edges.length) {
      const { min, max } = state.edgeRange, showAll = settings.edgeScores && state.edges.length <= 200;
      for (const e of state.edges) {
        const a = L.ids[e.a], b = L.ids[e.b], pa = SCR.get(a), pb = SCR.get(b);
        if (!pa || !pb) continue;
        const t = max > min ? (e.s - min) / (max - min) : 1, involved = pinned != null && (a === pinned || b === pinned);
        if (involved) { c.strokeStyle = `rgba(16,185,129,${0.55 + 0.4 * t})`; c.lineWidth = 1.4 + 1.6 * t; }
        else { c.strokeStyle = theme.line((0.1 + 0.4 * t) * (dimOthers ? 0.35 : 1)); c.lineWidth = 0.6 + 1.3 * t; }
        c.beginPath(); c.moveTo(pa[0], pa[1]); c.lineTo(pb[0], pb[1]); c.stroke();
        if (involved || showAll) scoreLabels.push([(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, e.m, involved]);
      }
    }
    // A pinned point always gets dashed lines to its top matches.
    if (pinned != null && SCR.has(pinned)) {
      const pp = SCR.get(pinned);
      for (const nb of neighbors(pinned, 5)) {
        const q = SCR.get(nb.id); if (!q) continue;
        const already = scoreLabels.some((s) => s[3] && Math.abs(s[0] - (pp[0] + q[0]) / 2) < 0.5 && Math.abs(s[1] - (pp[1] + q[1]) / 2) < 0.5);
        if (already) continue;
        c.setLineDash([4, 4]); c.strokeStyle = "rgba(16,185,129,0.55)"; c.lineWidth = 1.2;
        c.beginPath(); c.moveTo(pp[0], pp[1]); c.lineTo(q[0], q[1]); c.stroke(); c.setLineDash([]);
        scoreLabels.push([(pp[0] + q[0]) / 2, (pp[1] + q[1]) / 2, nb.s, true]);
      }
    }

    const r = settings.pointSize;
    const order = state.points.filter((p) => SCR.has(p.id));
    const rank = (p) => (p.id === pinned || p.id === state.hover ? 2 : (focus && focus.has(p.id)) || (hl && hl.has(p.id)) ? 1 : 0);
    if (dimOthers || state.hover != null) order.sort((a, b) => rank(a) - rank(b));
    for (const p of order) {
      const [x, y] = SCR.get(p.id), col = pointColor(p);
      const faded = (hl && !hl.has(p.id)) || (focus && !focus.has(p.id));
      c.globalAlpha = faded ? 0.22 : 1;
      if (!faded) { c.fillStyle = col + "2E"; c.beginPath(); c.arc(x, y, r + 4, 0, Math.PI * 2); c.fill(); }
      c.fillStyle = col; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
      c.globalAlpha = 1;
      if (p.id === pinned) { c.strokeStyle = theme.ink; c.lineWidth = 2; c.beginPath(); c.arc(x, y, r + 4, 0, Math.PI * 2); c.stroke(); }
      else if (p.id === state.hover) { c.strokeStyle = theme.muted; c.lineWidth = 1.5; c.beginPath(); c.arc(x, y, r + 3, 0, Math.PI * 2); c.stroke(); }
      else if (hl && hl.has(p.id)) { c.strokeStyle = "#10B981"; c.lineWidth = 2; c.beginPath(); c.arc(x, y, r + 4, 0, Math.PI * 2); c.stroke(); }
    }

    if (scoreLabels.length) {
      c.font = "700 11px system-ui, sans-serif"; c.textAlign = "center"; c.textBaseline = "middle";
      for (const [x, y, s, inv] of scoreLabels) {
        const txt = settings.metric === "cosine" ? s.toFixed(2) : fmt(s), w = c.measureText(txt).width + 10;
        c.fillStyle = inv ? "rgba(3,36,26,.92)" : theme.dark ? "rgba(9,15,30,.88)" : "rgba(255,255,255,.92)";
        roundRect(c, x - w / 2, y - 9, w, 18, 5); c.fill();
        c.fillStyle = inv ? "#5EF0C2" : theme.ink; c.fillText(txt, x, y + 0.5);
      }
    }

    if (settings.labelMode !== "none" || state.hover != null || pinned != null) {
      const n = state.points.length;
      const showAll = settings.labelMode === "all" || (settings.labelMode === "auto" && n <= Math.max(60, 60 * state.view.s));
      c.font = "600 12px system-ui, sans-serif"; c.textAlign = "left"; c.textBaseline = "middle"; c.lineJoin = "round";
      for (const p of order) {
        const special = p.id === state.hover || p.id === pinned || (focus && focus.has(p.id)) || (hl && hl.has(p.id));
        if (!(special || (showAll && settings.labelMode !== "none"))) continue;
        const [x, y] = SCR.get(p.id), faded = (hl && !hl.has(p.id)) || (focus && !focus.has(p.id));
        const txt = trunc(p.text, 30);
        c.globalAlpha = faded ? 0.3 : 1;
        c.strokeStyle = theme.halo; c.lineWidth = 4; c.strokeText(txt, x + r + 6, y);
        c.fillStyle = theme.dark ? pointColor(p) : theme.ink; c.fillText(txt, x + r + 6, y);
      }
      c.globalAlpha = 1;
    }
  }
  function roundRect(c, x, y, w, h, r) {
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }
  function hitTest(mx, my) {
    let best = null, bd = Math.max(settings.pointSize + 5, 10) ** 2;
    for (const [id, [x, y]] of SCR) { const d = (x - mx) ** 2 + (y - my) ** 2; if (d < bd) { bd = d; best = id; } }
    return best;
  }

  let drag = null;
  const local = (e) => { const r = map.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  map.addEventListener("pointerdown", (e) => {
    map.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y, moved: false };
  });
  map.addEventListener("pointermove", (e) => {
    const [mx, my] = local(e);
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; map.classList.add("panning"); hideTooltip(); }
      if (drag.moved) { state.view.x = drag.vx + dx; state.view.y = drag.vy + dy; requestDraw(); return; }
    }
    const h = hitTest(mx, my);
    if (h !== state.hover) { state.hover = h; requestDraw(); }
    map.classList.toggle("over", h != null);
    if (h != null) showTooltip(h, mx, my); else hideTooltip();
  });
  map.addEventListener("pointerup", (e) => {
    if (drag && !drag.moved) {
      const [mx, my] = local(e), h = hitTest(mx, my);
      if (h != null) pin(h);
      else if (state.pinned != null) pin(null);
      else if (state.highlight) { state.highlight = null; requestDraw(); }
    }
    drag = null; map.classList.remove("panning");
  });
  map.addEventListener("pointerleave", () => { if (!drag) { state.hover = null; hideTooltip(); requestDraw(); } });
  map.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [mx, my] = local(e), [wx, wy] = toWorld(mx, my);
    state.view.s = clamp(state.view.s * Math.exp(-e.deltaY * 0.0015), 0.25, 80);
    const b = baseScale() * state.view.s;
    state.view.x = mx - MW / 2 - wx * b; state.view.y = my - MH / 2 + wy * b;
    hideTooltip(); requestDraw();
  }, { passive: false });

  function fitView() { state.view = { s: 1, x: 0, y: 0 }; requestDraw(); }
  function centerOn(id) {
    const q = state.pos.get(id); if (!q) return;
    if (state.view.s < 1) state.view.s = 1;
    const b = baseScale() * state.view.s; state.view.x = -q.x * b; state.view.y = q.y * b; requestDraw();
  }
  function pin(id) {
    state.pinned = id; renderDetail(); requestDraw();
    $$(".ep-pt").forEach((el) => el.classList.toggle("on", +el.dataset.id === id));
  }

  // ── Tooltip & detail ───────────────────────────────────────────────────
  const tooltip = $("#epTooltip");
  const kv = (k, v) => `<div class="ep-kv"><span>${k}</span><b>${v}</b></div>`;
  function showTooltip(id, mx, my) {
    const p = state.byId.get(id); if (!p) return;
    const nb = neighbors(id, 3), cl = clusterOf(id);
    let h = `<div class="tt">${esc(trunc(p.text, 220))}</div>`;
    if (p.label) h += kv("Label", esc(p.label));
    if (cl != null && settings.colorBy === "cluster") h += kv("Cluster", cl + 1);
    if (nb.length) h += `<div class="sub">Closest by ${metricLabel()}</div>` + nb.map((n) => kv(esc(trunc(state.byId.get(n.id).text, 28)), fmt(n.s))).join("");
    tooltip.innerHTML = h; tooltip.style.display = "block";
    const w = tooltip.offsetWidth, ht = tooltip.offsetHeight;
    let x = mx + 16, y = my + 14;
    if (x + w > MW - 8) x = mx - w - 16;
    if (y + ht > MH - 8) y = Math.max(8, MH - ht - 8);
    tooltip.style.left = Math.max(8, x) + "px"; tooltip.style.top = y + "px";
  }
  const hideTooltip = () => { tooltip.style.display = "none"; };

  function renderDetail() {
    const el = $("#epDetail"), p = state.pinned != null ? state.byId.get(state.pinned) : null;
    if (!p) { el.hidden = true; return; }
    const nb = neighbors(p.id, 10), cl = clusterOf(p.id);
    const vals = nb.map((n) => n.s), lo = Math.min(...vals), hi = Math.max(...vals);
    const chips = [p.label ? `<span class="ep-chip">${esc(p.label)}</span>` : "", cl != null ? `<span class="ep-chip">Cluster ${cl + 1}</span>` : ""].join("");
    el.innerHTML = `
      <div class="d-head"><span class="dot" style="background:${pointColor(p)}"></span><p class="d-text">${esc(p.text)}</p>
        <button type="button" class="ep-icon-btn" data-act="close" aria-label="Close">×</button></div>
      ${chips ? `<div class="d-meta">${chips}</div>` : ""}
      <h3>Closest points by ${metricLabel()}${higherIsCloser() ? "" : " (lower is closer)"}</h3>
      ${nb.length ? `<ol class="ep-nb">${nb.map((n) => {
        const q = state.byId.get(n.id);
        return `<li data-id="${n.id}"><span class="n-t" title="${esc(q.text)}">${esc(q.text)}</span><span class="n-s">${fmt(n.s)}</span><span class="ep-bar"><i style="width:${barFrac(n.s, lo, hi) * 100}%"></i></span></li>`;
      }).join("")}</ol>` : `<p class="ep-note">${state.points.length > 1 ? "Calculating…" : "Add more points to compare."}</p>`}
      <div class="d-actions"><button type="button" class="ep-btn small" data-act="search">Search with this text</button><button type="button" class="ep-btn small danger" data-act="remove">Remove</button></div>`;
    el.hidden = false;
  }
  $("#epDetail").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) { const id = +li.dataset.id; pin(id); centerOn(id); return; }
    const act = e.target.closest("[data-act]"); if (!act) return;
    const id = state.pinned;
    if (act.dataset.act === "close") pin(null);
    else if (act.dataset.act === "remove") removePoint(id);
    else if (act.dataset.act === "search") { $("#epQuery").value = state.byId.get(id).text; $("#epSearchMode").value = "meaning"; updateSearchHelp(); setTab("search"); runSearch(); }
  });

  // ── Sidebar list ───────────────────────────────────────────────────────
  function renderList() {
    const f = $("#epPointFilter").value.trim().toLowerCase(), MAX = 400;
    const list = f ? state.points.filter((p) => p.text.toLowerCase().includes(f) || p.label.toLowerCase().includes(f)) : state.points;
    $("#epCount").textContent = state.points.length;
    let h = list.slice(0, MAX).map((p) => `<div class="ep-pt${p.id === state.pinned ? " on" : ""}" data-id="${p.id}" title="${esc(p.text)}" role="button" tabindex="0"><span class="dot" style="background:${pointColor(p)}"></span><span class="t">${esc(p.text)}</span>${p.label ? `<span class="lb">${esc(p.label)}</span>` : ""}<button type="button" class="x" data-remove="${p.id}" aria-label="Remove ${esc(trunc(p.text, 30))}">×</button></div>`).join("");
    if (list.length > MAX) h += `<p class="ep-note">Showing ${MAX} of ${list.length}. Filter to narrow the list.</p>`;
    if (!state.points.length) h = '<p class="ep-note">Points you embed appear here.</p>';
    else if (!list.length) h = '<p class="ep-note">No points match that filter.</p>';
    $("#epPointList").innerHTML = h;
  }
  function onListActivate(e) {
    const rm = e.target.closest("[data-remove]");
    if (rm) { removePoint(+rm.dataset.remove); return; }
    const row = e.target.closest(".ep-pt"); if (!row) return;
    const id = +row.dataset.id; setTab("map"); pin(id); centerOn(id);
  }
  $("#epPointList").addEventListener("click", onListActivate);
  $("#epPointList").addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches(".ep-pt")) { e.preventDefault(); onListActivate(e); } });
  $("#epPointFilter").addEventListener("input", renderList);

  function updateMeta() {
    const n = state.points.length;
    $("#epMeta").textContent = n ? `${n} point${n === 1 ? "" : "s"}, ${state.dims} dims, ${state.modelName}` : "";
  }
  function updateModelWarn() {
    const el = $("#epModelWarn"), mismatch = state.points.length && state.modelKey && state.modelKey !== connKey();
    el.hidden = !mismatch;
    if (mismatch) el.textContent = `Your points were embedded with ${state.modelName}. New text can only be compared after you re-embed all points with ${modelLabel()}, or switch back.`;
    $("#epReembedBtn").classList.toggle("primary", !!mismatch);
  }

  // ── Similarity matrix ──────────────────────────────────────────────────
  const heat = $("#epHeat"), hctx = heat.getContext("2d");
  let HM = null;
  function cmap(t) {
    t = clamp(t, 0, 1) * (STOPS.length - 1);
    const i = Math.min(STOPS.length - 2, Math.floor(t)), f = t - i, a = STOPS[i], b = STOPS[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }
  function heatOrder() {
    const L = state.layout; if (!L) return [];
    const idx = L.ids.map((id, i) => i).filter((i) => state.byId.has(L.ids[i]));
    if (settings.hmOrder === "cluster" && L.clusters) idx.sort((a, b) => L.clusters[a] - L.clusters[b] || a - b);
    if (settings.hmOrder === "label") idx.sort((a, b) => (state.byId.get(L.ids[a]).label || "￿").localeCompare(state.byId.get(L.ids[b]).label || "￿") || a - b);
    return idx;
  }
  function drawHeatmap() {
    const r = heat.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    heat.width = Math.max(1, Math.round(r.width * dpr)); heat.height = Math.max(1, Math.round(r.height * dpr));
    const c = hctx; c.setTransform(dpr, 0, 0, dpr, 0, 0); c.fillStyle = theme.bg; c.fillRect(0, 0, r.width, r.height);
    const L = state.layout, order = heatOrder(), n = order.length;
    HM = null;
    if (!L || n < 2) {
      c.fillStyle = theme.muted; c.font = "600 14px system-ui, sans-serif"; c.textAlign = "center";
      c.fillText(state.points.length < 2 ? "Embed at least two points to compare them here." : "Calculating…", r.width / 2, r.height / 2);
      return;
    }
    const N = L.ids.length, inv = !higherIsCloser();
    let min = Infinity, max = -Infinity;
    for (const i of order) for (const j of order) if (i !== j) { const v = scoreAt(i, j); if (v < min) min = v; if (v > max) max = v; }
    if (!(max > min)) min = max - 1;
    const norm = (v) => { const t = (v - min) / (max - min); return inv ? 1 - t : t; };
    const labels = n <= 40, left = labels ? Math.min(170, r.width * 0.28) : 20, top = labels ? 120 : 20;
    const size = Math.max(40, Math.min(r.width - left - 20, r.height - top - 20)), cell = size / n;
    const off = document.createElement("canvas"); off.width = n; off.height = n;
    const octx = off.getContext("2d"), img = octx.createImageData(n, n);
    for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
      const t = a === b ? 1 : norm(scoreAt(order[a], order[b])), [R, G, B] = cmap(t), o = (a * n + b) * 4;
      img.data[o] = R; img.data[o + 1] = G; img.data[o + 2] = B; img.data[o + 3] = 255;
    }
    octx.putImageData(img, 0, 0);
    c.imageSmoothingEnabled = false; c.drawImage(off, left, top, size, size);
    if (labels) {
      c.font = "600 11px system-ui, sans-serif"; c.fillStyle = theme.ink;
      c.textAlign = "right"; c.textBaseline = "middle";
      order.forEach((i, a) => c.fillText(trunc(state.byId.get(L.ids[i]).text, 22), left - 8, top + (a + 0.5) * cell));
      c.textAlign = "left";
      order.forEach((i, a) => { c.save(); c.translate(left + (a + 0.5) * cell, top - 8); c.rotate(-Math.PI / 4); c.fillText(trunc(state.byId.get(L.ids[i]).text, 18), 0, 0); c.restore(); });
      if (cell >= 26) {
        c.font = "700 10px system-ui, sans-serif"; c.textAlign = "center";
        for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
          if (a === b) continue;
          const v = scoreAt(order[a], order[b]), t = norm(v);
          c.fillStyle = t > 0.6 ? (theme.dark ? "#03241A" : "#FFFFFF") : theme.ink;
          c.fillText(settings.metric === "dot" && Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(2), left + (b + 0.5) * cell, top + (a + 0.5) * cell);
        }
      }
    }
    $("#epHmMin").textContent = (inv ? max : min).toFixed(2) + (inv ? " far" : "");
    $("#epHmMax").textContent = (inv ? min : max).toFixed(2) + (inv ? " close" : "");
    HM = { order, left, top, cell, n, N };
  }
  heat.addEventListener("mousemove", (e) => {
    const out = $("#epHmReadout"); if (!HM) return;
    const rr = heat.getBoundingClientRect(), x = e.clientX - rr.left - HM.left, y = e.clientY - rr.top - HM.top;
    const b = Math.floor(x / HM.cell), a = Math.floor(y / HM.cell);
    if (a < 0 || b < 0 || a >= HM.n || b >= HM.n) { out.textContent = "Hover a cell to see the score"; return; }
    const L = state.layout, i = HM.order[a], j = HM.order[b];
    out.textContent = `${trunc(state.byId.get(L.ids[i]).text, 40)}  vs  ${trunc(state.byId.get(L.ids[j]).text, 40)}:  ${fmt(scoreAt(i, j))}`;
  });
  heat.addEventListener("click", (e) => {
    if (!HM) return;
    const rr = heat.getBoundingClientRect(), a = Math.floor((e.clientY - rr.top - HM.top) / HM.cell);
    if (a < 0 || a >= HM.n) return;
    const id = state.layout.ids[HM.order[a]]; setTab("map"); pin(id); centerOn(id);
  });
  new ResizeObserver(() => { if (state.tab === "heatmap") drawHeatmap(); }).observe(heat.parentElement);

  function exportCsv() {
    const L = state.layout; if (!L || state.points.length < 2) { toast("Embed at least two points first.", "warn"); return; }
    const idx = heatOrder(), q = (s) => `"${String(s).replace(/"/g, '""')}"`;
    const names = idx.map((i) => state.byId.get(L.ids[i]).text);
    const rows = [["", ...names].map(q).join(",")];
    idx.forEach((i, a) => rows.push([q(names[a]), ...idx.map((j) => scoreAt(i, j).toFixed(4))].join(",")));
    download(`similarity-matrix-${settings.metric}.csv`, new Blob([rows.join("\n")], { type: "text/csv" }));
  }

  // ── Search ─────────────────────────────────────────────────────────────
  function updateSearchHelp() {
    const math = $("#epSearchMode").value === "math";
    $("#epSearchHelp").innerHTML = math
      ? "Add and subtract meanings, then see which points sit closest to the result. Put spaces around the signs, for example <code>developer - code + servers</code>. Points that appear in the expression are left out of the results."
      : "Find the points closest in meaning to your query, even when they share no words with it. The query is not added as a point. Matches are highlighted on the map.";
    $("#epQuery").placeholder = math ? "developer - code + servers" : "Search your points by meaning";
  }
  async function runSearch() {
    const q = $("#epQuery").value.trim(), mode = $("#epSearchMode").value, topN = +$("#epTopN").value;
    if (!q) { toast("Type a query first.", "warn"); return; }
    if (!state.points.length) { toast("Embed some points first, then search them.", "warn"); return; }
    if (state.modelKey !== connKey()) { toast(`Your points were embedded with ${state.modelName}. Switch back to it or re-embed all points to search.`, "error"); return; }
    $("#epSearchBtn").disabled = true; setStatus("busy", "Searching…");
    try {
      let raw, exclude = new Set();
      if (mode === "math") {
        const parts = q.replace(/−/g, "-").split(/\s+([+-])\s+/);
        const terms = [{ sign: 1, text: parts[0].trim() }];
        for (let i = 1; i < parts.length; i += 2) terms.push({ sign: parts[i] === "-" ? -1 : 1, text: (parts[i + 1] || "").trim() });
        const clean = terms.filter((t) => t.text);
        if (clean.length < 2) throw new Error('Vector math needs at least two terms, like "developer - code + servers".');
        const { vecs } = await embedTexts(clean.map((t) => t.text));
        raw = new Float32Array(vecs[0].length);
        clean.forEach((t, k) => { const u = normalize(vecs[k]); for (let d = 0; d < raw.length; d++) raw[d] += t.sign * u[d]; });
        clean.forEach((t) => exclude.add(t.text.toLowerCase()));
      } else {
        raw = (await embedTexts([q])).vecs[0];
      }
      if (raw.length !== state.dims) throw new Error("The query vector has a different size than your points. Re-embed all points.");
      const unit = normalize(raw);
      const score = (p) => {
        let s = 0;
        if (settings.metric === "cosine") for (let d = 0; d < unit.length; d++) s += unit[d] * p.vec[d];
        else if (settings.metric === "dot") for (let d = 0; d < raw.length; d++) s += raw[d] * p.raw[d];
        else { for (let d = 0; d < raw.length; d++) { const x = raw[d] - p.raw[d]; s += x * x; } s = Math.sqrt(s); }
        return s;
      };
      const scored = state.points.filter((p) => !exclude.has(p.text.toLowerCase())).map((p) => ({ p, s: score(p) })).sort(closer).slice(0, topN);
      const vals = scored.map((x) => x.s), lo = Math.min(...vals), hi = Math.max(...vals);
      state.highlight = new Set(scored.map((x) => x.p.id)); state.pinned = null; renderDetail(); requestDraw();
      $("#epResults").innerHTML = `
        <div class="res-head"><span>${scored.length} closest to “${esc(trunc(q, 60))}” by ${metricLabel()}</span><button type="button" class="ep-btn small" id="epShowOnMap">Show on map</button></div>
        <ol class="ep-results">${scored.map((x, i) => `
          <li><span class="rank">${i + 1}</span>
            <div><div class="r-text" title="${esc(x.p.text)}">${esc(x.p.text)}${x.p.label ? `<span class="ep-chip">${esc(x.p.label)}</span>` : ""}</div>
            <div class="ep-bar"><i style="width:${barFrac(x.s, lo, hi) * 100}%"></i></div></div>
            <span class="r-score">${fmt(x.s)}</span>
            <button type="button" class="ep-btn small ghost" data-show="${x.p.id}">Locate</button></li>`).join("")}</ol>`;
      setStatus("ready", "Ready");
    } catch (e) { setStatus("error", "Search failed"); toast(e.message, "error"); }
    finally { $("#epSearchBtn").disabled = false; }
  }
  $("#epResults").addEventListener("click", (e) => {
    if (e.target.closest("#epShowOnMap")) { setTab("map"); requestDraw(); return; }
    const b = e.target.closest("[data-show]"); if (!b) return;
    const id = +b.dataset.show; setTab("map"); pin(id); centerOn(id);
  });
  $("#epSearchBtn").addEventListener("click", runSearch);
  $("#epQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });
  $("#epSearchMode").addEventListener("change", () => { settings.searchMode = $("#epSearchMode").value; updateSearchHelp(); saveSession(); });
  $("#epTopN").addEventListener("change", () => { settings.topN = +$("#epTopN").value; saveSession(); });

  // ── Tabs ───────────────────────────────────────────────────────────────
  const tabs = $$(".ep-tab");
  function setTab(t) {
    state.tab = t;
    tabs.forEach((b) => { const on = b.dataset.tab === t; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    ["map", "heatmap", "search"].forEach((k) => { $("#epPanel-" + k).hidden = k !== t; });
    hideTooltip();
    if (t === "map") requestDraw();
    if (t === "heatmap") requestAnimationFrame(drawHeatmap);
    if (t === "search") $("#epQuery").focus();
  }
  tabs.forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $(".ep-tabs").addEventListener("keydown", (e) => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0 || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus(); setTab(next.dataset.tab);
  });

  // ── Settings controls ──────────────────────────────────────────────────
  function syncControls() {
    $("#epProvider").value = settings.provider; $("#epModel").value = settings.model; $("#epMetric").value = settings.metric;
    $("#epConnFields").hidden = settings.provider === "demo"; $("#epDemoNote").hidden = settings.provider !== "demo";
    $$(".ep-seg[data-setting]").forEach((seg) => $$("button", seg).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === settings[seg.dataset.setting]))));
    $$("input[type=range][data-setting]").forEach((inp) => { inp.value = settings[inp.dataset.setting]; showRange(inp); });
    $("#epEdgeScores").checked = settings.edgeScores; $("#epSplitLines").checked = settings.splitLines;
    $("#epPerplexityRow").hidden = settings.method !== "tsne"; $("#epNeighborsRow").hidden = settings.method !== "umap";
    $("#epKRow").hidden = settings.edgeMode !== "knn"; $("#epThrRow").hidden = settings.edgeMode !== "threshold";
    $("#epEdgeScores").parentElement.hidden = settings.edgeMode === "off";
    $("#epClusterRow").hidden = settings.colorBy !== "cluster";
    $("#epSearchMode").value = settings.searchMode; $("#epTopN").value = String(settings.topN);
    $("#epMethodNote").textContent = {
      pca: "Keeps the overall spread honest. Fast and repeatable, but close groups can overlap.",
      tsne: "Pulls similar points into tight groups. Distances between groups mean little.",
      umap: "Groups like t-SNE but keeps more of the overall shape. Good for large sets.",
    }[settings.method];
  }
  function showRange(inp) {
    const k = inp.dataset.setting, out = $("#" + inp.id + "Out");
    if (out) out.textContent = k === "threshold" ? (+inp.value).toFixed(2) : inp.value;
  }
  function setSetting(k, v) { settings[k] = v; syncControls(); applyEffect(k); saveSession(); }
  function applyEffect(k) {
    switch (k) {
      case "method": case "perplexity": case "nNeighbors": if (state.points.length) scheduleLayout(0); break;
      case "edgeMode": case "k": case "threshold": recomputeEdges(); requestDraw(); break;
      case "metric": requestMetric(); renderDetail(); break;
      case "colorBy": renderList(); renderDetail(); requestDraw(); if (settings.colorBy === "cluster" && state.layout && !state.layout.clusters) requestClusters(); break;
      case "clusterK": requestClusters(); break;
      case "hmOrder": if (state.tab === "heatmap") drawHeatmap(); break;
      case "provider": case "model": updateModelWarn(); break;
      default: requestDraw();
    }
  }
  $$(".ep-seg[data-setting]").forEach((seg) => seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]"); if (b) setSetting(seg.dataset.setting, b.dataset.v);
  }));
  $$("input[type=range][data-setting]").forEach((inp) => {
    const k = inp.dataset.setting;
    inp.addEventListener("input", () => { settings[k] = +inp.value; showRange(inp); if (inp.dataset.live) applyEffect(k); });
    inp.addEventListener("change", () => { settings[k] = +inp.value; if (!inp.dataset.live) applyEffect(k); saveSession(); });
  });
  $("#epEdgeScores").addEventListener("change", (e) => setSetting("edgeScores", e.target.checked));
  $("#epSplitLines").addEventListener("change", (e) => setSetting("splitLines", e.target.checked));
  $("#epProvider").addEventListener("change", (e) => { setSetting("provider", e.target.value); if (settings.provider !== "demo") loadModels(true); });
  $("#epModel").addEventListener("change", (e) => setSetting("model", e.target.value.trim()));
  $("#epMetric").addEventListener("change", (e) => setSetting("metric", e.target.value));
  $("#epApiKey").addEventListener("input", (e) => { settings.apiKey = e.target.value; });

  // Models for the chosen provider, from the gateway's backend list.
  async function loadModels(quiet) {
    try {
      const res = await fetch(BASE_URL + "/admin/servers", { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const backends = await res.json();
      const ids = [...new Set(backends.filter((b) => b.backend_type === settings.provider).flatMap((b) => b.models || []))];
      $("#epModelList").innerHTML = ids.map((id) => `<option value="${esc(id)}">`).join("");
      const guess = ids.find((m) => /embed|bge|e5|gte|minilm|nomic|mxbai|arctic/i.test(m));
      if (!settings.model && (guess || ids.length === 1)) setSetting("model", guess || ids[0]);
      if (!quiet) toast(ids.length ? `Found ${ids.length} model${ids.length > 1 ? "s" : ""}: ${ids.slice(0, 5).join(", ")}${ids.length > 5 ? "…" : ""}` : "No servers of this type list any models.");
    } catch (e) { if (!quiet) toast("Couldn't load models: " + e.message, "error"); }
  }
  $("#epRefreshModels").addEventListener("click", () => loadModels(false));
  $("#epTestBtn").addEventListener("click", async () => {
    setStatus("busy", "Testing…");
    try {
      const v = settings.provider === "demo" ? [demoEmbed("test")] : await apiEmbed(["connection test"]);
      setStatus("ready", "Ready");
      toast(`Connected. ${modelLabel()} returns ${v[0].length}-dimension vectors.`);
    } catch (e) { setStatus("error", "Connection failed"); toast(e.message, "error"); }
  });
  $("#epReembedBtn").addEventListener("click", reembedAll);
  $("#epClearCache").addEventListener("click", async () => {
    memCache.clear();
    await tx("cache", "readwrite", (st) => { st.clear(); return null; });
    toast("Embedding cache cleared. Texts will be fetched again.");
  });
  $("#epSideToggle").addEventListener("click", (e) => {
    const open = e.currentTarget.getAttribute("aria-expanded") !== "true";
    e.currentTarget.setAttribute("aria-expanded", String(open));
    $("#epSide").classList.toggle("is-open", open);
  });

  // ── Composer, files, samples ───────────────────────────────────────────
  async function embedFromInput() {
    const items = parseInput($("#epInput").value, settings.splitLines);
    if (await addItems(items)) $("#epInput").value = "";
  }
  $("#epEmbedBtn").addEventListener("click", embedFromInput);
  $("#epInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); embedFromInput(); } });

  function parseCsv(text, sep = ",") {
    const rows = []; let row = [], cur = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
      else if (ch === '"') q = true;
      else if (ch === sep) { row.push(cur); cur = ""; }
      else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); rows.push(row); row = []; cur = ""; }
      else cur += ch;
    }
    if (cur || row.length) { row.push(cur); rows.push(row); }
    return rows.filter((r) => r.some((c) => c.trim()));
  }
  $("#epUploadBtn").addEventListener("click", () => $("#epUploadFile").click());
  $("#epUploadFile").addEventListener("change", async (e) => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    const text = await f.text(), ext = f.name.split(".").pop().toLowerCase();
    let items;
    if (ext === "csv" || ext === "tsv") {
      const rows = parseCsv(text, ext === "tsv" ? "\t" : ",");
      const head = rows[0].map((h) => h.trim().toLowerCase()), ti = head.indexOf("text"), li = head.indexOf("label");
      const body = ti >= 0 ? rows.slice(1) : rows, tcol = ti >= 0 ? ti : 0, lcol = ti >= 0 ? li : rows[0].length > 1 ? 1 : -1;
      items = body.map((r) => ({ text: (r[tcol] || "").trim(), label: lcol >= 0 ? (r[lcol] || "").trim() : "" })).filter((x) => x.text);
    } else items = parseInput(text, true);
    if (!items.length) { toast(`No text found in ${f.name}.`, "warn"); return; }
    if (items.length > 5000) { toast(`${f.name} has ${items.length} lines; embedding the first 5000.`, "warn"); items = items.slice(0, 5000); }
    if (await addItems(items)) toast(`Added points from ${f.name}.`);
  });

  const SAMPLES = {
    tech: [["developer", "role"], ["software engineer", "role"], ["devops engineer", "role"], ["site reliability engineer", "role"], ["data scientist", "role"], ["frontend developer", "role"],
      ["docker", "tool"], ["kubernetes", "tool"], ["terraform", "tool"], ["jenkins", "tool"], ["prometheus", "tool"],
      ["CI/CD pipeline", "practice"], ["infrastructure as code", "practice"], ["continuous deployment", "practice"], ["monitoring and alerting", "practice"], ["code review", "practice"], ["unit testing", "practice"]],
    animals: [["cat", "animal"], ["dog", "animal"], ["horse", "animal"], ["elephant", "animal"], ["tiger", "animal"], ["rabbit", "animal"], ["eagle", "animal"], ["dolphin", "animal"],
      ["car", "vehicle"], ["bus", "vehicle"], ["bicycle", "vehicle"], ["truck", "vehicle"], ["motorcycle", "vehicle"], ["airplane", "vehicle"], ["train", "vehicle"], ["boat", "vehicle"]],
    topics: [["Simmer the tomatoes with garlic for twenty minutes", "cooking"], ["Knead the dough until it is smooth and elastic", "cooking"], ["Season the soup with salt and fresh herbs", "cooking"], ["Roast the vegetables at a high temperature", "cooking"],
      ["The striker scored in the final minute of the match", "sports"], ["She trained for months before the marathon", "sports"], ["The team lost the championship on penalties", "sports"], ["He broke the national record in the 100 metres", "sports"],
      ["The function returns null when the list is empty", "programming"], ["We moved the API to a serverless backend", "programming"], ["Fix the memory leak before the next release", "programming"], ["Write a unit test for the login flow", "programming"],
      ["Interest rates rose for the third time this year", "finance"], ["The company reported strong quarterly earnings", "finance"], ["Diversify your portfolio to reduce risk", "finance"], ["The stock fell after the merger was cancelled", "finance"]],
    multi: [["The weather is nice today", "weather"], ["आज मौसम अच्छा है", "weather"], ["Il fait beau aujourd'hui", "weather"], ["Hoy hace buen tiempo", "weather"],
      ["I love reading books", "reading"], ["मुझे किताबें पढ़ना बहुत पसंद है", "reading"], ["J'adore lire des livres", "reading"], ["Me encanta leer libros", "reading"],
      ["Where is the train station?", "station"], ["रेलवे स्टेशन कहाँ है?", "station"], ["Où est la gare ?", "station"], ["¿Dónde está la estación de tren?", "station"],
      ["My phone battery is dead", "battery"], ["मेरे फ़ोन की बैटरी ख़त्म हो गई है", "battery"], ["La batterie de mon téléphone est vide", "battery"], ["Mi teléfono se quedó sin batería", "battery"]],
    apple: [["apple pie recipe", "fruit"], ["banana smoothie", "fruit"], ["fresh oranges at the market", "fruit"], ["how to grow mango trees", "fruit"],
      ["Apple releases a new iPhone", "tech"], ["Microsoft Windows update", "tech"], ["Google search algorithm", "tech"], ["Apple stock price today", "tech"],
      ["apple", ""], ["orange", ""]],
  };
  async function loadSample(key) {
    const s = SAMPLES[key]; if (!s) return;
    if (state.points.length && !confirm("Replace the current points with this sample?")) return;
    clearAll();
    setSetting("colorBy", s.some((x) => x[1]) ? "label" : "point");
    await addItems(s.map(([text, label]) => ({ text, label })));
  }
  $("#epSampleSelect").addEventListener("change", (e) => { const v = e.target.value; e.target.value = ""; loadSample(v); });
  $$("[data-sample]").forEach((b) => b.addEventListener("click", () => loadSample(b.dataset.sample)));

  // ── Import / export ────────────────────────────────────────────────────
  function download(name, blob) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  $("#epExportBtn").addEventListener("click", () => {
    if (!state.points.length) { toast("There is nothing to export yet.", "warn"); return; }
    const data = {
      app: "llm-gateway-embedding-playground", version: 2, exportedAt: new Date().toISOString(),
      connection: { provider: settings.provider, model: settings.model }, modelKey: state.modelKey, modelName: state.modelName, dims: state.dims,
      points: state.points.map((p) => ({ text: p.text, label: p.label, embedding: Array.from(p.raw, (v) => Math.round(v * 1e6) / 1e6) })),
    };
    download("embeddings.json", new Blob([JSON.stringify(data)], { type: "application/json" }));
  });
  $("#epImportBtn").addEventListener("click", () => $("#epImportFile").click());
  $("#epImportFile").addEventListener("change", async (e) => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      const pts = Array.isArray(data) ? data : data.points;
      if (!Array.isArray(pts) || !pts.length) throw new Error("No points found in this file.");
      if (state.points.length && !confirm("Replace the current points with the imported ones?")) return;
      // Earlier exports used "vector"; current ones use "embedding".
      const vecOf = (p) => (Array.isArray(p.embedding) ? p.embedding : Array.isArray(p.vector) ? p.vector : null);
      const withVec = pts.every((p) => vecOf(p) && vecOf(p).length);
      clearAll();
      if (data.connection) { Object.assign(settings, { provider: data.connection.provider || settings.provider, model: data.connection.model || settings.model }); syncControls(); }
      else if (data.model) { settings.model = data.model; if (data.provider) settings.provider = data.provider; syncControls(); }
      if (withVec) {
        const dim = vecOf(pts[0]).length;
        if (!pts.every((p) => vecOf(p).length === dim)) throw new Error("Embeddings in this file have different sizes.");
        state.points = pts.map((p, i) => { const raw = Float32Array.from(vecOf(p)); return { id: state.nextId++, text: String(p.text), label: String(p.label || ""), color: i, raw, vec: normalize(raw) }; });
        state.nextColor = state.points.length; state.dims = dim;
        state.modelKey = data.modelKey || connKey(); state.modelName = data.modelName || modelLabel();
        if (state.points.some((p) => p.label)) setSetting("colorBy", "label");
        onPointsChanged(); toast(`Imported ${state.points.length} points without any API calls.`);
      } else {
        await addItems(pts.map((p) => ({ text: String(p.text || p), label: String(p.label || "") })));
      }
    } catch (err) { toast("Import failed: " + err.message, "error"); }
  });
  $("#epClearBtn").addEventListener("click", () => {
    if (!state.points.length) return;
    if (confirm(`Remove all ${state.points.length} points? Cached embeddings are kept, so re-adding them is instant.`)) clearAll();
  });
  $("#epFitBtn").addEventListener("click", fitView);
  $("#epPngBtn").addEventListener("click", () => { drawMap(); map.toBlob((b) => b && download("embedding-map.png", b)); });
  $("#epCsvBtn").addEventListener("click", exportCsv);

  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select") || document.querySelector('[role="dialog"]:not(.hidden)')) return;
    if (e.key === "Escape" && (state.pinned != null || state.highlight)) { pin(null); state.highlight = null; requestDraw(); }
    if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && state.tab === "map") fitView();
  });
  document.addEventListener("admin:themechange", () => requestAnimationFrame(() => { readTheme(); requestDraw(); if (state.tab === "heatmap") drawHeatmap(); }));

  // ── Start ──────────────────────────────────────────────────────────────
  (async () => {
    readTheme();
    await loadSession();
    syncControls(); updateSearchHelp();
    onPointsChanged();
    if (settings.provider !== "demo") loadModels(true);
  })();
})();
