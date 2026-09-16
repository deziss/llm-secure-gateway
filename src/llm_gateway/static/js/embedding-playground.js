// embedding-playground.js — Embedding Playground page logic

let embeddings = []; // { text, vector, color, id }
let projected = []; // { x, y }
let method = "pca";

// Canvas pan+zoom state
let scale = 1,
  panX = 0,
  panY = 0,
  dragging = false,
  dragStart = null;

// Color palette
const PALETTE = [
  "#10b981",
  "#6366f1",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#ec4899",
  "#8b5cf6",
  "#84cc16",
  "#f97316",
  "#14b8a6",
];

const canvas = document.getElementById("embedCanvas");
const ctx = canvas.getContext("2d");
const overlay = document.getElementById("canvas-overlay");
const tooltip = document.getElementById("tooltip");

function resizeCanvas() {
  const container = document.getElementById("canvas-container");
  canvas.width = container.clientWidth;
  canvas.height = container.clientHeight;
  draw();
}
window.addEventListener("resize", resizeCanvas);

function setStatus(text, color = "green") {
  const colors = {
    green: "bg-green-500",
    yellow: "bg-yellow-500",
    red: "bg-red-500",
  };
  document.getElementById("statusDisplay").innerHTML =
    `<span class="w-2 h-2 rounded-full ${colors[color]} inline-block"></span> ${text}`;
}

function setMethod(m) {
  method = m;
  document.getElementById("methodPCA").className =
    "flex-1 text-xs py-1.5 rounded-lg transition font-semibold " +
    (m === "pca"
      ? "bg-emerald-600 text-white"
      : "bg-slate-700 text-slate-300");
  document.getElementById("methodTSNE").className =
    "flex-1 text-xs py-1.5 rounded-lg transition font-semibold " +
    (m === "tsne"
      ? "bg-emerald-600 text-white"
      : "bg-slate-700 text-slate-300");
  if (embeddings.length >= 2) reproject();
}

// Fetch backends
document.getElementById("fetchBtn").addEventListener("click", async () => {
  const provider = document.getElementById("providerSelect").value;
  const res = await fetch(BASE_URL + "/admin/backends", {
    credentials: "include",
  });
  const backends = await res.json();
  const models = [
    ...new Set(
      backends
        .filter((b) => b.backend_type === provider)
        .flatMap((b) => b.models || []),
    ),
  ];
  const sel = document.getElementById("modelSelect");
  sel.innerHTML =
    models.map((m) => `<option value="${m}">${m}</option>`).join("") ||
    "<option>No models</option>";
});

async function addTexts() {
  const raw = document.getElementById("embed-textarea").value.trim();
  if (!raw) return;
  const texts = raw
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);

  const apiKey = document.getElementById("apiKey").value;
  const provider = document.getElementById("providerSelect").value;
  const model =
    document.getElementById("modelInput").value.trim() ||
    document.getElementById("modelSelect").value;

  if (!model) {
    showToast("Error", "Select a model first.", "error");
    return;
  }

  const btn = document.getElementById("add-btn");
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Embedding...';
  setStatus("Embedding...", "yellow");

  const isOllama = provider === "ollama";
  const embedUrl = isOllama
    ? `${BASE_URL}/provider/${provider}/api/embeddings`
    : `${BASE_URL}/provider/${provider}/v1/embeddings`;

  const buildBody = (text) =>
    isOllama ? { model, prompt: text } : { model, input: text };

  const extractVector = (json) =>
    isOllama ? json.embedding : json.data[0].embedding;

  try {
    for (const text of texts) {
      const opts = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody(text)),
      };
      if (apiKey) opts.headers["Authorization"] = "Bearer " + apiKey;

      const res = await fetchWithCsrf(embedUrl, opts);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const vector = extractVector(await res.json());
      addPoint(text, vector);
    }
    document.getElementById("pointsCount").textContent = embeddings.length;
    document.getElementById("embed-textarea").value = "";
    if (embeddings.length >= 2) reproject();
    else if (embeddings.length === 1) {
      overlay.style.display = "none";
      draw();
    }
    setStatus("Ready");
  } catch (e) {
    showToast("Embed Error", e.message, "error");
    setStatus("Error", "red");
  }
  btn.disabled = false;
  btn.innerHTML = '<i class="fas fa-plus mr-1"></i>Embed';
}

