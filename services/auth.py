import os
import random
import string
import secrets
import bcrypt
import smtplib
import httpx
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

#Import database functions
from database import (
    get_user_by_email, 
    create_user_record,
    redis
)

# ============================================================================
# TEMPLATES — تهيئة محرك القوالب (مرجع مشترك لجميع الملفات)
# ============================================================================
_BASE_DIR     = Path(__file__).resolve().parent.parent   # جذر المشروع
_TEMPLATES_DIR = _BASE_DIR / "templates"
if not _TEMPLATES_DIR.exists():
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ============================================================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ============================================================================

TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "noreply@orgteh.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: str = ""

class SendVerificationRequest(BaseModel):
    email: EmailStr
    turnstile_token: str

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    verification_code: str
    turnstile_token: str

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_nexus_key() -> str:
    """Generate unique API key for user with format: ogh-{30 random chars}"""
    chars = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(30))
    return f"ogh-{random_part}"

def generate_verification_code() -> str:
    """Generate 6-digit numeric verification code"""
    return ''.join(random.choices(string.digits, k=6))

def get_current_user_email(request: Request) -> str | None:
    """Get current logged-in user email from session"""
    return request.session.get("user_email")

# ============================================================================
# CLOUDFLARE TURNSTILE VERIFICATION
# ============================================================================

async def verify_turnstile_token(token: str, remote_ip: str = None) -> bool:
    """
    Verify Cloudflare Turnstile token
    Returns True if valid, False otherwise
    """
    if not TURNSTILE_SECRET_KEY:
        print("[Auth] Warning: TURNSTILE_SECRET_KEY not set, skipping verification")
        return True  # Allow in development

    if not token:
        return False

    try:
        async with httpx.AsyncClient() as client:
            data = {
                "secret": TURNSTILE_SECRET_KEY,
                "response": token
            }
            if remote_ip:
                data["remoteip"] = remote_ip

            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=data,
                timeout=10.0
            )
            result = response.json()

            success = result.get("success", False)
            if not success:
                error_codes = result.get("error-codes", [])
                print(f"[Auth] Turnstile verification failed: {error_codes}")

            return success

    except Exception as e:
        print(f"[Auth] Turnstile verification error: {e}")
        return False

# ============================================================================
# EMAIL SERVICE
# ============================================================================

def _send_email_raw(to_email: str, subject: str, html_content: str) -> bool:
    """Low-level SMTP email sender."""
    if not all([SMTP_EMAIL, SMTP_PASSWORD]):
        print(f"[DEV MODE] Email to {to_email}: {subject}")
        return True
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Orgteh Infra <{SMTP_EMAIL}>"
        msg['To'] = to_email
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[Auth] Email send error: {e}")
        return False

