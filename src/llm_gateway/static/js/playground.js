// playground.js — API Playground page logic

// State
let routingMode = "standard";
let backends = [];

// Endpoint map per backend type
const ENDPOINTS_BY_TYPE = {
  ollama: [
    { value: "api/chat", label: "api/chat (Chat)" },
    { value: "api/generate", label: "api/generate (Generate)" },
    { value: "api/tags", label: "api/tags (List Models)" },
  ],
  vllm: [
    { value: "v1/chat/completions", label: "v1/chat/completions (Chat)" },
    { value: "v1/completions", label: "v1/completions (Generate)" },
    { value: "v1/models", label: "v1/models (List Models)" },
  ],
  openai: [
    { value: "v1/chat/completions", label: "v1/chat/completions (Chat)" },
    { value: "v1/completions", label: "v1/completions (Completions)" },
    { value: "v1/models", label: "v1/models (List Models)" },
    { value: "v1/embeddings", label: "v1/embeddings (Embeddings)" },
  ],
  anthropic: [
    { value: "v1/messages", label: "v1/messages (Chat)" },
  ],
  google: [
    { value: "v1/chat/completions", label: "v1/chat/completions (Chat)" },
    { value: "v1/models", label: "v1/models (List Models)" },
  ],
  groq: [
    { value: "v1/chat/completions", label: "v1/chat/completions (Chat)" },
    { value: "v1/models", label: "v1/models (List Models)" },
  ],
  custom: [
    { value: "v1/chat/completions", label: "v1/chat/completions (Chat)" },
    { value: "api/chat", label: "api/chat (Ollama-style)" },
    { value: "v1/models", label: "v1/models (List Models)" },
    { value: "api/tags", label: "api/tags (List Models)" },
  ],
};

// Sanitize header values (strip non-ISO-8859-1 characters from copy-paste)
function sanitizeHeader(val) {
  return val.replace(/[^\x00-\xFF]/g, "").trim();
}

// UI Elements
const modeStandardBtn = document.getElementById("modeStandard");
const modeDirectBtn = document.getElementById("modeDirect");
const providerContainer = document.getElementById("providerContainer");
const backendContainer = document.getElementById("backendContainer");
const backendSelect = document.getElementById("backendSelect");

// Init
document.addEventListener("DOMContentLoaded", async () => {
  // Fetch backends
  try {
    const res = await fetch(BASE_URL + "/admin/backends", { credentials: "include" });
    if (res.ok) {
      backends = await res.json();
      populateBackends();
    }
  } catch (e) {
    console.error("Failed to fetch backends:", e);
  }
  updateEndpointDropdown();
  updateModelDropdown();
  updateRequestPreview();
});

function populateBackends() {
  backendSelect.innerHTML = "";
  if (backends.length === 0) {
    const opt = document.createElement("option");
    opt.text = "No backends found";
    backendSelect.add(opt);
    return;
  }
  backends.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b.name;
    opt.text = `${b.name} (${b.backend_type})`;
    opt.dataset.type = b.backend_type;
    backendSelect.add(opt);
  });
  updateEndpointDropdown();
  updateModelDropdown();
}

// Get the current backend type based on routing mode
function getCurrentBackendType() {
  if (routingMode === "direct") {
    const selected = backendSelect.selectedOptions[0];
    return selected ? (selected.dataset.type || "").toLowerCase() : "ollama";
  }
  return document.getElementById("provider").value.toLowerCase();
}

// Update endpoint dropdown based on backend type
function updateEndpointDropdown() {
  const endpointSelect = document.getElementById("endpoint");
  const previousValue = endpointSelect.value;
  const backendType = getCurrentBackendType();
  const endpoints = ENDPOINTS_BY_TYPE[backendType] || ENDPOINTS_BY_TYPE.custom;

  endpointSelect.innerHTML = "";
  endpoints.forEach((ep) => {
    const opt = document.createElement("option");
    opt.value = ep.value;
    opt.textContent = ep.label;
    endpointSelect.appendChild(opt);
  });

  // Try to preserve previous selection if it exists in new list
  const match = endpoints.find((ep) => ep.value === previousValue);
  if (match) {
    endpointSelect.value = previousValue;
  }

  updateFieldVisibility();
  updateRequestPreview();
}

