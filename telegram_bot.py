import os
import json
import gzip
import time
import logging
import httpx
from datetime import datetime
from typing import Optional

logger = logging.getLogger("telegram_bot")

# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================

# ── البوت الأصلي: إشعارات اتصل بنا + Enterprise ──────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── بوت منفصل: تخزين جلسات الوكيل V2 + سجل محادثات V1 ──────────────────
#    أنشئه من @BotFather — توكن مختلف تماماً عن الأول
AGENT_TG_BOT_TOKEN = os.environ.get("AGENT_TG_BOT_TOKEN", "")

# ── معرف المالك — مشترك بين البوتين (نفس محادثتك الخاصة) ─────────────────
TELEGRAM_OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")

# ============================================================================
# TELEGRAM API HELPERS
# ============================================================================

def _api_url(method: str) -> str:
    """البوت الأصلي — إشعارات الموقع."""
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

def _agent_api_url(method: str) -> str:
    """بوت الوكيل — تخزين الجلسات والسجلات."""
    return f"https://api.telegram.org/bot{AGENT_TG_BOT_TOKEN}/{method}"

def _agent_configured() -> bool:
    return bool(AGENT_TG_BOT_TOKEN and TELEGRAM_OWNER_ID)

def _esc(text: str) -> str:
    """تنظيف HTML."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def _compress(data: dict) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return gzip.compress(raw, compresslevel=6)

def _decompress(data: bytes) -> dict:
    return json.loads(gzip.decompress(data).decode('utf-8'))

# ============================================================================
# [A] البوت الأصلي — إرسال الإشعارات (لم يتغير)
# ============================================================================

async def send_telegram_message(text: str) -> bool:
    """يرسل رسالة نصية إلى حساب المالك عبر البوت الأصلي."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_OWNER_ID:
        print("[TelegramBot] ⚠️  TELEGRAM_BOT_TOKEN أو TELEGRAM_OWNER_ID غير مضبوطَين.")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _api_url("sendMessage"),
                json={
                    "chat_id": TELEGRAM_OWNER_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
            )
            data = response.json()
            if data.get("ok"):
                print(f"[TelegramBot] ✅ تم الإرسال بنجاح.")
                return True
            else:
                print(f"[TelegramBot] ❌ فشل الإرسال: {data.get('description')}")
                return False
    except Exception as e:
        print(f"[TelegramBot] ❌ خطأ: {e}")
        return False

# ============================================================================
# [B] بوت الوكيل — تسجيل محادثات V1 (Code Hub الكلاسيكي)
# ============================================================================

async def log_v1_conversation(
    user_email:   str,
    instruction:  str,
    chat_mode:    str,      # auto | build | chat
    model_id:     str,
    files_count:  int = 0,
    output_files: list = None,  # أسماء الملفات المُنشأة
    had_error:    bool = False,
) -> bool:
    """
    يُسجّل كل محادثة V1 (code_processor) في تلجرام كرسالة نصية.
    يُرسل عبر AGENT_TG_BOT بصمت (disable_notification=True).
    لا يُزعج المالك — فقط للمراقبة والإحصاء.
    """
    if not _agent_configured():
        return False

    now        = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    mode_emoji = {"auto": "🔮", "build": "🔨", "chat": "💬"}.get(chat_mode, "🔵")
    status_em  = "❌" if had_error else "✅"

    # اقتطاع التعليمات الطويلة
    instr_short = instruction[:200] + "..." if len(instruction) > 200 else instruction

    files_line = ""
    if output_files:
        files_line = f"\n📁 <b>الملفات:</b> {_esc(', '.join(output_files[:6]))}"
        if len(output_files) > 6:
            files_line += f" +{len(output_files)-6}"

    text = (
        f"{status_em} <b>Code Hub V1 — محادثة جديدة</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>المستخدم:</b> {_esc(user_email)}\n"
        f"{mode_emoji} <b>الوضع:</b> {chat_mode}\n"
        f"🤖 <b>النموذج:</b> {_esc(model_id.split('/')[-1])}\n"
        f"📎 <b>ملفات مرفقة:</b> {files_count}\n"
        f"💬 <b>الطلب:</b>\n<i>{_esc(instr_short)}</i>"
        f"{files_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}"
    )

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                _agent_api_url("sendMessage"),
                json={
                    "chat_id":              TELEGRAM_OWNER_ID,
                    "text":                 text,
                    "parse_mode":           "HTML",
                    "disable_notification": True,   # صامت
                    "disable_web_page_preview": True,
                }
            )
            return resp.json().get("ok", False)
    except Exception as e:
        logger.debug(f"V1 log failed (non-critical): {e}")
        return False

# ============================================================================
# [C] بوت الوكيل — حفظ جلسات V2 كملفات JSON مضغوطة
# ============================================================================

