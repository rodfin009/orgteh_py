import os
import re
import hmac
import json
import time
import secrets
import hashlib
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

from database import redis, get_db_connection


# ════════════════════════════════════════════════════════════════
# ██  SECURITY — Layer 1 : Embed Token (HMAC-SHA256)
# ════════════════════════════════════════════════════════════════
# رمز موقّع مرتبط بـ (widget_id + origin + نافذة الوقت)
# يتجدد كل ساعة — widget-loader.js يجلبه تلقائياً
# ✅ ضع WIDGET_SIGNING_SECRET في env في الإنتاج

_SIGNING_SECRET = os.environ.get(
    "WIDGET_SIGNING_SECRET",
    "orgteh-widget-signing-secret-change-in-prod-2024"
).encode()
_TOKEN_TTL = 3600   # ثانية (ساعة واحدة)


def _time_bucket() -> int:
    return int(time.time()) // _TOKEN_TTL


def generate_embed_token(widget_id: str, origin: str) -> str:
    bucket = _time_bucket()
    msg    = f"{widget_id}:{origin}:{bucket}".encode()
    sig    = hmac.new(_SIGNING_SECRET, msg, hashlib.sha256).hexdigest()
    return f"{bucket}.{sig}"


def _verify_embed_token(token: str, widget_id: str, origin: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        bucket_str, received = token.split(".", 1)
        bucket = int(bucket_str)
    except (ValueError, AttributeError):
        return False
    current = _time_bucket()
    if bucket not in (current, current - 1):   # نافذة حالية أو سابقة
        return False
    msg      = f"{widget_id}:{origin}:{bucket}".encode()
    expected = hmac.new(_SIGNING_SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


# ════════════════════════════════════════════════════════════════
# ██  SECURITY — Layer 2 : Origin / Domain Validation
# ════════════════════════════════════════════════════════════════
# كل widget له قائمة allowed_domains — أي نطاق خارجها يُرفض
# الطلبات بلا Origin header (curl / Postman) تُرفض دائماً

_ORGTEH_ORIGINS = [
    "localhost:8000",
    "localhost:3000",
    "127.0.0.1:8000",
    # أضف النطاقات التالية للاختبار:
    "cdpn.io",       # الخاص بـ CodePen
    "replit.dev",    # الخاص بـ Replit الجديد
    "repl.co"        # الخاص بـ Replit القديم
]


def _normalize_origin(o: str) -> str:
    o = o.lower().strip()
    for p in ("https://", "http://"):
        if o.startswith(p):
            o = o[len(p):]
    return o.rstrip("/")


def _is_origin_allowed(origin: str, allowed_domains: list) -> bool:
    if not origin:
        return False
    n = _normalize_origin(origin)
    if n in _ORGTEH_ORIGINS:          # معاينة لوحة التحكم دائماً مسموحة
        return True
    if not allowed_domains:
        return False
    for domain in allowed_domains:
        d = _normalize_origin(domain)
        if d.startswith("*."):
            suffix = d[1:]            # .example.com
            if n.endswith(suffix) or n == d[2:]:
                return True
        else:
            if n == d or n == f"www.{d}" or f"www.{n}" == d:
                return True
    return False


# ════════════════════════════════════════════════════════════════
# ██  SECURITY — Layer 3 : Branding Lock
# ════════════════════════════════════════════════════════════════
# "Powered by Orgteh" مقفول على ثلاثة مستويات:
#   أ) show_branding = True دائماً في Redis (لا يمكن تغييره)
#   ب) مضمون في system prompt في كل طلب
#   ج) BRANDING_SSE_EVENT يُرسَل أول شيء في كل stream

_BRANDING_TEXT      = "Powered by Orgteh"
_BRANDING_URL       = "https://orgteh.com"
_BRANDING_SSE_EVENT = (
    f'data: {{"type":"branding","text":"{_BRANDING_TEXT}",'
    f'"url":"{_BRANDING_URL}","required":true}}\n\n'
).encode("utf-8")

_BRANDING_PROTECTED = {"show_branding", "branding", "powered_by", "hide_branding"}


def _strip_branding_fields(updates: dict) -> dict:
    """يحذف أي حقل يحاول لمس الـ branding من طلب التحديث."""
    return {k: v for k, v in updates.items() if k not in _BRANDING_PROTECTED}


def _lock_branding(w: dict) -> dict:
    """يُجبر show_branding = True — يُستدعى قبل كل حفظ."""
    w["show_branding"] = True
    return w


def _inject_branding_prompt(prompt: str) -> str:
    """يضمن وجود سطر Branding في system prompt حتى لو حُذف."""
    if _BRANDING_TEXT not in prompt:
        prompt += f"\n\n{_BRANDING_TEXT} — {_BRANDING_URL}"
    return prompt


# ════════════════════════════════════════════════════════════════
# ██  SECURITY — Layer 4 : IP-level Rate Limit (Redis)
# ════════════════════════════════════════════════════════════════
# حد لكل IP لمنع الاستخدام المفرط من نفس الجهاز
# (الطابور الرئيسي يتعامل مع الضغط الكلي لكل widget)

_IP_LIMIT_PER_MIN = 15   # طلب/دقيقة لكل IP عبر كل الـ widgets


def _check_ip_rate(client_ip: str) -> bool:
    """يُعيد True إذا الـ IP ضمن الحد، False إذا تجاوزه."""
    if not redis:
        return True
    try:
        key   = f"wip:{client_ip}"
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 60)
        return count <= _IP_LIMIT_PER_MIN
    except Exception:
        return True


# ════════════════════════════════════════════════════════════════
# ██  SECURITY — Layer 5 : Audit Log + IP Block
# ════════════════════════════════════════════════════════════════

def _log_suspicious(reason: str, widget_id: str, client_ip: str,
                    origin: str = "", extra: str = "") -> None:
    if not redis:
        return
    try:
        event = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason, "widget_id": widget_id,
            "ip": client_ip, "origin": origin, "extra": extra,
        }, ensure_ascii=False)
        redis.lpush("widget_security_log", event)
        redis.ltrim("widget_security_log", 0, 999)
        abuse_key = f"wabuse:{client_ip}"
        c = redis.incr(abuse_key)
        if c == 1:
            redis.expire(abuse_key, 3600)
        if c >= 50:
            redis.setex(f"wblocked:{client_ip}", 3600, "1")
    except Exception:
        pass


