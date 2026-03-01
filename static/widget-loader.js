/**
 * Orgteh Widget Loader  —  widget-loader.js
 * ==========================================
 * يُضاف في موقع العميل هكذا:
 *   <script src="https://orgteh.com/static/widget-loader.js"
 *           data-widget-id="wx_XXXX"
 *           data-lang="ar"
 *           defer></script>
 *
 * 🔒 آليات الحماية المُضمّنة:
 *   1. يطلب Embed Token من /api/widget/:id/token ويجدّده كل 50 دقيقة
 *   2. يُرسل التوكن في header: X-Widget-Token
 *   3. يتحقق من وجود branding event في كل stream قبل عرض الرد
 *   4. يُنشئ الـ UI كاملاً بما فيه "Powered by Orgteh" (لا يمكن حذفه من الـ DOM)
 *   5. MutationObserver يراقب محاولات إخفاء شعار Orgteh ويستعيده
 */

(function () {
  "use strict";

  /* ─── إعدادات أساسية ──────────────────────────────────────────────── */
  const BASE_URL     = "https://orgteh.com";
  const SCRIPT_EL    = document.currentScript || (() => {
    const scripts = document.querySelectorAll('script[data-widget-id]');
    return scripts[scripts.length - 1];
  })();
  const WIDGET_ID    = SCRIPT_EL?.getAttribute("data-widget-id") || "";
  const LANG         = SCRIPT_EL?.getAttribute("data-lang") || "ar";
  const TOKEN_REFRESH_MS = 50 * 60 * 1000; // 50 دقيقة

  if (!WIDGET_ID) {
    console.warn("[Orgteh Widget] data-widget-id مفقود.");
    return;
  }

  /* ─── حالة الـ Widget ─────────────────────────────────────────────── */
  let _embedToken  = "";
  let _tokenTimer  = null;
  let _history     = [];
  let _isOpen      = false;
  let _isThinking  = false;

  /* ─── Layer 1: Embed Token ────────────────────────────────────────── */
  async function fetchToken() {
    try {
      const res = await fetch(`${BASE_URL}/api/widget/${WIDGET_ID}/token`, {
        credentials: "omit",
      });
      if (!res.ok) throw new Error("token_fetch_failed");
      const data = await res.json();
      _embedToken = data.token || "";
    } catch (e) {
      console.error("[Orgteh Widget] Token error:", e.message);
      _embedToken = "";
    }
  }

  function startTokenRefresh() {
    fetchToken();
    if (_tokenTimer) clearInterval(_tokenTimer);
    _tokenTimer = setInterval(fetchToken, TOKEN_REFRESH_MS);
  }

  /* ─── Layer 3: Branding Protection (MutationObserver) ───────────── */
  function _guardBranding(brandingEl) {
    if (!brandingEl) return;
    const observer = new MutationObserver(() => {
      // إذا تغيّر style أو حُذف ← نستعيده
      brandingEl.style.cssText = BRANDING_STYLE;
      brandingEl.innerHTML     = BRANDING_HTML;
    });
    observer.observe(brandingEl, {
      attributes:    true,
      childList:     true,
      characterData: true,
      subtree:       true,
    });
    // راقب أيضاً الـ parent لمنع إزالة العنصر نفسه
    const parent = brandingEl.parentElement;
    if (parent) {
      const parentObserver = new MutationObserver((mutations) => {
        for (const m of mutations) {
          if (m.type === "childList") {
            const removed = Array.from(m.removedNodes);
            if (removed.includes(brandingEl)) {
              parent.appendChild(brandingEl); // أعِده فوراً
            }
          }
        }
      });
      parentObserver.observe(parent, { childList: true });
    }
  }

  const BRANDING_STYLE = [
    "display:flex !important",
    "align-items:center",
    "justify-content:center",
    "padding:8px",
    "font-size:11px",
    "color:#9ca3af",
    "background:#f9fafb",
    "border-top:1px solid #e5e7eb",
    "visibility:visible !important",
    "opacity:1 !important",
  ].join(";");

  const BRANDING_HTML = `<a href="https://orgteh.com" target="_blank" rel="noopener"
    style="color:#7c3aed;text-decoration:none;font-weight:600;">
    ⚡ Powered by Orgteh
  </a>`;

  /* ─── بناء الـ UI ─────────────────────────────────────────────────── */
  function buildWidget() {
    // إزالة أي widget سابق
    document.getElementById("orgteh-widget-root")?.remove();

    const root = document.createElement("div");
    root.id = "orgteh-widget-root";
    root.innerHTML = `
      <style>
        #orgteh-widget-root * { box-sizing: border-box; font-family: inherit; }
        #orgteh-fab {
          position:fixed; bottom:24px; ${LANG==="ar"?"left":"right"}:24px;
          width:56px; height:56px; border-radius:50%;
          background:#7c3aed; color:#fff; border:none;
          cursor:pointer; box-shadow:0 4px 20px rgba(124,58,237,.4);
          font-size:22px; display:flex; align-items:center; justify-content:center;
          z-index:2147483640; transition:transform .2s;
        }
        #orgteh-fab:hover { transform:scale(1.1); }
        #orgteh-panel {
          position:fixed; bottom:90px; ${LANG==="ar"?"left":"right"}:24px;
          width:360px; max-height:520px; border-radius:16px;
          background:#fff; box-shadow:0 8px 40px rgba(0,0,0,.18);
          display:flex; flex-direction:column; overflow:hidden;
          z-index:2147483639; transition:opacity .2s, transform .2s;
        }
        #orgteh-panel.hidden { opacity:0; pointer-events:none; transform:translateY(12px); }
        #orgteh-header {
          background:#7c3aed; color:#fff; padding:14px 16px;
          display:flex; align-items:center; justify-content:space-between;
        }
        #orgteh-header h3 { margin:0; font-size:15px; font-weight:700; }
        #orgteh-close { background:none; border:none; color:#fff; font-size:18px; cursor:pointer; }
        #orgteh-messages {
          flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px;
          min-height:200px;
        }
        .orgteh-msg { max-width:82%; padding:10px 13px; border-radius:12px; font-size:14px; line-height:1.5; }
        .orgteh-msg.user { background:#7c3aed; color:#fff; align-self:flex-end; border-radius:12px 12px 2px 12px; }
        .orgteh-msg.bot  { background:#f3f4f6; color:#111; align-self:flex-start; border-radius:12px 12px 12px 2px; }
        #orgteh-input-area { display:flex; gap:8px; padding:12px; border-top:1px solid #e5e7eb; }
        #orgteh-input {
          flex:1; border:1px solid #e5e7eb; border-radius:10px; padding:9px 12px;
          font-size:14px; outline:none; resize:none;
          direction:${LANG==="ar"?"rtl":"ltr"};
        }
        #orgteh-input:focus { border-color:#7c3aed; }
        #orgteh-send {
          background:#7c3aed; color:#fff; border:none; border-radius:10px;
          padding:0 16px; cursor:pointer; font-size:15px; font-weight:700;
        }
        #orgteh-send:disabled { opacity:.5; cursor:not-allowed; }
        #orgteh-branding {
          ${BRANDING_STYLE};
        }
      </style>

      <button id="orgteh-fab" aria-label="Chat">💬</button>

      <div id="orgteh-panel" class="hidden">
        <div id="orgteh-header">
          <h3 id="orgteh-title">مساعد ذكي</h3>
          <button id="orgteh-close">✕</button>
        </div>
        <div id="orgteh-messages"></div>
        <div id="orgteh-input-area">
          <textarea id="orgteh-input" rows="1"
            placeholder="${LANG==="ar"?"اكتب رسالتك...":"Type your message..."}"></textarea>
          <button id="orgteh-send">➤</button>
        </div>
        <!-- Layer 3: Branding — لا تحذف هذا العنصر أبداً -->
        <div id="orgteh-branding">${BRANDING_HTML}</div>
      </div>
    `;

    document.body.appendChild(root);

    // ── Layer 3: تفعيل المراقبة على الـ branding ───────────────────────
    const brandingEl = root.querySelector("#orgteh-branding");
    setTimeout(() => _guardBranding(brandingEl), 500);

    // ── أحداث الـ UI ────────────────────────────────────────────────────
    root.querySelector("#orgteh-fab").onclick   = togglePanel;
    root.querySelector("#orgteh-close").onclick = togglePanel;
    root.querySelector("#orgteh-send").onclick  = sendMessage;
    root.querySelector("#orgteh-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });

    // رسالة ترحيبية
    appendMessage("bot", LANG === "ar"
      ? "مرحباً! كيف يمكنني مساعدتك اليوم؟"
      : "Hello! How can I help you today?");
  }

  function togglePanel() {
    _isOpen = !_isOpen;
    document.getElementById("orgteh-panel")?.classList.toggle("hidden", !_isOpen);
    document.getElementById("orgteh-fab").textContent = _isOpen ? "✕" : "💬";
  }

  function appendMessage(role, text) {
    const msgs = document.getElementById("orgteh-messages");
    if (!msgs) return;
    const el = document.createElement("div");
    el.className = `orgteh-msg ${role}`;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  /* ─── Layer 3: Branding Verification in Stream ────────────────────── */
  async function sendMessage() {
    const input = document.getElementById("orgteh-input");
    const sendBtn = document.getElementById("orgteh-send");
    const msg = (input?.value || "").trim();
    if (!msg || _isThinking) return;

    input.value = "";
    appendMessage("user", msg);
    _history.push({ role: "user", content: msg });

    _isThinking = true;
    if (sendBtn) sendBtn.disabled = true;

    const botEl  = appendMessage("bot", "…");
    let   buffer = "";
    let   _brandingVerified = false;  // يجب أن يصل branding event

    try {
      const res = await fetch(`${BASE_URL}/api/widget/${WIDGET_ID}/chat`, {
        method: "POST",
        headers: {
          "Content-Type":  "application/json",
          "X-Widget-Token": _embedToken,   // Layer 1: Embed Token
        },
        body: JSON.stringify({
          message: msg,
          history: _history.slice(-8),
          lang:    LANG,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (botEl) botEl.textContent = err.error || "حدث خطأ، حاول مجدداً.";
        return;
      }

      const reader = res.body.getReader();
      const dec    = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = dec.decode(value, { stream: true });
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") break;
          try {
            const obj = JSON.parse(raw);

            // ── Layer 3: التحقق من Branding Event ──────────────────────
            if (obj.type === "branding" && obj.required === true) {
              _brandingVerified = true;
              continue; // لا نعرضه في الرسائل
            }

            const delta = obj.choices?.[0]?.delta?.content;
            if (delta) {
              buffer += delta;
              if (botEl) botEl.textContent = buffer;
            }
          } catch (_) {}
        }
      }

      // ── Layer 3: إذا لم يصل branding event → خطأ أمني ───────────────
      if (!_brandingVerified) {
        console.error("[Orgteh Widget] Branding verification failed!");
        if (botEl) botEl.textContent = "⚠️ خطأ في التحقق من الأمان. تواصل مع الدعم.";
        return;
      }

      if (buffer) {
        _history.push({ role: "assistant", content: buffer });
      }

    } catch (e) {
      if (botEl) botEl.textContent = "تعذّر الاتصال. تحقق من الإنترنت.";
    } finally {
      _isThinking = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  /* ─── تهيئة ──────────────────────────────────────────────────────── */
  function init() {
    startTokenRefresh();   // Layer 1: ابدأ جلب التوكن
    buildWidget();         // بناء الـ UI
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();
