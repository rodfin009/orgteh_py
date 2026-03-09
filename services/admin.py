import os
import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from database import (
    get_db_connection,
    get_user_by_email,
    add_user_subscription,
    sync_all_usage_to_db,
    redis,
    record_visit,
    get_visitor_stats,
)
from services.auth import get_current_user_email

# ============================================================================
# CONSTANTS
# ============================================================================
ADMIN_EMAIL = "rodfin0202@gmail.com"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", secrets.token_urlsafe(32))

router = APIRouter()

# ============================================================================
# HELPERS
# ============================================================================

def verify_admin(request: Request) -> str:
    """يتحقق من أن الطلب قادم من الأدمن (جلسة أو X-Admin-Token)."""
    email = get_current_user_email(request)
    if email == ADMIN_EMAIL:
        return email
    token = request.headers.get("X-Admin-Token", "")
    if token and token == ADMIN_TOKEN:
        return ADMIN_EMAIL
    raise HTTPException(status_code=403, detail="Admin access required")


def _is_plan_active(plan: dict) -> bool:
    try:
        return datetime.fromisoformat(plan.get("expires", "")) > datetime.utcnow()
    except Exception:
        return False


def _is_gmail(email: str) -> bool:
    return "@gmail.com" in email.lower()


def _since(period: str) -> datetime:
    """يُعيد datetime يُمثّل بداية النافذة الزمنية المطلوبة."""
    now = datetime.utcnow()
    return {
        "1h":  now - timedelta(hours=1),
        "24h": now - timedelta(hours=24),
        "1m":  now - timedelta(days=30),
        "1y":  now - timedelta(days=365),
    }.get(period, now - timedelta(hours=24))

# ============================================================================
# VISITOR MIDDLEWARE HELPER — يُستدعى من main.py
# ============================================================================

async def track_page_visit(request: Request):
    """
    سجّل الزيارة في Redis + TiDB.
    استدعه من middleware في main.py:
        @app.middleware("http")
        async def visitor_middleware(request: Request, call_next):
            await track_page_visit(request)
            return await call_next(request)
    """
    path = request.url.path
    skip_prefixes = (
        "/api/", "/static/", "/favicon", "/_", "/debug/",
        "/sitemap", "/robots", "/health", "/ready",
    )
    if any(path.startswith(p) for p in skip_prefixes):
        return

    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    referer    = request.headers.get("Referer", "")
    user_agent = request.headers.get("User-Agent", "")

    try:
        record_visit(ip=ip, referer=referer, user_agent=user_agent, path=path)
    except Exception:
        pass

# ============================================================================
# PAGE ROUTES
# ============================================================================

@router.get("/admin", response_class=HTMLResponse)
async def admin_redirect():
    return RedirectResponse("/en/admin")


@router.get("/{lang}/admin", response_class=HTMLResponse)
async def admin_page(request: Request, lang: str):
    """صفحة لوحة الإدارة — لـ rodfin0202@gmail.com فقط."""
    # استيراد متأخر لتجنب الدوران الدائري
    from services.auth import get_template_context, templates

    email = get_current_user_email(request)
    if not email:
        return RedirectResponse(f"/{lang}/login?next=/{lang}/admin")
    if email != ADMIN_EMAIL:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:100px;'>"
            "<h1 style='color:#ef4444;'>🔒 وصول مرفوض</h1>"
            "<p>هذه الصفحة متاحة للمسؤول فقط.</p>"
            "<a href='/'>← العودة للرئيسية</a></body></html>",
            status_code=403,
        )
    context = get_template_context(request, lang)
    context["admin_token"] = ADMIN_TOKEN
    return templates.TemplateResponse("admin.html", context)

# ============================================================================
# API: DASHBOARD STATS (مع فلتر الوقت)
# ============================================================================