// Update model dropdown based on selected provider/backend
function updateModelDropdown() {
  const modelSelect = document.getElementById("model");
  const previousValue = modelSelect.value;
  modelSelect.innerHTML = "";

  let backendModels = [];

  if (routingMode === "direct") {
    const selectedBackendName = backendSelect.value;
    const backend = backends.find((b) => b.name === selectedBackendName);
    if (backend && backend.models) {
      backendModels = backend.models;
    }
  } else {
    const providerType = document.getElementById("provider").value;
    backends.forEach((b) => {
      if (
        b.backend_type.toLowerCase() === providerType.toLowerCase() &&
        b.models
      ) {
        backendModels = backendModels.concat(b.models);
      }
    });
    backendModels = [...new Set(backendModels)];
  }

  if (backendModels.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No models available — click Fetch Models";
    modelSelect.appendChild(opt);
  } else {
    backendModels.forEach((modelName) => {
      const opt = document.createElement("option");
      opt.value = modelName;
      opt.textContent = modelName;
      modelSelect.appendChild(opt);
    });
    // Preserve previous selection if possible
    if (backendModels.includes(previousValue)) {
      modelSelect.value = previousValue;
    }
  }

  updateRequestPreview();
}

// =============================================
// Fetch Models via /admin/models (unified endpoint)
// =============================================
document
  .getElementById("fetchModelsBtn")
  .addEventListener("click", async () => {
    const statusEl = document.getElementById("modelFetchStatus");
    statusEl.textContent = "Fetching models from all backends...";
    statusEl.className = "text-caption text-accent mt-1";

    try {
      const response = await fetch(BASE_URL + "/admin/models", { credentials: "include" });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errText}`);
      }

      const data = await response.json();
      const modelSelect = document.getElementById("model");
      modelSelect.innerHTML = "";

      let allModels = data.models || [];

      // Filter by routing mode
      if (routingMode === "standard") {
        const providerType = document.getElementById("provider").value;
        allModels = allModels.filter((m) => m.backend_type === providerType);
      } else if (routingMode === "direct") {
        const selectedBackend = backendSelect.value;
        allModels = allModels.filter((m) => m.backend === selectedBackend);
      }

      if (allModels.length > 0) {
        allModels.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.model;
          opt.textContent = `${m.model} (${m.backend})`;
          modelSelect.appendChild(opt);
        });
        statusEl.textContent = `Found ${allModels.length} model(s)`;
        statusEl.className = "text-caption text-ok mt-1";
      } else {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No models found";
        modelSelect.appendChild(opt);
        statusEl.textContent =
          "No models found. Try syncing backends first.";
        statusEl.className = "text-caption text-warn mt-1";
      }
    } catch (error) {
      statusEl.textContent = error.message;
      statusEl.className = "text-caption text-down mt-1";
    }
    updateRequestPreview();
  });

// =============================================
// Request Editor (Curl + JSON body preview)
// =============================================
const toggleBtn = document.getElementById("toggleRequestEditor");
const editorContainer = document.getElementById("requestEditorContainer");
let editorVisible = false;

toggleBtn.addEventListener("click", () => {
  editorVisible = !editorVisible;
  editorContainer.classList.toggle("hidden", !editorVisible);
  toggleBtn.innerHTML = editorVisible
    ? '<i class="fas fa-code mr-1"></i>Hide Request Editor'
    : '<i class="fas fa-code mr-1"></i>Show Request Editor';
  if (editorVisible) updateRequestPreview();
});

function buildUrl() {
  const endpoint = document.getElementById("endpoint").value;
  if (routingMode === "standard") {
    const provider = document.getElementById("provider").value;
    return BASE_URL + "/" + provider + "/" + endpoint;
  } else {
    const backendName = backendSelect.value;
    return BASE_URL + "/direct/" + backendName + "/" + endpoint;
  }
}

function buildBody() {
  const model = document.getElementById("model").value;
  const message = document.getElementById("message").value;
  const streaming = document.getElementById("streaming").checked;
  const endpoint = document.getElementById("endpoint").value;

  // For tag/model listing endpoints, no body needed
  if (isBodylessEndpoint(endpoint)) {
    return null;
  }

  const body = {
    model: model,
    messages: [{ role: "user", content: message }],
    stream: streaming,
  };
  // Ask OpenAI-compatible servers to append token usage to the stream, so
  // the timing bar can show tokens and speed for streamed replies too.
  if (streaming && endpoint === "v1/chat/completions") body.stream_options = { include_usage: true };
  return body;
}

function updateRequestPreview() {
  if (typeof refreshCode === "function") refreshCode();
  if (!editorVisible) return;

  const method = document.getElementById("httpMethod").value;
  const url = buildUrl();
  const apiKey = document.getElementById("apiKey").value || "YOUR_API_KEY";
  const body = buildBody();

  // Curl preview
  let curl = `curl -X ${method} '${url}'`;
  curl += ` \\\n  -H 'Content-Type: application/json'`;
  curl += ` \\\n  -H 'Authorization: Bearer ${apiKey}'`;
  if (body) {
    curl += ` \\\n  -d '${JSON.stringify(body, null, 2)}'`;
  }
  document.getElementById("curlPreview").value = curl;

  // JSON body preview
  const bodyEditor = document.getElementById("requestBodyEditor");
  if (!document.getElementById("useCustomBody").checked) {
    bodyEditor.value = body
      ? JSON.stringify(body, null, 2)
      : "(no body for this endpoint)";
  }
}

// Copy curl to clipboard
document.getElementById("copyCurlBtn").addEventListener("click", () => {
  const curlEl = document.getElementById("curlPreview");
  navigator.clipboard.writeText(curlEl.value).then(() => {
    const btn = document.getElementById("copyCurlBtn");
    btn.innerHTML = '<i class="fas fa-check"></i>';
    setTimeout(() => {
      btn.innerHTML = '<i class="fas fa-copy"></i>';
    }, 1500);
  });
});

// Live preview updates
["model", "message", "apiKey"].forEach((id) => {
  document.getElementById(id).addEventListener("input", updateRequestPreview);
});
["httpMethod", "streaming"].forEach((id) => {
  document
    .getElementById(id)
    .addEventListener("change", updateRequestPreview);
});

// Endpoint change updates visibility + preview
document.getElementById("endpoint").addEventListener("change", () => {
  updateFieldVisibility();
  updateRequestPreview();
});

// Provider change updates endpoints + models
document.getElementById("provider").addEventListener("change", () => {
  updateEndpointDropdown();
  updateModelDropdown();
});

// Backend (direct mode) change updates endpoints + models
backendSelect.addEventListener("change", () => {
  updateEndpointDropdown();
  updateModelDropdown();
});

// Check if endpoint is a body-less GET endpoint
function isBodylessEndpoint(endpoint) {
  const bodyless = ["api/tags", "v1/models", "api/version"];
  return bodyless.includes(endpoint);
}

// Toggle UI fields based on endpoint type
function updateFieldVisibility() {
  const endpoint = document.getElementById("endpoint").value;
  const bodyless = isBodylessEndpoint(endpoint);

  const modelContainer = document.getElementById("modelContainer");
  const messageContainer = document.getElementById("messageContainer");
  const streamingContainer = document.getElementById("streamingContainer");
  const noBodyBanner = document.getElementById("noBodyBanner");

  if (bodyless) {
    modelContainer.classList.add("hidden");
    messageContainer.classList.add("hidden");
    streamingContainer.classList.add("hidden");
    noBodyBanner.classList.remove("hidden");
    document.getElementById("httpMethod").value = "GET";
  } else {
    modelContainer.classList.remove("hidden");
    messageContainer.classList.remove("hidden");
    streamingContainer.classList.remove("hidden");
    noBodyBanner.classList.add("hidden");
    document.getElementById("httpMethod").value = "POST";
  }
}

// Toggle Logic
function setMode(mode) {
  routingMode = mode;
  const activeClasses = ["bg-accent", "text-white"];
  const inactiveClasses = ["bg-transparent", "text-ink-muted"];

  if (mode === "standard") {
    modeStandardBtn.classList.add(...activeClasses);
    modeStandardBtn.classList.remove(...inactiveClasses);
    modeDirectBtn.classList.remove(...activeClasses);
    modeDirectBtn.classList.add(...inactiveClasses);

    providerContainer.classList.remove("hidden");
    backendContainer.classList.add("hidden");
  } else {
    modeDirectBtn.classList.add(...activeClasses);
    modeDirectBtn.classList.remove(...inactiveClasses);
    modeStandardBtn.classList.remove(...activeClasses);
    modeStandardBtn.classList.add(...inactiveClasses);

    providerContainer.classList.add("hidden");
    backendContainer.classList.remove("hidden");
  }
  updateEndpointDropdown();
  updateModelDropdown();
}

modeStandardBtn.addEventListener("click", () => setMode("standard"));
modeDirectBtn.addEventListener("click", () => setMode("direct"));

// =============================================
// Request / response state
// =============================================
const $id = (id) => document.getElementById(id);
const HISTORY_KEY = "apiTester.history.v1";
const HISTORY_MAX = 20;
const STREAM_MAX_ROWS = 2000;
const GATEWAY_HEADERS = {
  "x-cache": "Gateway response cache: HIT, SEMANTIC-HIT or MISS",
  "x-cache-similarity": "Similarity of the semantic cache match",
  "x-budget-spent": "Spend so far this month for this key/owner (USD)",
  "x-budget-limit": "Monthly budget for this key/owner (USD)",
};

let last = null; // { text, json, headers, events, status }

function readForm() {
  return {
    mode: routingMode,
    provider: $id("provider").value,
    backend: backendSelect.value,
    endpoint: $id("endpoint").value,
    method: $id("httpMethod").value,
    model: $id("model").value,
    message: $id("message").value,
    stream: $id("streaming").checked,
    customBody: $id("useCustomBody").checked ? $id("requestBodyEditor").value : null,
  };
}

function currentBody() {
  if ($id("useCustomBody").checked) {
    const t = $id("requestBodyEditor").value;
    if (!t || t === "(no body for this endpoint)") return null;
    return JSON.parse(t);
  }
  return buildBody();
}

// Pull reply text and usage out of one parsed chunk (OpenAI, Ollama,
// Anthropic and plain-completion shapes).
function extractDelta(d) {
  if (!d || typeof d !== "object") return "";
  if (d.message && typeof d.message.content === "string") return d.message.content;
  const c = d.choices && d.choices[0];
  if (c) {
    if (c.delta && typeof c.delta.content === "string") return c.delta.content;
    if (c.message && typeof c.message.content === "string") return c.message.content;
    if (typeof c.text === "string") return c.text;
  }
  if (d.delta && typeof d.delta.text === "string") return d.delta.text; // Anthropic stream
  if (Array.isArray(d.content)) return d.content.map((b) => b.text || "").join(""); // Anthropic message
  if (typeof d.response === "string") return d.response;
  return "";
}
function extractUsage(d, usage) {
  if (!d || typeof d !== "object") return usage;
  const u = d.usage;
  if (u) {
    if (u.prompt_tokens != null) usage.in = u.prompt_tokens;
    if (u.completion_tokens != null) usage.out = u.completion_tokens;
    if (u.input_tokens != null) usage.in = u.input_tokens;
    if (u.output_tokens != null) usage.out = u.output_tokens;
  }
  if (d.message && d.message.usage) extractUsage(d.message, usage); // Anthropic message_start
  if (d.prompt_eval_count != null) usage.in = d.prompt_eval_count; // Ollama
  if (d.eval_count != null) usage.out = d.eval_count;
  return usage;
}

// =============================================
// Timing bar
// =============================================
const fmtMs = (ms) => (ms == null ? "–" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`);
function showTiming(t) {
  $id("timing").classList.remove("hidden");
  const st = $id("tStatus");
  st.textContent = t.status ?? "–";
  st.className = "text-sm font-bold tabular-nums " + (t.status >= 400 ? "text-down" : t.status ? "text-ok" : "text-ink");
  $id("tTtfb").textContent = fmtMs(t.ttfb);
  $id("tFirst").textContent = fmtMs(t.first);
  $id("tTotal").textContent = fmtMs(t.total);
  const u = t.usage || {};
  $id("tTokens").textContent = u.in != null || u.out != null ? `${u.in ?? "?"} / ${u.out ?? "?"}` : "–";
  const genMs = t.total != null && t.first != null ? t.total - t.first : null;
  $id("tSpeed").textContent = u.out && genMs > 0 ? `${(u.out / (genMs / 1000)).toFixed(1)} tok/s` : "–";
}

// =============================================
// Response tabs
// =============================================
let rtab = "body";
let bodyView = "text";
let codeLang = "curl";
const rtabs = [...document.querySelectorAll("[data-rtab]")];
function setRtab(name) {
  rtab = name;
  rtabs.forEach((b) => {
    const on = b.dataset.rtab === name;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    $id("rpanel-" + b.dataset.rtab).hidden = !on;
  });
  $id("bodyViewSeg").classList.toggle("hidden", name !== "body");
  $id("codeLangSeg").classList.toggle("hidden", name !== "code");
  if (name === "code") refreshCode();
}
rtabs.forEach((b) => b.addEventListener("click", () => setRtab(b.dataset.rtab)));
rtabs[0].parentElement.addEventListener("keydown", (e) => {
  const i = rtabs.indexOf(document.activeElement);
  if (i < 0 || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
  const n = rtabs[(i + (e.key === "ArrowRight" ? 1 : rtabs.length - 1)) % rtabs.length];
  n.focus(); setRtab(n.dataset.rtab);
});
function segSelect(container, attr, value) {
  container.querySelectorAll(`[data-${attr}]`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
}
$id("bodyViewSeg").addEventListener("click", (e) => {
  const b = e.target.closest("[data-bodyview]"); if (!b) return;
  bodyView = b.dataset.bodyview;
  segSelect($id("bodyViewSeg"), "bodyview", bodyView);
  $id("response").classList.toggle("hidden", bodyView !== "text");
  $id("responseJson").classList.toggle("hidden", bodyView !== "json");
});
$id("codeLangSeg").addEventListener("click", (e) => {
  const b = e.target.closest("[data-lang]"); if (!b) return;
  codeLang = b.dataset.lang;
  segSelect($id("codeLangSeg"), "lang", codeLang);
  refreshCode();
});

// Minimal JSON syntax colouring; input is escaped before any markup is added.
function highlightJson(obj) {
  const json = JSON.stringify(obj, null, 2);
  if (json === undefined) return "";
  const safe = json.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return safe.replace(/("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g, (m) => {
    let cls = "j-num";
    if (/^"/.test(m)) cls = /:$/.test(m) ? "j-key" : "j-str";
    else if (/true|false|null/.test(m)) cls = "j-lit";
    return `<span class="${cls}">${m}</span>`;
  });
}

function renderHeaders(headers) {
  const rows = [];
  headers.forEach((v, k) => rows.push([k, v]));
  rows.sort((a, b) => (GATEWAY_HEADERS[b[0]] ? 1 : 0) - (GATEWAY_HEADERS[a[0]] ? 1 : 0) || a[0].localeCompare(b[0]));
  const body = $id("headersBody");
  body.replaceChildren(...rows.map(([k, v]) => {
    const tr = document.createElement("tr");
    tr.className = "border-b border-hairline align-top" + (GATEWAY_HEADERS[k] ? " hist-hdr-gw" : "");
    const a = document.createElement("td"); a.className = "py-1 pr-3 whitespace-nowrap text-ink-muted"; a.textContent = k;
    if (GATEWAY_HEADERS[k]) a.title = GATEWAY_HEADERS[k];
    const b = document.createElement("td"); b.className = "py-1 break-all text-ink"; b.textContent = v;
    tr.append(a, b);
    return tr;
  }));
  $id("headersEmpty").classList.toggle("hidden", rows.length > 0);
  $id("headersTable").classList.toggle("hidden", rows.length === 0);
  $id("headersCount").textContent = rows.length ? String(rows.length) : "";
}

function addStreamRow(ms, line) {
  const list = $id("streamList");
  if (list.childElementCount >= STREAM_MAX_ROWS) return;
  const li = document.createElement("li");
  li.className = "flex gap-3";
  const t = document.createElement("span"); t.className = "shrink-0 w-16 text-right text-ink-muted tabular-nums"; t.textContent = `+${Math.round(ms)}ms`;
  const v = document.createElement("span"); v.className = "min-w-0 break-all text-ink"; v.textContent = line.length > 600 ? line.slice(0, 600) + "…" : line;
  li.append(t, v);
  list.appendChild(li);
}

function resetResponse() {
  last = { text: "", json: null, events: [], headers: null, status: null };
  $id("response").textContent = "";
  $id("response").className = "text-ink whitespace-pre-wrap break-words font-mono text-sm" + (bodyView === "text" ? "" : " hidden");
  $id("responseJson").innerHTML = "";
  $id("streamList").replaceChildren();
  $id("streamEmpty").classList.remove("hidden");
  $id("streamCount").textContent = "";
  renderHeaders(new Headers());
  $id("timing").classList.add("hidden");
}

$id("copyViewBtn").addEventListener("click", () => {
  let text = "";
  if (rtab === "body") text = bodyView === "json" ? $id("responseJson").textContent : $id("response").textContent;
  else if (rtab === "headers") { const h = []; $id("headersBody").querySelectorAll("tr").forEach((tr) => h.push(tr.children[0].textContent + ": " + tr.children[1].textContent)); text = h.join("\n"); }
  else if (rtab === "stream") text = (last && last.events.map((e) => e.line).join("\n")) || "";
  else text = $id("codeView").textContent;
  if (!text) return;
  const done = () => { const s = $id("copyViewBtn").querySelector("span"); s.textContent = "Copied"; setTimeout(() => { s.textContent = "Copy"; }, 1400); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => {});
  else { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ } ta.remove(); }
});

// =============================================
// Copy as code
// =============================================
function refreshCode() {
  const view = $id("codeView");
  if (!view) return;
  let body;
  try { body = currentBody(); } catch (e) { view.textContent = "The custom request body is not valid JSON: " + e.message; return; }
  const method = $id("httpMethod").value;
  const url = new URL(buildUrl(), window.location.origin).href;
  const endpoint = $id("endpoint").value;
  const json = body ? JSON.stringify(body, null, 2) : null;
  const stream = !!(body && body.stream);
  if (codeLang === "curl") {
    let c = `curl ${stream ? "-N " : ""}-X ${method} '${url}' \\\n  -H 'Content-Type: application/json' \\\n  -H "Authorization: Bearer $LLM_GATEWAY_KEY"`;
    if (json && method !== "GET") c += ` \\\n  -d '${json.replace(/'/g, "'\\''")}'`;
    view.textContent = c;
    return;
  }
  const sdkPath = /^v1\/(chat\/completions|completions|embeddings|models)$/.test(endpoint);
  if (codeLang === "python") {
    if (sdkPath && body !== undefined) {
      const baseUrl = url.slice(0, url.indexOf("/v1/") + 3);
      const call = endpoint === "v1/chat/completions" ? "client.chat.completions.create"
        : endpoint === "v1/completions" ? "client.completions.create"
        : endpoint === "v1/embeddings" ? "client.embeddings.create" : "client.models.list";
      const args = body ? Object.entries(body).map(([k, v]) => `    ${k}=${pyLiteral(v)},`).join("\n") : "";
      let c = `import os\nfrom openai import OpenAI\n\nclient = OpenAI(\n    base_url="${baseUrl}",\n    api_key=os.environ["LLM_GATEWAY_KEY"],\n)\n\n`;
      if (call === "client.models.list") c += `for model in client.models.list():\n    print(model.id)\n`;
      else if (stream) c += `stream = ${call}(\n${args}\n)\nfor chunk in stream:\n    if chunk.choices and chunk.choices[0].delta.content:\n        print(chunk.choices[0].delta.content, end="", flush=True)\n`;
      else c += `resp = ${call}(\n${args}\n)\nprint(resp)\n`;
      view.textContent = c;
      return;
    }
    let c = `import os\nimport requests\n\nresp = requests.request(\n    "${method}",\n    "${url}",\n    headers={"Authorization": f"Bearer {os.environ['LLM_GATEWAY_KEY']}"},\n`;
    if (json && method !== "GET") c += `    json=${pyLiteral(body)},\n`;
    if (stream) c += `    stream=True,\n)\nfor line in resp.iter_lines():\n    if line:\n        print(line.decode())\n`;
    else c += `)\nprint(resp.status_code, resp.json())\n`;
    view.textContent = c;
    return;
  }
  let c = `const res = await fetch("${url}", {\n  method: "${method}",\n  headers: {\n    "Content-Type": "application/json",\n    Authorization: \`Bearer \${process.env.LLM_GATEWAY_KEY}\`,\n  },\n`;
  if (json && method !== "GET") c += `  body: JSON.stringify(${json.replace(/\n/g, "\n  ")}),\n`;
  c += `});\n`;
  if (stream) c += `\nconst reader = res.body.getReader();\nconst decoder = new TextDecoder();\nfor (;;) {\n  const { done, value } = await reader.read();\n  if (done) break;\n  process.stdout.write(decoder.decode(value, { stream: true }));\n}\n`;
  else c += `console.log(res.status, await res.json());\n`;
  view.textContent = c;
}
function pyLiteral(v, ind = 4) {
  if (v === null) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return JSON.stringify(v);
  const pad = " ".repeat(ind + 4), end = " ".repeat(ind);
  if (Array.isArray(v)) return v.length ? `[\n${v.map((x) => pad + pyLiteral(x, ind + 4)).join(",\n")},\n${end}]` : "[]";
  const ents = Object.entries(v);
  return ents.length ? `{\n${ents.map(([k, x]) => `${pad}${JSON.stringify(k)}: ${pyLiteral(x, ind + 4)}`).join(",\n")},\n${end}}` : "{}";
}
$id("message").addEventListener("input", refreshCode);
$id("endpoint").addEventListener("change", refreshCode);
$id("requestBodyEditor").addEventListener("input", refreshCode);
$id("useCustomBody").addEventListener("change", refreshCode);
$id("model").addEventListener("change", refreshCode);

// =============================================
// History
// =============================================
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch (e) { return []; }
}
function saveHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX))); } catch (e) { /* storage full or blocked */ }
}
function pushHistory(entry) {
  saveHistory([{ id: Date.now() + Math.random(), at: Date.now(), ...entry }, ...loadHistory()]);
  renderHistory();
}
function timeAgo(ts) {
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
function renderHistory() {
  const list = $id("historyList");
  const items = loadHistory();
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "text-caption text-ink-muted px-1";
    li.textContent = "No requests yet.";
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...items.map((h) => {
    const li = document.createElement("li");
    li.className = "hist-row";
    const ok = typeof h.status === "number" && h.status < 400;
    const dot = document.createElement("span");
    dot.className = "h-2 w-2 shrink-0 rounded-full " + (ok ? "bg-ok" : "bg-down");
    dot.title = String(h.status);
    const main = document.createElement("button");
    main.type = "button"; main.className = "hist-main"; main.dataset.restore = h.id;
    main.title = "Load into the form";
    const l1 = document.createElement("div"); l1.className = "truncate text-ink font-medium";
    l1.textContent = `${h.method} ${h.endpoint}`;
    const l2 = document.createElement("div"); l2.className = "truncate text-ink-muted";
    l2.textContent = [h.mode === "direct" ? h.backend : h.provider, h.model, h.ms != null ? fmtMs(h.ms) : null, timeAgo(h.at)].filter(Boolean).join(" · ");
    main.append(l1, l2);
    const rerun = document.createElement("button");
    rerun.type = "button"; rerun.dataset.rerun = h.id;
    rerun.className = "shrink-0 rounded-control px-1.5 py-1 text-ink-muted hover:text-accent hover:bg-surface-raised";
    rerun.setAttribute("aria-label", `Run again: ${h.method} ${h.endpoint}`); rerun.title = "Run again";
    rerun.textContent = "↻";
    li.append(dot, main, rerun);
    return li;
  }));
}
function ensureOption(select, value) {
  if (!value) return;
  if (![...select.options].some((o) => o.value === value)) {
    const o = document.createElement("option"); o.value = value; o.textContent = value; select.appendChild(o);
  }
  select.value = value;
}
function restoreHistory(h) {
  setMode(h.mode === "direct" ? "direct" : "standard");
  if (h.mode === "direct") ensureOption(backendSelect, h.backend); else $id("provider").value = h.provider;
  updateEndpointDropdown();
  updateModelDropdown();
  ensureOption($id("endpoint"), h.endpoint);
  updateFieldVisibility();
  $id("httpMethod").value = h.method;
  ensureOption($id("model"), h.model);
  $id("message").value = h.message || "";
  $id("streaming").checked = !!h.stream;
  const custom = h.customBody != null;
  $id("useCustomBody").checked = custom;
  if (custom) {
    if (!editorVisible) toggleBtn.click();
    $id("requestBodyEditor").value = h.customBody;
  }
  updateRequestPreview();
}
$id("historyList").addEventListener("click", (e) => {
  const r = e.target.closest("[data-restore], [data-rerun]"); if (!r) return;
  const id = Number(r.dataset.restore || r.dataset.rerun);
  const h = loadHistory().find((x) => x.id === id); if (!h) return;
  restoreHistory(h);
  if (r.dataset.rerun) $id("apiForm").requestSubmit();
});
$id("historyClear").addEventListener("click", () => { saveHistory([]); renderHistory(); });
renderHistory();
setInterval(renderHistory, 60000);

