import os
import random
import string
import secrets
import bcrypt
import smtplib
import httpx
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr
from fastapi import HTTPException, Request

#Import database functions
from database import (
    get_user_by_email, 
    create_user_record,
    redis
)

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
    # Development mode: print to console if SMTP not configured
    if not all([SMTP_EMAIL, SMTP_PASSWORD]):
        print(f"\\n{'='*60}")
        print(f"[DEV MODE] Verification code for {to_email}: {code}")
        print(f"{'='*60}\\n")
        return True

    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'رمز التحقق - Orgteh Infra | Verification Code'
        msg['From'] = f"Orgteh Infra <{SMTP_EMAIL}>"
        msg['To'] = to_email

        # Arabic/English HTML email template
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

        # Send email
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
    """
    Store verification code in Redis with expiry
    Default: 10 minutes (600 seconds)
    """
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
    """Retrieve verification code from Redis"""
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
    """Delete verification code from Redis"""
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
    """
    Validate password strength
    Returns: (is_valid: bool, error_message: str)
    """
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
    """
    Handle sending verification code
    Returns success message or raises HTTPException
    """
    # Verify Turnstile token
    client_ip = request.headers.get("X-Forwarded-For", request.client.host)
    is_valid = await verify_turnstile_token(data.turnstile_token, client_ip)

    if not is_valid:
        raise HTTPException(status_code=400, detail="فشل التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    # Gmail only validation
    if not data.email.lower().endswith('@gmail.com'):
        raise HTTPException(status_code=400, detail="يُقبل فقط البريد الإلكتروني من Gmail (@gmail.com)")

    # Check if email already exists
    existing_user = get_user_by_email(data.email)
    if existing_user:
        # Check if this account was created via GitHub OAuth
        if redis and redis.exists(f"github:{data.email}"):
            raise HTTPException(
                status_code=400,
                detail="لديك حساب GitHub مرتبط بهذا البريد الإلكتروني. يرجى تسجيل الدخول باستخدام زر GitHub أدناه."
            )
        raise HTTPException(
            status_code=400,
            detail="البريد الإلكتروني مستخدم بالفعل. يمكنك تسجيل الدخول مباشرةً."
        )

    # Mark turnstile as verified for this email in session
    request.session["turnstile_verified_email"] = data.email

    # Generate verification code
    code = generate_verification_code()

    # Store in Redis (10 minutes expiry)
    store_verification_code(data.email, code, 600)

    # Send email
    success = send_verification_email(data.email, code)
    if not success:
        raise HTTPException(status_code=500, detail="فشل إرسال البريد الإلكتروني. يرجى المحاولة لاحقاً.")

    return {"message": "تم إرسال رمز التحقق", "email": data.email}

async def handle_register(request: Request, data: RegisterRequest) -> dict:
    """
    Handle user registration
    Returns success message with API key or raises HTTPException
    """
    # NOTE: Turnstile token was already verified in send_verification step.
    # Re-verifying the same token will always fail because Cloudflare tokens
    # are single-use. We validate the session flag instead.
    verified_email = request.session.get("turnstile_verified_email")
    if verified_email != data.email:
        raise HTTPException(status_code=400, detail="لم يتم إكمال التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    # Check if email exists
    if get_user_by_email(data.email):
        raise HTTPException(status_code=400, detail="البريد الإلكتروني مستخدم بالفعل")

    # Verify code from Redis
    stored_code = get_verification_code(data.email)

    if not stored_code or stored_code != data.verification_code:
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية")

    # Validate password
    is_valid_password, error_msg = validate_password(data.password)
    if not is_valid_password:
        raise HTTPException(status_code=400, detail=error_msg)

    # Hash password
    hashed = bcrypt.hashpw(
        data.password.encode('utf-8'), 
        bcrypt.gensalt(rounds=12)
    ).decode('utf-8')

    # Generate API key
    new_key = generate_nexus_key()

    # Create user record
    if create_user_record(data.email, hashed, new_key):
        # Delete verification code
        delete_verification_code(data.email)

        # Clear turnstile session flag
        request.session.pop("turnstile_verified_email", None)

        # Set session
        request.session["user_email"] = data.email

        print(f"[Auth] New user registered: {data.email}")
        return {
            "message": "تم إنشاء الحساب بنجاح",
            "key": new_key
        }

    raise HTTPException(status_code=500, detail="خطأ في قاعدة البيانات. يرجى المحاولة لاحقاً.")

async def handle_login(request: Request, data: LoginRequest) -> dict:
    """
    Handle user login
    Returns success message or raises HTTPException
    """
    # Verify Turnstile token
    client_ip = request.headers.get("X-Forwarded-For", request.client.host)
    is_valid = await verify_turnstile_token(data.turnstile_token, client_ip)
    if not is_valid:
        raise HTTPException(status_code=400, detail="فشل التحقق من الكابتشا. يرجى المحاولة مرة أخرى.")

    user = get_user_by_email(data.email)

    if not user:
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    # Verify password
    try:
        is_valid = bcrypt.checkpw(
            data.password.encode('utf-8'), 
            user['password'].encode('utf-8')
        )
    except Exception:
        is_valid = False

    if not is_valid:
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")

    # Set session
    request.session["user_email"] = data.email

    # Send security notification if enabled
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
    """Handle user logout"""
    email = get_current_user_email(request)
    request.session.clear()

    if email:
        print(f"[Auth] User logged out: {email}")

    return {"message": "تم تسجيل الخروج"}

# ============================================================================
# MIDDLEWARE & CONTEXT HELPERS
# ============================================================================

def get_auth_context(request: Request) -> dict:
    """
    Get authentication context for templates
    Returns dict with user info
    """
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
# GITHUB OAUTH INTEGRATION (Add to auth.py)
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
    """Initiate GitHub OAuth flow"""
    from fastapi.responses import RedirectResponse

    # Generate state for CSRF protection
    import secrets
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    auth_url = get_github_auth_url(state)
    return RedirectResponse(auth_url)

async def handle_github_callback(request: Request, code: str, state: str):
    """Handle GitHub OAuth callback"""
    from fastapi.responses import RedirectResponse
    from database import get_user_by_email, create_user_record, redis

    # Verify state
    saved_state = request.session.get("oauth_state")
    if not saved_state or saved_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    # Exchange code for token
    token_data = await exchange_code_for_token(code)
    access_token = token_data.get("access_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get access token")

    # Get GitHub user info
    github_user = await get_github_user(access_token)

    # Check if user exists with this GitHub email
    email = github_user.email or f"{github_user.login}@github.user"
    existing_user = get_user_by_email(email)

    if existing_user:
        # Link GitHub to existing account
        request.session["user_email"] = email
        store_github_token(request, token_data)

        # Store GitHub info in Redis for later use
        if redis:
            redis.hset(f"github:{email}", mapping={
                "login": github_user.login,
                "avatar": github_user.avatar_url,
                "token": access_token
            })

        return RedirectResponse("/dashboard?github_connected=true")

    else:
        # Create new user with GitHub
        new_key = generate_nexus_key()

        # Generate random password (user can reset later)
        import bcrypt
        temp_password = secrets.token_urlsafe(16)
        hashed = bcrypt.hashpw(
            temp_password.encode('utf-8'), 
            bcrypt.gensalt(rounds=12)
        ).decode('utf-8')

        if create_user_record(email, hashed, new_key):
            request.session["user_email"] = email
            store_github_token(request, token_data)

            # Store GitHub info
            if redis:
                redis.hset(f"github:{email}", mapping={
                    "login": github_user.login,
                    "avatar": github_user.avatar_url,
                    "token": access_token
                })

            return RedirectResponse("/dashboard?github_connected=true&new_user=true")

        raise HTTPException(status_code=500, detail="Failed to create user")

async def handle_github_logout(request: Request):
    """Disconnect GitHub account"""
    from fastapi.responses import JSONResponse

    clear_github_session(request)
    return JSONResponse({"message": "GitHub disconnected successfully"})

def get_github_context(request: Request) -> dict:
    """Get GitHub connection status for templates"""
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
                    "login": gh_info.get(b"login", b"").decode(),
                    "avatar": gh_info.get(b"avatar", b"").decode()
                }

    return context