@router.get("/api/admin/dashboard-stats")
async def admin_dashboard_stats(request: Request, period: str = "24h"):
    """
    period: 1h | 24h | 1m | 1y
    يُعيد إحصائيات الزوار + المستخدمين + الطلبات مُفلتَرة حسب النافذة الزمنية.
    """
    verify_admin(request)
    _redis = redis
    now       = datetime.utcnow()
    today_str = str(now.date())
    since     = _since(period)

    # ─── إحصائيات اليوم ─────────────────────────────────────────────────────
    global_stats: dict = {}
    if _redis:
        try:
            raw = _redis.get(f"global_stats:{today_str}")
            if raw:
                global_stats = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    total_reqs   = global_stats.get("total_requests", 0)
    errors       = global_stats.get("errors", 0)
    avg_latency  = (
        round(global_stats["latency_sum"] / total_reqs)
        if total_reqs > 0 and global_stats.get("latency_sum", 0) > 0
        else 0
    )
    success_rate = round((1 - errors / total_reqs) * 100, 1) if total_reqs > 0 else 100

    # ─── طلبات آخر 7 أيام ───────────────────────────────────────────────────
    daily_requests = []
    for i in range(6, -1, -1):
        day     = now - timedelta(days=i)
        day_str = str(day.date())
        day_stats: dict = {}
        if _redis:
            try:
                raw = _redis.get(f"global_stats:{day_str}")
                if raw:
                    day_stats = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass
        daily_requests.append({
            "date":     day.strftime("%m/%d"),
            "requests": day_stats.get("total_requests", 0),
        })
    week_requests = sum(d["requests"] for d in daily_requests)

    # ─── استخدام النماذج ─────────────────────────────────────────────────────
    model_name_map = {
        "deepseek": "DeepSeek", "kimi": "Kimi", "mistral": "Mistral",
        "llama": "Llama", "gemma": "Gemma", "unknown": "Other",
    }
    model_usage = {
        model_name_map.get(k, k): (v.get("reqs", 0) if isinstance(v, dict) else 0)
        for k, v in global_stats.get("models", {}).items()
    }

    # ─── قراءة المستخدمين (Gmail فقط) ───────────────────────────────────────
    all_users_data:    list = []
    active_paid_count  = 0
    new_in_period      = 0
    plan_distribution: dict = {}
    recent_subs:       list = []

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
                if not _is_gmail(u["email"]):
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
                            "activated": p.get("activated", ""),
                        })
                else:
                    plan_distribution["Free Tier"] = plan_distribution.get("Free Tier", 0) + 1

                # تسجيلات جديدة ضمن النافذة الزمنية
                created_at_str = u.get("created_at", "")
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                        if created_at >= since:
                            new_in_period += 1
                    except Exception:
                        pass
        except Exception as e:
            print(f"[Admin] Redis read error: {e}")

    total_gmail_users = len(all_users_data)

    # fallback TiDB
    if total_gmail_users == 0:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM users WHERE email LIKE '%@gmail.com'"
                    )
                    row = cur.fetchone()
                    total_gmail_users = row["cnt"] if row else 0
            except Exception:
                pass
            finally:
                conn.close()

    recent_subs.sort(key=lambda x: x.get("activated", ""), reverse=True)

    # ─── إحصائيات الزوار ────────────────────────────────────────────────────
    visitor_stats = get_visitor_stats(period=period)

    return JSONResponse({
        "total_users":        total_gmail_users,
        "active_paid_subs":   active_paid_count,
        "new_in_period":      new_in_period,
        "period":             period,
        "today_requests":     total_reqs,
        "today_errors":       errors,
        "today_tokens":       global_stats.get("total_tokens", 0),
        "avg_latency":        avg_latency,
        "blocked_today":      global_stats.get("blocked", 0),
        "success_rate":       success_rate,
        "week_requests":      week_requests,
        "week_tokens":        global_stats.get("total_tokens", 0),
        "plan_distribution":  plan_distribution,
        "daily_requests":     daily_requests,
        "model_usage":        model_usage,
        "global_stats":       global_stats,
        "recent_subs":        recent_subs[:10],
        "visitor_stats":      visitor_stats,
    })

# ============================================================================
# API: USERS LIST (Gmail فقط، مرتبة بأحدث تسجيل)
# ============================================================================

