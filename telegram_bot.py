import os
import httpx
from datetime import datetime

# ============================================================================
# ENVIRONMENT VARIABLES — SET THESE IN YOUR .env / HOSTING CONFIG
# ============================================================================

# توكن البوت من @BotFather
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# الـ User ID الخاص بك (احصل عليه من @userinfobot)
TELEGRAM_OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")

# ============================================================================
# TELEGRAM API BASE URL
# ============================================================================

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


# ============================================================================
# CORE: إرسال رسالة نصية
# ============================================================================

async def send_telegram_message(text: str) -> bool:
    """
    يرسل رسالة نصية إلى حساب المالك فقط.
    يدعم Markdown v2 لتنسيق الرسالة.
    Returns True إذا نجح الإرسال، False إذا فشل.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_OWNER_ID:
        print("[TelegramBot] ⚠️  TELEGRAM_BOT_TOKEN أو TELEGRAM_OWNER_ID غير مضبوطَين في متغيرات البيئة.")
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
                print(f"[TelegramBot] ✅ تم الإرسال بنجاح إلى المالك.")
                return True
            else:
                print(f"[TelegramBot] ❌ فشل الإرسال: {data.get('description')}")
                return False
    except Exception as e:
        print(f"[TelegramBot] ❌ خطأ في الاتصال بـ Telegram: {e}")
        return False


# ============================================================================
# MESSAGE BUILDERS — بناء نص الرسالة لكل نوع
# ============================================================================

def build_contact_message(name: str, email: str, message: str) -> str:
    """
    رسالة من صفحة 'تواصل معنا' (contacts)
    """
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
    """
    رسالة من صفحة 'حلول مخصصة' (enterprise)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ترجمة القيم للعربية لتسهيل القراءة
    type_map = {
        "cs": "خدمة عملاء / بوت",
        "api": "API مخصص",
        "web": "موقع / تطبيق",
        "other": "غير ذلك"
    }
    volume_map = {
        "low": "بسيط (شركات ناشئة)",
        "medium": "متوسط (أعمال متنامية)",
        "high": "عالي (Enterprise)"
    }
    needs_map = {
        "price": "سعر خاص (خصم كميات)",
        "setup": "إعداد كامل وتطوير",
        "vps": "سيرفر خاص (VPS)",
        "unsure": "غير متأكد — يحتاج استشارة"
    }

    desc_line = f"\n📝 <b>الوصف:</b> {_esc(description)}" if description.strip() else ""

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
# HELPER
# ============================================================================

def _esc(text: str) -> str:
    """تنظيف النص من أي HTML يمكن أن يكسر التنسيق"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ============================================================================
# PUBLIC API — الدوال المستخدمة من main.py
# ============================================================================

async def notify_contact_form(name: str, email: str, message: str) -> bool:
    """استخدم هذه الدالة من endpoint تواصل معنا"""
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
    """استخدم هذه الدالة من endpoint الحلول المخصصة"""
    text = build_enterprise_message(
        project_type, volume, needs,
        contact_method, contact_value, description
    )
    return await send_telegram_message(text)