async def save_agent_session(
    session_id:      str,
    user_email:      str,
    title:           str,
    status:          str,
    plan:            Optional[dict],
    files:           Optional[dict],
    history:         Optional[list],
    current_task:    int,
    total_tasks:     int,
    metadata:        Optional[dict] = None,
    existing_msg_id: Optional[int]  = None,
) -> Optional[dict]:
    """
    يحفظ جلسة الوكيل V2 في تلجرام كملف JSON.gz مضغوط.
    يُعيد: { message_id, file_id, file_size } أو None عند الفشل.
    """
    if not _agent_configured():
        logger.warning("AGENT_TG_BOT_TOKEN غير مضبوط — لن يُحفظ")
        return None

    session_data = {
        "session_id":         session_id,
        "user_email":         user_email,
        "title":              title,
        "status":             status,
        "plan":               plan or {},
        "files":              files or {},
        "history":            (history or [])[-20:],
        "current_task_index": current_task,
        "total_tasks":        total_tasks,
        "metadata":           metadata or {},
        "saved_at":           datetime.utcnow().isoformat(),
        "_version":           "v2.0",
    }

    try:
        compressed = _compress(session_data)
    except Exception as e:
        logger.error(f"Compression failed: {e}")
        return None

    now          = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    status_emoji = {"completed":"✅","running":"⚙️","failed":"❌","paused":"⏸️"}.get(status,"🔵")
    file_count   = len(files or {})
    file_names   = ", ".join(list((files or {}).keys())[:5])
    if file_count > 5:
        file_names += f" +{file_count-5}"

    caption = (
        f"🤖 <b>Agent V2 — Session</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{_esc(session_id)}</code>\n"
        f"👤 {_esc(user_email)}\n"
        f"📌 {_esc(title or 'Untitled')}\n"
        f"{status_emoji} {_esc(status)} | 📋 {current_task}/{total_tasks}\n"
        f"📁 {file_count} ملفات: {_esc(file_names) if file_names else '—'}\n"
        f"💾 {len(compressed)/1024:.1f} KB\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}"
    )

    safe_id   = session_id.replace(":", "_").replace("/", "_")
    file_name = f"ag_{safe_id}.json.gz"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # حذف الرسالة القديمة إن وُجدت
            if existing_msg_id:
                try:
                    await client.post(
                        _agent_api_url("deleteMessage"),
                        json={"chat_id": TELEGRAM_OWNER_ID, "message_id": existing_msg_id}
                    )
                except Exception:
                    pass

            resp = await client.post(
                _agent_api_url("sendDocument"),
                data={
                    "chat_id":              TELEGRAM_OWNER_ID,
                    "caption":              caption,
                    "parse_mode":           "HTML",
                    "disable_notification": "true",
                    "disable_content_type_detection": "true",
                },
                files={"document": (file_name, compressed, "application/gzip")}
            )

        result = resp.json()
        if not result.get("ok"):
            logger.error(f"sendDocument failed: {result.get('description')}")
            return None

        msg = result["result"]
        doc = msg.get("document", {})
        logger.info(f"✅ Session saved: {session_id}")
        return {
            "message_id": msg["message_id"],
            "file_id":    doc.get("file_id", ""),
            "file_size":  len(compressed),
        }
    except Exception as e:
        logger.error(f"save_agent_session error: {e}")
        return None


async def load_agent_session(file_id: str) -> Optional[dict]:
    """يُحمّل ويفك ضغط جلسة الوكيل من تلجرام بـ file_id."""
    if not _agent_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_agent_api_url("getFile"), json={"file_id": file_id})
            result = resp.json()
            if not result.get("ok"):
                logger.error(f"getFile failed: {result.get('description')}")
                return None
            file_path = result["result"]["file_path"]
            dl_url    = f"https://api.telegram.org/file/bot{AGENT_TG_BOT_TOKEN}/{file_path}"
            dl        = await client.get(dl_url)
            if dl.status_code != 200:
                return None
            try:
                return _decompress(dl.content)
            except Exception:
                return json.loads(dl.content.decode('utf-8'))
    except Exception as e:
        logger.error(f"load_agent_session error: {e}")
        return None


async def notify_agent_complete(
    session_id: str,
    user_email: str,
    title:      str,
    file_count: int,
    task_count: int,
) -> bool:
    """إشعار منبّه (غير صامت) عند اكتمال مشروع وكيل."""
    if not _agent_configured():
        return False
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"✅ <b>اكتمل مشروع وكيل!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {_esc(user_email)}\n"
        f"📌 {_esc(title)}\n"
        f"📁 {file_count} ملفات | ✔️ {task_count} مهام\n"
        f"🆔 <code>{_esc(session_id)}</code>\n"
        f"🕐 {now}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _agent_api_url("sendMessage"),
                json={
                    "chat_id":    TELEGRAM_OWNER_ID,
                    "text":       text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
            )
            return resp.json().get("ok", False)
    except Exception as e:
        logger.error(f"notify error: {e}")
        return False


