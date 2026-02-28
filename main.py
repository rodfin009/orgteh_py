import os
import json
import sys
import gzip
import secrets
import asyncio
import brotli
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from pydantic import BaseModel

from services.auth import (
    LoginRequest, SendVerificationRequest, RegisterRequest,
    handle_send_verification, handle_register, handle_login, handle_logout,
    get_current_user_email, get_auth_context,
    handle_google_login, handle_google_callback,
    validate_password as _validate_password
)
from database import (
    get_user_by_email, get_user_by_api_key, get_global_stats, add_user_subscription,
    get_subscription_history, get_db_connection
)
from services.subscriptions import get_user_subscription_status
from services.limits import get_user_limits_and_usage, check_trial_allowance, has_active_paid_subscription
from services.providers import MODELS_METADATA, HIDDEN_MODELS, smart_chat_stream, acquire_provider_slot
from services.request_router import handle_chat_request
from customer_service import router as customer_service_router
from tools import router as tools_router
from tools.registry import TOOLS_DB
from code_processor import process_code_merge_stream, CODE_HUB_MODELS_INFO
from services.payments import generate_payment_link, verify_spaceremit_payment

# ============================================================================
# CONFIGURATION
# ============================================================================

SECRET_KEY   = os.environ.get("SESSION_SECRET_KEY", "super-secret-key-change-in-production")

# ─── Admin ───────────────────────────────────────────────────────────────────
ADMIN_EMAIL  = "rodfin0202@gmail.com"
ADMIN_TOKEN  = os.environ.get("ADMIN_TOKEN", secrets.token_urlsafe(32))

# ─── SpaceRemit Webhook IP Whitelist (اختياري — اتركه فارغاً إذا غير معروف) ─
SPACEREMIT_WEBHOOK_IPS = [
    ip.strip() for ip in os.environ.get("SPACEREMIT_WEBHOOK_IPS", "").split(",") if ip.strip()
]

# ============================================================================
# OPTIMIZED STATIC FILES
# ============================================================================
class OptimizedStaticFiles(StaticFiles):
    def __init__(self, *args, cache_control="public, max-age=31536000, immutable", **kwargs):
        self.cache_control = cache_control
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if any(path.endswith(ext) for ext in ['.css', '.js', '.webp', '.png', '.jpg', '.woff2', '.woff']):
            response.headers["Cache-Control"] = self.cache_control
            response.headers["Vary"] = "Accept-Encoding"
        if path.endswith(('.js', '.css', '.html')):
            response.headers["Vary"] = "Accept-Encoding"
        return response

# ============================================================================
# SECURITY HEADERS MIDDLEWARE  (Pure ASGI — safe for streaming)
# ============================================================================
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.datastructures import MutableHeaders

class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                # ✅ FIX: ALLOWALL غير صالح في أي متصفح — استبدلناه بـ SAMEORIGIN
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = (
                    "accelerometer=(), camera=(), geolocation=(), "
                    "gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
                )
                headers["Link"] = (
                    "<https://fonts.gstatic.com>; rel=preconnect, "
                    "<https://cdnjs.cloudflare.com>; rel=preconnect, "
                    "<https://fonts.googleapis.com>; rel=preconnect"
                )
                content_type = headers.get("content-type", "")
                if any(x in content_type for x in ["ndjson", "event-stream", "octet-stream", "text/plain"]):
                    headers["X-Accel-Buffering"] = "no"
                    headers["Cache-Control"] = "no-cache, no-transform"
                    headers["Transfer-Encoding"] = "chunked"
            await send(message)

        await self.app(scope, receive, send_with_headers)

# ============================================================================
# APP SETUP
# ============================================================================
app = FastAPI(title="Orgteh Infra", docs_url=None, redoc_url=None)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False)

BASE_DIR      = Path(__file__).resolve().parent
STATIC_DIR    = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

if not STATIC_DIR.exists():    STATIC_DIR.mkdir()
if not TEMPLATES_DIR.exists(): TEMPLATES_DIR.mkdir()

