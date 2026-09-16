// chat-playground.js — Chat Playground page logic

const messages = []; // conversation history
const pendingImages = []; // base64 image data
let backends = [];

// Configure marked.js
marked.setOptions({
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang))
      return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  },
  breaks: true,
  gfm: true,
});

// Temperature slider
document.getElementById("temperature").addEventListener("input", function () {
  document.getElementById("tempDisplay").textContent = this.value;
});

// Vision toggle
document
  .getElementById("visionToggle")
  .addEventListener("change", function () {
    const badge = document.getElementById("visionBadge");
    const imageBtn = document.getElementById("imageUploadBtn");
    badge.classList.toggle("hidden", !this.checked);
    imageBtn.classList.toggle("hidden", !this.checked);
  });

// Auto-resize textarea
const textarea = document.getElementById("chat-textarea");
textarea.addEventListener("input", () => {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
});

// Send on Enter, shift+enter for newline
textarea.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Image upload
document
  .getElementById("imageInput")
  .addEventListener("change", async function () {
    const bar = document.getElementById("image-preview-bar");
    bar.classList.remove("hidden");
    for (const file of this.files) {
      const b64 = await fileToBase64(file);
      const id = Date.now() + Math.random();
      pendingImages.push({ id, b64, type: file.type });

      const thumb = document.createElement("div");
      thumb.className = "img-thumb";
      thumb.id = "thumb-" + id;
      thumb.innerHTML = `<img src="${b64}" /><span class="img-thumb-remove" onclick="removeImage(${id})"><i class="fas fa-times text-white"></i></span>`;
      bar.appendChild(thumb);
    }
    this.value = "";
  });

function removeImage(id) {
  const idx = pendingImages.findIndex((i) => i.id === id);
  if (idx !== -1) pendingImages.splice(idx, 1);
  const el = document.getElementById("thumb-" + id);
  if (el) el.remove();
  if (pendingImages.length === 0)
    document.getElementById("image-preview-bar").classList.add("hidden");
}

function fileToBase64(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target.result);
    reader.readAsDataURL(file);
  });
}

// Fetch models
async function fetchModels() {
  const provider = document.getElementById("providerSelect").value;
  const statusEl = document.getElementById("modelStatus");
  statusEl.textContent = "Fetching...";
  try {
    const res = await fetch(BASE_URL + "/admin/backends", {
      credentials: "include",
    });
    backends = await res.json();
    const select = document.getElementById("modelSelect");
    select.innerHTML = "";
    let models = [];
    backends
      .filter((b) => b.backend_type === provider)
      .forEach((b) => {
        (b.models || []).forEach((m) => {
          if (!models.includes(m)) models.push(m);
        });
      });
    if (models.length === 0) {
      select.innerHTML = '<option value="">No models found</option>';
      statusEl.textContent = "No models found";
      return;
    }
    models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      select.appendChild(opt);
    });
    statusEl.textContent = models.length + " model(s)";
    updateHeader();
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
}

function updateHeader() {
  const model = document.getElementById("modelSelect").value;
  const provider = document.getElementById("providerSelect").value;
  document.getElementById("headerModelName").textContent =
    model || "No model";
  document.getElementById("headerProvider").textContent = provider;
}

document
  .getElementById("fetchModelsBtn")
  .addEventListener("click", fetchModels);
document
  .getElementById("providerSelect")
  .addEventListener("change", fetchModels);
document
  .getElementById("modelSelect")
  .addEventListener("change", updateHeader);

// Clear chat
function clearChat() {
  messages.length = 0;
  const container = document.getElementById("messages-container");
  // Keep only empty state
  container.innerHTML = `<div id="empty-state" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;pointer-events:none;opacity:0.6;">
    <div class="text-5xl mb-4 opacity-30">&#x1F4AC;</div>
    <p class="text-slate-500 text-sm">Start a conversation</p><p class="text-slate-600 text-xs mt-1">Select a model and type a message below</p></div>`;
  document.getElementById("tokenCount").textContent = "";
}

function addMessageToUI(role, content, images = []) {
  const container = document.getElementById("messages-container");
  const emptyState = document.getElementById("empty-state");
  if (emptyState) emptyState.remove();

  const wrapper = document.createElement("div");

  if (role === "user") {
    wrapper.className = "msg-user";
    let html = `<div class="bubble-user">`;
    images.forEach((img) => {
      html += `<img src="${img}" style="max-width:200px;border-radius:8px;margin-bottom:8px;display:block;" />`;
    });
    html += `<span>${escapeHtml(content)}</span></div>`;
    wrapper.innerHTML = html;
  } else {
    wrapper.className = "msg-ai";
    wrapper.innerHTML = `
      <div class="ai-avatar">AI</div>
      <div class="bubble-ai">${sanitizeHTML(marked.parse(content || ""))}</div>`;
  }

  container.appendChild(wrapper);
  container.scrollTop = container.scrollHeight;
  return wrapper;
}

