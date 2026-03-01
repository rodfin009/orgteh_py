import os
import re
import json
import secrets
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional

# ──────────────────────────────────────────────
# المتغيرات العالمية (تُستدعى من main.py)
# ──────────────────────────────────────────────
from database import redis, get_db_connection

# ============================================================
# هيكل بيانات الـ Widget
# ============================================================
def _default_widget(owner_email: str, name: str = "مساعدي الذكي") -> dict:
    return {
        "id":              _new_widget_id(),
        "owner":           owner_email,
        "name":            name,
        "created_at":      datetime.utcnow().isoformat(),
        "knowledge_mode":  "manual",   # manual | url | files | all
        "manual_content":  "",
        "urls":            [],
        "crawled_text":    "",
        "crawled_at":      None,
        "personality":     "friendly", # friendly | formal | technical | sales
        "color":           "#7c3aed",
        "assistant_name_ar": "مساعد ذكي",
        "assistant_name_en": "Smart Assistant",
        "welcome_ar":      "مرحباً! كيف يمكنني مساعدتك اليوم؟",
        "welcome_en":      "Hello! How can I help you today?",
        "position":        "right",    # right | left
        "widget_style":    "icon",     # icon | bubble | fullchat
        "widget_size":     "medium",   # small | medium | large
        "show_branding":   True,
        "daily_usage":     0,
        "total_usage":     0,
        "last_reset_date": str(datetime.utcnow().date()),
        "active":          True,
    }

def _new_widget_id() -> str:
    return "wx_" + secrets.token_urlsafe(10)

# ============================================================
# Redis helpers
# ============================================================
WIDGET_PREFIX  = "widget:"
USER_WIDGETS   = "user_widgets:"

def _save_widget(w: dict):
    if not redis:
        return False
    try:
        redis.set(f"{WIDGET_PREFIX}{w['id']}", json.dumps(w, ensure_ascii=False))
        # أضف المعرّف لقائمة المستخدم (لا تكرار)
        key = f"{USER_WIDGETS}{w['owner']}"
        ids = _get_user_widget_ids(w['owner'])
        if w['id'] not in ids:
            redis.rpush(key, w['id'])
        return True
    except Exception as e:
        print(f"[Widget] save error: {e}")
        return False

def _get_widget(widget_id: str) -> Optional[dict]:
    if not redis:
        return None
    try:
        raw = redis.get(f"{WIDGET_PREFIX}{widget_id}")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None

def _delete_widget_from_redis(widget_id: str, owner_email: str):
    if not redis:
        return
    try:
        redis.delete(f"{WIDGET_PREFIX}{widget_id}")
        key = f"{USER_WIDGETS}{owner_email}"
        redis.lrem(key, 0, widget_id)
    except Exception as e:
        print(f"[Widget] delete error: {e}")

def _get_user_widget_ids(email: str) -> list:
    if not redis:
        return []
    try:
        ids = redis.lrange(f"{USER_WIDGETS}{email}", 0, -1)
        return [i.decode() if isinstance(i, bytes) else i for i in (ids or [])]
    except Exception:
        return []

def get_user_widgets(email: str) -> list:
    ids = _get_user_widget_ids(email)
    widgets = []
    for wid in ids:
        w = _get_widget(wid)
        if w:
            # لا نرسل crawled_text الكبير للقائمة
            w_safe = {k: v for k, v in w.items() if k != "crawled_text"}
            widgets.append(w_safe)
    return widgets

# ============================================================
# CRUD
# ============================================================
def create_widget(owner_email: str, name: str) -> dict:
    w = _default_widget(owner_email, name)
    _save_widget(w)
    return w

def update_widget(widget_id: str, owner_email: str, updates: dict) -> Optional[dict]:
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return None
    # حقول مسموح تعديلها
    allowed = {
        "name", "knowledge_mode", "manual_content", "urls",
        "personality", "color", "assistant_name_ar", "assistant_name_en",
        "welcome_ar", "welcome_en", "position", "active",
        "widget_style", "widget_size"
    }
    for k, v in updates.items():
        if k in allowed:
            w[k] = v
    _save_widget(w)
    return w

