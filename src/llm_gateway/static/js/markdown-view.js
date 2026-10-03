// markdown-view.js — shared Markdown rendering for the playground pages.
//
//   MarkdownView.render(el, text)   parse, sanitize, then enhance
//   MarkdownView.enhance(el)        links open in a new tab, code is
//                                   highlighted and gets a Copy button
//   MarkdownView.copy(text, btn)    clipboard with visual confirmation
//
// Rendering order matters: marked output is passed through DOMPurify
// (sanitizeHTML in globals.js) *before* anything is added, and the extras are
// added with DOM APIs, never by concatenating untrusted strings.
(function () {
  "use strict";

  // Models often wrap a whole answer in ```markdown ... ```, which would show
  // the document as source in a code block. A fence labelled markdown/md is
  // content meant to be read, so render it. A reply that is one such fence is
  // unwrapped by its first and last lines, so fences nested inside survive.
  const MD_FENCE = /^(`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*$/i;
  function unwrapMarkdownFences(text) {
    const lines = text.replace(/\r\n/g, "\n").trim().split("\n");
    const open = lines.length > 1 && lines[0].match(MD_FENCE);
    if (open && lines[lines.length - 1].trim() === open[1]) {
      return lines.slice(1, -1).join("\n");
    }
    // Otherwise unwrap md fences that contain no nested fence.
    return text.replace(/^(`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*\n((?:(?!^(?:`{3}|~{3}))[\s\S])*?)^\1[ \t]*$/gim, "$2");
  }

  function parse(text) {
    if (!window.marked) return null;
    return window.marked.parse(unwrapMarkdownFences(String(text || "")), { gfm: true, breaks: true });
  }

  function copy(text, btn) {
    const done = (ok) => {
      if (!btn) return;
      const label = btn.querySelector("[data-copy-label]") || btn;
      const prev = label.textContent;
      label.textContent = ok ? "Copied" : "Copy failed";
      btn.classList.toggle("is-copied", ok);
      setTimeout(() => { label.textContent = prev; btn.classList.remove("is-copied"); }, 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(() => done(true), () => done(fallback(text)));
    } else {
      done(fallback(text));
    }
  }

  // execCommand fallback for non-secure contexts (e.g. plain http on a LAN IP).
  function fallback(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove();
    return ok;
  }

  function copyButton(title) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "md-copy";
    b.setAttribute("aria-label", title);
    b.title = title;
    b.innerHTML = '<i data-lucide="copy" aria-hidden="true"></i><span data-copy-label>Copy</span>';
    return b;
  }

  function enhance(el) {
    if (!el) return;
    // Links: open outside the playground; never leak the referrer/opener.
    el.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href") || "";
      if (/^(https?:|mailto:)/i.test(href)) {
        a.target = "_blank";
        a.rel = "noopener noreferrer nofollow";
      }
    });
    // Bare URLs that marked left as text (e.g. inside lists) stay as-is;
    // GFM autolinking already handles the common case.
    el.querySelectorAll("pre").forEach((pre) => {
      if (pre.parentElement && pre.parentElement.classList.contains("md-code")) return;
      const code = pre.querySelector("code");
      if (code && window.hljs && !code.classList.contains("hljs")) {
        try { window.hljs.highlightElement(code); } catch (e) {}
      }
      const wrap = document.createElement("div");
      wrap.className = "md-code";
      pre.replaceWith(wrap);
      wrap.appendChild(pre);
      const lang = code && (code.className.match(/language-([\w+-]+)/) || [])[1];
      const bar = document.createElement("div");
      bar.className = "md-code-bar";
      const tag = document.createElement("span");
      tag.textContent = lang || "code";
      bar.appendChild(tag);
      const btn = copyButton("Copy code");
      btn.dataset.copyCode = "";
      bar.appendChild(btn);
      wrap.insertBefore(bar, pre);
    });
  }

  function render(el, text) {
    const html = parse(text);
    if (html === null) { el.textContent = text; return; }
    el.innerHTML = typeof sanitizeHTML === "function" ? sanitizeHTML(html) : html;
    enhance(el);
  }

  // One delegated handler for every copy button on the page.
  document.addEventListener("click", (e) => {
    const codeBtn = e.target.closest("[data-copy-code]");
    if (codeBtn) {
      const pre = codeBtn.closest(".md-code")?.querySelector("pre");
      if (pre) copy(pre.innerText.replace(/\n$/, ""), codeBtn);
      return;
    }
    const textBtn = e.target.closest("[data-copy-source]");
    if (textBtn) {
      const src = document.getElementById(textBtn.dataset.copySource);
      const raw = src && (src.dataset.raw != null ? src.dataset.raw : src.innerText);
      if (raw != null) copy(raw, textBtn);
    }
  });

  window.MarkdownView = { render, parse, enhance, copy, copyButton };
})();
