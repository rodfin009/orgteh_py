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

# ============================================================================
# FASTAPI ROUTER — مسارات الدفع (SpaceRemit)
# يُستورد في main.py عبر:  from services.payments import router as payments_router
# ============================================================================
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from database import get_redis, add_user_subscription
from services.auth import get_current_user_email

# ─── قائمة IPs المسموح بها لـ Webhook ────────────────────────────────────────
SPACEREMIT_WEBHOOK_IPS = [
    ip.strip()
    for ip in os.environ.get("SPACEREMIT_WEBHOOK_IPS", "").split(",")
    if ip.strip()
]

# ─── خريطة أسماء الخطط ────────────────────────────────────────────────────────
_PLAN_NAME_MAP = {
    "deepseek": "DeepSeek V3",  "kimi":    "Kimi k2",
    "mistral":  "Mistral Large", "gemma":   "Gemma 3",
    "llama":    "Llama 3.2",     "agents":  "Chat Agents",
    "global":   "Nexus Global",
}

router = APIRouter()

# ── Pydantic models ────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_name: str
    period:    str
    amount:    float


class VerifyPaymentRequest(BaseModel):
    payment_code: str
    plan_key:     str
    period:       str


# ══════════════════════════════════════════════════════════════════════════════
# /api/payments/checkout-data
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/payments/checkout-data")
async def api_checkout_data(request: Request, data: CheckoutRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized. Please login."}, status_code=401)
    try:
        result = await generate_payment_link(email, data.plan_name, data.period, data.amount)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# /api/payments/verify-and-activate
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/payments/verify-and-activate")
async def api_verify_and_activate(request: Request, data: VerifyPaymentRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized. Please login."}, status_code=401)

    plan_name = _PLAN_NAME_MAP.get(data.plan_key)
    if not plan_name:
        return JSONResponse({"error": f"Unknown plan key: {data.plan_key}"}, status_code=400)

    if data.period not in ("monthly", "yearly"):
        return JSONResponse({"error": "Invalid period. Use 'monthly' or 'yearly'."}, status_code=400)

    _redis = get_redis()
    if _redis:
        if _redis.exists(f"used_payment:{data.payment_code}"):
            return JSONResponse({"error": "This payment code has already been used."}, status_code=400)

    payment_info = await verify_spaceremit_payment(data.payment_code)
    if not payment_info:
        return JSONResponse(
            {"error": "Payment not verified. It may be pending, failed, or invalid."},
            status_code=400,
        )

    if _redis:
        try:
            _redis.setex(f"used_payment:{data.payment_code}", 365 * 24 * 3600, email)
        except Exception:
            pass

    success = add_user_subscription(email, data.plan_key, plan_name, data.period)
    if success:
        return JSONResponse({"status": "success", "message": f"Subscription '{plan_name}' activated successfully."})
    return JSONResponse(
        {"error": "Payment verified but failed to activate subscription. Please contact support."},
        status_code=500,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /api/webhooks/spaceremit
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/webhooks/spaceremit")
async def spaceremit_webhook(request: Request):
    try:
        if SPACEREMIT_WEBHOOK_IPS:
            client_ip = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or getattr(request.client, "host", "")
            )
            if client_ip not in SPACEREMIT_WEBHOOK_IPS:
                return JSONResponse({"error": "Forbidden"}, status_code=403)

        payload  = await request.json()
        tx_code  = (
            payload.get("spaceremit_code")
            or payload.get("transaction_id")
            or payload.get("payment_id")
        )

        if not tx_code:
            return JSONResponse({"error": "No transaction code provided"}, status_code=400)

        _redis = get_redis()
        if _redis and _redis.exists(f"used_payment:{tx_code}"):
            return JSONResponse({"status": "Already processed"}, status_code=200)

        payment_info = await verify_spaceremit_payment(tx_code)
        if not payment_info:
            return JSONResponse({"status": "Failed", "error": "Payment not verified"}, status_code=400)

        notes    = payment_info.get("notes", "")
        plan_key = period = email = ""
        for part in notes.split("|"):
            if part.startswith("plan:"):   plan_key = part.split(":", 1)[1].strip()
            if part.startswith("period:"): period   = part.split(":", 1)[1].strip()
            if part.startswith("user:"):   email    = part.split(":", 1)[1].strip()

        if not email or not plan_key or not period:
            return JSONResponse({"error": "Missing plan/period/user in notes"}, status_code=400)

        plan_name = _PLAN_NAME_MAP.get(plan_key, "Free Tier")
        success   = add_user_subscription(email, plan_key, plan_name, period)

        if success:
            if _redis:
                try:
                    _redis.setex(f"used_payment:{tx_code}", 365 * 24 * 3600, email)
                except Exception:
                    pass
            return JSONResponse({"status": "Success", "message": "Plan activated"})

        return JSONResponse({"status": "Failed", "error": "Could not activate plan"}, status_code=400)

    except Exception:
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)
