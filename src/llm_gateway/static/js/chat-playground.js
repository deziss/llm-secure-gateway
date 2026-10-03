// chat-playground.js — Chat Playground page logic

const messages = []; // conversation history
const pendingImages = []; // base64 image data
let backends = [];

// Configure marked.js.  Code blocks are highlighted after rendering via
// hljs.highlightElement() — marked dropped its built-in `highlight` option in
// v5, so configuring it here would silently do nothing.
marked.setOptions({
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

// The attach control is a <label> (so a click opens the file picker natively).
// Labels aren't keyboard-activatable, so map Enter/Space to the picker too.
document.getElementById("imageUploadBtn").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    document.getElementById("imageInput").click();
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
      thumb.innerHTML = `<img src="${b64}" /><span class="img-thumb-remove" role="button" tabindex="0" aria-label="Remove image" data-remove-image="${id}"><i class="fas fa-times text-white" aria-hidden="true"></i></span>`;
      bar.appendChild(thumb);
    }
    this.value = "";
  });

// Delegated: ids are rendered as data attributes rather than inline handlers.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove-image]");
  if (btn) removeImage(Number(btn.dataset.removeImage));
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-remove-image]")) {
    e.preventDefault();
    removeImage(Number(e.target.dataset.removeImage));
  }
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
    const res = await fetch(BASE_URL + "/admin/servers", {
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
    <p class="text-slate-500 text-sm">Start a conversation</p><p class="text-ink-muted text-xs mt-1">Select a model and type a message below</p></div>`;
  convo.turns = convo.prompt = convo.completion = convo.lastPrompt = 0;
  convo.estimated = false;
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
      <div class="bubble-ai"></div>`;
    const b = wrapper.querySelector(".bubble-ai");
    if (window.MarkdownView) MarkdownView.render(b, content || "");
    else b.innerHTML = sanitizeHTML(MarkdownView.parse(content || ""));
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
  try {
    await _sendMessage();
  } catch (err) {
    // Never fail silently: re-enable Send and say what went wrong.
    console.error("sendMessage failed", err);
    const btn = document.getElementById("send-btn");
    if (btn) btn.disabled = false;
    if (typeof setStatus === "function") setStatus("Error", "red");
    showToast("Message not sent", err && err.message ? err.message : String(err), "error");
  }
}

async function _sendMessage() {
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
        // Ask for the token usage summary in the final stream chunk; without
        // it, streamed replies carry no usage at all.
        ...(streaming ? { stream_options: { include_usage: true } } : {}),
        temperature,
        max_tokens: maxTokens,
      };

  let thinkTimer = null;
  let pendingReply = null;
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

    // The reply bubble appears immediately with a "Thinking" state, and keeps
    // it until the first real text arrives. Previously the dots were removed
    // as soon as response headers came back, leaving an empty bubble for the
    // whole time a reasoning model was thinking.
    typing.remove();
    const aiWrapper = document.createElement("div");
    aiWrapper.className = "msg-ai";
    aiWrapper.innerHTML = `<div class="ai-avatar">AI</div><div class="bubble-ai">${thinkingHTML()}</div>`;
    document.getElementById("messages-container").appendChild(aiWrapper);
    aiBubble = aiWrapper.querySelector(".bubble-ai");
    thinkTimer = startThinkingClock(aiBubble);
    pendingReply = aiWrapper;

    const t0 = performance.now();
    let tFirst = null;          // first token of any kind (time to first token)
    let reasoning = "";
    let usage = null;           // exact counts reported by the backend, if any

    // The reply is built once and then updated in place. Rebuilding the whole
    // bubble on every streamed token reset the Thinking panel to open and made
    // it impossible to collapse (or animate) while the model was still thinking.
    let reasoningEl = null, reasoningBody = null, reasoningLabel = null, answerEl = null;
    let userToggledReasoning = false, autoCollapseTimer = null;
    const ensureLayout = () => {
      if (answerEl) return;
      aiBubble.innerHTML = "";
      if (reasoning) {
        reasoningEl = document.createElement("details");
        reasoningEl.className = "reasoning";
        reasoningEl.open = true;
        reasoningEl.innerHTML = `<summary><span class="reasoning-label">Thinking…</span></summary><div class="reasoning-body"></div>`;
        reasoningLabel = reasoningEl.querySelector(".reasoning-label");
        reasoningBody = reasoningEl.querySelector(".reasoning-body");
        // A manual open/close wins over the automatic collapse.
        reasoningEl.querySelector("summary").addEventListener("click", () => {
          userToggledReasoning = true;
          clearTimeout(autoCollapseTimer);
        });
        aiBubble.appendChild(reasoningEl);
        // Show the live reasoning briefly, then fold it away so the answer
        // area stays in view.
        autoCollapseTimer = setTimeout(() => collapseReasoning(reasoningEl), 3000);
      }
      answerEl = document.createElement("div");
      answerEl.className = "reply-answer";
      aiBubble.appendChild(answerEl);
    };
    const render = () => {
      if (!reasoning && !fullContent) return; // keep the initial thinking indicator
      ensureLayout();
      if (reasoningEl) {
        const thinkingSecs = ((tFirstContent || performance.now()) - t0) / 1000;
        reasoningLabel.textContent = tFirstContent
          ? `Thought for ${thinkingSecs.toFixed(1)}s`
          : `Thinking… ${Math.round(thinkingSecs)}s`;
        const atBottom = reasoningBody.scrollHeight - reasoningBody.scrollTop - reasoningBody.clientHeight < 24;
        reasoningBody.textContent = reasoning;
        if (atBottom) reasoningBody.scrollTop = reasoningBody.scrollHeight; // follow the stream
      }
      if (fullContent) answerEl.innerHTML = sanitizeHTML(MarkdownView.parse(fullContent));
      else if (!answerEl.querySelector(".thinking")) answerEl.innerHTML = thinkingHTML();
      const mc = document.getElementById("messages-container");
      mc.scrollTop = mc.scrollHeight;
    };
    let tFirstContent = null;
    const take = (data) => {
      const delta = data.choices?.[0]?.delta || data.choices?.[0]?.message || {};
      const r = delta.reasoning_content || delta.reasoning || data.message?.thinking || "";
      const c = delta.content || data.message?.content || data.response || "";
      if ((r || c) && tFirst === null) tFirst = performance.now();
      if (c && tFirstContent === null) tFirstContent = performance.now();
      reasoning += r;
      fullContent += typeof c === "string" ? c : "";
      if (data.usage) usage = data.usage;                       // OpenAI-compatible
      if (data.prompt_eval_count != null || data.eval_count != null) {   // Ollama
        usage = { prompt_tokens: data.prompt_eval_count || 0, completion_tokens: data.eval_count || 0 };
      }
    };

    const response = await doFetch(requestUrl, requestBody);
    if (!response.ok)
      throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    setStatus("Receiving...", "blue");

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
          const jsonStr = trimmed.startsWith("data: ") ? trimmed.slice(6) : trimmed;
          try { take(JSON.parse(jsonStr)); } catch (e) {}
        }
        render();
      }
    } else {
      const data = await response.json();
      take(data);
      if (!fullContent && !reasoning) fullContent = JSON.stringify(data, null, 2);
    }
    clearInterval(thinkTimer);
    pendingReply = null;
    if (!fullContent && reasoning) fullContent = "_(The model returned reasoning but no final answer.)_";
    render();

    const tEnd = performance.now();
    recordTurnStats(aiWrapper, {
      usage, t0, tFirst, tEnd,
      promptText: JSON.stringify(allMessages),
      completionText: reasoning + fullContent,
      replyText: fullContent,
      contextMessages: allMessages.length,
      model,
    });

    // Apply syntax highlighting
    // Code highlighting, per-block Copy buttons and new-tab links.
    if (window.MarkdownView) MarkdownView.enhance(aiBubble);
    else aiBubble.querySelectorAll("pre code").forEach((el) => hljs.highlightElement(el));

    // Add to history
    messages.push({ role: "assistant", content: fullContent });
    setStatus("Ready", "green");
  } catch (err) {
    typing.remove();
    clearInterval(thinkTimer);
    // Drop the half-built reply (still showing "Thinking…") before the error.
    if (pendingReply && pendingReply.querySelector(".thinking") && !pendingReply.querySelector(".reasoning")) pendingReply.remove();
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

// ── Thinking indicator, token stats and context memory ──────────────────

// Animated collapse of a reply's Thinking panel. Height and opacity ease out
// over 350 ms, then the <details> closes so it can be re-opened normally.
function collapseReasoning(details) {
  if (!details || !details.open) return;
  const body = details.querySelector(".reasoning-body");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!body || reduce || typeof body.animate !== "function") { details.open = false; return; }
  const h = body.offsetHeight;
  const anim = body.animate(
    [{ height: h + "px", opacity: 1 }, { height: "0px", opacity: 0, marginTop: "0px" }],
    { duration: 350, easing: "cubic-bezier(0.4, 0, 0.2, 1)" }
  );
  anim.onfinish = () => { details.open = false; };
}