def _is_ip_blocked(client_ip: str) -> bool:
    if not redis:
        return False
    try:
        return bool(redis.get(f"wblocked:{client_ip}"))
    except Exception:
        return False


def get_security_log(limit: int = 100) -> list:
    """للأدمن — يُعيد آخر N حدث مشبوه."""
    if not redis:
        return []
    try:
        items = redis.lrange("widget_security_log", 0, limit - 1)
        return [json.loads(i) for i in (items or [])]
    except Exception:
        return []


def unblock_ip(client_ip: str) -> bool:
    """للأدمن — يرفع الحظر عن IP."""
    if not redis:
        return False
    try:
        redis.delete(f"wblocked:{client_ip}")
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════
# ██  QUEUE — Token Bucket (منفصل عن API، داخلي تماماً)
# ════════════════════════════════════════════════════════════════
#  20 طلب/دقيقة لكل widget_id
#  الطلب الزائد ينتظر في طابور (حد 40) حتى 30 ثانية
#  المستخدم لا يرى أي شيء — شفاف بالكامل

_Q_MAX_RPS      = 20
_Q_WINDOW_SEC   = 60
_Q_QUEUE_MAX    = 40
_Q_MAX_WAIT_SEC = 30

_buckets: dict = {}
_buckets_lock  = asyncio.Lock()


def _get_bucket(widget_id: str) -> dict:
    if widget_id not in _buckets:
        _buckets[widget_id] = {
            "tokens":      _Q_MAX_RPS,
            "last_refill": time.monotonic(),
            "sem":         asyncio.Semaphore(_Q_QUEUE_MAX),
        }
    return _buckets[widget_id]


def _refill(bucket: dict) -> None:
    now     = time.monotonic()
    elapsed = now - bucket["last_refill"]
    refill  = int((elapsed / _Q_WINDOW_SEC) * _Q_MAX_RPS)
    if refill >= 1:
        bucket["tokens"]      = min(_Q_MAX_RPS, bucket["tokens"] + refill)
        bucket["last_refill"] = now