app.mount("/static", OptimizedStaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(customer_service_router)

# ============================================================================
# HEALTH CHECK & WARM-UP
# ============================================================================
@app.get("/api/health")
async def health_check():
    return JSONResponse({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "cache": "enabled",
        "compression": "gzip"
    })

@app.get("/api/ready")
async def readiness_check():
    return {"ready": True}

# ============================================================================
# Static Files Debug
# ============================================================================
@app.get("/debug/static-files")
async def check_static_files():
    file_structure = []
    total_size = 0
    if STATIC_DIR.exists():
        for root, dirs, files in os.walk(str(STATIC_DIR)):
            for name in files:
                full_path = os.path.join(root, name)
                rel_path  = os.path.relpath(full_path, str(BASE_DIR))
                size      = os.path.getsize(full_path)
                total_size += size
                file_structure.append({"path": rel_path, "size_kb": round(size / 1024, 2)})
    return {
        "base_dir": str(BASE_DIR),
        "static_dir": str(STATIC_DIR),
        "total_files": len(file_structure),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "webp_files": [f for f in file_structure if f["path"].endswith(".webp")],
        "found_files": file_structure[:20]
    }

# ============================================================================
# ADMIN HELPER — التحقق من صلاحيات الأدمن
# ============================================================================
def verify_admin_request(request: Request) -> str:
    """
    يقبل الطلب إذا:
      - الجلسة النشطة هي لـ ADMIN_EMAIL
      - أو الـ Header يحمل X-Admin-Token الصحيح
    """
    email = get_current_user_email(request)
    if email == ADMIN_EMAIL:
        return email
    token = request.headers.get("X-Admin-Token", "")
    if token and token == ADMIN_TOKEN:
        return ADMIN_EMAIL
    raise HTTPException(status_code=403, detail="Admin access required")


# ============================================================================
# TEMPLATE CONTEXT HELPER
# ============================================================================
def get_template_context(request: Request, lang: str = "en"):
    try:
        context = get_auth_context(request)
    except Exception:
        context = {"is_logged_in": False, "user_email": "", "user_api_key": ""}

    context["request"]    = request

    if lang not in ["ar", "en"]:
        lang = "en"
    context["lang"]        = lang
    context["api_key"]     = context.get("user_api_key", "")
    context["user_email"]  = context.get("user_email", "")
    context["is_logged_in"] = context.get("is_logged_in", False)

    # ✅ Admin token — يُرسَل للقالب فقط للمسؤول
    user_email = context.get("user_email", "")
    context["admin_token"] = ADMIN_TOKEN if user_email == ADMIN_EMAIL else ""
    context["is_admin"]    = (user_email == ADMIN_EMAIL)

    models_data = []
    for m in MODELS_METADATA:
        if m["id"] in HIDDEN_MODELS:
            continue
        if len(models_data) >= 12:
            break
        model_copy = dict(m)
        if "image" in model_copy and model_copy["image"]:
            original_url = model_copy["image"]
            if "cdn.jsdelivr.net" in original_url or "https://cdn." in original_url:
                model_id = model_copy.get("short_key", model_copy.get("id", "")).lower()
                if "deepseek" in original_url.lower():
                    model_copy["image"] = "/static/deepseek.webp"
                elif "meta" in original_url.lower() or "llama" in model_id:
                    model_copy["image"] = "/static/meta.webp"
                elif "gemma" in original_url.lower():
                    model_copy["image"] = "/static/gemma.webp"
                elif "mistral" in original_url.lower():
                    model_copy["image"] = "/static/mistral.webp"
                elif "kimi" in original_url.lower():
                    model_copy["image"] = "/static/kimi.webp"
                else:
                    model_copy["image"] = f"/static/{model_id}.webp"
        models_data.append(model_copy)

    context["models_metadata"] = models_data
    return context

# ============================================================================
# ROUTES
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    return RedirectResponse("/en/")

@app.get("/{lang}/", response_class=HTMLResponse)
async def home(request: Request, lang: str):
    return templates.TemplateResponse("index.html", get_template_context(request, lang))

@app.get("/login", response_class=HTMLResponse)
async def login_redirect(request: Request):
    lang_cookie = request.cookies.get("preferred_lang")
    if lang_cookie in ["ar", "en"]:
        return RedirectResponse(f"/{lang_cookie}/auth")
    return RedirectResponse("/en/login")

@app.get("/{lang}/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/register", response_class=HTMLResponse)
async def register_redirect(request: Request):
    lang_cookie = request.cookies.get("preferred_lang")
    lang = lang_cookie if lang_cookie in ["ar", "en"] else "en"
    return RedirectResponse(f"/{lang}/auth?tab=register")

@app.get("/{lang}/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/profile", response_class=HTMLResponse)
async def profile_redirect():
    return RedirectResponse("/en/profile")

@app.get("/{lang}/profile", response_class=HTMLResponse)
async def profile_page(request: Request, lang: str):
    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login")
    try:
        user       = get_user_by_email(email)
        sub_status = get_user_subscription_status(email)
        limits, usage = get_user_limits_and_usage(email)
        context    = get_template_context(request, lang)

        active_plans = user.get("active_plans", [])
        now          = datetime.utcnow()
        valid_plans  = []
        for p in active_plans:
            try:
                if datetime.fromisoformat(p["expires"]) > now:
                    valid_plans.append(p)
            except:
                pass

        plan_names        = [p["name"] for p in valid_plans]
        display_plan_name = " + ".join(plan_names) if plan_names else "Free Tier"

        def calc_pct(used, limit):
            return min(100, (used / limit) * 100) if limit > 0 else 0

        context.update({
            "api_key":       context.get("user_api_key", ""),
            "plan_name":     display_plan_name,
            "active_plans":  valid_plans,
            "is_active_sub": len(valid_plans) > 0,
            "is_perpetual":  sub_status.get("is_perpetual", False) if sub_status else False,
            "days_left":     sub_status.get("days_left", 0) if sub_status else 0,
            "usage":         usage  if usage  else {},
            "limits":        limits if limits else {},
            "pct_deepseek":  calc_pct(usage.get("deepseek", 0),      limits.get("deepseek", 0)),
            "pct_kimi":      calc_pct(usage.get("kimi", 0),           limits.get("kimi", 0)),
            "pct_mistral":   calc_pct(usage.get("mistral", 0),        limits.get("mistral", 0)),
            "pct_llama":     calc_pct(usage.get("llama", 0),          limits.get("llama", 0)),
            "pct_gemma":     calc_pct(usage.get("gemma", 0),          limits.get("gemma", 0)),
            "pct_extra":     calc_pct(usage.get("unified_extra", 0),  limits.get("unified_extra", 0)),
        })
        return templates.TemplateResponse("insights.html", context)
    except Exception:
        return RedirectResponse(f"/{lang}/login")

# ============================================================================
# SETTINGS PAGES
# ============================================================================

from github_integration import (
    handle_github_login, handle_github_callback, handle_github_logout,
    DeployRequest, is_github_connected, get_github_token, one_click_deploy,
    get_github_context
)

@app.get("/settings", response_class=HTMLResponse)
async def settings_redirect():
    return RedirectResponse("/ar/settings/account")

@app.get("/{lang}/settings", response_class=HTMLResponse)
async def settings_lang_redirect(request: Request, lang: str):
    qs  = request.url.query
    url = f"/{lang}/settings/integrations" if "github_connected" in qs else f"/{lang}/settings/account"
    if qs:
        url += f"?{qs}"
    return RedirectResponse(url)

@app.get("/{lang}/settings/{tab}", response_class=HTMLResponse)
async def settings_page_tab(request: Request, lang: str, tab: str):
    valid_tabs = ["account", "integrations", "notifications", "billing", "security"]
    if tab not in valid_tabs:
        return RedirectResponse(f"/{lang}/settings/account")

    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login?next=/{lang}/settings/{tab}")

    try:
        user = get_user_by_email(email)
        if not user or not isinstance(user, dict):
            request.session.clear()
            return RedirectResponse(f"/{lang}/login")

        try:
            sub_status = get_user_subscription_status(email)
        except Exception:
            sub_status = {}

        active_plans = user.get("active_plans", []) if user else []
        now          = datetime.utcnow()
        valid_plans  = []
        if isinstance(active_plans, list):
            for p in active_plans:
                try:
                    if isinstance(p, dict) and "expires" in p:
                        if datetime.fromisoformat(p["expires"]) > now:
                            valid_plans.append(p)
                except Exception:
                    pass

        plan_names   = [p.get("name", "Unknown Plan") for p in valid_plans if isinstance(p, dict)]
        display_plan = " + ".join(plan_names) if plan_names else "Free Tier"
        days_left    = sub_status.get("days_left", 0) if isinstance(sub_status, dict) else 0

        context = get_template_context(request, lang)

        try:
            sub_history = get_subscription_history(email)
        except Exception:
            sub_history = []

        context.update({
            "active_tab":         tab,
            "plan_name":          display_plan,
            "active_plans":       valid_plans,
            "is_active_sub":      bool(valid_plans),
            "days_left":          days_left,
            "profile_first_name": user.get("first_name", ""),
            "profile_last_name":  user.get("last_name", ""),
            "subscription_history": sub_history,
            "now":                datetime.utcnow(),
        })

        if tab == "integrations":
            context.update(get_github_context(request))
            context["github_just_connected"] = request.query_params.get("github_connected") == "true"

        return templates.TemplateResponse("settings.html", context)

    except Exception as e:
        print(f"=== [Settings Page Error] ===\n{e}")
        traceback.print_exc()
        fallback_context = {
            "request": request, "lang": lang, "active_tab": tab,
            "user_email": email, "is_logged_in": True,
            "plan_name": "Free Tier", "active_plans": [],
            "is_active_sub": False, "days_left": 0,
            "profile_first_name": "", "profile_last_name": "",
            "subscription_history": []
        }
        return templates.TemplateResponse("settings.html", fallback_context)

# ─── Settings API Endpoints ──────────────────────────────────────────────────

class ProfileUpdateRequest(BaseModel):
    first_name: str = ""
    last_name:  str = ""

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str

class DeleteAccountRequest(BaseModel):
    email: str


@app.post("/api/settings/profile")
async def update_profile(request: Request, data: ProfileUpdateRequest):
    """
    ✅ FIX: كان يستخدم redis.hset() الذي ينشئ نوع بيانات مختلف (Hash) ويُفسد
    cache المستخدم. الآن يقرأ البيانات كاملة ثم يحدثها كـ JSON string في
    كل من Redis وTiDB.
    """
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")

    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")

    user["first_name"] = data.first_name
    user["last_name"]  = data.last_name

    # 1. Update TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET data = %s WHERE email = %s",
                    (json.dumps(user), email)
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            conn.close()

    # 2. Update Redis (كـ JSON string — نفس النوع الذي يقرأه get_user_by_email)
    from database import redis as _redis
    if _redis:
        try:
            _redis.set(f"user:{email}", json.dumps(user))
        except Exception:
            pass

    return {"message": "تم حفظ الاسم بنجاح"}


@app.post("/api/settings/change-password")
async def change_password_endpoint(request: Request, data: ChangePasswordRequest):
    """
    ✅ FIX 1: كان يستخدم redis.hset() — أصبح يكتب JSON string كامل.
    ✅ FIX 2: توحيد قواعد التحقق من كلمة المرور مع auth.py.
    ✅ FIX 3: يحدث TiDB أيضاً وليس Redis فقط.
    """
    import bcrypt as _bcrypt
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")

    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")

    # التحقق من الباسورد الحالي
    try:
        valid = _bcrypt.checkpw(
            data.current_password.encode("utf-8"),
            user["password"].encode("utf-8")
        )
    except Exception:
        valid = False

    if not valid:
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")

    # ✅ FIX: استخدام validate_password الموحّدة من auth.py
    is_valid_pw, error_msg = _validate_password(data.new_password)
    if not is_valid_pw:
        raise HTTPException(status_code=400, detail=error_msg)

    hashed     = _bcrypt.hashpw(data.new_password.encode("utf-8"), _bcrypt.gensalt(12)).decode("utf-8")
    user["password"] = hashed

    # 1. Update TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s, data = %s WHERE email = %s",
                    (hashed, json.dumps(user), email)
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            conn.close()

    # 2. Update Redis (كـ JSON string كامل)
    from database import redis as _redis
    if _redis:
        try:
            _redis.set(f"user:{email}", json.dumps(user))
        except Exception:
            pass

    return {"message": "تم تغيير كلمة المرور بنجاح"}


@app.delete("/api/settings/delete-account")
async def delete_account_endpoint(request: Request, data: DeleteAccountRequest):
    """
    ✅ FIX: كان يحذف من Redis فقط — الآن يحذف من TiDB أولاً ثم Redis،
    ويزيل مفتاح API القديم.
    """
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")
    if email != data.email:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني غير مطابق")

    # جلب بيانات المستخدم لنعرف مفتاح API القديم
    user    = get_user_by_email(email)
    old_key = user.get("api_key") if user else None

    # 1. Delete from TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE email = %s", (email,))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            conn.close()

    # 2. Delete from Redis (user + github + api_key index)
    from database import redis as _redis
    if _redis:
        try:
            _redis.delete(f"user:{email}")
            _redis.delete(f"github:{email}")
            if old_key:
                _redis.delete(f"api_key:{old_key}")
        except Exception:
            pass

    request.session.clear()
    return {"message": "تم حذف الحساب بنجاح"}


@app.post("/auth/logout-all")
async def logout_all_devices(request: Request):
    request.session.clear()
    return {"message": "تم تسجيل الخروج من جميع الأجهزة"}

# ─── End Settings ─────────────────────────────────────────────────────────────

@app.get("/accesory", response_class=HTMLResponse)
async def accesory_redirect():
    return RedirectResponse("/en/accesory")

@app.get("/{lang}/accesory", response_class=HTMLResponse)
async def accesory_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["tools"]       = list(TOOLS_DB.values())
    context["active_tool"] = None
    return templates.TemplateResponse("tools.html", context)

@app.get("/{lang}/accesory/{tool_id}", response_class=HTMLResponse)
async def accesory_detail_page(request: Request, lang: str, tool_id: str):
    context = get_template_context(request, lang)
    tool    = TOOLS_DB.get(tool_id)
    if not tool:
        return RedirectResponse(f"/{lang}/accesory")
    context["active_tool"] = tool
    context["tools"]       = list(TOOLS_DB.values())
    return templates.TemplateResponse("tools.html", context)

@app.get("/cart", response_class=HTMLResponse)
async def cart_redirect():
    return RedirectResponse("/en/cart")

@app.get("/{lang}/cart", response_class=HTMLResponse)
async def cart_page(request: Request, lang: str):
    try:
        context = get_template_context(request, lang)
        if context.get("is_logged_in"):
            try:
                sub_status = get_user_subscription_status(context["user_email"])
                context["current_plan"] = sub_status.get("plan_name", "Free Tier") if sub_status else "Free Tier"
            except Exception:
                context["current_plan"] = "Free Tier"
        else:
            context["current_plan"] = "Free Tier"
        return templates.TemplateResponse("pricing.html", context)
    except Exception:
        return templates.TemplateResponse("pricing.html", {
            "request": request, "lang": lang, "is_logged_in": False,
            "user_email": "", "api_key": "", "current_plan": "Free Tier", "models_metadata": []
        })

@app.get("/contacts", response_class=HTMLResponse)
async def contacts_redirect():
    return RedirectResponse("/en/contacts")

@app.get("/{lang}/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request, lang: str):
    return templates.TemplateResponse("contacts.html", get_template_context(request, lang))

@app.get("/auth", response_class=HTMLResponse)
async def auth_redirect():
    return RedirectResponse("/en/login")

@app.get("/{lang}/auth", response_class=HTMLResponse)
async def auth_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_redirect():
    return RedirectResponse("/en/cart")

@app.get("/{lang}/pricing", response_class=HTMLResponse)
async def pricing(request: Request, lang: str):
    try:
        context = get_template_context(request, lang)
        if context.get("is_logged_in"):
            try:
                sub_status = get_user_subscription_status(context["user_email"])
                context["current_plan"] = sub_status.get("plan_name", "Free Tier") if sub_status else "Free Tier"
            except Exception:
                context["current_plan"] = "Free Tier"
        else:
            context["current_plan"] = "Free Tier"
        return templates.TemplateResponse("pricing.html", context)
    except Exception:
        return templates.TemplateResponse("pricing.html", {
            "request": request, "lang": lang, "is_logged_in": False,
            "user_email": "", "api_key": "", "current_plan": "Free Tier", "models_metadata": []
        })

@app.get("/models", response_class=HTMLResponse)
async def models_redirect():
    return RedirectResponse("/en/models")

@app.get("/{lang}/models", response_class=HTMLResponse)
async def models_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["models"] = context["models_metadata"]
    return templates.TemplateResponse("models.html", context)

@app.get("/{lang}/models/{model_key}", response_class=HTMLResponse)
async def model_detail_page(request: Request, lang: str, model_key: str):
    context    = get_template_context(request, lang)
    model_info = next((m for m in MODELS_METADATA if m.get("short_key") == model_key or model_key in m.get("id", "")), None)
    if not model_info:
        return RedirectResponse(f"/{lang}/models")
    context["model"]         = model_info
    context["models"]        = context["models_metadata"]
    context["seo_model_key"] = model_key
    return templates.TemplateResponse("models.html", context)

@app.get("/api/model-description/{model_key}")
async def get_model_description(model_key: str, lang: str = "en"):
    try:
        file_path = STATIC_DIR / "models_translation" / f"{model_key}.html"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return JSONResponse(
                {"html": html_content},
                headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400", "Vary": "Accept-Encoding"}
            )
        return JSONResponse({"error": f"Model description not found for: {model_key}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================================
# OAUTH ROUTES (GitHub & Google)
# ============================================================================

@app.get("/auth/github/login")
async def github_login(request: Request):
    return await handle_github_login(request)

@app.get("/auth/github/callback")
async def github_callback(request: Request, code: str, state: str):
    return await handle_github_callback(request, code, state)

@app.post("/auth/github/logout")
async def github_logout(request: Request):
    return await handle_github_logout(request)

@app.get("/auth/google/login")
async def google_login(request: Request):
    return await handle_google_login(request)

@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str, state: str):
    return await handle_google_callback(request, code, state)

@app.post("/api/deploy/github")
async def deploy_to_github(request: Request, deploy_data: DeployRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, 401)
    if not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected", "connect_url": "/auth/github/login"}, 403)
    token = get_github_token(request)
    try:
        body  = await request.json()
        files = body.get("files", [])
        if not files:
            return JSONResponse({"error": "No files provided"}, 400)
        result = await one_click_deploy(
            access_token=token,
            project_name=deploy_data.repo_name,
            files=files,
            description=deploy_data.description,
            is_private=deploy_data.is_private,
            enable_pages=deploy_data.enable_pages
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/github/repos")
async def get_user_repos(request: Request):
    email = get_current_user_email(request)
    if not email or not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected"}, 403)
    from github_integration import GitHubDeployer
    token    = get_github_token(request)
    deployer = GitHubDeployer(token)
    repos    = deployer.get_repos()
    return {"repos": repos}

app.include_router(tools_router, prefix="/api")
app.include_router(tools_router, prefix="/v1")

# ============================================================================
# PREMIUM TOOLS MIDDLEWARE — حماية أدوات OCR و RAG
# ============================================================================
PREMIUM_TOOL_IDS = {"nexus-ocr", "orgteh-ocr", "nexus-rag", "orgteh-rag"}

@app.middleware("http")
async def premium_tools_guard(request: Request, call_next):
    path = request.url.path
    if "/tools/execute/" in path:
        parts = path.split("/tools/execute/")
        if len(parts) == 2:
            tool_id = parts[1].strip("/").split("/")[0]
            if tool_id in PREMIUM_TOOL_IDS:
                email = get_current_user_email(request)
                if not email:
                    return JSONResponse(
                        {"error": "يجب تسجيل الدخول لاستخدام هذه الأداة. / Login required to use this tool."},
                        status_code=401
                    )
                if not has_active_paid_subscription(email):
                    return JSONResponse(
                        {
                            "error": "هذه الأداة متاحة للمشتركين فقط. / This tool requires an active paid subscription.",
                            "upgrade_url": "/cart"
                        },
                        status_code=403
                    )
    return await call_next(request)

# ─── Redirects ───────────────────────────────────────────────────────────────

@app.get("/tools", response_class=HTMLResponse)
async def tools_legacy_redirect():
    return RedirectResponse("/en/accesory")

@app.get("/{lang}/tools", response_class=HTMLResponse)
async def tools_legacy_lang_redirect(lang: str):
    return RedirectResponse(f"/{lang}/accesory")

@app.get("/code-hub", response_class=HTMLResponse)
async def code_hub_redirect():
    return RedirectResponse("/en/code-hub")

@app.get("/{lang}/code-hub", response_class=HTMLResponse)
async def code_hub_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["mode"] = "standard"
    merged_models   = []
    for m in MODELS_METADATA:
        if m["id"] in HIDDEN_MODELS:
            continue
        model_entry = m.copy()
        if m["id"] in CODE_HUB_MODELS_INFO:
            model_entry.update(CODE_HUB_MODELS_INFO[m["id"]])
        else:
            model_entry.update({"desc_en": "General AI", "desc_ar": "ذكاء اصطناعي عام", "badge_en": "", "badge_ar": ""})
        merged_models.append(model_entry)
    context["models"] = merged_models
    context["tools"]  = list(TOOLS_DB.values())
    return templates.TemplateResponse("code_hub/index.html", context)

@app.get("/enterprise", response_class=HTMLResponse)
async def ent_redirect():
    return RedirectResponse("/en/enterprise")

@app.get("/{lang}/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request, lang: str):
    return templates.TemplateResponse("enterprise.html", get_template_context(request, lang))

@app.get("/docs", response_class=HTMLResponse)
async def docs_redirect():
    return RedirectResponse("/en/docs")

@app.get("/{lang}/docs", response_class=HTMLResponse)
async def docs_page(request: Request, lang: str):
    return templates.TemplateResponse("docs.html", get_template_context(request, lang))

@app.get("/policy", response_class=HTMLResponse)
async def policy_redirect():
    return RedirectResponse("/en/policy")

@app.get("/{lang}/policy", response_class=HTMLResponse)
async def policy_page(request: Request, lang: str):
    return templates.TemplateResponse("policy.html", get_template_context(request, lang))

@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    stats   = get_global_stats()
    context = get_template_context(request, "en")
    context.update({"stats": stats, "last_update": datetime.utcnow().isoformat(), "active_nodes": 5})
    return templates.TemplateResponse("performance.html", context)

@app.get("/{lang}/performance", response_class=HTMLResponse)
async def performance_page_lang(request: Request, lang: str):
    stats   = get_global_stats()
    context = get_template_context(request, lang)
    context.update({"stats": stats, "last_update": datetime.utcnow().isoformat(), "active_nodes": 5})
    return templates.TemplateResponse("performance.html", context)

@app.get("/dashboard", response_class=HTMLResponse)
async def dash_redirect():
    return RedirectResponse("/en/profile")

@app.get("/{lang}/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, lang: str):
    return RedirectResponse(f"/{lang}/profile")

# ============================================================================
# AUTH API ENDPOINTS
# ============================================================================

@app.post("/auth/send-verification")
async def send_verification(request: Request, data: SendVerificationRequest):
    return await handle_send_verification(request, data)

@app.post("/auth/register")
async def register(request: Request, data: RegisterRequest):
    return await handle_register(request, data)

@app.post("/auth/login")
async def login(request: Request, data: LoginRequest):
    return await handle_login(request, data)

@app.post("/auth/logout")
async def logout(request: Request):
    return await handle_logout(request)

@app.post("/api/set-language")
async def set_language(request: Request):
    body = await request.json()
    lang = body.get("lang", "en")
    if lang not in ["ar", "en"]:
        lang = "en"
    response = JSONResponse({"ok": True, "lang": lang})
    response.set_cookie("preferred_lang", lang, max_age=60 * 60 * 24 * 365, path="/", samesite="lax")
    return response

# ============================================================================
# CHAT ENDPOINTS
# ============================================================================

@app.post("/api/chat/trial")
async def trial_chat_endpoint(request: Request):
    try:
        data  = await request.json()
        email = get_current_user_email(request)
        if not email:
            return JSONResponse({"error": "Unauthorized"}, 401)

        model_id = data.get("model_id")
        if model_id in HIDDEN_MODELS:
            return JSONResponse({"error": {"message": "Model unavailable.", "code": "model_unavailable"}}, 404)

        allowed = await check_trial_allowance(email, model_id)
        if not allowed:
            return JSONResponse({"error": "Daily trial limit reached (10 msgs)."}, 429)

        payload = {
            "model":       model_id,
            "messages":    data.get("messages", []),
            "temperature": float(data.get("temperature", 0.5)),
            "top_p":       float(data.get("top_p", 0.7)),
            "max_tokens":  int(data.get("max_tokens", 1024)),
            "stream":      data.get("stream", True)
        }
        if "extra_params" in data:
            payload.update(data["extra_params"])
        if "deepseek" in payload["model"] and "chat_template_kwargs" not in payload:
            payload["chat_template_kwargs"] = {"thinking": True}

        await acquire_provider_slot(is_priority=False)
        return StreamingResponse(smart_chat_stream(payload, email, is_trial=True), media_type="text/event-stream")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/chat")
async def internal_chat_ui(request: Request):
    try:
        data  = await request.json()
        email = get_current_user_email(request)
        if not email:
            return JSONResponse({"error": "Unauthorized"}, 401)

        is_trial = data.get("is_trial", False)
        if is_trial:
            model_id = data.get("model_id")
            allowed  = await check_trial_allowance(email, model_id)
            if not allowed:
                return JSONResponse({"error": "Daily trial limit reached."}, 429)
            payload = {
                "model":       model_id,
                "messages":    data.get("messages", [{"role": "user", "content": data.get("message")}]),
                "temperature": float(data.get("temperature", 0.5)),
                "stream":      data.get("stream", False)
            }
            if "deepseek" in payload["model"]:
                payload["chat_template_kwargs"] = {"thinking": True}
            await acquire_provider_slot(is_priority=False)
            return StreamingResponse(smart_chat_stream(payload, email, is_trial=True), media_type="text/event-stream")

        payload = {
            "model":       data.get("model_id"),
            "messages":    [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "stream":      data.get("stream", False)
        }
        if "deepseek" in payload["model"]:
            payload["chat_template_kwargs"] = {"thinking": True}

        return await handle_chat_request(email, payload)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/chat/completions")
async def openai_compatible_proxy(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)

    api_key = auth_header.split(" ")[1]
    user    = get_user_by_api_key(api_key)
    if not user:
        return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, 400)

    return await handle_chat_request(user["email"], body)


@app.post("/api/process-code")
async def process_code_endpoint(
    request:      Request,
    instruction:  str          = Form(...),
    target_model: str          = Form("deepseek-ai/deepseek-v3.2"),
    target_tools: str          = Form(""),
    chat_history: str          = Form("[]"),
    chat_mode:    str          = Form("build"),
    files:        list[UploadFile] = File(default=[])
):
    if target_model in HIDDEN_MODELS:
        return JSONResponse({"error": "Target model unavailable"}, 400)

    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Login required"}, 401)

    user         = get_user_by_email(email)
    user_api_key = user.get("api_key", "YOUR_API_KEY")

    try:
        history_list = json.loads(chat_history)
    except Exception:
        history_list = []

    files_data = []
    if files:
        for f in files:
            if f.filename:
                try:
                    files_data.append({"name": f.filename, "content": (await f.read()).decode("utf-8")})
                except Exception:
                    pass

    async def event_generator():
        async for event in process_code_merge_stream(instruction, files_data, user_api_key, target_model, history_list, target_tools, chat_mode):
            if event["type"] in ["thinking", "code", "error"]:
                yield json.dumps({"type": event["type"], "content": event["content"]}, ensure_ascii=False).encode("utf-8") + b"\n"
                await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform", "Content-Encoding": "identity"}
    )


@app.post("/api/generate-key")
async def regenerate_my_key(request: Request):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, 401)
    try:
        from services.auth import generate_nexus_key
        from database import update_api_key as _update_key
        new_key = generate_nexus_key()
        success = _update_key(email, new_key)
        if success:
            return JSONResponse({"key": new_key, "ok": True})
        return JSONResponse({"error": "Failed to save new key"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


from telegram_bot import notify_contact_form, notify_enterprise_form


@app.post("/api/support/chat")
async def support_chat(request: Request):
    """
    ✅ FIX: أُضيف Rate Limiting — 10 رسائل في الدقيقة لكل IP لمنع
    استنزاف حصة NVIDIA API من قِبل أي طرف خارجي.
    """
    try:
        # ─── Rate Limiting ────────────────────────────────────────
        from database import redis as _redis
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
                    or getattr(request.client, "host", "unknown")
        if _redis:
            rate_key = f"support_rate:{client_ip}"
            count    = _redis.incr(rate_key)
            if count == 1:
                _redis.expire(rate_key, 60)  # نافذة 60 ثانية
            if count > 10:
                return JSONResponse({"error": "Rate limit exceeded. Please wait a moment."}, status_code=429)

        body    = await request.json()
        message = body.get("message", "").strip()
        lang    = body.get("lang", "en")

        if not message:
            return JSONResponse({"error": "No message provided"}, 400)

        system_prompt = (
            "أنت مساعد خدمة عملاء ذكي لموقع Orgteh Infra. "
            "يجب أن ترد دائماً باللغة العربية فقط. "
            "موقعنا يوفر بنية تحتية موحدة للوصول إلى نماذج الذكاء الاصطناعي. "
            "كن مفيداً ومختصراً وودياً."
        ) if lang == "ar" else (
            "You are a smart customer support assistant for Orgteh Infra. "
            "Always reply in English only. Be helpful, concise and friendly."
        )

        payload = {
            "model":      "gpt-4o-mini",
            "messages":   [{"role": "system", "content": system_prompt}, {"role": "user", "content": message}],
            "max_tokens": 800,
            "stream":     True
        }

        async def generate():
            try:
                async for chunk in smart_chat_stream(payload, "support@orgteh.com", is_trial=True):
                    text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, bytes) else str(chunk)
                    if text.startswith("data: "):
                        text = text[6:]
                    if text.strip() and text.strip() != "[DONE]":
                        try:
                            content = json.loads(text).get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                yield content.encode("utf-8")
                        except Exception:
                            pass
            except Exception:
                yield ("عذراً، حدث خطأ." if lang == "ar" else "Sorry, an error occurred.").encode("utf-8")

        return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@app.get("/api/github/status")
async def github_status(request: Request):
    try:
        connected = is_github_connected(request)
        context   = {}
        if connected:
            email = get_current_user_email(request)
            if email:
                try:
                    from database import redis as _redis
                    if _redis:
                        gh_info = _redis.hgetall(f"github:{email}")
                        if gh_info:
                            def _dec(v): return v.decode() if isinstance(v, bytes) else v
                            context = {
                                "login":  _dec(gh_info.get(b"login", gh_info.get("login", ""))),
                                "avatar": _dec(gh_info.get(b"avatar", gh_info.get("avatar", "")))
                            }
                except Exception:
                    pass
        return JSONResponse({"connected": connected, "user": context if connected else None, "connect_url": "/auth/github/login"})
    except Exception as e:
        return JSONResponse({"connected": False, "error": str(e)}, 500)


@app.post("/api/contact")
async def api_contact(request: Request):
    try:
        body    = await request.json()
        name    = body.get("name", "").strip()
        email   = body.get("email", "").strip()
        message = body.get("message", "").strip()
        if not name or not email or not message:
            return JSONResponse({"detail": "جميع الحقول مطلوبة."}, status_code=400)
        await notify_contact_form(name, email, message)
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"detail": "خطأ في الخادم."}, status_code=500)


@app.post("/api/enterprise/contact")
async def api_enterprise_contact(request: Request):
    try:
        body           = await request.json()
        project_type   = body.get("projectType", "").strip()
        volume         = body.get("volume", "").strip()
        needs          = body.get("needs", "").strip()
        contact_method = body.get("contactMethod", "").strip()
        contact_value  = body.get("contactValue", "").strip()
        description    = body.get("description", "").strip()
        if not project_type or not volume or not needs or not contact_value:
            return JSONResponse({"detail": "يرجى ملء الحقول المطلوبة."}, status_code=400)
        await notify_enterprise_form(project_type, volume, needs, contact_method, contact_value, description)
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"detail": "خطأ في الخادم."}, status_code=500)

# ============================================================================
# PAYMENT ROUTES (SpaceRemit)
# ============================================================================

class CheckoutRequest(BaseModel):
    plan_name: str
    period:    str
    amount:    float


@app.post("/api/payments/checkout-data")
async def api_checkout_data(request: Request, data: CheckoutRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized. Please login."}, status_code=401)
    try:
        result = await generate_payment_link(email, data.plan_name, data.period, data.amount)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class VerifyPaymentRequest(BaseModel):
    payment_code: str
    plan_key:     str
    period:       str


@app.post("/api/payments/verify-and-activate")
async def api_verify_and_activate(request: Request, data: VerifyPaymentRequest):
    """
    ✅ FIX: أُضيف فحص إعادة استخدام كود الدفع — يمنع المستخدم من استخدام
    نفس payment_code أكثر من مرة لتفعيل اشتراكات متعددة مجاناً.
    """
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized. Please login."}, status_code=401)

    plan_name_map = {
        "deepseek": "DeepSeek V3",  "kimi": "Kimi k2",
        "mistral":  "Mistral Large", "gemma": "Gemma 3",
        "llama":    "Llama 3.2",     "agents": "Chat Agents", "global": "Nexus Global"
    }
    plan_name = plan_name_map.get(data.plan_key)
    if not plan_name:
        return JSONResponse({"error": f"Unknown plan key: {data.plan_key}"}, status_code=400)

    if data.period not in ("monthly", "yearly"):
        return JSONResponse({"error": "Invalid period. Use 'monthly' or 'yearly'."}, status_code=400)

    # ✅ FIX: تحقق من أن الكود لم يُستخدم من قبل
    from database import redis as _redis
    if _redis:
        used_key = f"used_payment:{data.payment_code}"
        if _redis.exists(used_key):
            return JSONResponse(
                {"error": "This payment code has already been used."},
                status_code=400
            )

    payment_info = await verify_spaceremit_payment(data.payment_code)
    if not payment_info:
        return JSONResponse(
            {"error": "Payment not verified. It may be pending, failed, or invalid."},
            status_code=400
        )

    # ✅ تسجيل الكود كمستخدم (يُحفظ لمدة سنة)
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
        status_code=500
    )


@app.post("/api/webhooks/spaceremit")
async def spaceremit_webhook(request: Request):
    """
    ✅ FIX 1: أُضيف IP whitelist اختياري للـ webhook.
    ✅ FIX 2: أُضيف فحص إعادة استخدام الكود لمنع التفعيل المزدوج.
    """
    try:
        # ─── IP Whitelist (اختياري) ───────────────────────────────
        if SPACEREMIT_WEBHOOK_IPS:
            client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
                        or getattr(request.client, "host", "")
            if client_ip not in SPACEREMIT_WEBHOOK_IPS:
                return JSONResponse({"error": "Forbidden"}, status_code=403)

        payload = await request.json()
        tx_code = payload.get("spaceremit_code") or payload.get("transaction_id") or payload.get("payment_id")

        if not tx_code:
            return JSONResponse({"error": "No transaction code provided"}, status_code=400)

        # ✅ FIX: تحقق من أن الكود لم يُعالَج من قبل
        from database import redis as _redis
        if _redis:
            used_key = f"used_payment:{tx_code}"
            if _redis.exists(used_key):
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

        plan_name_map = {
            "deepseek": "DeepSeek V3",  "kimi": "Kimi k2",
            "mistral":  "Mistral Large", "gemma": "Gemma 3",
            "llama":    "Llama 3.2",     "agents": "Chat Agents", "global": "Nexus Global"
        }
        plan_name = plan_name_map.get(plan_key, "Free Tier")
        success   = add_user_subscription(email, plan_key, plan_name, period)

        if success:
            # ✅ تسجيل الكود كمستخدم
            if _redis:
                try:
                    _redis.setex(f"used_payment:{tx_code}", 365 * 24 * 3600, email)
                except Exception:
                    pass
            return JSONResponse({"status": "Success", "message": "Plan activated"})

        return JSONResponse({"status": "Failed", "error": "Could not activate plan"}, status_code=400)

    except Exception:
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)