async def check_agent_bot_status() -> dict:
    """يتحقق من أن بوت الوكيل يعمل."""
    if not _agent_configured():
        return {"ok": False, "error": "AGENT_TG_BOT_TOKEN not set"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(_agent_api_url("getMe"))
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                return {
                    "ok":       True,
                    "bot_name": bot.get("first_name"),
                    "username": "@" + bot.get("username", ""),
                    "owner_id": TELEGRAM_OWNER_ID,
                }
            return {"ok": False, "error": data.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ============================================================================
# MESSAGE BUILDERS — البوت الأصلي (لم تتغير)
# ============================================================================

def build_contact_message(name: str, email: str, message: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"📩 <b>رسالة جديدة — اتصل بنا</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>الاسم:</b> {_esc(name)}\n"
        f"📧 <b>البريد:</b> {_esc(email)}\n"
        f"💬 <b>الرسالة:</b>\n{_esc(message)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 <b>الوقت:</b> {now}\n"
        f"📍 <b>المصدر:</b> صفحة تواصل معنا"
    )


def build_enterprise_message(
    project_type: str,
    volume: str,
    needs: str,
    contact_method: str,
    contact_value: str,
    description: str
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    type_map   = {"cs":"خدمة عملاء / بوت","api":"API مخصص","web":"موقع / تطبيق","other":"غير ذلك"}
    volume_map = {"low":"بسيط (شركات ناشئة)","medium":"متوسط (أعمال متنامية)","high":"عالي (Enterprise)"}
    needs_map  = {"price":"سعر خاص (خصم كميات)","setup":"إعداد كامل وتطوير","vps":"سيرفر خاص (VPS)","unsure":"غير متأكد — يحتاج استشارة"}
    desc_line  = f"\n📝 <b>الوصف:</b> {_esc(description)}" if description.strip() else ""
    return (
        f"🏢 <b>طلب حل مخصص — Enterprise</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 <b>نوع المشروع:</b> {_esc(type_map.get(project_type, project_type))}\n"
        f"📊 <b>حجم الاستخدام:</b> {_esc(volume_map.get(volume, volume))}\n"
        f"🎯 <b>الاحتياج الأساسي:</b> {_esc(needs_map.get(needs, needs))}\n"
        f"📬 <b>وسيلة التواصل:</b> {_esc(contact_method)}\n"
        f"🔗 <b>معلومات التواصل:</b> {_esc(contact_value)}"
        f"{desc_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 <b>الوقت:</b> {now}\n"
        f"📍 <b>المصدر:</b> صفحة الحلول المخصصة"
    )

# ============================================================================
# PUBLIC API — الدوال المستخدمة من main.py (لم تتغير)
# ============================================================================

async def notify_contact_form(name: str, email: str, message: str) -> bool:
    text = build_contact_message(name, email, message)
    return await send_telegram_message(text)


async def notify_enterprise_form(
    project_type: str,
    volume: str,
    needs: str,
    contact_method: str,
    contact_value: str,
    description: str = ""
) -> bool:
    text = build_enterprise_message(
        project_type, volume, needs,
        contact_method, contact_value, description
    )
    return await send_telegram_message(text)

# ============================================================================
# HIGH-LEVEL HANDLERS — تُستدعى مباشرة من main.py (validation + إرسال)
# ============================================================================

async def handle_contact_form(body: dict) -> dict:
    """
    يتحقق من بيانات نموذج 'اتصل بنا' ويُرسل الإشعار عبر تلجرام.
    يُعيد dict يحتوي على: ok (bool), detail (str), status (int).
    """
    name    = body.get("name", "").strip()
    email   = body.get("email", "").strip()
    message = body.get("message", "").strip()

    if not name or not email or not message:
        return {"ok": False, "detail": "جميع الحقول مطلوبة.", "status": 400}

    try:
        await notify_contact_form(name, email, message)
        return {"ok": True, "status": 200}
    except Exception:
        return {"ok": False, "detail": "خطأ في الخادم.", "status": 500}


async def handle_enterprise_form(body: dict) -> dict:
    """
    يتحقق من بيانات نموذج Enterprise ويُرسل الإشعار عبر تلجرام.
    يُعيد dict يحتوي على: ok (bool), detail (str), status (int).
    """
    project_type   = body.get("projectType", "").strip()
    volume         = body.get("volume", "").strip()
    needs          = body.get("needs", "").strip()
    contact_method = body.get("contactMethod", "").strip()
    contact_value  = body.get("contactValue", "").strip()
    description    = body.get("description", "").strip()

    if not project_type or not volume or not needs or not contact_value:
        return {"ok": False, "detail": "يرجى ملء الحقول المطلوبة.", "status": 400}

    try:
        await notify_enterprise_form(
            project_type, volume, needs,
            contact_method, contact_value, description
        )
        return {"ok": True, "status": 200}
    except Exception:
        return {"ok": False, "detail": "خطأ في الخادم.", "status": 500}


async def schedule_log_v1(
    email: str,
    instruction: str,
    chat_mode: str,
    model_id: str,
    files_count: int,
) -> None:
    """
    يُجدول تسجيل محادثة V1 في الخلفية (fire-and-forget).
    آمن للاستدعاء بدون await — يُنشئ asyncio.Task داخلياً.
    """
    import asyncio as _asyncio
    try:
        _asyncio.create_task(log_v1_conversation(
            user_email  = email,
            instruction = instruction,
            chat_mode   = chat_mode,
            model_id    = model_id,
            files_count = files_count,
        ))
    except Exception:
        pass