async def _queue_acquire(widget_id: str) -> bool:
    async with _buckets_lock:
        b = _get_bucket(widget_id)
        _refill(b)
        if b["tokens"] > 0:
            b["tokens"] -= 1
            return True
        sem = b["sem"]
        if sem._value == 0:   # type: ignore[attr-defined]
            return False      # الطابور ممتلئ — رفض صامت

    try:
        await asyncio.wait_for(sem.acquire(), timeout=_Q_MAX_WAIT_SEC)
        async with _buckets_lock:
            _refill(b)
            if b["tokens"] > 0:
                b["tokens"] -= 1
                sem.release()
                return True
            sem.release()
        return False
    except asyncio.TimeoutError:
        return False


# ════════════════════════════════════════════════════════════════
# هيكل بيانات الـ Widget
# ════════════════════════════════════════════════════════════════

def _new_widget_id() -> str:
    return "wx_" + secrets.token_urlsafe(10)


def _default_widget(owner_email: str, name: str = "مساعدي الذكي") -> dict:
    return {
        "id":                _new_widget_id(),
        "owner":             owner_email,
        "name":              name,
        "created_at":        datetime.utcnow().isoformat(),
        "knowledge_mode":    "manual",
        "manual_content":    "",
        "urls":              [],
        "crawled_text":      "",
        "crawled_at":        None,
        "personality":       "friendly",
        "color":             "#7c3aed",
        "assistant_name_ar": "مساعد ذكي",
        "assistant_name_en": "Smart Assistant",
        "welcome_ar":        "مرحباً! كيف يمكنني مساعدتك اليوم؟",
        "welcome_en":        "Hello! How can I help you today?",
        "position":          "right",
        "widget_style":      "icon",
        "widget_size":       "medium",
        "show_branding":     True,        # ⛔ مقفول — لا يتغير أبداً
        "allowed_domains":   [],          # النطاقات المصرّح بها للتضمين
        "daily_usage":       0,
        "total_usage":       0,
        "last_reset_date":   str(datetime.utcnow().date()),
        "active":            True,
    }


# ════════════════════════════════════════════════════════════════
# Redis helpers
# ════════════════════════════════════════════════════════════════

WIDGET_PREFIX = "widget:"
USER_WIDGETS  = "user_widgets:"


def _save_widget(w: dict):
    if not redis:
        return False
    w = _lock_branding(w)   # ⛔ Branding مقفول دائماً قبل أي حفظ
    try:
        redis.set(f"{WIDGET_PREFIX}{w['id']}", json.dumps(w, ensure_ascii=False))
        key = f"{USER_WIDGETS}{w['owner']}"
        ids = _get_user_widget_ids(w["owner"])
        if w["id"] not in ids:
            redis.rpush(key, w["id"])
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
        redis.lrem(f"{USER_WIDGETS}{owner_email}", 0, widget_id)
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
    ids     = _get_user_widget_ids(email)
    widgets = []
    for wid in ids:
        w = _get_widget(wid)
        if w:
            widgets.append({k: v for k, v in w.items() if k != "crawled_text"})
    return widgets


# ════════════════════════════════════════════════════════════════
# CRUD
# ════════════════════════════════════════════════════════════════

def create_widget(owner_email: str, name: str) -> dict:
    w = _default_widget(owner_email, name)
    _save_widget(w)
    return w


def update_widget(widget_id: str, owner_email: str, updates: dict) -> Optional[dict]:
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return None

    # ⛔ احذف أي محاولة لتغيير الـ branding قبل أي شيء
    updates = _strip_branding_fields(updates)

    allowed = {
        "name", "knowledge_mode", "manual_content", "urls",
        "personality", "color", "assistant_name_ar", "assistant_name_en",
        "welcome_ar", "welcome_en", "position", "active",
        "widget_style", "widget_size",
        "allowed_domains",
    }
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k == "allowed_domains":
            if isinstance(v, list) and all(isinstance(d, str) for d in v):
                w[k] = v[:10]   # حد 10 نطاقات
        else:
            w[k] = v

    _save_widget(w)   # _lock_branding مدمج داخل _save_widget
    return w