# ============================================================================
# ████████████████████████████████████████████████████████████████████████████
# ADMIN SECTION — لوحة الإدارة (rodfin0202@gmail.com فقط)
# ████████████████████████████████████████████████████████████████████████████
# ============================================================================

# ─── Admin Page Routes ────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_redirect():
    return RedirectResponse("/en/admin")


@app.get("/{lang}/admin", response_class=HTMLResponse)
async def admin_page(request: Request, lang: str):
    """صفحة لوحة الإدارة — متاحة فقط لـ rodfin0202@gmail.com"""
    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login?next=/{lang}/admin")
    if email != ADMIN_EMAIL:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:100px;'>"
            "<h1 style='color:#ef4444;'>🔒 وصول مرفوض</h1>"
            "<p>هذه الصفحة متاحة للمسؤول فقط.</p>"
            "<a href='/'>← العودة للرئيسية</a></body></html>",
            status_code=403
        )
    context = get_template_context(request, lang)
    context["admin_token"] = ADMIN_TOKEN
    return templates.TemplateResponse("admin.html", context)


# ─── Admin API: Dashboard Stats ──────────────────────────────────────────────

def _is_plan_active(plan: dict) -> bool:
    try:
        return datetime.fromisoformat(plan.get("expires", "")) > datetime.utcnow()
    except Exception:
        return False


