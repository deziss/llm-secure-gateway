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
    statusEl.className = "text-xs text-blue-400 mt-1";

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
        statusEl.className = "text-xs text-green-400 mt-1";
      } else {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No models found";
        modelSelect.appendChild(opt);
        statusEl.textContent =
          "No models found. Try syncing backends first.";
        statusEl.className = "text-xs text-yellow-400 mt-1";
      }
    } catch (error) {
      statusEl.textContent = error.message;
      statusEl.className = "text-xs text-red-400 mt-1";
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

  return {
    model: model,
    messages: [{ role: "user", content: message }],
    stream: streaming,
  };
}

function updateRequestPreview() {
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
  const activeClasses = ["bg-blue-600", "text-white"];
  const inactiveClasses = ["bg-transparent", "text-slate-400"];

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
// Submit Handler
// =============================================
document.getElementById("apiForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const endpoint = document.getElementById("endpoint").value;
  const model = document.getElementById("model").value;
  const message = document.getElementById("message").value;
  const streaming = document.getElementById("streaming").checked;
  const apiKey = document.getElementById("apiKey").value;
  const httpMethod = document.getElementById("httpMethod").value;

  const responseEl = document.getElementById("response");
  const statusEl = document.getElementById("status");
  const metricsEl = document.getElementById("metrics");
  const sendBtn = document.getElementById("sendBtn");

  // Reset
  responseEl.textContent = "";
  responseEl.className =
    "text-slate-300 whitespace-pre-wrap font-mono text-sm";
  statusEl.textContent = "Connecting...";
  statusEl.className = "text-sm text-yellow-400";
  metricsEl.classList.add("hidden");
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Sending...';

  // Show loading animation
  const loadingIndicator = document.getElementById("loadingIndicator");
  const loadingText = document.getElementById("loadingText");
  loadingIndicator.classList.remove("hidden");
  loadingText.textContent = "Sending request...";

  const startTime = Date.now();

  // Determine body
  let body = null;
  const useCustomBody = document.getElementById("useCustomBody").checked;

  if (useCustomBody) {
    const customBodyText = document.getElementById("requestBodyEditor").value;
    if (customBodyText && customBodyText !== "(no body for this endpoint)") {
      try {
        body = JSON.parse(customBodyText);
      } catch (parseErr) {
        showToast("Error", "Invalid JSON in request body editor: " + parseErr.message, "error");
        sendBtn.disabled = false;
        sendBtn.textContent = "Send Request";
        return;
      }
    }
  } else {
    body = buildBody();
  }

  const url = buildUrl();

  const fetchOptions = {
    method: httpMethod,
    headers: {
      "Content-Type": "application/json",
    },
  };
  if (apiKey) {
    fetchOptions.headers["Authorization"] = "Bearer " + sanitizeHeader(apiKey);
  }
  if (body && httpMethod !== "GET") {
    fetchOptions.body = JSON.stringify(body);
  }

  try {
    const response = await fetchWithCsrf(url, fetchOptions);

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`HTTP ${response.status}: ${error}`);
    }

    statusEl.textContent = "Receiving...";
    statusEl.className = "text-sm text-blue-400";
    loadingIndicator.classList.add("hidden");

    const isStreamingRequest = body && body.stream === true;

    if (isStreamingRequest) {
      // Streaming response
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = "";
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === "data: [DONE]") continue;

          const jsonStr = trimmed.startsWith("data: ")
            ? trimmed.slice(6)
            : trimmed;
          try {
            const data = JSON.parse(jsonStr);
            if (data.message?.content) {
              fullContent += data.message.content;
            } else if (data.choices?.[0]?.delta?.content) {
              fullContent += data.choices[0].delta.content;
            } else if (data.response) {
              fullContent += data.response;
            }
          } catch (e) {
            // parse error for streaming line, skip
          }
        }
        responseEl.textContent = fullContent;
        responseEl.parentElement.scrollTop =
          responseEl.parentElement.scrollHeight;
      }

      if (buffer.trim()) {
        const trimmedBuffer = buffer.trim();
        if (trimmedBuffer !== "data: [DONE]") {
          const jsonStr = trimmedBuffer.startsWith("data: ")
            ? trimmedBuffer.slice(6)
            : trimmedBuffer;
          try {
            const data = JSON.parse(jsonStr);
            if (data.message?.content) {
              fullContent += data.message.content;
            } else if (data.choices?.[0]?.delta?.content) {
              fullContent += data.choices[0].delta.content;
            } else if (data.response) {
              fullContent += data.response;
            }
            responseEl.textContent = fullContent;
          } catch (e) {
            // final buffer parse error, skip
          }
        }
      }
    } else {
      // Non-streaming response
      loadingIndicator.classList.add("hidden");
      const data = await response.json();
      if (data.message?.content) {
        responseEl.textContent = data.message.content;
      } else if (data.choices?.[0]?.message?.content) {
        responseEl.textContent = data.choices[0].message.content;
      } else if (data.response) {
        responseEl.textContent = data.response;
      } else {
        responseEl.textContent = JSON.stringify(data, null, 2);
      }
    }

    const duration = ((Date.now() - startTime) / 1000).toFixed(2);
    statusEl.textContent = "Complete";
    statusEl.className = "text-sm text-green-400";
    document.getElementById("duration").textContent = `${duration}s`;
    metricsEl.classList.remove("hidden");
  } catch (error) {
    statusEl.textContent = "Error";
    statusEl.className = "text-sm text-red-400";
    responseEl.textContent = error.message;
    responseEl.className =
      "text-red-400 whitespace-pre-wrap font-mono text-sm";
  } finally {
    sendBtn.disabled = false;
    sendBtn.innerHTML = '<i class="fas fa-paper-plane mr-2"></i>Send Request';
    loadingIndicator.classList.add("hidden");
  }
});

document.getElementById("clearBtn").addEventListener("click", () => {
  document.getElementById("response").textContent = "";
  document.getElementById("status").textContent = "";
  document.getElementById("metrics").classList.add("hidden");
});
