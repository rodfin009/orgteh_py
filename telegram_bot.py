import os
import io
import json
import gzip
import time
import hashlib
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

# ── البوت الثالث: قاعدة بيانات المستخدمين — توكن مختلف فقط ─────────────
#    أنشئه من @BotFather → /newbot
#    يرسل إلى TELEGRAM_OWNER_ID (نفس محادثتك الشخصية — مثل البوتين الآخرين)
USERS_TG_BOT_TOKEN = os.environ.get("USERS_TG_BOT_TOKEN", "")

# ============================================================================
# TELEGRAM API HELPERS
# ============================================================================

def _api_url(method: str) -> str:
    """البوت الأصلي — إشعارات الموقع."""
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

def _agent_api_url(method: str) -> str:
    """بوت الوكيل — تخزين الجلسات والسجلات."""
    return f"https://api.telegram.org/bot{AGENT_TG_BOT_TOKEN}/{method}"

def _users_api_url(method: str) -> str:
    """البوت الثالث — قاعدة بيانات المستخدمين."""
    return f"https://api.telegram.org/bot{USERS_TG_BOT_TOKEN}/{method}"

def _agent_configured() -> bool:
    return bool(AGENT_TG_BOT_TOKEN and TELEGRAM_OWNER_ID)

def _users_bot_configured() -> bool:
    return bool(USERS_TG_BOT_TOKEN and TELEGRAM_OWNER_ID)

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

def _v1_conv_file_name(email: str) -> str:
    h    = hashlib.md5(email.encode()).hexdigest()[:8]
    slug = email.replace("@","_at_").replace(".","_").replace("+","_")[:40]
    return f"v1_conv_{slug}_{h}.txt"

def _v1_conv_redis_key(email: str) -> str:
    return f"tg_v1_conv:{email}"

def _build_v1_turn_txt(turn: dict) -> str:
    """يبني كتلة نص لدورة واحدة (user ↔ model)."""
    sep = "─" * 64
    lines = [
        f"[{turn.get('ts','?')}] session={turn.get('session_id','?')}",
        f"  model={turn.get('model_id','?')} | mode={turn.get('chat_mode','?')} | {'❌ ERROR' if turn.get('had_error') else '✅ OK'}",
        sep,
        "👤 USER:",
        turn.get("user_msg",""),
    ]
    thinking = (turn.get("thinking") or "").strip()
    if thinking:
        snippet = thinking[:4000] + ("\n[... مقطوع ...]" if len(thinking) > 4000 else "")
        lines += [sep, "🧠 THINKING:", snippet]
    resp = (turn.get("response") or "").strip()
    r_snippet = resp[:6000] + ("\n[... مقطوع ...]" if len(resp) > 6000 else "")
    lines += [sep, "🤖 RESPONSE:", r_snippet]
    att = turn.get("files_attached") or []
    gen = turn.get("files_generated") or []
    if att: lines.append(f"📎 ATTACHED : {', '.join(att)}")
    if gen: lines.append(f"📁 GENERATED: {', '.join(gen)}")
    lines.append("=" * 64)
    return "\n".join(lines)