function addTypingIndicator() {
  const container = document.getElementById("messages-container");
  const wrapper = document.createElement("div");
  wrapper.className = "msg-ai";
  wrapper.id = "typing-indicator";
  wrapper.innerHTML = `
    <div class="ai-avatar">AI</div>
    <div class="bubble-ai flex items-center gap-1.5">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  container.appendChild(wrapper);
  container.scrollTop = container.scrollHeight;
  return wrapper;
}

async function sendMessage() {
  const input = document.getElementById("chat-textarea").value.trim();
  if (!input && pendingImages.length === 0) return;

  const model = document.getElementById("modelSelect").value;
  const apiKey = document.getElementById("apiKey").value;
  const provider = document.getElementById("providerSelect").value;
  const streaming = document.getElementById("streamToggle").checked;
  const temperature = parseFloat(
    document.getElementById("temperature").value,
  );
  const maxTokens = parseInt(document.getElementById("maxTokens").value);
  const systemPrompt = document.getElementById("systemPrompt").value;

  if (!model) {
    showToast("Error", "Please select a model.", "error");
    return;
  }

  // Build user message with optional images
  const imageCopies = [...pendingImages];
  const msg_images = imageCopies.map((i) => i.b64);

  let userMsg;
  if (imageCopies.length > 0 && provider === "ollama") {
    userMsg = {
      role: "user",
      content: input,
      images: imageCopies.map((i) => i.b64.split(",")[1]),
    };
  } else if (imageCopies.length > 0) {
    userMsg = {
      role: "user",
      content: [
        { type: "text", text: input },
        ...imageCopies.map((i) => ({
          type: "image_url",
          image_url: { url: i.b64 },
        })),
      ],
    };
  } else {
    userMsg = { role: "user", content: input };
  }

  // Add to history
  messages.push(userMsg);

  // Add to UI
  addMessageToUI("user", input, msg_images);

  // Clear input and images
  document.getElementById("chat-textarea").value = "";
  document.getElementById("chat-textarea").style.height = "auto";
  pendingImages.length = 0;
  document.getElementById("image-preview-bar").innerHTML = "";
  document.getElementById("image-preview-bar").classList.add("hidden");

  // Show typing
  const typing = addTypingIndicator();
  const sendBtn = document.getElementById("send-btn");
  sendBtn.disabled = true;

  setStatus("Sending...", "yellow");

  // Build messages array including system prompt
  const allMessages = [];
  if (systemPrompt)
    allMessages.push({ role: "system", content: systemPrompt });
  allMessages.push(...messages);

  // Choose endpoint and body format based on provider type
  const isOllama = provider === "ollama";
  const requestUrl = isOllama
    ? `${BASE_URL}/provider/${provider}/api/chat`
    : `${BASE_URL}/provider/${provider}/v1/chat/completions`;

  const requestBody = isOllama
    ? {
        model,
        messages: allMessages.map((m) => ({
          role: m.role,
          content: m.content,
          ...(m.images ? { images: m.images } : {}),
        })),
        stream: streaming,
        options: { temperature },
      }
    : {
        model,
        messages: allMessages,
        stream: streaming,
        temperature,
        max_tokens: maxTokens,
      };

  try {
    let fullContent = "";
    let aiBubble = null;

    const doFetch = async (fetchUrl, fetchBody) => {
      const fetchOptions = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fetchBody),
      };
      if (apiKey) {
        fetchOptions.headers["Authorization"] = "Bearer " + apiKey;
      }
      return await fetchWithCsrf(fetchUrl, fetchOptions);
    };

    const response = await doFetch(requestUrl, requestBody);

    if (!response.ok)
      throw new Error(`HTTP ${response.status}: ${await response.text()}`);

    typing.remove();
    setStatus("Receiving...", "blue");

    // Add AI bubble
    const aiWrapper = document.createElement("div");
    aiWrapper.className = "msg-ai";
    aiWrapper.innerHTML = `<div class="ai-avatar">AI</div><div class="bubble-ai"></div>`;
    document.getElementById("messages-container").appendChild(aiWrapper);
    aiBubble = aiWrapper.querySelector(".bubble-ai");

    if (streaming) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === "data: [DONE]") continue;
          // OpenAI SSE format: "data: {...}"
          const jsonStr = trimmed.startsWith("data: ")
            ? trimmed.slice(6)
            : trimmed;
          try {
            const data = JSON.parse(jsonStr);
            if (data.message?.content) fullContent += data.message.content;
            else if (data.choices?.[0]?.delta?.content)
              fullContent += data.choices[0].delta.content;
            else if (data.response) fullContent += data.response;
            aiBubble.innerHTML = sanitizeHTML(marked.parse(fullContent));
            document.getElementById("messages-container").scrollTop =
              document.getElementById("messages-container").scrollHeight;
          } catch (e) {}
        }
      }
    } else {
      const data = await response.json();
      if (data.message?.content) fullContent = data.message.content;
      else if (data.choices?.[0]?.message?.content)
        fullContent = data.choices[0].message.content;
      else if (data.response) fullContent = data.response;
      else fullContent = JSON.stringify(data, null, 2);
      aiBubble.innerHTML = sanitizeHTML(marked.parse(fullContent));
    }

    // Apply syntax highlighting
    aiBubble
      .querySelectorAll("pre code")
      .forEach((el) => hljs.highlightElement(el));

    // Add to history
    messages.push({ role: "assistant", content: fullContent });
    setStatus("Ready", "green");
  } catch (err) {
    typing.remove();
    addMessageToUI("ai", `**Error**: ${err.message}`);
    setStatus("Error", "red");
  } finally {
    sendBtn.disabled = false;
    document.getElementById("messages-container").scrollTop =
      document.getElementById("messages-container").scrollHeight;
  }
}

function setStatus(text, color) {
  const colors = {
    green: "bg-green-500",
    yellow: "bg-yellow-500",
    blue: "bg-blue-500",
    red: "bg-red-500",
  };
  const statusEl = document.getElementById("statusDisplay");
  statusEl.innerHTML = `<span class="status-dot ${colors[color]}"></span> ${text}`;
}

// Init
fetchModels();