function thinkingHTML() {
  return `<span class="thinking"><span class="dots"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></span><span class="think-label">Thinking…</span></span>`;
}

// Shows elapsed seconds in the thinking label so a long wait reads as
// progress rather than a hang.
function startThinkingClock(bubble) {
  const started = performance.now();
  return setInterval(() => {
    const label = bubble.querySelector(".think-label");
    if (label) label.textContent = `Thinking… ${Math.round((performance.now() - started) / 1000)}s`;
  }, 1000);
}

// Running totals for this conversation. Reset by clearChat().
const convo = { turns: 0, prompt: 0, completion: 0, lastPrompt: 0, estimated: false };

// ~4 characters per token: only used when the backend reports no usage.
const estimateTokens = (text) => Math.max(1, Math.round((text || "").length / 4));
const fmt = (n) => Number(n || 0).toLocaleString();

function contextWindowFor(model) {
  const el = document.getElementById("contextWindow");
  const v = el && parseInt(el.value, 10);
  return Number.isFinite(v) && v > 0 ? v : null;
}

function ctxGauge(used, windowSize) {
  if (!windowSize) return `<span>Context <b>${fmt(used)}</b> tokens</span>`;
  const pct = Math.min(100, (used / windowSize) * 100);
  const cls = pct >= 90 ? "down" : pct >= 70 ? "warn" : "";
  return `<span title="Prompt tokens of this turn ÷ context window">Context <b>${fmt(used)}</b> / ${fmt(windowSize)} (${pct.toFixed(0)}%) <span class="ctx-bar ${cls}"><i style="width:${pct.toFixed(1)}%"></i></span></span>`;
}