@app.get("/api/admin/dashboard-stats")
async def admin_dashboard_stats(request: Request):
    verify_admin_request(request)

    from database import redis as _redis

    now       = datetime.utcnow()
    today_str = str(now.date())

    # ─── Global Stats Today ───
    global_stats = {}
    if _redis:
        try:
            raw = _redis.get(f"global_stats:{today_str}")
            if raw:
                global_stats = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    total_reqs = global_stats.get("total_requests", 0)
    errors     = global_stats.get("errors", 0)
    avg_latency = round(global_stats["latency_sum"] / total_reqs) \
        if total_reqs > 0 and global_stats.get("latency_sum", 0) > 0 else 0
    success_rate = round((1 - errors / total_reqs) * 100, 1) if total_reqs > 0 else 100

    # ─── Read All Users ───
    all_users_data   = []
    active_paid_count = 0
    new_today        = 0
    plan_distribution = {}
    recent_subs      = []

    if _redis:
        try:
            keys = _redis.keys("user:*")
            for key in keys:
                raw = _redis.get(key)
                if not raw:
                    continue
                u = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(u, dict) or not u.get("email"):
                    continue
                all_users_data.append(u)
                active_plans = [p for p in u.get("active_plans", []) if _is_plan_active(p)]
                if active_plans:
                    active_paid_count += 1
                    for p in active_plans:
                        pk = p.get("name", p.get("plan_key", "Unknown"))
                        plan_distribution[pk] = plan_distribution.get(pk, 0) + 1
                        recent_subs.append({
                            "email":     u.get("email"),
                            "plan_key":  p.get("plan_key"),
                            "plan_name": p.get("name"),
                            "period":    p.get("period"),
                            "activated": p.get("activated", "")
                        })
                else:
                    plan_distribution["Free Tier"] = plan_distribution.get("Free Tier", 0) + 1
                if today_str in u.get("created_at", ""):
                    new_today += 1
        except Exception as e:
            print(f"[Admin] Redis read error: {e}")

    total_users = len(all_users_data)
    if total_users == 0:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) as cnt FROM users")
                    row = cur.fetchone()
                    total_users = row["cnt"] if row else 0
            except Exception:
                pass
            finally:
                conn.close()

    # ─── Daily Requests (7 days) ───
    daily_requests = []
    for i in range(6, -1, -1):
        day     = now - timedelta(days=i)
        day_str = str(day.date())
        day_stats = {}
        if _redis:
            try:
                raw = _redis.get(f"global_stats:{day_str}")
                if raw:
                    day_stats = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass
        daily_requests.append({"date": day.strftime("%m/%d"), "requests": day_stats.get("total_requests", 0)})

    week_requests = sum(d["requests"] for d in daily_requests)

    # ─── Model Usage ───
    model_name_map = {"deepseek": "DeepSeek", "kimi": "Kimi", "mistral": "Mistral",
                      "llama": "Llama", "gemma": "Gemma", "unknown": "Other"}
    model_usage = {}
    for k, v in global_stats.get("models", {}).items():
        label = model_name_map.get(k, k)
        model_usage[label] = v.get("reqs", 0) if isinstance(v, dict) else 0

    recent_subs.sort(key=lambda x: x.get("activated", ""), reverse=True)

    return JSONResponse({
        "total_users":      total_users,
        "active_paid_subs": active_paid_count,
        "new_today":        new_today,
        "today_requests":   total_reqs,
        "today_errors":     errors,
        "today_tokens":     global_stats.get("total_tokens", 0),
        "avg_latency":      avg_latency,
        "blocked_today":    global_stats.get("blocked", 0),
        "success_rate":     success_rate,
        "week_requests":    week_requests,
        "week_tokens":      global_stats.get("total_tokens", 0),
        "plan_distribution": plan_distribution,
        "daily_requests":   daily_requests,
        "model_usage":      model_usage,
        "global_stats":     global_stats,
        "recent_subs":      recent_subs[:10]
    })