async def save_v1_turn(
    user_email:      str,
    session_id:      str,
    model_id:        str,
    chat_mode:       str,
    user_msg:        str,
    thinking:        str  = "",
    response:        str  = "",
    files_attached:  list = None,
    files_generated: list = None,
    had_error:       bool = False,
) -> bool:
    """
    يُضيف دورة (user ↔ model) كاملة إلى ملف .txt الخاص بالمستخدم في Telegram.

    الهيكل في التخزين:
      Telegram  → ملف .txt يحمل كل الدورات (المصدر الوحيد للمحتوى)
      Redis     → { message_id, file_id } فقط للجلب السريع
      TiDB      → { message_id, file_id } احتياطي

    يُستدعى من endpoint مستقل /api/hub/save-turn بعد اكتمال البث
    في المتصفح — لا علاقة له بـ Vercel timeouts أو حدود الـ streaming.
    """
    if not _agent_configured():
        return False

    try:
        from database import redis as _redis
    except Exception:
        _redis = None

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    turn = {
        "session_id":      session_id,
        "ts":              now,
        "model_id":        model_id.split("/")[-1],
        "chat_mode":       chat_mode,
        "user_msg":        user_msg[:2000],
        "thinking":        thinking[:6000],
        "response":        response[:8000],
        "files_attached":  (files_attached  or [])[:20],
        "files_generated": (files_generated or [])[:20],
        "had_error":       had_error,
    }

    # ── اجلب message_id + file_id من Redis (مرجع فقط) ──
    existing_msg_id  = None
    existing_file_id = None
    if _redis:
        try:
            stored = _redis.get(_v1_conv_redis_key(user_email))
            if stored:
                meta             = json.loads(stored) if isinstance(stored, str) else stored
                existing_msg_id  = meta.get("message_id")
                existing_file_id = meta.get("file_id")
        except Exception:
            pass

    # ── اجلب الملف القديم من Telegram وأضف الدورة الجديدة ──
    prev_content = ""
    turn_count   = 0
    if existing_file_id:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(_agent_api_url("getFile"),
                                      json={"file_id": existing_file_id})
                if r.json().get("ok"):
                    path = r.json()["result"]["file_path"]
                    dl   = await client.get(
                        f"https://api.telegram.org/file/bot{AGENT_TG_BOT_TOKEN}/{path}"
                    )
                    if dl.status_code == 200:
                        prev_content = dl.text
                        # احسب عدد الدورات الموجودة (سطور "===")
                        turn_count = prev_content.count("\n[20")  # تقريبي بالتاريخ
        except Exception as e:
            logger.debug(f"[V1Conv] fetch old file failed (OK): {e}")

    turn_count += 1
    header = (
        f"{'='*64}\n"
        f"  ORGTEH CODE HUB V1 — CONVERSATION LOG\n"
        f"  User: {user_email}\n"
        f"  Updated: {now} | Turns: {turn_count}\n"
        f"{'='*64}\n\n"
    )
    # الملف الجديد = header + دورة جديدة + الدورات القديمة
    new_content = header + _build_v1_turn_txt(turn) + "\n\n" + \
                  (prev_content.split("\n\n", 1)[1] if "\n\n" in prev_content else "")
    encoded = new_content.encode("utf-8")
    fname   = _v1_conv_file_name(user_email)
    caption = (
        f"💬 <b>V1 Conv Log</b> — {_esc(user_email)}\n"
        f"📊 {turn_count} دورة | 💾 {len(encoded)/1024:.1f} KB\n"
        f"🕐 {now}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if existing_msg_id:
                try:
                    await client.post(_agent_api_url("deleteMessage"),
                                      json={"chat_id": TELEGRAM_OWNER_ID,
                                            "message_id": existing_msg_id})
                except Exception:
                    pass
            resp = await client.post(
                _agent_api_url("sendDocument"),
                data={"chat_id": TELEGRAM_OWNER_ID, "caption": caption,
                      "parse_mode": "HTML", "disable_notification": "true"},
                files={"document": (fname, io.BytesIO(encoded), "text/plain")},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error(f"[V1Conv] sendDocument failed: {data.get('description')}")
                return False

            msg         = data["result"]
            doc         = msg.get("document", {})
            new_msg_id  = msg["message_id"]
            new_file_id = doc.get("file_id", "")

            # Redis: مرجع فقط
            if _redis:
                try:
                    _redis.set(_v1_conv_redis_key(user_email), json.dumps({
                        "message_id": new_msg_id,
                        "file_id":    new_file_id,
                        "turn_count": turn_count,
                        "updated_at": datetime.utcnow().isoformat(),
                    }))
                except Exception:
                    pass

            # TiDB: مرجع احتياطي
            _save_v1_ref_to_db(user_email, new_msg_id, new_file_id)
            logger.info(f"[V1Conv] ✅ {user_email} turn#{turn_count} saved")
            return True
    except Exception as e:
        logger.error(f"[V1Conv] save_v1_turn error: {e}")
        return False


def _save_v1_ref_to_db(email: str, message_id: int, file_id: str) -> None:
    try:
        from database import get_db_connection, get_user_by_email, redis as _redis
        user = get_user_by_email(email)
        if not user: return
        user["tg_v1_conv_file"] = {
            "message_id": message_id, "file_id": file_id,
            "updated_at": datetime.utcnow().isoformat(),
        }
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET data = %s WHERE email = %s",
                                (json.dumps(user), email))
                if _redis: _redis.set(f"user:{email}", json.dumps(user))
            finally: conn.close()
    except Exception as e:
        logger.debug(f"[V1Conv] _save_ref silent fail: {e}")

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



# ============================================================================
# [D] البوت الثالث — قاعدة بيانات المستخدمين (User Profile TXT)
# ============================================================================
#
# لكل مستخدم ملف .txt خاص به يُرسل إلى محادثتك الشخصية (TELEGRAM_OWNER_ID).
# يُحدَّث الملف عند كل حدث: طلب، اشتراك، دخول، أداة...
# المرجع (message_id) يُخزَّن في Redis + TiDB للبحث السريع.
#
# متغير البيئة الوحيد المطلوب:
#   USERS_TG_BOT_TOKEN  — توكن البوت الثالث من @BotFather (مختلف عن الأول والثاني)
#   يستخدم TELEGRAM_OWNER_ID الموجود أصلاً — لا حاجة لأي إعداد إضافي
# ============================================================================

def _user_file_redis_key(email: str) -> str:
    """مفتاح Redis لمرجع ملف المستخدم في تلجرام."""
    return f"tg_user_file:{email}"

def _safe_file_name(email: str) -> str:
    """اسم ملف آمن من الإيميل."""
    h    = hashlib.md5(email.encode()).hexdigest()[:8]
    slug = email.replace("@", "_at_").replace(".", "_").replace("+", "_")[:40]
    return f"user_{slug}_{h}.txt"

def _build_user_txt(profile: dict) -> str:
    """يبني محتوى ملف .txt للمستخدم من dict البيانات."""
    now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    email = profile.get("email", "unknown")

    lines = [
        "=" * 60,
        f"  ORGTEH USER PROFILE — {email}",
        "=" * 60,
        f"Last Updated : {now}",
        f"Email        : {email}",
        f"API Key      : {profile.get('api_key', 'N/A')}",
        f"Plan         : {profile.get('plan', 'Free Tier')}",
        f"Created At   : {profile.get('created_at', 'N/A')}",
        f"Sub End      : {profile.get('subscription_end', 'N/A')}",
        "",
        "─" * 60,
        "  ACTIVE SUBSCRIPTIONS",
        "─" * 60,
    ]
    for i, p in enumerate(profile.get("active_plans", []), 1):
        lines.append(f"  [{i}] {p.get('name','?')} ({p.get('period','?')})")
        lines.append(f"      Activated : {p.get('activated','N/A')}")
        lines.append(f"      Expires   : {p.get('expires','N/A')}")
    if not profile.get("active_plans"):
        lines.append("  No active subscriptions.")

    lines += [
        "",
        "─" * 60,
        "  TODAY'S USAGE",
        "─" * 60,
    ]
    usage = profile.get("usage", {})
    lines.append(f"  Date          : {usage.get('date','N/A')}")
    lines.append(f"  Total Requests: {usage.get('total_requests', 0)}")
    lines.append(f"  Total Tokens  : {usage.get('total_tokens', 0)}")
    lines.append(f"  Errors        : {usage.get('errors', 0)}")

    lines += [
        "",
        "─" * 60,
        "  ACTIVITY LOG (last 100 events)",
        "─" * 60,
    ]
    for entry in profile.get("activity_log", [])[-100:]:
        lines.append(f"  [{entry.get('ts','?')}] {entry.get('type','?')}: {entry.get('detail','')}")
    if not profile.get("activity_log"):
        lines.append("  No activity recorded yet.")

    lines += [
        "",
        "─" * 60,
        "  SUBSCRIPTION HISTORY",
        "─" * 60,
    ]
    for h in profile.get("subscription_history", []):
        lines.append(f"  {h.get('activated','?')} → {h.get('name','?')} ({h.get('type','?')})")
    if not profile.get("subscription_history"):
        lines.append("  No history.")

    lines += ["", "=" * 60, "  END OF FILE", "=" * 60, ""]
    return "\n".join(lines)


async def _upload_user_file(
    email: str,
    txt_content: str,
    existing_msg_id: Optional[int] = None,
) -> Optional[dict]:
    """يرفع ملف .txt إلى القناة، ويحذف القديم إن وُجد."""
    if not _users_bot_configured():
        return None

    encoded  = txt_content.encode("utf-8")
    caption  = (
        f"👤 <b>User Profile</b>\n"
        f"📧 {_esc(email)}\n"
        f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if existing_msg_id:
                try:
                    await client.post(
                        _users_api_url("deleteMessage"),
                        json={"chat_id": TELEGRAM_OWNER_ID, "message_id": existing_msg_id},
                    )
                except Exception:
                    pass

            resp = await client.post(
                _users_api_url("sendDocument"),
                data={
                    "chat_id":              TELEGRAM_OWNER_ID,
                    "caption":              caption,
                    "parse_mode":           "HTML",
                    "disable_notification": "true",
                },
                files={
                    "document": (_safe_file_name(email), io.BytesIO(encoded), "text/plain"),
                },
            )
            data = resp.json()
            if data.get("ok"):
                msg = data["result"]
                doc = msg.get("document", {})
                return {
                    "message_id": msg["message_id"],
                    "file_id":    doc.get("file_id", ""),
                }
            logger.error(f"[UserBot] sendDocument failed: {data.get('description')}")
            return None
    except Exception as e:
        logger.error(f"[UserBot] upload error: {e}")
        return None


async def update_user_profile_file(
    email:        str,
    profile:      dict,
    event_type:   str = "update",
    event_detail: str = "",
) -> bool:
    """
    يُضيف الحدث لسجل النشاط ثم يرفع ملف المستخدم المحدَّث إلى تلجرام.
    يُخزّن message_id في Redis + TiDB.

    الاستدعاء:
        await update_user_profile_file(email, user_dict, "code_hub_request", "model=deepseek")
    """
    if not _users_bot_configured():
        return False

    try:
        from database import redis as _redis
    except Exception:
        _redis = None

    # أضف الحدث
    activity = profile.get("activity_log", [])
    activity.append({
        "ts":     datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "type":   event_type,
        "detail": str(event_detail)[:200],
    })
    profile["activity_log"] = activity[-500:]  # احتفظ بآخر 500 حدث

    txt = _build_user_txt(profile)

    # اجلب message_id القديم من Redis
    existing_msg_id = None
    if _redis:
        try:
            stored = _redis.get(_user_file_redis_key(email))
            if stored:
                meta = json.loads(stored) if isinstance(stored, str) else stored
                existing_msg_id = meta.get("message_id")
        except Exception:
            pass

    result = await _upload_user_file(email, txt, existing_msg_id)
    if not result:
        return False

    # خزّن message_id الجديد في Redis
    if _redis:
        try:
            _redis.set(
                _user_file_redis_key(email),
                json.dumps({
                    "message_id": result["message_id"],
                    "file_id":    result["file_id"],
                    "updated_at": datetime.utcnow().isoformat(),
                })
            )
        except Exception as e:
            logger.warning(f"[UserBot] Redis set failed: {e}")

    # خزّن المرجع في TiDB أيضاً (احتياطي)
    _save_user_file_ref_to_db(email, result["message_id"], result["file_id"])

    logger.info(f"[UserBot] ✅ {email} — msg_id={result['message_id']}")
    return True


def _save_user_file_ref_to_db(email: str, message_id: int, file_id: str) -> None:
    """يحفظ مرجع الملف في حقل data['tg_user_file'] في TiDB — صامت."""
    try:
        from database import get_db_connection, get_user_by_email, redis as _redis
        user = get_user_by_email(email)
        if not user:
            return
        user["tg_user_file"] = {
            "message_id": message_id,
            "file_id":    file_id,
            "updated_at": datetime.utcnow().isoformat(),
        }
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET data = %s WHERE email = %s",
                        (json.dumps(user), email)
                    )
                if _redis:
                    _redis.set(f"user:{email}", json.dumps(user))
            finally:
                conn.close()
    except Exception as e:
        logger.debug(f"[UserBot] _save_ref_to_db silent fail: {e}")


async def get_user_file_content(email: str) -> Optional[str]:
    """يجلب محتوى ملف المستخدم من تلجرام (للإدارة والتشخيص)."""
    if not _users_bot_configured():
        return None

    file_id = None
    try:
        from database import redis as _redis
        if _redis:
            stored = _redis.get(_user_file_redis_key(email))
            if stored:
                file_id = json.loads(stored).get("file_id")
    except Exception:
        pass

    if not file_id:
        try:
            from database import get_user_by_email
            user = get_user_by_email(email)
            if user:
                file_id = user.get("tg_user_file", {}).get("file_id")
        except Exception:
            pass

    if not file_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_users_api_url("getFile"), json={"file_id": file_id})
            if not r.json().get("ok"):
                return None
            path = r.json()["result"]["file_path"]
            dl   = await client.get(
                f"https://api.telegram.org/file/bot{USERS_TG_BOT_TOKEN}/{path}"
            )
            return dl.text if dl.status_code == 200 else None
    except Exception as e:
        logger.error(f"[UserBot] get_file error: {e}")
        return None


