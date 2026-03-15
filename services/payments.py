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

# ─── مفاتيح Wayl ─────────────────────────────────────────────────────────────
WAYL_API_KEY        = os.environ.get("WAYL_API_KEY", "")
WAYL_IQD_RATE       = float(os.environ.get("WAYL_IQD_RATE", "1310"))   # سعر صرف USD → IQD
WAYL_WEBHOOK_SECRET = os.environ.get("WAYL_WEBHOOK_SECRET", "")
WAYL_BASE_URL       = "https://api.thewayl.com"
WAYL_WEBHOOK_URL    = os.environ.get("WAYL_WEBHOOK_URL",  "https://orgteh.com/api/webhooks/wayl")
WAYL_REDIRECT_URL   = os.environ.get("WAYL_REDIRECT_URL", "https://orgteh.com/pricing?payment=success")

if WAYL_API_KEY:
    logger.info("Wayl payment gateway configured successfully.")
else:
    logger.warning("WAYL_API_KEY not set – Wayl payments will be unavailable.")

# ============================================================================
# FASTAPI ROUTER — مسارات الدفع (SpaceRemit + Wayl)
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
    amount:    float   # USD — الأسعار تُعرض للمستخدم بالدولار دائماً


class VerifyPaymentRequest(BaseModel):
    payment_code: str
    plan_key:     str
    period:       str


