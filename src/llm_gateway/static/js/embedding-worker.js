// embedding-worker.js — all O(n²)+ work for the Embedding Playground runs
// here, off the main thread: pairwise similarity, the chosen metric's matrix,
// PCA, t-SNE, UMAP and k-means.
//
// Messages in:
//   {type:"layout", jobId, n, d, unit, raw, metric, method, perplexity, nNeighbors, iters, clusterK}
//   {type:"metric", jobId, metric}        recompute the metric matrix only
//   {type:"cluster", jobId, k}            recompute clusters only
// Messages out (all carry jobId):
//   sim {sim}  metric {metric, m}  clusters {labels}  progress {coords, frac}
//   done {coords, info}  error {message}
//
// The page terminates this worker when a new layout starts, so a job never
// needs to check whether it has been superseded.
"use strict";

const UMAP_URL = "/static/js/umap-js.min.js";
let U = null, R = null, N = 0, D = 0, SIM = null, umapLoaded = false;

function mulberry(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Cosine similarity: vectors in U are unit length, so it is a dot product.
function computeSim() {
  SIM = new Float32Array(N * N);
  for (let i = 0; i < N; i++) {
    const oi = i * D;
    SIM[i * N + i] = 1;
    for (let j = i + 1; j < N; j++) {
      const oj = j * D;
      let s = 0;
      for (let k = 0; k < D; k++) s += U[oi + k] * U[oj + k];
      SIM[i * N + j] = s; SIM[j * N + i] = s;
    }
  }
}

// Matrix for the selected metric. Cosine reuses SIM; dot and euclidean use
// the raw (unnormalised) vectors.
function metricMatrix(metric) {
  if (metric === "cosine") return SIM.slice();
  const M = new Float32Array(N * N);
  for (let i = 0; i < N; i++) {
    const oi = i * D;
    for (let j = i; j < N; j++) {
      const oj = j * D;
      let s = 0;
      if (metric === "dot") for (let k = 0; k < D; k++) s += R[oi + k] * R[oj + k];
      else { for (let k = 0; k < D; k++) { const x = R[oi + k] - R[oj + k]; s += x * x; } s = Math.sqrt(s); }
      M[i * N + j] = s; M[j * N + i] = s;
    }
  }
  return M;
}

// PCA through the double-centred Gram matrix (Gram = cosine similarity for
// unit vectors). Cost is O(n²) per iteration instead of O(n·d), and it also
// yields the share of variance each axis explains.
function pca(dims) {
  const n = N, coords = new Float64Array(n * dims);
  if (n < 2) return { coords, explained: [] };
  const r = new Float64Array(n);
  let m = 0;
  for (let i = 0; i < n; i++) { let s = 0; for (let j = 0; j < n; j++) s += SIM[i * n + j]; r[i] = s / n; m += r[i]; }
  m /= n;
  const G = new Float64Array(n * n);
  let trace = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) G[i * n + j] = SIM[i * n + j] - r[i] - r[j] + m;
  for (let i = 0; i < n; i++) trace += G[i * n + i];
  const rand = mulberry(7), vecs = [], vals = [];
  for (let c = 0; c < dims; c++) {
    const v = new Float64Array(n);
    for (let i = 0; i < n; i++) v[i] = rand() - 0.5;
    let nv = 0; for (let i = 0; i < n; i++) nv += v[i] * v[i]; nv = Math.sqrt(nv) || 1;
    for (let i = 0; i < n; i++) v[i] /= nv;
    let lambda = 0;
    const w = new Float64Array(n);
    for (let it = 0; it < 300; it++) {
      for (let i = 0; i < n; i++) { let s = 0; const o = i * n; for (let j = 0; j < n; j++) s += G[o + j] * v[j]; w[i] = s; }
      for (let k = 0; k < c; k++) { let d = 0; for (let i = 0; i < n; i++) d += vecs[k][i] * v[i]; for (let i = 0; i < n; i++) w[i] -= vals[k] * d * vecs[k][i]; }
      lambda = 0; for (let i = 0; i < n; i++) lambda += v[i] * w[i];
      let nw = 0; for (let i = 0; i < n; i++) nw += w[i] * w[i]; nw = Math.sqrt(nw);
      if (nw < 1e-12) break;
      let diff = 0;
      for (let i = 0; i < n; i++) { const x = w[i] / nw; diff += Math.abs(x - v[i]); v[i] = x; }
      if (diff < 1e-10) break;
    }
    lambda = Math.max(lambda, 0);
    vecs.push(v); vals.push(lambda);
    const sq = Math.sqrt(lambda);
    for (let i = 0; i < n; i++) coords[i * dims + c] = v[i] * sq;
  }
  return { coords, explained: vals.map((l) => (trace > 0 ? l / trace : 0)) };
}