function recordTurnStats(wrapper, s) {
  const exact = !!(s.usage && (s.usage.prompt_tokens != null || s.usage.completion_tokens != null));
  const prompt = exact ? (s.usage.prompt_tokens || 0) : estimateTokens(s.promptText);
  const completion = exact ? (s.usage.completion_tokens || 0) : estimateTokens(s.completionText);
  const total = exact && s.usage.total_tokens != null ? s.usage.total_tokens : prompt + completion;
  const secs = (s.tEnd - s.t0) / 1000;
  const genSecs = s.tFirst ? (s.tEnd - s.tFirst) / 1000 : secs;
  const tps = genSecs > 0 ? completion / genSecs : 0;
  const approx = exact ? "" : "≈";

  convo.turns += 1;
  convo.prompt += prompt;
  convo.completion += completion;
  convo.lastPrompt = prompt;
  convo.estimated = convo.estimated || !exact;

  const windowSize = contextWindowFor(s.model);
  const stats = document.createElement("div");
  stats.className = "msg-stats";
  stats.innerHTML = [
    `<span title="Tokens the model read for this reply (system prompt + whole history)">In <b>${approx}${fmt(prompt)}</b></span>`,
    `<span title="Tokens the model generated">Out <b>${approx}${fmt(completion)}</b></span>`,
    `<span>Total <b>${approx}${fmt(total)}</b></span>`,
    `<span>${secs.toFixed(1)}s${s.tFirst ? ` · first token ${((s.tFirst - s.t0) / 1000).toFixed(1)}s` : ""}${tps ? ` · ${tps.toFixed(0)} tok/s` : ""}</span>`,
    `<span title="Messages sent as context, including the system prompt">Memory <b>${s.contextMessages}</b> msgs</span>`,
    ctxGauge(prompt, windowSize),
    exact ? "" : `<span title="The server didn't report usage; counts are estimated at ~4 characters per token">estimated</span>`,
  ].filter(Boolean).join("");
  const bubble = wrapper.querySelector(".bubble-ai");
  if (bubble) {
    bubble.id = bubble.id || "reply-" + Date.now() + "-" + Math.floor(Math.random() * 1e6);
    bubble.dataset.raw = s.replyText || "";
    const tools = document.createElement("span");
    tools.className = "reply-tools";
    const btn = window.MarkdownView ? MarkdownView.copyButton("Copy reply") : null;
    if (btn) { btn.dataset.copySource = bubble.id; tools.appendChild(btn); stats.prepend(tools); }
  }
  wrapper.after(stats);
  renderConvoTotals();
}

function renderConvoTotals() {
  const el = document.getElementById("tokenCount");
  if (!el) return;
  if (!convo.turns) { el.textContent = ""; return; }
  const a = convo.estimated ? "≈" : "";
  const w = contextWindowFor();
  const ctx = w ? ` · context ${Math.round((convo.lastPrompt / w) * 100)}%` : "";
  el.textContent = `${convo.turns} turn${convo.turns === 1 ? "" : "s"} · ${a}${fmt(convo.prompt + convo.completion)} tokens (in ${a}${fmt(convo.prompt)} · out ${a}${fmt(convo.completion)})${ctx}`;
}

// Remember the context window per model.
(function () {
  const input = document.getElementById("contextWindow");
  const modelSel = document.getElementById("modelSelect");
  if (!input || !modelSel) return;
  const key = () => "ctxWindow:" + (modelSel.value || "");
  const load = () => {
    try { input.value = localStorage.getItem(key()) || ""; } catch (e) {}
  };
  input.addEventListener("change", () => {
    try {
      if (input.value) localStorage.setItem(key(), input.value);
      else localStorage.removeItem(key());
    } catch (e) {}
    renderConvoTotals();
  });
  modelSel.addEventListener("change", load);
  load();
})();