def delete_widget(widget_id: str, owner_email: str) -> bool:
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return False
    _delete_widget_from_redis(widget_id, owner_email)
    return True

# ============================================================
# Web Crawling (بسيط، بدون مكتبات خارجية)
# ============================================================

class _TextExtractor(HTMLParser):
    """يستخرج النص الصافي من HTML ويتجاهل العلامات."""
    def __init__(self):
        super().__init__()
        self.result = []
        self._skip_depth = 0   # عداد للتعامل مع الوسوم المتداخلة بشكل صحيح
        self._skip_tags = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if len(text) > 10:  # تجاهل النصوص القصيرة جداً
                self.result.append(text)

def _extract_text_from_html(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = " ".join(parser.result)
    # تنظيف: أسطر متعددة، مسافات زائدة
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _fetch_url(url: str, timeout: int = 10) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; OrgBot/1.0; +https://orgteh.com)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.5",
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # اكتشاف الترميز
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip().split(";")[0]
            try:
                return raw.decode(charset, errors="replace")
            except Exception:
                return raw.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[Widget Crawl] Error fetching {url}: {e}")
        return None

def _extract_links(html: str, base_url: str) -> list:
    """يستخرج روابط من نفس النطاق."""
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"
    pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    links = []
    for match in pattern.findall(html):
        href = match.strip()
        if href.startswith("http"):
            if parsed_base.netloc in href:
                links.append(href.split("#")[0])
        elif href.startswith("/"):
            links.append(base_domain + href.split("#")[0])
    return list(set(links))[:20]  # حد أقصى 20 رابط

async def crawl_url(widget_id: str, owner_email: str, url: str, max_pages: int = 5) -> dict:
    """
    يزحف على الموقع ويخزّن النص المستخرج في Redis.
    يُعيد {"ok": True, "pages": N, "chars": N} أو {"error": "..."}
    """
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return {"error": "Widget not found"}

    visited = set()
    all_text = []
    queue = [url]

    for _ in range(max_pages):
        if not queue:
            break
        current_url = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        html = await asyncio.get_event_loop().run_in_executor(None, _fetch_url, current_url)
        if not html:
            continue

        text = _extract_text_from_html(html)
        if text:
            all_text.append(f"[صفحة: {current_url}]\n{text}")

        # أضف روابط من نفس النطاق
        if len(visited) < max_pages:
            new_links = _extract_links(html, url)
            for link in new_links:
                if link not in visited and link not in queue:
                    queue.append(link)

    combined = "\n\n---\n\n".join(all_text)
    # حد 50,000 حرف
    combined = combined[:50000]

    w["crawled_text"] = combined
    w["crawled_at"]   = datetime.utcnow().isoformat()
    if url not in w.get("urls", []):
        w.setdefault("urls", []).append(url)
    _save_widget(w)

    return {
        "ok":    True,
        "pages": len(visited),
        "chars": len(combined),
        "url":   url
    }

# ============================================================
# RAG Chat (مبسّط)
# ============================================================

def _build_system_prompt(w: dict, lang: str = "ar") -> str:
    """يبني system prompt يدمج معلومات الـ Widget."""
    personality_map = {
        "friendly":   {"ar": "ودود ومرح وقريب من الناس", "en": "friendly, warm and approachable"},
        "formal":     {"ar": "رسمي ومهني وموضوعي",       "en": "formal, professional and objective"},
        "technical":  {"ar": "تقني ودقيق في التفاصيل",    "en": "technical, precise and detail-oriented"},
        "sales":      {"ar": "مقنع ومحفّز للشراء",         "en": "persuasive and conversion-focused"},
    }
    personality = personality_map.get(w.get("personality", "friendly"), personality_map["friendly"])
    assistant_name = w.get("assistant_name_ar" if lang == "ar" else "assistant_name_en", "مساعد ذكي")

    # جمع مصادر المعرفة
    knowledge_parts = []
    mode = w.get("knowledge_mode", "manual")

    if mode in ("manual", "all") and w.get("manual_content", "").strip():
        knowledge_parts.append("=== معلومات يدوية ===\n" + w["manual_content"].strip())

    if mode in ("url", "all") and w.get("crawled_text", "").strip():
        # أخذ أول 8000 حرف من المحتوى الزاحف
        crawled = w["crawled_text"][:8000]
        knowledge_parts.append("=== محتوى الموقع الإلكتروني ===\n" + crawled)

    knowledge_section = "\n\n".join(knowledge_parts) if knowledge_parts else ""

    if lang == "ar":
        prompt = f"""أنت {assistant_name}، مساعد ذكي متخصص. أسلوبك: {personality['ar']}.
يجب أن ترد دائماً باللغة العربية ما لم يتحدث المستخدم بلغة أخرى.
كن مختصراً ومفيداً. لا تخترع معلومات غير موجودة في المصادر أدناه.
إذا لم تجد الإجابة في المصادر، قل ذلك بلطف واقترح التواصل المباشر."""
    else:
        prompt = f"""You are {assistant_name}, a specialized AI assistant. Your style: {personality['en']}.
Always reply in English unless the user writes in another language.
Be concise and helpful. Do not invent information not found in the sources below.
If you cannot find the answer in the sources, say so politely and suggest direct contact."""

    if knowledge_section:
        prompt += f"\n\n{knowledge_section}"

    prompt += "\n\nPowered by Orgteh Infra"
    return prompt

async def widget_chat_stream(widget_id: str, message: str, history: list, lang: str = "ar"):
    """
    نقطة المحادثة للـ Widget (عامة، بدون مصادقة مستخدم).
    تُعيد generator يولّد أجزاء SSE عبر NVIDIA Llama 3.1-8b-instruct.
    """
    import os
    import httpx

    w = _get_widget(widget_id)
    if not w:
        yield b'data: {"error": "Widget not found"}\n\n'
        return
    if not w.get("active", True):
        yield b'data: {"error": "Widget is disabled"}\n\n'
        return

    # تحديث الاستخدام
    _increment_usage(w)

    system_prompt = _build_system_prompt(w, lang)

    messages = [{"role": "user", "content": system_prompt + "\n\n---"}]
    for h in history[-6:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    nvidia_api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not nvidia_api_key:
        yield b'data: {"error": "NVIDIA API key not configured"}\n\n'
        return

    payload = {
        "model":       "meta/llama-3.1-8b-instruct",
        "messages":    messages,
        "temperature": 0.3,
        "top_p":       0.7,
        "max_tokens":  4096,
        "stream":      True,
    }

    headers = {
        "Authorization": f"Bearer {nvidia_api_key}",
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield json.dumps({"error": f"NVIDIA API error {resp.status_code}"}).encode()
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        break
                    yield f"data: {raw}\n\n".encode("utf-8")
    except Exception as e:
        yield json.dumps({"error": str(e)}).encode("utf-8")

def _increment_usage(w: dict):
    today = str(datetime.utcnow().date())
    if w.get("last_reset_date") != today:
        w["daily_usage"]     = 0
        w["last_reset_date"] = today
    w["daily_usage"]  = w.get("daily_usage", 0) + 1
    w["total_usage"]  = w.get("total_usage", 0) + 1
    _save_widget(w)

# ============================================================
# Admin helpers
# ============================================================
def get_all_widgets_admin() -> list:
    """يُعيد كل الودجتات (للأدمن)."""
    if not redis:
        return []
    try:
        keys = redis.keys(f"{WIDGET_PREFIX}*")
        result = []
        for k in (keys or []):
            raw = redis.get(k)
            if raw:
                w = json.loads(raw)
                result.append({k2: v for k2, v in w.items() if k2 != "crawled_text"})
        return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)
    except Exception:
        return []

def get_widget_stats_admin() -> dict:
    widgets = get_all_widgets_admin()
    return {
        "total":        len(widgets),
        "active":       sum(1 for w in widgets if w.get("active")),
        "total_usage":  sum(w.get("total_usage", 0) for w in widgets),
        "daily_usage":  sum(w.get("daily_usage", 0) for w in widgets),
    }