function addPoint(text, vector) {
  const id = Date.now() + Math.random();
  const color = PALETTE[embeddings.length % PALETTE.length];
  embeddings.push({ id, text, vector, color });
  document.getElementById("embeddingDim").textContent =
    `Dims: ${vector.length}`;

  // Add chip
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.id = "chip-" + id;
  chip.title = text;
  chip.style.background = color + "22";
  chip.style.color = color;
  chip.style.border = `1px solid ${color}44`;
  chip.innerHTML = `<span class="chip-dot" style="background:${color}"></span>${text.slice(0, 28)}${text.length > 28 ? "\u2026" : ""}
    <span onclick="removePoint(${id})" style="margin-left:2px;opacity:0.5;hover:opacity:1">\u00d7</span>`;
  document.getElementById("chips").prepend(chip);
}

function removePoint(id) {
  const idx = embeddings.findIndex((e) => e.id === id);
  if (idx !== -1) embeddings.splice(idx, 1);
  const chip = document.getElementById("chip-" + id);
  if (chip) chip.remove();
  document.getElementById("pointsCount").textContent = embeddings.length;
  if (embeddings.length < 2) {
    projected = [];
    draw();
    return;
  }
  reproject();
}

function clearAll() {
  embeddings = [];
  projected = [];
  document.getElementById("chips").innerHTML = "";
  document.getElementById("pointsCount").textContent = "0";
  overlay.style.display = "flex";
  draw();
}

// === PCA ===
function pcaProject(vectors) {
  const n = vectors.length;
  const dim = vectors[0].length;
  // Mean center
  const mean = new Array(dim).fill(0);
  vectors.forEach((v) => v.forEach((val, i) => (mean[i] += val / n)));
  const centered = vectors.map((v) => v.map((val, i) => val - mean[i]));

  // Power iteration for 2 principal components
  function powerIterate(mat, iters = 150) {
    let v = mat[0].map(() => Math.random() - 0.5);
    for (let i = 0; i < iters; i++) {
      let nv = new Array(dim).fill(0);
      mat.forEach((row) => {
        const dot = row.reduce((s, x, j) => s + x * v[j], 0);
        row.forEach((x, j) => (nv[j] += dot * x));
      });
      const norm = Math.sqrt(nv.reduce((s, x) => s + x * x, 0));
      v = nv.map((x) => x / norm);
    }
    return v;
  }

  const pc1 = powerIterate(centered);
  // Deflate
  const deflated = centered.map((row) => {
    const proj = row.reduce((s, x, i) => s + x * pc1[i], 0);
    return row.map((x, i) => x - proj * pc1[i]);
  });
  const pc2 = powerIterate(deflated);

  return centered.map((row) => ({
    x: row.reduce((s, x, i) => s + x * pc1[i], 0),
    y: row.reduce((s, x, i) => s + x * pc2[i], 0),
  }));
}

// Simple t-SNE via random projection (approximation for demo)
function simpleProject(vectors) {
  const n = vectors.length;
  const proj = vectors.map(() => ({
    x: (Math.random() - 0.5) * 400,
    y: (Math.random() - 0.5) * 400,
  }));
  const lr = 20;
  for (let iter = 0; iter < 500; iter++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const sim = cosineSim(vectors[i], vectors[j]);
        const dx = proj[i].x - proj[j].x;
        const dy = proj[i].y - proj[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy) + 1e-8;
        const target = (1 - sim) * 200;
        const force = ((dist - target) / dist) * lr * 0.05;
        proj[i].x -= dx * force;
        proj[i].y -= dy * force;
        proj[j].x += dx * force;
        proj[j].y += dy * force;
      }
    }
  }
  return proj;
}

function cosineSim(a, b) {
  const dot = a.reduce((s, x, i) => s + x * b[i], 0);
  const na = Math.sqrt(a.reduce((s, x) => s + x * x, 0));
  const nb = Math.sqrt(b.reduce((s, x) => s + x * x, 0));
  return dot / (na * nb + 1e-8);
}