# ─── Admin API: Users List ────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_get_users(request: Request):
    verify_admin_request(request)

    from database import redis as _redis
    users = []

    if _redis:
        try:
            keys = _redis.keys("user:*")
            for key in keys:
                raw = _redis.get(key)
                if raw:
                    u = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(u, dict) and u.get("email"):
                        users.append({k: v for k, v in u.items() if k != "password"})
        except Exception as e:
            print(f"[Admin] Users load error: {e}")

    if not users:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM users LIMIT 500")
                    for row in cur.fetchall():
                        if row and row.get("data"):
                            u = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
                            if isinstance(u, dict) and u.get("email"):
                                users.append({k: v for k, v in u.items() if k != "password"})
            except Exception as e:
                print(f"[Admin] TiDB users load error: {e}")
            finally:
                conn.close()

    users.sort(
        key=lambda u: -len([p for p in u.get("active_plans", []) if _is_plan_active(p)])
    )
    return JSONResponse(users)


# ─── Admin API: Grant Plan ────────────────────────────────────────────────────

class AdminGrantRequest(BaseModel):
    email:     str
    plan_key:  str
    plan_name: str
    period:    str


@app.post("/api/admin/grant-plan")
async def admin_grant_plan(request: Request, data: AdminGrantRequest):
    verify_admin_request(request)

    if data.period not in ("monthly", "yearly"):
        return JSONResponse({"error": "Invalid period"}, status_code=400)

    valid_plans = {"deepseek", "kimi", "mistral", "gemma", "llama",
                   "agents", "global", "nexus_global", "chat_agents", "free_tier"}
    if data.plan_key not in valid_plans:
        return JSONResponse({"error": f"Unknown plan: {data.plan_key}"}, status_code=400)

    success = add_user_subscription(data.email, data.plan_key, data.plan_name, data.period)
    if success:
        return JSONResponse({"status": "success", "message": f"Plan '{data.plan_name}' granted to {data.email}"})
    return JSONResponse({"error": "User not found or DB error"}, status_code=400)