// Exact t-SNE, PCA-initialised so runs are repeatable.
function tsne(perp, iters, post) {
  const n = N, Dm = new Float64Array(n * n);
  for (let i = 0; i < n * n; i++) Dm[i] = Math.max(0, 2 - 2 * SIM[i]);
  const P = new Float64Array(n * n), row = new Float64Array(n), logU = Math.log(perp);
  for (let i = 0; i < n; i++) {
    let dmin = Infinity;
    for (let j = 0; j < n; j++) if (j !== i && Dm[i * n + j] < dmin) dmin = Dm[i * n + j];
    let beta = 1, lo = -Infinity, hi = Infinity;
    for (let t = 0; t < 100; t++) {
      let sum = 0, dsum = 0;
      for (let j = 0; j < n; j++) {
        if (j === i) { row[j] = 0; continue; }
        const d = Dm[i * n + j] - dmin, v = Math.exp(-d * beta);
        row[j] = v; sum += v; dsum += d * v;
      }
      const H = Math.log(sum) + (beta * dsum) / sum, diff = H - logU;
      if (Math.abs(diff) < 1e-5) break;
      if (diff > 0) { lo = beta; beta = hi === Infinity ? beta * 2 : (beta + hi) / 2; }
      else { hi = beta; beta = lo === -Infinity ? beta / 2 : (beta + lo) / 2; }
    }
    let sum = 0;
    for (let j = 0; j < n; j++) sum += row[j];
    for (let j = 0; j < n; j++) P[i * n + j] = row[j] / (sum || 1);
  }
  const Ps = new Float64Array(n * n);
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (i !== j) Ps[i * n + j] = Math.max((P[i * n + j] + P[j * n + i]) / (2 * n), 1e-12);

  const init = pca(2).coords;
  let sd = 0;
  for (let i = 0; i < n; i++) sd += init[2 * i] * init[2 * i];
  sd = Math.sqrt(sd / n) || 1;
  const rand = mulberry(42), Y = new Float64Array(n * 2);
  for (let i = 0; i < n * 2; i++) Y[i] = (init[i] / sd) * 1e-4 + (rand() - 0.5) * 1e-6;

  const lr = Math.max(n / 12 / 4, 50);
  const gains = new Float64Array(n * 2).fill(1), upd = new Float64Array(n * 2), grad = new Float64Array(n * 2), Q = new Float64Array(n * n);
  const early = Math.min(250, Math.floor(iters / 4));
  for (let it = 0; it < iters; it++) {
    const ex = it < early ? 12 : 1, mom = it < early ? 0.5 : 0.8;
    let sumQ = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const dx = Y[2 * i] - Y[2 * j], dy = Y[2 * i + 1] - Y[2 * j + 1], q = 1 / (1 + dx * dx + dy * dy);
      Q[i * n + j] = q; Q[j * n + i] = q; sumQ += 2 * q;
    }
    for (let i = 0; i < n; i++) {
      let gx = 0, gy = 0;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const q = Q[i * n + j], mm = (ex * Ps[i * n + j] - q / sumQ) * q;
        gx += mm * (Y[2 * i] - Y[2 * j]); gy += mm * (Y[2 * i + 1] - Y[2 * j + 1]);
      }
      grad[2 * i] = 4 * gx; grad[2 * i + 1] = 4 * gy;
    }
    let cx = 0, cy = 0;
    for (let d = 0; d < n * 2; d++) {
      const g = grad[d];
      gains[d] = Math.sign(g) !== Math.sign(upd[d]) ? gains[d] + 0.2 : gains[d] * 0.8;
      if (gains[d] < 0.01) gains[d] = 0.01;
      upd[d] = mom * upd[d] - lr * gains[d] * g;
      Y[d] += upd[d];
    }
    for (let i = 0; i < n; i++) { cx += Y[2 * i]; cy += Y[2 * i + 1]; }
    cx /= n; cy /= n;
    for (let i = 0; i < n; i++) { Y[2 * i] -= cx; Y[2 * i + 1] -= cy; }
    if (it % 10 === 0 || it === iters - 1) post(Y, (it + 1) / iters);
  }
  return Y;
}

