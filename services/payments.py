import os
import httpx
import logging
from fastapi import HTTPException
from database import get_user_by_email

# إعداد نظام تسجيل الأخطاء (Logging) ليعمل بكفاءة مع Vercel و Replit
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] SpaceRemit: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─── جلب المفاتيح ────────────────────────────────────────────────────
SPACEREMIT_PUBLIC_KEY = os.environ.get("SPACEREMIT_LIVE_PUBLIC_KEY")
SPACEREMIT_SECRET_KEY = os.environ.get("SPACEREMIT_LIVE_SECRET_KEY")

_is_live = True

# ─── التبديل لمفاتيح الاختبار إن لم توجد الأساسية ─────────────────────
if not SPACEREMIT_PUBLIC_KEY or not SPACEREMIT_SECRET_KEY:
    logger.warning("Live keys not found. Falling back to Test Keys.")
    SPACEREMIT_PUBLIC_KEY = os.environ.get("SPACEREMIT_TEST_PUBLIC_KEY")
    SPACEREMIT_SECRET_KEY = os.environ.get("SPACEREMIT_TEST_SECRET_KEY")
    _is_live = False

# تحقق أمني عند بدء التشغيل
if not SPACEREMIT_PUBLIC_KEY:
    logger.critical("CRITICAL ERROR: No SpaceRemit Public Key found in Environment Variables!")
else:
    # طباعة أول 8 أحرف فقط من المفتاح السري للتأكد من وجوده دون كشفه
    safe_secret = f"{SPACEREMIT_SECRET_KEY[:8]}***" if SPACEREMIT_SECRET_KEY else "MISSING"
    logger.info(f"Initialized. Mode: {'LIVE' if _is_live else 'TEST'}. Secret Key exists: {safe_secret}")


async def generate_payment_link(email: str, plan_name: str, period: str, amount: float):
    """جلب بيانات الدفع للواجهة الأمامية بأمان"""
    logger.info(f"Generating payment data for: email={email}, plan={plan_name}, amount={amount}")

    if not SPACEREMIT_PUBLIC_KEY:
        logger.error("Failed to generate link: PUBLIC_KEY is missing on the server.")
        raise HTTPException(status_code=500, detail="Payment Gateway configuration error (Missing Keys).")

    user = get_user_by_email(email)
    if not user:
        logger.error(f"User not found in DB: {email}")
        raise HTTPException(status_code=404, detail="User not found")

    # إرسال البيانات للواجهة
    return {
        "amount": amount,
        "email": email,
        "public_key": SPACEREMIT_PUBLIC_KEY,
        "is_live": _is_live
    }


async def verify_spaceremit_payment(payment_code: str):
    """التحقق من صحة الدفع عبر الخادم والتواصل مع API الخاص بهم"""
    logger.info(f"Starting payment verification for code: {payment_code}")

    if not SPACEREMIT_SECRET_KEY:
        logger.error("Cannot verify payment: SECRET_KEY is missing on the server.")
        return None

    url = "https://spaceremit.com/api/v2/payment_info/"
    payload = {
        "private_key": SPACEREMIT_SECRET_KEY,
        "payment_id": payment_code
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            logger.info("Sending verification request to SpaceRemit...")
            response = await client.post(url, json=payload)

            # تسجيل الاستجابة الخام لتتبع الأخطاء بدقة
            logger.info(f"SpaceRemit HTTP Status Code: {response.status_code}")

            data = response.json()
            logger.info(f"SpaceRemit Response Body: {data}")

            if data.get("response_status") == "success":
                payment_data = data.get("data", {})
                status_tag = payment_data.get("status_tag")

                # A = مكتمل / T = اختبار
                if status_tag in ['A', 'T']:
                    logger.info(f"Payment {payment_code} verified successfully! Status: {status_tag}")
                    return payment_data
                else:
                    logger.warning(f"Payment {payment_code} rejected. Unacceptable status_tag: {status_tag}")
            else:
                logger.error(f"SpaceRemit API rejected verification. Message: {data.get('message')}")

            return None

    except httpx.ReadTimeout:
        logger.error("SpaceRemit API Timeout: Server took too long to respond.")
        return None
    except httpx.RequestError as exc:
        logger.error(f"Network error while connecting to SpaceRemit: {exc}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during verification: {str(e)}", exc_info=True)
        return None