async def check_users_bot_status() -> dict:
    """يتحقق من حالة البوت الثالث."""
    if not _users_bot_configured():
        return {
            "ok":    False,
            "error": "USERS_TG_BOT_TOKEN أو TELEGRAM_OWNER_ID غير مضبوطَين.",
            "hint":  "أنشئ بوتاً جديداً من @BotFather وأضف USERS_TG_BOT_TOKEN في البيئة.",
        }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            data = (await client.post(_users_api_url("getMe"))).json()
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


def schedule_user_profile_update(
    email:        str,
    profile:      dict,
    event_type:   str = "update",
    event_detail: str = "",
) -> None:
    """
    يُجدِّل تحديث ملف المستخدم في الخلفية (fire-and-forget).
    آمن للاستدعاء بدون await — يُنشئ asyncio.Task داخلياً.

    مثال:
        from telegram_bot import schedule_user_profile_update
        schedule_user_profile_update(email, user_data, "code_hub_request", f"model={model_id}")
    """
    import asyncio as _asyncio
    try:
        _asyncio.create_task(update_user_profile_file(
            email        = email,
            profile      = profile,
            event_type   = event_type,
            event_detail = event_detail,
        ))
    except Exception as e:
        logger.debug(f"[UserBot] schedule failed (non-critical): {e}")

# ============================================================================
# أنواع الأحداث المقترحة (event_type) للاتساق في جميع أنحاء المشروع:
#   login | register | logout
#   api_request | code_hub_request | agent_v2_request
#   tool_used | subscription_new | subscription_renew
#   api_key_reset | contact_form | enterprise_form | error
# ============================================================================
