/**
 * Swiss LMS & Portfolio RAG - Embeddable Floating Chat Widget
 * Zero external dependencies, scoped inline styling.
 * 
 * Usage:
 * <script src="http://127.0.0.1:8000/static/widget.js" 
 *         data-project-id="portfolio" 
 *         data-title="Ask About My Projects"
 *         data-api-url="http://127.0.0.1:8000"></script>
 */
(function () {
  "use strict";

  if (window.__swissRagWidgetInitialized) return;
  window.__swissRagWidgetInitialized = true;

  // Read configuration from document.currentScript
  const currentScript = document.currentScript;
  let defaultApiUrl = "";
  if (currentScript && currentScript.src) {
    try {
      defaultApiUrl = new URL(currentScript.src).origin;
    } catch (e) {
      defaultApiUrl = window.location.origin;
    }
  } else {
    defaultApiUrl = window.location.origin;
  }

  const projectId = (currentScript && currentScript.getAttribute("data-project-id")) || "portfolio";
  const title = (currentScript && currentScript.getAttribute("data-title")) || "Ask AI Assistant";
  const rawApiUrl = (currentScript && currentScript.getAttribute("data-api-url")) || defaultApiUrl;
  const apiUrl = rawApiUrl.replace(/\/+$/, "");
  const iconUrl = (currentScript && currentScript.getAttribute("data-icon")) || `${apiUrl}/static/bot-avatar.png`;

  // Persistent session_id in sessionStorage
  const storageKey = `swiss_rag_session_${projectId}`;
  let sessionId = sessionStorage.getItem(storageKey);
  if (!sessionId) {
    sessionId = "widget_" + Math.random().toString(36).substring(2, 11);
    sessionStorage.setItem(storageKey, sessionId);
  }

  // Escape HTML special characters for safe badge rendering
  function escapeHtml(text) {
    if (!text) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Extract clean visible text from the host webpage (excluding scripts, styles, noscript, svg, and this widget)
  function getPageContext() {
    try {
      if (!document.body) return "";
      const clone = document.body.cloneNode(true);
      const unwanted = clone.querySelectorAll("script, style, noscript, svg, #swiss-rag-widget-root");
      unwanted.forEach((el) => el.remove());
      const rawText = clone.innerText || clone.textContent || "";
      return rawText.replace(/\s+/g, " ").trim().slice(0, 15000);
    } catch (e) {
      return "";
    }
  }

  // Lightweight Markdown Formatter
  function formatMarkdown(text) {
    if (!text) return "";
    let escaped = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");

    escaped = escaped.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    escaped = escaped.replace(/`([^`]+)`/g, '<code style="background: rgba(0,0,0,0.06); padding: 1px 4px; border-radius: 4px; font-family: monospace; font-size: 0.9em;">$1</code>');

    const lines = escaped.split("\n");
    let inList = false;
    let formattedLines = [];

    for (let line of lines) {
      const bulletMatch = line.match(/^(\s*)[\*\-]\s+(.*)$/);
      if (bulletMatch) {
        if (!inList) {
          formattedLines.push('<ul style="margin: 4px 0; padding-left: 18px; list-style-type: disc;">');
          inList = true;
        }
        formattedLines.push(`<li style="margin: 2px 0;">${bulletMatch[2]}</li>`);
      } else {
        if (inList) {
          formattedLines.push("</ul>");
          inList = false;
        }
        formattedLines.push(line);
      }
    }
    if (inList) {
      formattedLines.push("</ul>");
    }

    return formattedLines.join("<br>").replace(/<\/ul><br>/g, "</ul>").replace(/<br><ul/g, "<ul");
  }

  // Root Container with scoped isolation
  const root = document.createElement("div");
  root.id = "swiss-rag-widget-root";
  root.style.cssText = "all: initial; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;";

  // Scoped styles for living waving mascot animation, floating Hello! text, and responsive sizing
  const styleEl = document.createElement("style");
  styleEl.textContent = `
    @keyframes botWave {
      0%, 100% {
        transform: translateY(0) rotate(0deg);
        filter: drop-shadow(0 6px 14px rgba(0, 229, 255, 0.45));
      }
      20% {
        transform: translateY(-6px) rotate(-6deg);
        filter: drop-shadow(0 8px 18px rgba(0, 229, 255, 0.62));
      }
      45% {
        transform: translateY(-9px) rotate(8deg);
        filter: drop-shadow(0 10px 22px rgba(0, 229, 255, 0.72));
      }
      70% {
        transform: translateY(-4px) rotate(-4deg);
        filter: drop-shadow(0 8px 18px rgba(0, 229, 255, 0.6));
      }
    }
    @keyframes helloFloat {
      0%, 100% {
        opacity: 0;
        transform: translateY(6px) scale(0.85);
      }
      25%, 55% {
        opacity: 1;
        transform: translateY(-4px) scale(1.05);
      }
      85% {
        opacity: 0;
        transform: translateY(-18px) scale(0.95);
      }
    }
    #swiss-rag-launcher-btn.swiss-rag-launcher-mascot {
      width: 88px;
      height: 88px;
      background: transparent !important;
      border: none !important;
      box-shadow: none !important;
      padding: 0 !important;
      outline: none !important;
      overflow: visible !important;
      transition: transform 0.25s ease;
    }
    #swiss-rag-launcher-btn.swiss-rag-launcher-mascot:hover {
      transform: scale(1.12) rotate(5deg);
    }
    #swiss-rag-launcher-btn .swiss-rag-bot-mascot {
      width: 88px;
      height: 88px;
      object-fit: contain;
      display: block;
      pointer-events: none;
      animation: botWave 2.6s ease-in-out infinite;
      filter: drop-shadow(0 6px 14px rgba(0, 229, 255, 0.45));
      transition: filter 0.25s ease;
    }
    #swiss-rag-launcher-btn:hover .swiss-rag-bot-mascot {
      animation-play-state: paused;
      filter: drop-shadow(0 8px 22px rgba(0, 229, 255, 0.85));
    }
    #swiss-rag-launcher-btn .swiss-rag-hello-badge {
      position: absolute;
      top: -12px;
      left: -10px;
      pointer-events: none;
      white-space: nowrap;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      font-weight: 800;
      font-size: 14px;
      letter-spacing: 0.02em;
      background: linear-gradient(135deg, #00d2ff 0%, #00f5a0 60%, #a8ff78 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      filter: drop-shadow(0 0 8px rgba(0, 245, 160, 0.55));
      animation: helloFloat 2.6s ease-in-out infinite;
      z-index: 2;
    }
    @media (max-width: 639px) {
      #swiss-rag-launcher-btn.swiss-rag-launcher-mascot,
      #swiss-rag-launcher-btn .swiss-rag-bot-mascot {
        width: 72px;
        height: 72px;
      }
      #swiss-rag-launcher-btn .swiss-rag-hello-badge {
        top: -10px;
        left: -8px;
        font-size: 13px;
      }
    }
  `;
  root.appendChild(styleEl);

  // Floating Toggle Button (Animated Mascot + Hello! badge when closed, clean X button when open)
  const btn = document.createElement("button");
  btn.id = "swiss-rag-launcher-btn";
  btn.setAttribute("aria-label", "Toggle AI Chat");

  const botAvatarImg = `
    <span class="swiss-rag-hello-badge">Hello!</span>
    <img class="swiss-rag-bot-mascot" src="${escapeHtml(iconUrl)}" alt="${escapeHtml(title)}" />
  `;
  const closeIcon = `
    <svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
    </svg>
  `;

  let isOpen = false;

  function renderLauncherButton() {
    if (isOpen) {
      btn.className = "";
      btn.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        width: 56px;
        height: 56px;
        border-radius: 50%;
        background: #0f172a;
        color: #ffffff;
        border: none;
        padding: 0;
        cursor: pointer;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.35);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 999999;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      `;
      btn.innerHTML = closeIcon;
    } else {
      btn.className = "swiss-rag-launcher-mascot";
      btn.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        border-radius: 0;
        background: transparent;
        border: none;
        padding: 0;
        cursor: pointer;
        box-shadow: none;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 999999;
      `;
      btn.innerHTML = botAvatarImg;
    }
  }

  btn.onmouseover = () => {
    if (isOpen) {
      btn.style.transform = "scale(1.06)";
    }
  };
  btn.onmouseout = () => {
    if (isOpen) {
      btn.style.transform = "scale(1)";
    }
  };

  renderLauncherButton();

  // Chat Window Popup
  const popup = document.createElement("div");
  popup.style.cssText = `
    position: fixed;
    bottom: 92px;
    right: 24px;
    width: 360px;
    height: 500px;
    max-width: calc(100vw - 32px);
    max-height: calc(100vh - 120px);
    background: #ffffff;
    border-radius: 16px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.05);
    display: none;
    flex-direction: column;
    overflow: hidden;
    z-index: 999999;
    box-sizing: border-box;
  `;

  popup.innerHTML = `
    <!-- Header -->
    <div style="background: #0f172a; color: #ffffff; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; border-top-left-radius: 16px; border-top-right-radius: 16px;">
      <div style="display: flex; align-items: center; gap: 10px;">
        <div style="width: 36px; height: 36px; border-radius: 50%; background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(0, 229, 255, 0.35); overflow: hidden; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
          <img src="${escapeHtml(iconUrl)}" alt="${escapeHtml(title)}" style="width: 100%; height: 100%; object-fit: contain; display: block;" />
        </div>
        <div>
          <div style="font-weight: 600; font-size: 14px; line-height: 1.2;">${escapeHtml(title)}</div>
          <div style="font-size: 11px; opacity: 0.7;">Project: ${escapeHtml(projectId)}</div>
        </div>
      </div>
      <button id="swiss-rag-close-btn" style="background: transparent; border: none; color: #ffffff; cursor: pointer; opacity: 0.8; padding: 4px; border-radius: 6px; display: flex; align-items: center;">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
        </svg>
      </button>
    </div>

    <!-- Messages Container -->
    <div id="swiss-rag-messages" style="flex: 1; overflow-y: auto; padding: 14px; background: #f8fafc; display: flex; flex-direction: column; gap: 12px; font-size: 13px; line-height: 1.5; color: #1e293b;">
      <div style="align-self: flex-start; max-width: 85%; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; border-top-left-radius: 2px; padding: 10px 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.03);">
        Hello! I can answer questions grounded in official <strong>${escapeHtml(projectId)}</strong> documents. How can I help?
      </div>
    </div>

    <!-- Input Footer -->
    <form id="swiss-rag-form" style="display: flex; gap: 8px; padding: 12px; background: #ffffff; border-top: 1px solid #e2e8f0; margin: 0;">
      <input 
        type="text" 
        id="swiss-rag-input" 
        placeholder="Ask a question..." 
        autocomplete="off"
        style="flex: 1; padding: 9px 12px; border: 1px solid #cbd5e1; border-radius: 10px; font-size: 13px; outline: none; background: #f8fafc; color: #0f172a; box-sizing: border-box;"
      />
      <button 
        type="submit" 
        id="swiss-rag-submit" 
        style="background: #0f172a; color: #ffffff; border: none; border-radius: 10px; padding: 0 14px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: opacity 0.2s;"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
        </svg>
      </button>
    </form>
  `;

  root.appendChild(popup);
  root.appendChild(btn);
  document.body.appendChild(root);

  // Toggle Popup Visibility
  function toggleWidget() {
    isOpen = !isOpen;
    popup.style.display = isOpen ? "flex" : "none";
    renderLauncherButton();
    if (isOpen) {
      setTimeout(() => {
        const inp = popup.querySelector("#swiss-rag-input");
        if (inp) inp.focus();
      }, 50);
    }
  }

  btn.onclick = toggleWidget;
  popup.querySelector("#swiss-rag-close-btn").onclick = toggleWidget;

  // Form Submit Handler
  const form = popup.querySelector("#swiss-rag-form");
  const input = popup.querySelector("#swiss-rag-input");
  const messagesEl = popup.querySelector("#swiss-rag-messages");
  const submitBtn = popup.querySelector("#swiss-rag-submit");

  form.onsubmit = async function (e) {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;

    // Append User Message
    const userMsg = document.createElement("div");
    userMsg.style.cssText = "align-self: flex-end; max-width: 85%; background: #0f172a; color: #ffffff; border-radius: 12px; border-top-right-radius: 2px; padding: 10px 12px; font-size: 13px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); word-break: break-word;";
    userMsg.innerText = query;
    messagesEl.appendChild(userMsg);
    input.value = "";
    messagesEl.scrollTop = messagesEl.scrollHeight;

    // Show Thinking Placeholder
    submitBtn.disabled = true;
    submitBtn.style.opacity = "0.5";
    const thinkingEl = document.createElement("div");
    thinkingEl.style.cssText = "align-self: flex-start; max-width: 85%; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; border-top-left-radius: 2px; padding: 10px 12px; font-size: 12px; color: #64748b; display: flex; align-items: center; gap: 6px;";
    thinkingEl.innerHTML = `<span>Searching & verifying...</span>`;
    messagesEl.appendChild(thinkingEl);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      const res = await fetch(`${apiUrl}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          question: query,
          project_id: projectId,
          session_id: sessionId,
          page_context: getPageContext(),
        }),
      });

      if (!res.ok) {
        let errorDetail = `Server error (${res.status}). Please try again.`;
        try {
          const errData = await res.json();
          if (errData && errData.detail) {
            errorDetail = errData.detail;
          }
        } catch (_) {
          // Non-JSON error response (e.g., 502/504 HTML gateway page)
        }
        throw new Error(errorDetail);
      }

      const data = await res.json();
      if (thinkingEl.parentNode) thinkingEl.remove();

      const aiMsg = document.createElement("div");
      aiMsg.style.cssText = "align-self: flex-start; max-width: 90%; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; border-top-left-radius: 2px; padding: 10px 12px; font-size: 13px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); word-break: break-word;";

      let badgesHtml = "";
      if (data.grounded) {
        badgesHtml += `<span style="display: inline-block; background: #dcfce7; color: #166534; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 10px; border: 1px solid #bbf7d0; margin-right: 4px;">✓ Grounded</span>`;
      } else {
        badgesHtml += `<span style="display: inline-block; background: #fef3c7; color: #92400e; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 10px; border: 1px solid #fde68a; margin-right: 4px;">⚠ Fallback</span>`;
      }

      if (data.sources && data.sources.length > 0) {
        badgesHtml += data.sources.map(s =>
          `<span style="display: inline-block; background: #f1f5f9; color: #475569; font-size: 10px; padding: 1px 6px; border-radius: 4px; border: 1px solid #e2e8f0; margin-right: 4px;">📄 ${escapeHtml(s)}</span>`
        ).join("");
      }

      aiMsg.innerHTML = `
        <div>${formatMarkdown(data.answer)}</div>
        ${badgesHtml ? `<div style="margin-top: 8px; padding-top: 6px; border-top: 1px solid #f1f5f9; display: flex; flex-wrap: wrap; gap: 4px;">${badgesHtml}</div>` : ""}
      `;
      messagesEl.appendChild(aiMsg);
    } catch (err) {
      if (thinkingEl.parentNode) thinkingEl.remove();
      const errMsg = document.createElement("div");
      errMsg.style.cssText = "align-self: flex-start; max-width: 85%; background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; border-radius: 12px; padding: 10px 12px; font-size: 12px;";
      if (err && err.name === "AbortError") {
        errMsg.innerText = "Request timed out after 30s. Please try again.";
      } else {
        errMsg.innerText = (err && err.message) ? err.message : "Network error. Please try again.";
      }
      messagesEl.appendChild(errMsg);
    } finally {
      clearTimeout(timeoutId);
      if (thinkingEl.parentNode) thinkingEl.remove();
      submitBtn.disabled = false;
      submitBtn.style.opacity = "1";
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  };
})();