# ─── Admin API: Revoke Plans ──────────────────────────────────────────────────

class AdminEmailRequest(BaseModel):
    email: str


@app.post("/api/admin/revoke-plans")
async def admin_revoke_plans(request: Request, data: AdminEmailRequest):
    verify_admin_request(request)

    from database import redis as _redis

    user = get_user_by_email(data.email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    user["active_plans"]     = []
    user["plan"]             = "Free Tier"
    user["subscription_end"] = None

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET data = %s WHERE email = %s",
                            (json.dumps(user), data.email))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    if _redis:
        try:
            _redis.set(f"user:{data.email}", json.dumps(user))
        except Exception:
            pass

    return JSONResponse({"status": "success", "message": f"All plans revoked for {data.email}"})


# ─── Admin API: Reset Usage ───────────────────────────────────────────────────

@app.post("/api/admin/reset-usage")
async def admin_reset_usage(request: Request, data: AdminEmailRequest):
    verify_admin_request(request)

    from database import redis as _redis

    user = get_user_by_email(data.email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    user["usage"] = {
        "date": str(datetime.utcnow().date()),
        "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0,
        "unified_extra": 0, "trial_counts": {},
        "total_requests": 0, "total_tokens": 0,
        "latency_sum": 0, "errors": 0, "internal_ops": 0
    }

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET data = %s WHERE email = %s",
                            (json.dumps(user), data.email))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    if _redis:
        try:
            _redis.set(f"user:{data.email}", json.dumps(user))
        except Exception:
            pass

    return JSONResponse({"status": "success"})


# ─── Admin API: DB Sync (محمي الآن) ──────────────────────────────────────────

@app.get("/api/admin/sync-db")
async def trigger_db_sync(request: Request):
    """
    ✅ FIX: كان هذا المسار بلا مصادقة — الآن يتطلب Admin Token.
    يمكن ربطه بـ Cron Job:
      curl -H "X-Admin-Token: $ADMIN_TOKEN" https://yourdomain.com/api/admin/sync-db
    """
    verify_admin_request(request)
    from database import sync_all_usage_to_db
    result = sync_all_usage_to_db()
    return JSONResponse(result)

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        print("☁️ Vercel detected - Using Serverless")
    else:
        try:
            import uvicorn
            port = int(os.environ.get("PORT", 5000))
            print(f"🚀 Starting Orgteh Dev Server on port {port}")
            uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="info")
        except ImportError:
            print("⚠️ Install uvicorn: pip install uvicorn")