@router.get("/api/admin/users")
async def admin_get_users(request: Request, gmail_only: bool = True):
    """
    يُعيد قائمة المستخدمين مُرتَّبة بأحدث تسجيل.
    gmail_only=true (افتراضي): يعرض Gmail فقط ويتجاهل الإيميلات الوهمية.
    """
    verify_admin(request)
    _redis = redis
    users: list = []

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
                if gmail_only and not _is_gmail(u["email"]):
                    continue
                users.append({k: v for k, v in u.items() if k != "password"})
        except Exception as e:
            print(f"[Admin] Users load error: {e}")

    if not users:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    if gmail_only:
                        cur.execute(
                            "SELECT data FROM users WHERE email LIKE '%@gmail.com' LIMIT 1000"
                        )
                    else:
                        cur.execute("SELECT data FROM users LIMIT 1000")
                    for row in cur.fetchall():
                        if row and row.get("data"):
                            u = (
                                json.loads(row["data"])
                                if isinstance(row["data"], str)
                                else row["data"]
                            )
                            if isinstance(u, dict) and u.get("email"):
                                users.append({k: v for k, v in u.items() if k != "password"})
            except Exception as e:
                print(f"[Admin] TiDB users load error: {e}")
            finally:
                conn.close()

    # ✅ ترتيب بأحدث تسجيل أولاً
    users.sort(key=lambda u: u.get("created_at", ""), reverse=True)
    return JSONResponse(users)

# ============================================================================
# API: VISITOR STATS تفصيلي
# ============================================================================

@router.get("/api/admin/visitors")
async def admin_visitor_stats(request: Request, period: str = "24h"):
    """يُعيد إحصائيات تفصيلية عن الزوار."""
    verify_admin(request)
    return JSONResponse(get_visitor_stats(period=period))

# ============================================================================
# API: GRANT PLAN
# ============================================================================

class AdminGrantRequest(BaseModel):
    email:     str
    plan_key:  str
    plan_name: str
    period:    str


@router.post("/api/admin/grant-plan")
async def admin_grant_plan(request: Request, data: AdminGrantRequest):
    verify_admin(request)

    if data.period not in ("monthly", "yearly"):
        return JSONResponse({"error": "Invalid period"}, status_code=400)

    valid_plans = {
        "deepseek", "kimi", "mistral", "gemma", "llama",
        "agents", "global", "nexus_global", "chat_agents", "free_tier",
    }
    if data.plan_key not in valid_plans:
        return JSONResponse({"error": f"Unknown plan: {data.plan_key}"}, status_code=400)

    success = add_user_subscription(data.email, data.plan_key, data.plan_name, data.period)
    if success:
        return JSONResponse({
            "status": "success",
            "message": f"Plan '{data.plan_name}' granted to {data.email}",
        })
    return JSONResponse({"error": "User not found or DB error"}, status_code=400)

# ============================================================================
# API: REVOKE PLANS
# ============================================================================

class AdminEmailRequest(BaseModel):
    email: str


@router.post("/api/admin/revoke-plans")
async def admin_revoke_plans(request: Request, data: AdminEmailRequest):
    verify_admin(request)
    _redis = redis

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
                cur.execute(
                    "UPDATE users SET data = %s WHERE email = %s",
                    (json.dumps(user), data.email),
                )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    if _redis:
        try:
            _redis.set(f"user:{data.email}", json.dumps(user))
        except Exception:
            pass

    return JSONResponse({
        "status": "success",
        "message": f"All plans revoked for {data.email}",
    })

# ============================================================================
# API: RESET USAGE
# ============================================================================

@router.post("/api/admin/reset-usage")
async def admin_reset_usage(request: Request, data: AdminEmailRequest):
    verify_admin(request)
    _redis = redis

    user = get_user_by_email(data.email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    user["usage"] = {
        "date": str(datetime.utcnow().date()),
        "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0,
        "unified_extra": 0, "trial_counts": {},
        "total_requests": 0, "total_tokens": 0,
        "latency_sum": 0, "errors": 0, "internal_ops": 0,
    }

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET data = %s WHERE email = %s",
                    (json.dumps(user), data.email),
                )
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

# ============================================================================
# API: DB SYNC
# ============================================================================

@router.get("/api/admin/sync-db")
async def trigger_db_sync(request: Request):
    verify_admin(request)
    result = sync_all_usage_to_db()
    return JSONResponse(result)