class WaylCheckoutRequest(BaseModel):
    plan_key: str
    period:   str
    amount:   float   # USD — يُحوَّل إلى IQD داخلياً فقط، لا يُعرض للمستخدم
    method:   str     # "bank-cards" | "digital-wallets"


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

    # ── الدفع لا يزال قيد التأكيد (pending) ──────────────────────────────────
    if not payment_info:
        if _redis:
            import json
            pending_data = json.dumps({
                "plan_key":      data.plan_key,
                "plan_name":     plan_name,
                "period":        data.period,
                "payment_code":  data.payment_code,
                "submitted_at":  __import__('datetime').datetime.utcnow().isoformat()
            })
            # نحتفظ بالطلب المعلق لمدة 7 أيام — يُحذف تلقائياً بعدها
            _redis.setex(f"pending_payment:{email}:{data.plan_key}", 7 * 24 * 3600, pending_data)
            logger.info(f"Payment pending for {email} plan={plan_name} code={data.payment_code}")
        return JSONResponse(
            {"status": "pending", "message": "Your payment is being reviewed. We'll email you once confirmed."},
            status_code=202,
        )
    # ─────────────────────────────────────────────────────────────────────────

    if _redis:
        try:
            _redis.setex(f"used_payment:{data.payment_code}", 365 * 24 * 3600, email)
            # حذف أي pending قديم لهذه الخطة
            _redis.delete(f"pending_payment:{email}:{data.plan_key}")
        except Exception:
            pass

    success = add_user_subscription(email, data.plan_key, plan_name, data.period)
    if success:
        # ── إرسال إيميل تأكيد التفعيل ────────────────────────────────────────
        try:
            from datetime import datetime, timedelta
            from services.auth import send_subscription_email
            days = 365 if data.period == "yearly" else 30
            expires_date = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
            send_subscription_email(email, plan_name, expires_date, event="new")
            logger.info(f"Activation email sent to {email} for plan={plan_name}")
        except Exception as e:
            logger.warning(f"Failed to send activation email to {email}: {e}")
        # ─────────────────────────────────────────────────────────────────────
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
                    # حذف الـ pending عند التفعيل الناجح عبر Webhook
                    _redis.delete(f"pending_payment:{email}:{plan_key}")
                except Exception:
                    pass
            # ── إرسال إيميل تأكيد التفعيل عبر Webhook ───────────────────────
            try:
                from datetime import datetime, timedelta
                from services.auth import send_subscription_email
                days = 365 if period == "yearly" else 30
                expires_date = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
                send_subscription_email(email, plan_name, expires_date, event="new")
                logger.info(f"[Webhook] Activation email sent to {email} plan={plan_name}")
            except Exception as e:
                logger.warning(f"[Webhook] Failed to send activation email to {email}: {e}")
            # ─────────────────────────────────────────────────────────────────
            return JSONResponse({"status": "Success", "message": "Plan activated"})

        return JSONResponse({"status": "Failed", "error": "Could not activate plan"}, status_code=400)

    except Exception:
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# /api/payments/pending-plans  — جلب الخطط المعلقة للمستخدم الحالي
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/payments/pending-plans")
async def get_pending_plans(request: Request):
    """يُعيد قائمة بمفاتيح الخطط التي دفع المستخدم ثمنها وتنتظر التأكيد."""
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"pending": []})

    _redis = get_redis()
    if not _redis:
        return JSONResponse({"pending": []})

    import json
    pending = []
    try:
        pattern = f"pending_payment:{email}:*"
        for key in _redis.scan_iter(pattern):
            raw = _redis.get(key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                pending.append(data.get("plan_key"))
    except Exception as e:
        logger.warning(f"Failed to fetch pending plans for {email}: {e}")

    return JSONResponse({"pending": pending})


# ══════════════════════════════════════════════════════════════════════════════
# /api/payments/wayl-checkout
# ينشئ رابط دفع Wayl ويُعيده للواجهة الأمامية
# ملاحظة: السعر يُعرض للمستخدم بالدولار (USD) — يُحوَّل إلى IQD داخلياً فقط
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/payments/wayl-checkout")
async def api_wayl_checkout(request: Request, data: WaylCheckoutRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized. Please login."}, status_code=401)

    if not WAYL_API_KEY:
        return JSONResponse({"error": "Wayl payment gateway is not configured."}, status_code=503)

    plan_name = _PLAN_NAME_MAP.get(data.plan_key)
    if not plan_name:
        return JSONResponse({"error": f"Unknown plan key: {data.plan_key}"}, status_code=400)

    # ─── تحويل USD → IQD للـ Wayl API (الأسعار تُعرض للمستخدم بالدولار فقط) ──
    amount_iqd = int(round(data.amount * WAYL_IQD_RATE))
    # الحد الأدنى لـ Wayl هو 1000 دينار
    amount_iqd = max(amount_iqd, 1000)

    import uuid
    reference_id = f"orgteh-{data.plan_key}-{data.period}-{uuid.uuid4().hex[:12]}"

    payload = {
        "env":           "live",
        "referenceId":   reference_id,
        "total":         amount_iqd,
        "currency":      "IQD",
        "customParameter": f"plan:{data.plan_key}|period:{data.period}|user:{email}|usd:{data.amount}",
        "redirectionUrl": WAYL_REDIRECT_URL,
        "lineItem": [
            {
                "label":  f"Orgteh · {plan_name} ({data.period}) — ${data.amount} USD",
                "amount": amount_iqd,
                "type":   "increase"
            }
        ]
    }

    # ─── webhookSecret مطلوب من Wayl (10+ أحرف) — نولّده تلقائياً إن لم يُضبط ──
    import secrets as _secrets
    _webhook_secret = WAYL_WEBHOOK_SECRET if len(WAYL_WEBHOOK_SECRET) >= 10 else _secrets.token_hex(16)
    payload["webhookSecret"] = _webhook_secret

    # ─── webhookUrl اختياري — نُرسله فقط إن كان مضبوطاً ─────────────────────
    if WAYL_WEBHOOK_URL:
        payload["webhookUrl"] = WAYL_WEBHOOK_URL

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{WAYL_BASE_URL}/api/v1/links",
                json=payload,
                headers={
                    "X-WAYL-AUTHENTICATION": WAYL_API_KEY,
                    "Content-Type": "application/json"
                }
            )
            resp_data = resp.json()
            logger.info(f"[Wayl] Create link response {resp.status_code}: {resp_data}")

            if resp.status_code not in (200, 201):
                return JSONResponse(
                    {"error": resp_data.get("message", "Failed to create Wayl payment link.")},
                    status_code=502
                )

            link_url = resp_data.get("data", {}).get("url") or resp_data.get("data", {}).get("link")
            if not link_url:
                return JSONResponse({"error": "Wayl did not return a payment URL."}, status_code=502)

            # حفظ مرجع الدفع في Redis لحماية من التكرار
            _redis = get_redis()
            if _redis:
                import json as _json
                _redis.setex(
                    f"wayl_pending:{email}:{data.plan_key}",
                    7 * 24 * 3600,
                    _json.dumps({
                        "reference_id": reference_id,
                        "plan_key":     data.plan_key,
                        "plan_name":    plan_name,
                        "period":       data.period,
                        "amount_usd":   data.amount,
                        "amount_iqd":   amount_iqd
                    })
                )

            logger.info(f"[Wayl] Payment link created for {email}: {link_url}")
            return JSONResponse({"url": link_url, "reference_id": reference_id})

    except httpx.ReadTimeout:
        logger.error("[Wayl] Timeout creating payment link.")
        return JSONResponse({"error": "Wayl gateway timeout. Please try again."}, status_code=504)
    except Exception as e:
        logger.error(f"[Wayl] Unexpected error: {e}", exc_info=True)
        return JSONResponse({"error": "Unexpected error creating Wayl payment link."}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# /api/webhooks/wayl  — Webhook لاستقبال تأكيد الدفع من Wayl
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/webhooks/wayl")
async def wayl_webhook(request: Request):
    """
    يستقبل إشعار الدفع من Wayl بعد إتمام المستخدم للعملية.
    Wayl يرسل JWT في الـ body يمكن التحقق منه بـ WAYL_WEBHOOK_SECRET.
    """
    import json as _json
    import hmac, hashlib

    try:
        body = await request.body()
        payload = _json.loads(body)
        logger.info(f"[Wayl Webhook] Received: {payload}")

        # ─── التحقق من التوقيع (إن وُجد السر) ───────────────────────────────
        if WAYL_WEBHOOK_SECRET:
            signature = request.headers.get("x-wayl-signature", "")
            expected  = hmac.new(
                WAYL_WEBHOOK_SECRET.encode(),
                body,
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                logger.warning("[Wayl Webhook] Invalid signature — rejected.")
                return JSONResponse({"error": "Invalid signature"}, status_code=403)

        # ─── قراءة البيانات ───────────────────────────────────────────────────
        data_obj   = payload.get("data", payload)
        status     = str(data_obj.get("status", "")).lower()
        custom     = data_obj.get("customParameter", "")
        ref_id     = data_obj.get("referenceId", "")

        if status not in ("complete", "completed", "paid"):
            logger.info(f"[Wayl Webhook] Non-final status '{status}' — ignoring.")
            return JSONResponse({"status": "ignored"})

        # ─── تحليل الـ customParameter ───────────────────────────────────────
        plan_key = period = email = ""
        for part in custom.split("|"):
            if part.startswith("plan:"):   plan_key = part.split(":", 1)[1].strip()
            if part.startswith("period:"): period   = part.split(":", 1)[1].strip()
            if part.startswith("user:"):   email    = part.split(":", 1)[1].strip()

        if not email or not plan_key or not period:
            logger.error(f"[Wayl Webhook] Missing plan/period/user in customParameter: {custom}")
            return JSONResponse({"error": "Missing metadata"}, status_code=400)

        plan_name = _PLAN_NAME_MAP.get(plan_key, "Unknown Plan")

        _redis = get_redis()

        # ─── منع المعالجة المكررة ─────────────────────────────────────────────
        if _redis and _redis.exists(f"used_wayl_payment:{ref_id}"):
            logger.info(f"[Wayl Webhook] Already processed: {ref_id}")
            return JSONResponse({"status": "already_processed"})

        # ─── تفعيل الاشتراك ───────────────────────────────────────────────────
        success = add_user_subscription(email, plan_key, plan_name, period)
        if success:
            if _redis:
                _redis.setex(f"used_wayl_payment:{ref_id}", 365 * 24 * 3600, email)
                _redis.delete(f"wayl_pending:{email}:{plan_key}")
            try:
                from datetime import datetime, timedelta
                from services.auth import send_subscription_email
                days = 365 if period == "yearly" else 30
                expires_date = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
                send_subscription_email(email, plan_name, expires_date, event="new")
                logger.info(f"[Wayl Webhook] Activated {plan_name} for {email}")
            except Exception as e:
                logger.warning(f"[Wayl Webhook] Email failed for {email}: {e}")
            return JSONResponse({"status": "success"})

        logger.error(f"[Wayl Webhook] Failed to activate plan {plan_name} for {email}")
        return JSONResponse({"error": "Could not activate subscription"}, status_code=500)

    except Exception as e:
        logger.error(f"[Wayl Webhook] Unexpected error: {e}", exc_info=True)
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)