// UMAP via the vendored umap-js (same-origin, so the 'self' CSP allows it).
function loadUmap() {
  if (umapLoaded) return true;
  try { importScripts(UMAP_URL); umapLoaded = !!(self.UMAP && self.UMAP.UMAP); } catch (e) { umapLoaded = false; }
  return umapLoaded;
}
function flat(emb) {
  const out = new Float64Array(emb.length * 2);
  emb.forEach((p, i) => { out[2 * i] = p[0]; out[2 * i + 1] = p[1]; });
  return out;
}
function umap(k, post) {
  const X = [];
  for (let i = 0; i < N; i++) X.push(Array.from(U.subarray(i * D, (i + 1) * D)));
  const u = new self.UMAP.UMAP({ nComponents: 2, nNeighbors: k, minDist: 0.1, random: mulberry(42) });
  const nE = u.initializeFit(X);
  for (let e = 0; e < nE; e++) { u.step(); if (e % 20 === 0) post(flat(u.getEmbedding()), e / nE); }
  return flat(u.getEmbedding());
}

// Spherical k-means with k-means++ seeding; labels renumbered by first appearance.
function kmeans(k, seed) {
  const n = N;
  k = Math.max(1, Math.min(k, n));
  const labels = new Int32Array(n).fill(-1);
  if (!n) return labels;
  const rand = mulberry(seed), chosen = [Math.floor(rand() * n)], dist = new Float64Array(n).fill(Infinity);
  for (let c = 1; c < k; c++) {
    const last = chosen[c - 1];
    let tot = 0;
    for (let i = 0; i < n; i++) { const d = Math.max(0, 1 - SIM[i * n + last]); if (d < dist[i]) dist[i] = d; tot += dist[i] * dist[i]; }
    let pick = Math.floor(rand() * n);
    if (tot > 0) { let r = rand() * tot; for (let i = 0; i < n; i++) { r -= dist[i] * dist[i]; if (r <= 0) { pick = i; break; } } }
    chosen.push(pick);
  }
  const C = new Float64Array(k * D);
  chosen.forEach((p, c) => { for (let d = 0; d < D; d++) C[c * D + d] = U[p * D + d]; });
  const counts = new Int32Array(k);
  for (let iter = 0; iter < 60; iter++) {
    let changed = false;
    for (let i = 0; i < n; i++) {
      let best = 0, bs = -Infinity;
      for (let c = 0; c < k; c++) { let s = 0; for (let d = 0; d < D; d++) s += U[i * D + d] * C[c * D + d]; if (s > bs) { bs = s; best = c; } }
      if (labels[i] !== best) { labels[i] = best; changed = true; }
    }
    if (!changed) break;
    C.fill(0); counts.fill(0);
    for (let i = 0; i < n; i++) { const c = labels[i]; counts[c]++; for (let d = 0; d < D; d++) C[c * D + d] += U[i * D + d]; }
    for (let c = 0; c < k; c++) {
      if (!counts[c]) { const p = Math.floor(rand() * n); for (let d = 0; d < D; d++) C[c * D + d] = U[p * D + d]; continue; }
      let nn = 0; for (let d = 0; d < D; d++) nn += C[c * D + d] * C[c * D + d]; nn = Math.sqrt(nn) || 1;
      for (let d = 0; d < D; d++) C[c * D + d] /= nn;
    }
  }
  const remap = new Map(), out = new Int32Array(n);
  for (let i = 0; i < n; i++) { if (!remap.has(labels[i])) remap.set(labels[i], remap.size); out[i] = remap.get(labels[i]); }
  return out;
}

self.onmessage = (e) => {
  const m = e.data;
  try {
    if (m.type === "layout") {
      U = m.unit; R = m.raw; N = m.n; D = m.d;
      computeSim();
      self.postMessage({ type: "sim", jobId: m.jobId, sim: SIM.slice() });
      self.postMessage({ type: "metric", jobId: m.jobId, metric: m.metric, m: metricMatrix(m.metric) });
      self.postMessage({ type: "clusters", jobId: m.jobId, labels: kmeans(m.clusterK, 42) });
      const post = (Y, frac) => self.postMessage({ type: "progress", jobId: m.jobId, coords: Float32Array.from(Y), frac });
      const info = { method: m.method };
      let coords = null;
      if (N < 4 && m.method !== "pca") info.small = true;
      else if (m.method === "tsne") coords = tsne(m.perplexity, m.iters, post);
      else if (m.method === "umap") { if (loadUmap()) coords = umap(m.nNeighbors, post); else info.fallback = true; }
      if (!coords) { const r = pca(2); coords = r.coords; info.explained = r.explained; }
      self.postMessage({ type: "done", jobId: m.jobId, coords: Float32Array.from(coords), info });
    } else if (m.type === "metric" && SIM) {
      self.postMessage({ type: "metric", jobId: m.jobId, metric: m.metric, m: metricMatrix(m.metric) });
    } else if (m.type === "cluster" && SIM) {
      self.postMessage({ type: "clusters", jobId: m.jobId, labels: kmeans(m.k, 42) });
    }
  } catch (err) {
    self.postMessage({ type: "error", jobId: m.jobId, message: String((err && err.message) || err) });
  }
};