// =============================================
// Submit Handler
// =============================================
$id("apiForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = readForm();
  const apiKey = $id("apiKey").value;
  const statusEl = $id("status");
  const responseEl = $id("response");
  const sendBtn = $id("sendBtn");
  const loadingIndicator = $id("loadingIndicator");

  let body;
  try {
    body = currentBody();
  } catch (parseErr) {
    showToast("Error", "Invalid JSON in request body editor: " + parseErr.message, "error");
    return;
  }

  resetResponse();
  statusEl.textContent = "Connecting...";
  statusEl.className = "text-caption font-bold px-2 py-1 rounded-control border border-hairline bg-surface-inset text-warn";
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1.5"></i>Sending...';
  loadingIndicator.classList.remove("hidden");
  $id("loadingText").textContent = "Sending request...";

  const fetchOptions = { method: form.method, headers: { "Content-Type": "application/json" } };
  if (apiKey) fetchOptions.headers.Authorization = "Bearer " + sanitizeHeader(apiKey);
  if (body && form.method !== "GET") fetchOptions.body = JSON.stringify(body);

  const t0 = performance.now();
  const timing = { status: null, ttfb: null, first: null, total: null, usage: {} };
  const onText = (piece) => {
    if (!piece) return;
    if (timing.first == null) timing.first = performance.now() - t0;
    last.text += piece;
    responseEl.textContent = last.text;
    const box = $id("responseContainer");
    if (rtab === "body") box.scrollTop = box.scrollHeight;
  };

  try {
    const response = await fetchWithCsrf(buildUrl(), fetchOptions);
    timing.ttfb = performance.now() - t0;
    timing.status = response.status;
    last.status = response.status;
    renderHeaders(response.headers);
    loadingIndicator.classList.add("hidden");

    const ctype = (response.headers.get("content-type") || "").toLowerCase();
    const isStream = !!(body && body.stream) && response.ok && !ctype.includes("application/json");

    if (!response.ok) {
      const text = await response.text();
      timing.total = performance.now() - t0;
      let parsed = null;
      try { parsed = JSON.parse(text); } catch (err) { /* not JSON */ }
      last.json = parsed;
      responseEl.textContent = `HTTP ${response.status}: ${parsed ? (parsed.detail ? (typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail)) : JSON.stringify(parsed)) : text}`;
      responseEl.className = "text-down whitespace-pre-wrap break-words font-mono text-sm" + (bodyView === "text" ? "" : " hidden");
      $id("responseJson").innerHTML = parsed ? highlightJson(parsed) : "";
      throw Object.assign(new Error(`HTTP ${response.status}`), { handled: true });
    }

    statusEl.textContent = "Receiving...";
    statusEl.className = "text-caption font-bold px-2 py-1 rounded-control border border-hairline bg-surface-inset text-accent";

    if (isStream) {
      $id("streamEmpty").classList.add("hidden");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", lastJson = null;
      const handleLine = (line) => {
        const trimmed = line.trim();
        if (!trimmed) return;
        const ms = performance.now() - t0;
        last.events.push({ ms, line: trimmed });
        addStreamRow(ms, trimmed);
        if (trimmed === "data: [DONE]" || trimmed.startsWith("event:")) return;
        const jsonStr = trimmed.startsWith("data:") ? trimmed.slice(5).trim() : trimmed;
        try {
          const d = JSON.parse(jsonStr);
          lastJson = d;
          extractUsage(d, timing.usage);
          onText(extractDelta(d));
        } catch (err) { /* not JSON */ }
      };
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        lines.forEach(handleLine);
        $id("streamCount").textContent = String(last.events.length);
      }
      buffer += decoder.decode();
      if (buffer.trim()) handleLine(buffer);
      $id("streamCount").textContent = String(last.events.length);
      last.json = { note: "Streamed reply. Every event is on the Stream tab; this is the last one.", last_event: lastJson };
    } else {
      const text = await response.text();
      let d = null;
      try { d = JSON.parse(text); } catch (err) { /* not JSON */ }
      if (d) {
        last.json = d;
        extractUsage(d, timing.usage);
        const reply = extractDelta(d);
        if (reply) onText(reply);
        else { last.text = JSON.stringify(d, null, 2); responseEl.textContent = last.text; }
      } else {
        onText(text);
      }
    }
    timing.total = performance.now() - t0;
    $id("responseJson").innerHTML = last.json ? highlightJson(last.json) : "";
    statusEl.textContent = "Complete";
    statusEl.className = "text-caption font-bold px-2 py-1 rounded-control border border-hairline bg-surface-inset text-ok";
  } catch (error) {
    if (timing.total == null) timing.total = performance.now() - t0;
    statusEl.textContent = "Error";
    statusEl.className = "text-caption font-bold px-2 py-1 rounded-control border border-hairline bg-surface-inset text-down";
    if (!error.handled) {
      responseEl.textContent = error.message;
      responseEl.className = "text-down whitespace-pre-wrap break-words font-mono text-sm" + (bodyView === "text" ? "" : " hidden");
    }
  } finally {
    sendBtn.disabled = false;
    sendBtn.innerHTML = '<i class="fas fa-paper-plane mr-1.5"></i>Send';
    loadingIndicator.classList.add("hidden");
    showTiming(timing);
    pushHistory({ ...form, status: timing.status ?? "error", ms: Math.round(timing.total) });
  }
});

// Ctrl/Cmd + Enter sends from anywhere in the form.
$id("apiForm").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $id("apiForm").requestSubmit(); }
});

$id("clearBtn").addEventListener("click", () => {
  resetResponse();
  $id("status").textContent = "WAITING";
  $id("status").className = "text-caption font-bold px-2 py-1 rounded-control bg-surface-inset text-ink-muted border border-hairline";
});

refreshCode();