function reproject() {
  overlay.style.display = "none";
  const vectors = embeddings.map((e) => e.vector);
  if (method === "pca") {
    projected = pcaProject(vectors);
  } else {
    projected = simpleProject(vectors);
  }
  // Normalize to canvas space
  normalize();
  draw();
}

function normalize() {
  if (projected.length < 2) return;
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
  projected.forEach((p) => {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  });
  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;
  projected = projected.map((p) => ({
    x: ((p.x - minX) / rangeX) * 0.8 + 0.1,
    y: ((p.y - minY) / rangeY) * 0.8 + 0.1,
  }));
  panX = 0;
  panY = 0;
  scale = 1;
}

function draw() {
  const W = canvas.width,
    H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Background grid
  ctx.save();
  ctx.strokeStyle = "rgba(16,185,129,0.04)";
  ctx.lineWidth = 1;
  const gridStep = 60;
  for (let x = 0; x < W; x += gridStep) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, H);
    ctx.stroke();
  }
  for (let y = 0; y < H; y += gridStep) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(W, y);
    ctx.stroke();
  }
  ctx.restore();

  if (projected.length < 1) return;

  const ps = parseInt(document.getElementById("pointSize").value);

  // Draw lines between close points
  if (projected.length > 1) {
    for (let i = 0; i < projected.length; i++) {
      for (let j = i + 1; j < projected.length; j++) {
        const sim = cosineSim(embeddings[i].vector, embeddings[j].vector);
        if (sim > 0.5) {
          const px1 = projected[i].x * W * scale + panX;
          const py1 = projected[i].y * H * scale + panY;
          const px2 = projected[j].x * W * scale + panX;
          const py2 = projected[j].y * H * scale + panY;
          const alpha = (sim - 0.5) * 0.6;
          ctx.beginPath();
          ctx.moveTo(px1, py1);
          ctx.lineTo(px2, py2);
          ctx.strokeStyle = `rgba(16,185,129,${alpha})`;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
    }
  }

  // Draw points
  embeddings.forEach((e, i) => {
    if (!projected[i]) return;
    const px = projected[i].x * W * scale + panX;
    const py = projected[i].y * H * scale + panY;

    // Glow
    ctx.shadowColor = e.color;
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.arc(px, py, ps, 0, Math.PI * 2);
    ctx.fillStyle = e.color;
    ctx.fill();
    ctx.shadowBlur = 0;

    // Short label
    ctx.fillStyle = e.color;
    ctx.font = `bold ${Math.max(10, Math.min(13, ps + 3))}px system-ui`;
    const label = e.text.length > 18 ? e.text.slice(0, 18) + "\u2026" : e.text;
    ctx.fillText(label, px + ps + 4, py + 4);
  });
}

// Point hover
canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const W = canvas.width,
    H = canvas.height;
  const ps = parseInt(document.getElementById("pointSize").value);

  let found = false;
  for (let i = 0; i < projected.length; i++) {
    const px = projected[i].x * W * scale + panX;
    const py = projected[i].y * H * scale + panY;
    if (Math.abs(mx - px) < ps + 6 && Math.abs(my - py) < ps + 6) {
      tooltip.style.display = "block";
      tooltip.style.left = e.clientX + 12 + "px";
      tooltip.style.top = e.clientY - 30 + "px";
      tooltip.textContent = embeddings[i].text;
      canvas.style.cursor = "pointer";
      found = true;
      break;
    }
  }
  if (!found) {
    tooltip.style.display = "none";
    canvas.style.cursor = "crosshair";
  }

  // Pan
  if (dragging && dragStart) {
    panX += mx - dragStart.x;
    panY += my - dragStart.y;
    dragStart = { x: mx, y: my };
    draw();
  }
});

canvas.addEventListener("mousedown", (e) => {
  dragging = true;
  const r = canvas.getBoundingClientRect();
  dragStart = { x: e.clientX - r.left, y: e.clientY - r.top };
});
canvas.addEventListener("mouseup", () => {
  dragging = false;
  dragStart = null;
});

// Scroll to zoom
canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    scale *= factor;
    draw();
  },
  { passive: false },
);

document.getElementById("pointSize").addEventListener("input", draw);

// Init
window.addEventListener("load", () => {
  resizeCanvas();
});