def delete_widget(widget_id: str, owner_email: str) -> bool:
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return False
    _delete_widget_from_redis(widget_id, owner_email)
    return True


# ════════════════════════════════════════════════════════════════
# Web Crawling
# ════════════════════════════════════════════════════════════════

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result      = []
        self._skip_depth = 0
        self._skip_tags  = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if len(text) > 10:
                self.result.append(text)


def _extract_text_from_html(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(parser.result)).strip()


def _fetch_url(url: str, timeout: int = 10) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent":      "Mozilla/5.0 (compatible; OrgBot/1.0; +https://orgteh.com)",
                "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.5",
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw          = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            charset      = "utf-8"
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
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"
    links       = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = href.strip()
        if href.startswith("http"):
            if parsed_base.netloc in href:
                links.append(href.split("#")[0])
        elif href.startswith("/"):
            links.append(base_domain + href.split("#")[0])
    return list(set(links))[:20]


async def crawl_url(widget_id: str, owner_email: str, url: str, max_pages: int = 5) -> dict:
    w = _get_widget(widget_id)
    if not w or w.get("owner") != owner_email:
        return {"error": "Widget not found"}

    visited, all_text, queue = set(), [], [url]

    for _ in range(max_pages):
        if not queue:
            break
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        html = await asyncio.get_event_loop().run_in_executor(None, _fetch_url, current)
        if not html:
            continue
        text = _extract_text_from_html(html)
        if text:
            all_text.append(f"[صفحة: {current}]\n{text}")
        if len(visited) < max_pages:
            for link in _extract_links(html, url):
                if link not in visited and link not in queue:
                    queue.append(link)

    combined           = "\n\n---\n\n".join(all_text)[:50000]
    w["crawled_text"]  = combined
    w["crawled_at"]    = datetime.utcnow().isoformat()
    if url not in w.get("urls", []):
        w.setdefault("urls", []).append(url)
    _save_widget(w)

    return {"ok": True, "pages": len(visited), "chars": len(combined), "url": url}


# ════════════════════════════════════════════════════════════════
# RAG Chat
# ════════════════════════════════════════════════════════════════

_MAX_MSG_LEN = 800

_PERSONALITY_MAP = {
    "friendly":  {"ar": "ودود ومرح وقريب من الناس",  "en": "friendly, warm and approachable"},
    "formal":    {"ar": "رسمي ومهني وموضوعي",          "en": "formal, professional and objective"},
    "technical": {"ar": "تقني ودقيق في التفاصيل",     "en": "technical, precise and detail-oriented"},
    "sales":     {"ar": "مقنع ومحفّز للشراء",           "en": "persuasive and conversion-focused"},
}


def _build_system_prompt(w: dict, lang: str = "ar") -> str:
    p    = _PERSONALITY_MAP.get(w.get("personality", "friendly"), _PERSONALITY_MAP["friendly"])
    name = w.get("assistant_name_ar" if lang == "ar" else "assistant_name_en", "مساعد ذكي")
    mode = w.get("knowledge_mode", "manual")

    parts = []
    if mode in ("manual", "all") and w.get("manual_content", "").strip():
        parts.append("=== معلومات يدوية ===\n" + w["manual_content"].strip())
    if mode in ("url", "all") and w.get("crawled_text", "").strip():
        parts.append("=== محتوى الموقع الإلكتروني ===\n" + w["crawled_text"][:8000])

    if lang == "ar":
        prompt = (
            f"أنت {name}، مساعد ذكي متخصص. أسلوبك: {p['ar']}.\n"
            "يجب أن ترد دائماً باللغة العربية ما لم يتحدث المستخدم بلغة أخرى.\n"
            "كن مختصراً ومفيداً. لا تخترع معلومات غير موجودة في المصادر أدناه.\n"
            "إذا لم تجد الإجابة في المصادر، قل ذلك بلطف واقترح التواصل المباشر."
        )
    else:
        prompt = (
            f"You are {name}, a specialized AI assistant. Your style: {p['en']}.\n"
            "Always reply in English unless the user writes in another language.\n"
            "Be concise and helpful. Do not invent information not found in the sources below.\n"
            "If you cannot find the answer in the sources, say so politely and suggest direct contact."
        )

    if parts:
        prompt += "\n\n" + "\n\n".join(parts)

    # ⛔ Branding مضمون في كل prompt — لا يُحذف أبداً
    prompt = _inject_branding_prompt(prompt)
    return prompt


async def widget_chat_stream(widget_id: str, message: str, history: list,
                              lang: str = "ar", origin: str = "",
                              embed_token: str = "", client_ip: str = ""):
    """
    Stream المحادثة — يمر عبر كل طبقات الأمان قبل الوصول لـ NVIDIA.

    الترتيب:
      L5 → حظر IP
      L4 → IP rate limit
      L1 → Embed Token
      L2 → Origin check
      Queue → Token Bucket
      L3 → Branding SSE event (أول شيء يُرسَل)
    """
    import httpx

    # ── L5: حظر IP ──────────────────────────────────────────────────────
    if _is_ip_blocked(client_ip):
        _log_suspicious("blocked_ip", widget_id, client_ip, origin)
        yield b'data: {"error":"try_again"}\n\n'
        return

    # ── L4: IP rate limit ────────────────────────────────────────────────
    if not _check_ip_rate(client_ip):
        _log_suspicious("ip_rate_exceeded", widget_id, client_ip, origin)
        yield b'data: {"error":"try_again"}\n\n'
        return

    # ── جلب الـ widget ────────────────────────────────────────────────────
    w = _get_widget(widget_id)
    if not w:
        yield b'data: {"error":"Widget not found"}\n\n'
        return
    if not w.get("active", True):
        yield b'data: {"error":"Widget is disabled"}\n\n'
        return

    # ── L1: Embed Token ──────────────────────────────────────────────────
    if not _verify_embed_token(embed_token, widget_id, origin):
        _log_suspicious("invalid_token", widget_id, client_ip, origin)
        yield b'data: {"error":"access_denied"}\n\n'
        return

    # ── L2: Origin ───────────────────────────────────────────────────────
    if not _is_origin_allowed(origin, w.get("allowed_domains", [])):
        _log_suspicious("origin_blocked", widget_id, client_ip, origin)
        yield b'data: {"error":"access_denied"}\n\n'
        return

    # ── Queue: Token Bucket (شفاف) ───────────────────────────────────────
    if not await _queue_acquire(widget_id):
        yield b'data: {"error":"try_again"}\n\n'
        return

    # ── L3: Branding SSE event (أول شيء يُرسَل — إجباري) ────────────────
    yield _BRANDING_SSE_EVENT

    message = message[:_MAX_MSG_LEN]
    _increment_usage(w)

    system_prompt = _build_system_prompt(w, lang)
    messages      = [{"role": "user", "content": system_prompt + "\n\n---"}]
    for h in history[-6:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    if not nvidia_key:
        yield b'data: {"error":"NVIDIA API key not configured"}\n\n'
        return

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                json={
                    "model":       "meta/llama-3.1-8b-instruct",
                    "messages":    messages,
                    "temperature": 0.3,
                    "top_p":       0.7,
                    "max_tokens":  4096,
                    "stream":      True,
                },
                headers={
                    "Authorization": f"Bearer {nvidia_key}",
                    "Content-Type":  "application/json",
                    "Accept":        "text/event-stream",
                },
            ) as resp:
                if resp.status_code != 200:
                    yield json.dumps({"error": f"upstream_{resp.status_code}"}).encode()
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
    w["daily_usage"] = w.get("daily_usage", 0) + 1
    w["total_usage"]  = w.get("total_usage", 0) + 1
    _save_widget(w)


# ════════════════════════════════════════════════════════════════
# Admin helpers
# ════════════════════════════════════════════════════════════════

def get_all_widgets_admin() -> list:
    if not redis:
        return []
    try:
        keys   = redis.keys(f"{WIDGET_PREFIX}*")
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
        "total":       len(widgets),
        "active":      sum(1 for w in widgets if w.get("active")),
        "total_usage": sum(w.get("total_usage", 0) for w in widgets),
        "daily_usage": sum(w.get("daily_usage", 0) for w in widgets),
    }