def send_security_alert_email(to_email: str, event: str = "new_login", extra: str = "") -> bool:
    """Send security notification email (login from new device, password change)."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if event == "password_change":
        subject = "🔒 Password Changed | تم تغيير كلمة المرور - Orgteh Infra"
        event_en = "Your password was changed"
        event_ar = "تم تغيير كلمة المرور الخاصة بك"
    else:
        subject = "🔐 New Login Detected | تسجيل دخول جديد - Orgteh Infra"
        event_en = "A new login was detected on your account"
        event_ar = "تم اكتشاف تسجيل دخول جديد لحسابك"

    html = f"""<!DOCTYPE html><html dir="ltr"><head><meta charset="UTF-8">
    <style>body{{font-family:Arial,sans-serif;background:#0a0a0a;color:#fff;margin:0;padding:20px;}}
    .card{{background:#111;border:1px solid #333;border-radius:16px;max-width:520px;margin:0 auto;padding:32px;}}
    .logo{{color:#7c3aed;font-size:22px;font-weight:bold;margin-bottom:24px;}}
    .alert{{background:#1f1033;border:1px solid #7c3aed44;border-radius:10px;padding:16px;margin:16px 0;}}
    .meta{{color:#888;font-size:13px;margin-top:16px;}} .cta{{display:inline-block;margin-top:20px;
    padding:12px 24px;background:#7c3aed;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;}}
    </style></head><body><div class="card">
    <div class="logo">⚡ Orgteh Infra</div>
    <h2 style="color:#f3f4f6">Security Alert | تنبيه أمان</h2>
    <div class="alert">
        <p style="margin:0"><strong>EN:</strong> {event_en}</p>
        <p style="margin:8px 0 0"><strong>AR:</strong> {event_ar}</p>
        {f'<p style="color:#aaa;margin:8px 0 0;font-size:13px">Details: {extra}</p>' if extra else ''}
    </div>
    <div class="meta">Time: {now}<br>Account: {to_email}</div>
    <p style="color:#888;font-size:12px;margin-top:24px">If this wasn't you, please change your password immediately.<br>
    إذا لم تكن أنت، يرجى تغيير كلمة المرور فوراً.</p>
    <a href="https://orgteh.com/ar/settings/account" class="cta">Secure Account | تأمين الحساب</a>
    </div></body></html>"""
    return _send_email_raw(to_email, subject, html)

def send_subscription_email(to_email: str, plan_name: str, expires_date: str, event: str = "new") -> bool:
    """Send billing/subscription notification email."""
    if event == "expiry_soon":
        subject = f"⚠️ Subscription Expiring Soon | اشتراكك ينتهي قريباً - Orgteh Infra"
        title_en = f"Your {plan_name} plan expires on {expires_date}"
        title_ar = f"خطتك {plan_name} تنتهي بتاريخ {expires_date}"
        cta_text = "Renew Subscription | تجديد الاشتراك"
        cta_url = "https://orgteh.com/ar/cart"
    else:
        subject = f"🎉 Subscription Activated | تم تفعيل اشتراكك - Orgteh Infra"
        title_en = f"Your {plan_name} plan is now active!"
        title_ar = f"خطتك {plan_name} مفعّلة الآن!"
        cta_text = "View Dashboard | لوحة التحكم"
        cta_url = "https://orgteh.com/ar/profile"

    html = f"""<!DOCTYPE html><html dir="ltr"><head><meta charset="UTF-8">
    <style>body{{font-family:Arial,sans-serif;background:#0a0a0a;color:#fff;margin:0;padding:20px;}}
    .card{{background:#111;border:1px solid #333;border-radius:16px;max-width:520px;margin:0 auto;padding:32px;}}
    .logo{{color:#7c3aed;font-size:22px;font-weight:bold;margin-bottom:24px;}}
    .info{{background:#0f1f0f;border:1px solid #22c55e44;border-radius:10px;padding:16px;margin:16px 0;}}
    .cta{{display:inline-block;margin-top:20px;padding:12px 24px;background:#22c55e;
    color:#000;border-radius:8px;text-decoration:none;font-weight:bold;}}
    </style></head><body><div class="card">
    <div class="logo">⚡ Orgteh Infra</div>
    <h2 style="color:#f3f4f6">Billing Update | تحديث الفاتورة</h2>
    <div class="info">
        <p style="margin:0;color:#22c55e"><strong>EN:</strong> {title_en}</p>
        <p style="margin:8px 0 0;color:#22c55e"><strong>AR:</strong> {title_ar}</p>
        <p style="color:#888;margin:8px 0 0;font-size:13px">Expires: {expires_date}</p>
    </div>
    <p style="color:#888;font-size:12px">Account: {to_email}</p>
    <a href="{cta_url}" class="cta">{cta_text}</a>
    </div></body></html>"""
    return _send_email_raw(to_email, subject, html)

def send_product_update_email(to_email: str, update_title: str, update_body: str) -> bool:
    """Send product update / discount notification email."""
    html = f"""<!DOCTYPE html><html dir="ltr"><head><meta charset="UTF-8">
    <style>body{{font-family:Arial,sans-serif;background:#0a0a0a;color:#fff;margin:0;padding:20px;}}
    .card{{background:#111;border:1px solid #333;border-radius:16px;max-width:520px;margin:0 auto;padding:32px;}}
    .logo{{color:#7c3aed;font-size:22px;font-weight:bold;margin-bottom:24px;}}
    .body{{color:#d1d5db;line-height:1.7;margin:16px 0;}}
    .cta{{display:inline-block;margin-top:20px;padding:12px 24px;background:#7c3aed;
    color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;}}
    .unsub{{color:#555;font-size:11px;margin-top:24px;}}
    </style></head><body><div class="card">
    <div class="logo">⚡ Orgteh Infra</div>
    <h2 style="color:#f3f4f6">{update_title}</h2>
    <div class="body">{update_body}</div>
    <a href="https://orgteh.com" class="cta">Explore Now | استكشف الآن</a>
    <p class="unsub">To unsubscribe from product updates, go to Settings → Notifications.</p>
    </div></body></html>"""
    return _send_email_raw(to_email, f"✨ {update_title} - Orgteh Infra", html)

def get_user_notification_prefs(email: str) -> dict:
    """Get notification preferences for user (stored in Redis)."""
    if not redis:
        return {"security": True, "product_updates": True, "billing": True, "promotions": True}
    try:
        key = f"notif_prefs:{email}"
        data = redis.get(key)
        if data:
            import json
            return json.loads(data) if isinstance(data, str) else data
    except Exception:
        pass
    return {"security": True, "product_updates": True, "billing": True, "promotions": True}

def save_user_notification_prefs(email: str, prefs: dict) -> bool:
    """Save notification preferences for user."""
    if not redis:
        return False
    try:
        import json
        redis.set(f"notif_prefs:{email}", json.dumps(prefs))
        return True
    except Exception as e:
        print(f"[Auth] Notif prefs save error: {e}")
        return False

def send_verification_email(to_email: str, code: str) -> bool:
    """
    Send verification code via email using SMTP
    Returns True if sent successfully, False otherwise
    """
    if not all([SMTP_EMAIL, SMTP_PASSWORD]):
        print(f"\n{'='*60}")
        print(f"[DEV MODE] Verification code for {to_email}: {code}")
        print(f"{'='*60}\n")
        return True

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'رمز التحقق - Orgteh Infra | Verification Code'
        msg['From'] = f"Orgteh Infra <{SMTP_EMAIL}>"
        msg['To'] = to_email

        html_content = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');
                body {{ 
                    font-family: 'Cairo', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    margin: 0; 
                    padding: 20px; 
                }}
                .container {{ 
                    max-width: 600px; 
                    margin: 0 auto; 
                    background: white; 
                    border-radius: 20px; 
                    overflow: hidden; 
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3); 
                }}
                .header {{ 
                    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); 
                    padding: 40px 20px; 
                    text-align: center; 
                }}
                .header h1 {{ 
                    color: white; 
                    margin: 0; 
                    font-size: 32px; 
                    font-weight: 700;
                }}
                .content {{ 
                    padding: 40px 30px; 
                    text-align: center; 
                }}
                .welcome {{ 
                    color: #1f2937; 
                    font-size: 24px; 
                    font-weight: 700; 
                    margin-bottom: 10px;
                }}
                .message {{ 
                    color: #6b7280; 
                    font-size: 16px; 
                    margin-bottom: 30px;
                    line-height: 1.6;
                }}
                .code-container {{ 
                    background: linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%);
                    border-radius: 16px; 
                    padding: 30px; 
                    margin: 30px 0;
                    border: 2px dashed #6366f1;
                }}
                .code {{ 
                    font-size: 48px; 
                    font-weight: 700; 
                    color: #6366f1; 
                    letter-spacing: 8px; 
                    direction: ltr;
                    display: inline-block;
                }}
                .code-label {{
                    color: #6b7280;
                    font-size: 14px;
                    margin-bottom: 10px;
                }}
                .expiry {{ 
                    color: #f59e0b; 
                    font-size: 14px; 
                    font-weight: 600;
                    margin-top: 20px;
                }}
                .footer {{ 
                    background: #f9fafb; 
                    padding: 30px; 
                    text-align: center; 
                    color: #9ca3af; 
                    font-size: 14px; 
                }}
                .warning {{ 
                    color: #ef4444; 
                    font-size: 13px; 
                    margin-top: 20px;
                    padding: 15px;
                    background: #fef2f2;
                    border-radius: 8px;
                    border-right: 4px solid #ef4444;
                }}
                .logo {{
                    width: 60px;
                    height: 60px;
                    background: white;
                    border-radius: 16px;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    margin-bottom: 20px;
                    font-size: 28px;
                    font-weight: 900;
                    color: #6366f1;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">O</div>
                    <h1>Orgteh Infra</h1>
                </div>
                <div class="content">
                    <div class="welcome">مرحباً بك! | Welcome!</div>
                    <div class="message">
                        شكراً لتسجيلك في Orgteh Infra.<br>
                        Thank you for registering with Orgteh Infra.
                    </div>

                    <div class="code-container">
                        <div class="code-label">رمز التحقق الخاص بك | Your verification code</div>
                        <div class="code">{code}</div>
                    </div>

                    <div class="expiry">
                        ⏰ صالح لمدة 10 دقائق | Valid for 10 minutes
                    </div>

                    <div class="warning">
                        <strong>تنبيه | Notice:</strong><br>
                        إذا لم تقم بطلب هذا الرمز، يرجى تجاهل هذا البريد.<br>
                        If you did not request this code, please ignore this email.
                    </div>
                </div>
                <div class="footer">
                    <p>© 2026 Orgteh Infra. جميع الحقوق محفوظة | All rights reserved.</p>
                    <p style="margin-top: 10px; font-size: 12px;">
                        هذا بريد إلكتروني تلقائي، يرجى عدم الرد عليه.<br>
                        This is an automated email, please do not reply.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"[Auth] Verification email sent to {to_email}")
        return True

    except Exception as e:
        print(f"[Auth] Failed to send email: {e}")
        return False

# ============================================================================
# REDIS VERIFICATION CODE OPERATIONS
# ============================================================================

def store_verification_code(email: str, code: str, expiry_seconds: int = 600) -> bool:
    if not redis:
        print("[Auth] Warning: Redis not available, code not stored")
        return False

    try:
        redis.setex(f"verify:{email}", expiry_seconds, code)
        print(f"[Auth] Verification code stored for {email}")
        return True
    except Exception as e:
        print(f"[Auth] Error storing verification code: {e}")
        return False

def get_verification_code(email: str) -> str | None:
    if not redis:
        return None

    try:
        code = redis.get(f"verify:{email}")
        if code:
            return code.decode('utf-8') if isinstance(code, bytes) else code
        return None
    except Exception as e:
        print(f"[Auth] Error getting verification code: {e}")
        return None

def delete_verification_code(email: str) -> bool:
    if not redis:
        return False

    try:
        redis.delete(f"verify:{email}")
        return True
    except Exception as e:
        print(f"[Auth] Error deleting verification code: {e}")
        return False

# ============================================================================
# PASSWORD VALIDATION
# ============================================================================

def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < 6:
        return False, "كلمة المرور يجب أن تكون 6 أحرف على الأقل"

    if not any(c.isupper() for c in password):
        return False, "كلمة المرور يجب أن تحتوي على حرف كبير (A-Z)"

    if not any(c.isdigit() for c in password):
        return False, "كلمة المرور يجب أن تحتوي على رقم (0-9)"

    return True, ""

# ============================================================================
# MAIN AUTHENTICATION FUNCTIONS
# ============================================================================

async def handle_send_verification(request: Request, data: SendVerificationRequest) -> dict:
    client_ip = request.headers.get("X-Forwarded-For", request.client.host)
    is_valid = await verify_turnstile_token(data.turnstile_token, client_ip)

    if not is_valid:
        raise HTTPException(status_code=400, detail="فشل التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    if not data.email.lower().endswith('@gmail.com'):
        raise HTTPException(status_code=400, detail="يُقبل فقط البريد الإلكتروني من Gmail (@gmail.com)")

    existing_user = get_user_by_email(data.email)
    if existing_user:
        if redis and redis.exists(f"github:{data.email}"):
            raise HTTPException(
                status_code=400,
                detail="لديك حساب GitHub مرتبط بهذا البريد الإلكتروني. يرجى تسجيل الدخول باستخدام زر GitHub أدناه."
            )
        raise HTTPException(
            status_code=400,
            detail="البريد الإلكتروني مستخدم بالفعل. يمكنك تسجيل الدخول مباشرةً."
        )

    request.session["turnstile_verified_email"] = data.email
    code = generate_verification_code()
    store_verification_code(data.email, code, 600)

    success = send_verification_email(data.email, code)
    if not success:
        raise HTTPException(status_code=500, detail="فشل إرسال البريد الإلكتروني. يرجى المحاولة لاحقاً.")

    return {"message": "تم إرسال رمز التحقق", "email": data.email}

async def handle_register(request: Request, data: RegisterRequest) -> dict:
    verified_email = request.session.get("turnstile_verified_email")
    if verified_email != data.email:
        raise HTTPException(status_code=400, detail="لم يتم إكمال التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    if get_user_by_email(data.email):
        raise HTTPException(status_code=400, detail="البريد الإلكتروني مستخدم بالفعل")

    stored_code = get_verification_code(data.email)

    if not stored_code or stored_code != data.verification_code:
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية")

    is_valid_password, error_msg = validate_password(data.password)
    if not is_valid_password:
        raise HTTPException(status_code=400, detail=error_msg)

    hashed = bcrypt.hashpw(
        data.password.encode('utf-8'), 
        bcrypt.gensalt(rounds=12)
    ).decode('utf-8')

    new_key = generate_nexus_key()

    if create_user_record(data.email, hashed, new_key):
        delete_verification_code(data.email)
        request.session.pop("turnstile_verified_email", None)
        request.session["user_email"] = data.email

        print(f"[Auth] New user registered: {data.email}")
        return {
            "message": "تم إنشاء الحساب بنجاح",
            "key": new_key
        }

    raise HTTPException(status_code=500, detail="خطأ في قاعدة البيانات. يرجى المحاولة لاحقاً.")

async def handle_login(request: Request, data: LoginRequest) -> dict:
    client_ip = request.headers.get("X-Forwarded-For", request.client.host)
    is_valid = await verify_turnstile_token(data.turnstile_token, client_ip)
    if not is_valid:
        raise HTTPException(status_code=400, detail="فشل التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    user = get_user_by_email(data.email)

    if not user:
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    try:
        is_valid = bcrypt.checkpw(
            data.password.encode('utf-8'), 
            user['password'].encode('utf-8')
        )
    except Exception:
        is_valid = False

    if not is_valid:
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    request.session["user_email"] = data.email

    try:
        prefs = get_user_notification_prefs(data.email)
        if prefs.get("security", True):
            ip = request.headers.get("X-Forwarded-For", getattr(request.client, 'host', ''))
            ua = request.headers.get("User-Agent", "")[:80]
            send_security_alert_email(data.email, "new_login", f"IP: {ip} | {ua}")
    except Exception:
        pass

    print(f"[Auth] User logged in: {data.email}")
    return {"message": "تم تسجيل الدخول بنجاح"}

async def handle_logout(request: Request) -> dict:
    email = get_current_user_email(request)
    request.session.clear()

    if email:
        print(f"[Auth] User logged out: {email}")

    return {"message": "تم تسجيل الخروج"}

# ============================================================================
# MIDDLEWARE & CONTEXT HELPERS
# ============================================================================

def get_auth_context(request: Request) -> dict:
    from database import get_user_by_api_key
    from services.providers import MODELS_METADATA, HIDDEN_MODELS

    user_email = get_current_user_email(request)
    user_key = None

    if user_email:
        u = get_user_by_email(user_email)
        if u:
            user_key = u.get("api_key")

    return {
        "request": request,
        "is_logged_in": user_email is not None,
        "user_email": user_email,
        "user_api_key": user_key,
        "models_metadata": [m for m in MODELS_METADATA if m["id"] not in HIDDEN_MODELS],
        "turnstile_site_key": TURNSTILE_SITE_KEY
    }

# ============================================================================
# GITHUB OAUTH INTEGRATION
# ============================================================================

from github_integration import (
    get_github_auth_url, 
    exchange_code_for_token, 
    get_github_user,
    store_github_token,
    get_github_token,
    clear_github_session,
    is_github_connected
)

async def handle_github_login(request: Request):
    from fastapi.responses import RedirectResponse
    import secrets
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    auth_url = get_github_auth_url(state)
    return RedirectResponse(auth_url)

async def handle_github_callback(request: Request, code: str, state: str):
    from fastapi.responses import RedirectResponse
    from database import get_user_by_email, create_user_record, redis

    saved_state = request.session.get("oauth_state")
    if not saved_state or saved_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    token_data = await exchange_code_for_token(code)
    access_token = token_data.get("access_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get access token")

    github_user = await get_github_user(access_token)
    email = github_user.email or f"{github_user.login}@github.user"
    existing_user = get_user_by_email(email)

    if existing_user:
        request.session["user_email"] = email
        store_github_token(request, token_data)

        if redis:
            redis.hset(f"github:{email}", "login",  github_user.login)
            redis.hset(f"github:{email}", "avatar", github_user.avatar_url)
            redis.hset(f"github:{email}", "token",  access_token)
        return RedirectResponse("/dashboard?github_connected=true")
    else:
        new_key = generate_nexus_key()
        import bcrypt
        temp_password = secrets.token_urlsafe(16)
        hashed = bcrypt.hashpw(temp_password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

        if create_user_record(email, hashed, new_key):
            request.session["user_email"] = email
            store_github_token(request, token_data)

            if redis:
                redis.hset(f"github:{email}", "login",  github_user.login)
                redis.hset(f"github:{email}", "avatar", github_user.avatar_url)
                redis.hset(f"github:{email}", "token",  access_token)
            return RedirectResponse("/dashboard?github_connected=true&new_user=true")
        raise HTTPException(status_code=500, detail="Failed to create user")

async def handle_github_logout(request: Request):
    from fastapi.responses import JSONResponse
    clear_github_session(request)
    return JSONResponse({"message": "GitHub disconnected successfully"})

def get_github_context(request: Request) -> dict:
    token = get_github_token(request)
    is_connected = bool(token)

    context = {
        "github_connected": is_connected,
        "github_login_url": "/auth/github/login",
        "github_disconnect_url": "/auth/github/logout"
    }

    if is_connected and redis:
        email = get_current_user_email(request)
        if email:
            gh_info = redis.hgetall(f"github:{email}")
            if gh_info:
                context["github_user"] = {
                    "login": gh_info.get(b"login", b"").decode() if isinstance(gh_info.get(b"login"), bytes) else gh_info.get("login", ""),
                    "avatar": gh_info.get(b"avatar", b"").decode() if isinstance(gh_info.get(b"avatar"), bytes) else gh_info.get("avatar", "")
                }

    return context

# ============================================================================
# GOOGLE OAUTH INTEGRATION
# ============================================================================

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "https://orgteh.com/auth/google/callback")

async def handle_google_login(request: Request):
    from fastapi.responses import RedirectResponse
    import urllib.parse
    import secrets

    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent"
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return RedirectResponse(auth_url)

async def handle_google_callback(request: Request, code: str, state: str):
    from fastapi.responses import RedirectResponse
    import httpx
    import bcrypt
    from database import get_user_by_email, create_user_record, redis

    saved_state = request.session.get("oauth_state")
    if not saved_state or saved_state != state:
        return RedirectResponse("/auth?google_error=token_failed")

    try:
        # 1. Exchange Code for Token
        async with httpx.AsyncClient() as client:
            token_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": GOOGLE_REDIRECT_URI
                }
            )
            token_data = token_res.json()
            access_token = token_data.get("access_token")

            if not access_token:
                return RedirectResponse("/auth?google_error=token_failed")

            # 2. Get User Info
            user_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_info = user_res.json()
            email = user_info.get("email")

            if not email:
                return RedirectResponse("/auth?google_error=no_user_info")

        # 3. Handle User in Database
        existing_user = get_user_by_email(email)

        if existing_user:
            request.session["user_email"] = email
            return RedirectResponse("/profile?google_connected=true")
        else:
            new_key = generate_nexus_key()
            temp_password = secrets.token_urlsafe(16)
            hashed = bcrypt.hashpw(temp_password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

            if create_user_record(email, hashed, new_key):
                request.session["user_email"] = email
                first_name = user_info.get("given_name", "")
                last_name = user_info.get("family_name", "")
                if redis:
                    redis.hset(f"user:{email}", "first_name", first_name)
                    redis.hset(f"user:{email}", "last_name",  last_name)

                return RedirectResponse("/profile?google_connected=true&new_user=true")

            return RedirectResponse("/auth?google_error=token_failed")

    except Exception as e:
        print(f"[Auth] Google login error: {e}")
        return RedirectResponse("/auth?google_error=token_failed")

# ============================================================================
# CHECK EMAIL AVAILABILITY  — /auth/check-email
# ============================================================================

class CheckEmailRequest(BaseModel):
    email: EmailStr

async def handle_check_email(data: CheckEmailRequest) -> dict:
    """
    يتحقق من أن البريد الإلكتروني غير مسجَّل بعد.
    يُستدعى من auth.html قبل الانتقال للخطوة الثانية.
    """
    if not data.email.lower().endswith('@gmail.com'):
        raise HTTPException(
            status_code=400,
            detail="يُقبل فقط البريد الإلكتروني من Gmail (@gmail.com)"
        )

    existing = get_user_by_email(data.email)
    if existing:
        # تحقق من ربط GitHub
        try:
            from database import redis as _redis
            if _redis and _redis.exists(f"github:{data.email}"):
                raise HTTPException(
                    status_code=400,
                    detail="لديك حساب GitHub مرتبط بهذا البريد الإلكتروني. سجّل الدخول عبر GitHub."
                )
        except HTTPException:
            raise
        except Exception:
            pass
        raise HTTPException(
            status_code=400,
            detail="البريد الإلكتروني مستخدم بالفعل. يمكنك تسجيل الدخول مباشرةً."
        )

    return {"available": True, "email": data.email}


# ============================================================================
# get_template_context — دالة السياق المشتركة (تُستورد من هنا في كل مكان)
# ============================================================================

def get_template_context(request: Request, lang: str = "en") -> dict:
    """تبني سياق القالب الموحّد لجميع الصفحات."""
    from services.providers import MODELS_METADATA, HIDDEN_MODELS

    try:
        context = get_auth_context(request)
    except Exception:
        context = {"is_logged_in": False, "user_email": "", "user_api_key": ""}

    context["request"] = request

    if lang not in ["ar", "en"]:
        lang = "en"
    context["lang"]         = lang
    context["api_key"]      = context.get("user_api_key", "")
    context["user_email"]   = context.get("user_email", "")
    context["is_logged_in"] = context.get("is_logged_in", False)

    # Admin token — استيراد متأخر لتجنب الدوران مع services.admin
    try:
        from services.admin import ADMIN_TOKEN, ADMIN_EMAIL
        user_email = context.get("user_email", "")
        context["admin_token"] = ADMIN_TOKEN if user_email == ADMIN_EMAIL else ""
        context["is_admin"]    = (user_email == ADMIN_EMAIL)
    except Exception:
        context["admin_token"] = ""
        context["is_admin"]    = False

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
# FASTAPI ROUTER — مسارات المصادقة والإعدادات والـ OAuth
# يُستورد في main.py عبر:  from services.auth import router as auth_router
# ============================================================================
import traceback
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel as _BaseModel

from database import (
    get_subscription_history,
    update_user_profile,
    change_user_password,
    delete_user_account,
)
from services.subscriptions import get_user_subscription_status
from services.limits        import get_user_limits_and_usage
from github_integration     import (
    DeployRequest, one_click_deploy,
)

router = APIRouter()

# ── Pydantic models (إعدادات الحساب) ─────────────────────────────────────────

class ProfileUpdateRequest(_BaseModel):
    first_name: str = ""
    last_name:  str = ""

class ChangePasswordRequest(_BaseModel):
    current_password: str
    new_password:     str

class DeleteAccountRequest(_BaseModel):
    email: str

# ══════════════════════════════════════════════════════════════════════════════
# صفحات المصادقة (Auth Pages)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/login", response_class=HTMLResponse)
async def login_redirect(request: Request):
    lang_cookie = request.cookies.get("preferred_lang")
    if lang_cookie in ["ar", "en"]:
        return RedirectResponse(f"/{lang_cookie}/auth")
    return RedirectResponse("/en/login")


@router.get("/{lang}/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))


@router.get("/register", response_class=HTMLResponse)
async def register_redirect(request: Request):
    lang_cookie = request.cookies.get("preferred_lang")
    lang = lang_cookie if lang_cookie in ["ar", "en"] else "en"
    return RedirectResponse(f"/{lang}/auth?tab=register")


@router.get("/{lang}/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))


@router.get("/auth", response_class=HTMLResponse)
async def auth_redirect():
    return RedirectResponse("/en/login")


@router.get("/{lang}/auth", response_class=HTMLResponse)
async def auth_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

# ══════════════════════════════════════════════════════════════════════════════
# صفحة الملف الشخصي (Profile)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/profile", response_class=HTMLResponse)
async def profile_redirect():
    return RedirectResponse("/en/profile")


@router.get("/{lang}/profile", response_class=HTMLResponse)
async def profile_page(request: Request, lang: str):
    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login")
    try:
        from database import get_user_by_email as _get_user
        user          = _get_user(email)
        sub_status    = get_user_subscription_status(email)
        limits, usage = get_user_limits_and_usage(email)
        context       = get_template_context(request, lang)

        active_plans = user.get("active_plans", [])
        now          = datetime.utcnow()
        valid_plans  = []
        for p in active_plans:
            try:
                if datetime.fromisoformat(p["expires"]) > now:
                    valid_plans.append(p)
            except Exception:
                pass

        # ── جلب الخطط المعلقة من Redis ────────────────────────────────────────
        pending_plan_keys = []
        try:
            from database import get_redis as _get_redis
            import json as _json
            _r = _get_redis()
            if _r:
                for key in _r.scan_iter(f"pending_payment:{email}:*"):
                    raw = _r.get(key)
                    if raw:
                        data = _json.loads(raw) if isinstance(raw, str) else _json.loads(raw.decode())
                        plan_key = data.get("plan_key", "")
                        plan_name_pending = data.get("plan_name", plan_key)
                        if plan_key and plan_name_pending not in [p["name"] for p in valid_plans]:
                            pending_plan_keys.append({"key": plan_key, "name": plan_name_pending})
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        plan_names        = [p["name"] for p in valid_plans]
        display_plan_name = " + ".join(plan_names) if plan_names else "Free Tier"

        def calc_pct(used, limit):
            return min(100, (used / limit) * 100) if limit > 0 else 0

        context.update({
            "api_key":       context.get("user_api_key", ""),
            "plan_name":     display_plan_name,
            "active_plans":  valid_plans,
            "pending_plans": pending_plan_keys,
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

# ══════════════════════════════════════════════════════════════════════════════
# صفحات الإعدادات (Settings Pages)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/settings", response_class=HTMLResponse)
async def settings_redirect():
    return RedirectResponse("/ar/settings/account")


@router.get("/{lang}/settings", response_class=HTMLResponse)
async def settings_lang_redirect(request: Request, lang: str):
    qs  = request.url.query
    url = f"/{lang}/settings/integrations" if "github_connected" in qs else f"/{lang}/settings/account"
    if qs:
        url += f"?{qs}"
    return RedirectResponse(url)


@router.get("/{lang}/settings/{tab}", response_class=HTMLResponse)
async def settings_page_tab(request: Request, lang: str, tab: str):
    valid_tabs = ["account", "integrations", "notifications", "billing", "security"]
    if tab not in valid_tabs:
        return RedirectResponse(f"/{lang}/settings/account")

    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login?next=/{lang}/settings/{tab}")

    try:
        from database import get_user_by_email as _get_user
        user = _get_user(email)
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
            "active_tab":           tab,
            "plan_name":            display_plan,
            "active_plans":         valid_plans,
            "is_active_sub":        bool(valid_plans),
            "days_left":            days_left,
            "profile_first_name":   user.get("first_name", ""),
            "profile_last_name":    user.get("last_name", ""),
            "subscription_history": sub_history,
            "now":                  datetime.utcnow(),
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

# ══════════════════════════════════════════════════════════════════════════════
# Dashboard redirect
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard", response_class=HTMLResponse)
async def dash_redirect():
    return RedirectResponse("/en/profile")


@router.get("/{lang}/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, lang: str):
    return RedirectResponse(f"/{lang}/profile")

# ══════════════════════════════════════════════════════════════════════════════
# Settings API Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/settings/profile")
async def update_profile(request: Request, data: ProfileUpdateRequest):
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")
    result = update_user_profile(email, data.first_name, data.last_name)
    if result.get("status") != 200:
        raise HTTPException(status_code=result["status"], detail=result["error"])
    return {"message": result["message"]}


@router.post("/api/settings/change-password")
async def change_password_endpoint(request: Request, data: ChangePasswordRequest):
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")
    result = change_user_password(
        email, data.current_password, data.new_password,
        validate_fn=validate_password
    )
    if result.get("status") != 200:
        raise HTTPException(status_code=result["status"], detail=result["error"])
    return {"message": result["message"]}


@router.delete("/api/settings/delete-account")
async def delete_account_endpoint(request: Request, data: DeleteAccountRequest):
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="غير مسجل الدخول")
    if email != data.email:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني غير مطابق")
    result = delete_user_account(email)
    if result.get("status") != 200:
        raise HTTPException(status_code=result["status"], detail=result["error"])
    request.session.clear()
    return {"message": result["message"]}


@router.post("/auth/logout-all")
async def logout_all_devices(request: Request):
    request.session.clear()
    return {"message": "تم تسجيل الخروج من جميع الأجهزة"}

# ══════════════════════════════════════════════════════════════════════════════
# Auth API Endpoints (Login / Register / Verification)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/auth/check-email")
async def check_email_endpoint(data: CheckEmailRequest):
    return await handle_check_email(data)


@router.post("/auth/send-verification")
async def send_verification(request: Request, data: SendVerificationRequest):
    return await handle_send_verification(request, data)


@router.post("/auth/register")
async def register(request: Request, data: RegisterRequest):
    return await handle_register(request, data)


@router.post("/auth/login")
async def login(request: Request, data: LoginRequest):
    return await handle_login(request, data)


@router.post("/auth/logout")
async def logout(request: Request):
    return await handle_logout(request)


@router.post("/api/set-language")
async def set_language(request: Request):
    body = await request.json()
    lang = body.get("lang", "en")
    if lang not in ["ar", "en"]:
        lang = "en"
    response = JSONResponse({"ok": True, "lang": lang})
    response.set_cookie("preferred_lang", lang, max_age=60 * 60 * 24 * 365, path="/", samesite="lax")
    return response

# ══════════════════════════════════════════════════════════════════════════════
# OAuth Routes (GitHub + Google)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/auth/github/login")
async def github_login(request: Request):
    return await handle_github_login(request)


@router.get("/auth/github/callback")
async def github_callback(request: Request, code: str, state: str):
    return await handle_github_callback(request, code, state)


@router.post("/auth/github/logout")
async def github_logout(request: Request):
    return await handle_github_logout(request)


@router.get("/auth/google/login")
async def google_login(request: Request):
    return await handle_google_login(request)


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str, state: str):
    return await handle_google_callback(request, code, state)

# ══════════════════════════════════════════════════════════════════════════════
# GitHub API Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/deploy/github")
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


@router.get("/api/github/repos")
async def get_user_repos(request: Request):
    email = get_current_user_email(request)
    if not email or not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected"}, 403)
    from github_integration import GitHubDeployer
    token    = get_github_token(request)
    deployer = GitHubDeployer(token)
    repos    = deployer.get_repos()
    return {"repos": repos}


@router.get("/api/github/status")
async def github_status(request: Request):
    try:
        connected = is_github_connected(request)
        ctx       = {}
        if connected:
            email = get_current_user_email(request)
            if email:
                try:
                    from database import get_redis as _get_redis
                    _r = _get_redis()
                    if _r:
                        gh_info = _r.hgetall(f"github:{email}")
                        if gh_info:
                            def _dec(v): return v.decode() if isinstance(v, bytes) else v
                            ctx = {
                                "login":  _dec(gh_info.get(b"login",  gh_info.get("login", ""))),
                                "avatar": _dec(gh_info.get(b"avatar", gh_info.get("avatar", "")))
                            }
                except Exception:
                    pass
        return JSONResponse({
            "connected":   connected,
            "user":        ctx if connected else None,
            "connect_url": "/auth/github/login",
        })
    except Exception as e:
        return JSONResponse({"connected": False, "error": str(e)}, 500)


@router.post("/api/generate-key")
async def regenerate_my_key(request: Request):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, 401)
    try:
        from database import update_api_key as _update_key
        new_key = generate_nexus_key()
        success = _update_key(email, new_key)
        if success:
            return JSONResponse({"key": new_key, "ok": True})
        return JSONResponse({"error": "Failed to save new key"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
