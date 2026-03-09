import json
from datetime import datetime
from fastapi import Request
from fastapi.responses import JSONResponse
from database import get_user_by_email, update_user_usage_struct
from services.providers import MODEL_MAPPING

# ─── Admin Configuration ──────────────────────────────────────────────────────
ADMIN_EMAIL = "rodfin0202@gmail.com"

# ─── Premium Tool IDs ─────────────────────────────────────────────────────────
PREMIUM_TOOL_IDS = {"nexus-ocr", "orgteh-ocr", "nexus-rag", "orgteh-rag"}

PLAN_CONFIGS = {
    "free_tier": {
        "daily_limits": {"llama": 10, "kimi": 5, "deepseek": 0, "mistral": 0, "gemma": 0},
        "overdraft": {"forever": 0}
    },
    "chat_agents": {
        "daily_limits": {"gemma": 270, "llama": 200, "kimi": 30, "deepseek": 0, "mistral": 0},
        "overdraft": {"monthly": 750, "yearly": 2000}
    },
    "nexus_global": {
        "daily_limits": {"deepseek": 150, "kimi": 100, "mistral": 50, "llama": 100, "gemma": 120},
        "overdraft": {"monthly": 200, "yearly": 500}
    },
    "deepseek": {
        "daily_limits": {"deepseek": 300, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0},
        "overdraft": {"monthly": 600, "yearly": 1500}
    },
    "kimi": {
        "daily_limits": {"kimi": 200, "deepseek": 0, "mistral": 0, "llama": 0, "gemma": 0},
        "overdraft": {"monthly": 300, "yearly": 1000}
    },
    "mistral": {
        "daily_limits": {"mistral": 100, "deepseek": 0, "kimi": 0, "llama": 0, "gemma": 0},
        "overdraft": {"monthly": 200, "yearly": 500}
    },
    "gemma": {
        "daily_limits": {"gemma": 500, "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0},
        "overdraft": {"monthly": 1000, "yearly": 3000}
    },
    "llama": {
        "daily_limits": {"llama": 400, "deepseek": 0, "kimi": 0, "mistral": 0, "gemma": 0},
        "overdraft": {"monthly": 800, "yearly": 2500}
    }
}

PLAN_NAME_MAP = {
    "Free Tier":     "free_tier",
    "Chat Agents":   "chat_agents",
    "Nexus Global":  "nexus_global",
    "DeepSeek V3":   "deepseek",
    "Kimi k2":       "kimi",
    "Mistral Large": "mistral",
    "Gemma 3":       "gemma",
    "Llama 3.2":     "llama",
}


def get_user_limits_and_usage(email):
    user = get_user_by_email(email)
    if not user:
        return {}, {}

    active_plans = user.get("active_plans", [])
    now = datetime.utcnow()
    valid_plans = []

    for p in active_plans:
        try:
            exp_date = datetime.fromisoformat(p["expires"])
            if exp_date > now:
                valid_plans.append(p)
        except:
            pass

    final_limits = PLAN_CONFIGS["free_tier"]["daily_limits"].copy()
    final_limits["unified_extra"] = 0

    if not valid_plans:
        db_limits = user.get("limits", {})
        if db_limits:
            for k, v in db_limits.items():
                if k in final_limits or k == "unified_extra":
                    final_limits[k] = max(final_limits.get(k, 0), v)
    else:
        for p in valid_plans:
            p_limits = p.get("limits", {})
            for k, v in p_limits.items():
                if k in final_limits or k == "unified_extra":
                    final_limits[k] += v

    usage = user.get("usage", {})
    today_str = str(now.date())

    if usage.get("date") != today_str:
        preserved_extra_usage = usage.get("unified_extra", 0)
        usage = {
            "date":            today_str,
            "deepseek":        0,
            "kimi":            0,
            "mistral":         0,
            "llama":           0,
            "gemma":           0,
            "unified_extra":   preserved_extra_usage,
            "trial_counts":    {},
            "total_requests":  0,
            "total_tokens":    0,
            "latency_sum":     0,
            "errors":          0,
            "internal_ops":    0,
        }
        update_user_usage_struct(email, usage)

    return final_limits, usage


async def check_request_allowance(email, model_id):
    if email == ADMIN_EMAIL:
        return True, True

    user = get_user_by_email(email)
    if not user:
        return False, False

    internal_key = MODEL_MAPPING.get(model_id)
    if not internal_key:
        return True, True

    limits, usage = get_user_limits_and_usage(email)

    daily_limit = limits.get(internal_key, 0)
    daily_usage = usage.get(internal_key, 0)

    if daily_usage < daily_limit:
        usage[internal_key] += 1
        usage["total_requests"] = usage.get("total_requests", 0) + 1
        update_user_usage_struct(email, usage)
        return True, True

    extra_limit = limits.get("unified_extra", 0)
    extra_usage  = usage.get("unified_extra", 0)

    if extra_usage < extra_limit:
        usage["unified_extra"] += 1
        usage["total_requests"] = usage.get("total_requests", 0) + 1
        update_user_usage_struct(email, usage)
        return True, False

    return False, False


async def check_trial_allowance(email, model_id):
    if email == ADMIN_EMAIL:
        return True

    _, usage = get_user_limits_and_usage(email)
    internal_key = MODEL_MAPPING.get(model_id, "unknown")
    trial_counts = usage.get("trial_counts", {})
    model_trial_count = trial_counts.get(internal_key, 0)

    if model_trial_count < 10:
        trial_counts[internal_key] = model_trial_count + 1
        usage["trial_counts"] = trial_counts
        update_user_usage_struct(email, usage)
        return True
    return False


def has_active_paid_subscription(email: str) -> bool:
    """
    Returns True if the user has at least one active non-free subscription.
    Used to gate access to premium tools (OCR, RAG).
    """
    if email == ADMIN_EMAIL:
        return True

    user = get_user_by_email(email)
    if not user:
        return False

    active_plans = user.get("active_plans", [])
    now = datetime.utcnow()
    for p in active_plans:
        try:
            exp_date = datetime.fromisoformat(p["expires"])
            if exp_date > now:
                plan_key = p.get("plan_key", "")
                if plan_key and plan_key != "free_tier":
                    return True
        except:
            pass
    return False


def get_limits_for_new_subscription(plan_key, period="monthly"):
    config = PLAN_CONFIGS.get(plan_key)
    if not config:
        limits = PLAN_CONFIGS["free_tier"]["daily_limits"].copy()
        limits["unified_extra"] = 0
        return limits

    limits = config["daily_limits"].copy()
    overdraft = config["overdraft"].get(period, 0)
    limits["unified_extra"] = overdraft
    return limits


# ============================================================================
# PREMIUM TOOLS ACCESS GUARD — يُستخدم كـ middleware في main.py
# ============================================================================

async def check_premium_tool_access(request: Request):
    """
    تتحقق من صلاحية الوصول للأدوات المميزة.
    تُعيد None إذا كان الوصول مسموحاً، أو JSONResponse جاهز في حالة الرفض.
    """
    path = request.url.path
    if "/tools/execute/" not in path:
        return None

    parts = path.split("/tools/execute/")
    if len(parts) != 2:
        return None

    tool_id = parts[1].strip("/").split("/")[0]
    if tool_id not in PREMIUM_TOOL_IDS:
        return None

    # local import لتجنب الاستيراد الدائري
    from services.auth import get_current_user_email

    email = get_current_user_email(request)
    if not email:
        return JSONResponse(
            {"error": "يجب تسجيل الدخول لاستخدام هذه الأداة. / Login required to use this tool."},
            status_code=401,
        )

    if not has_active_paid_subscription(email):
        return JSONResponse(
            {
                "error":       "هذه الأداة متاحة للمشتركين فقط. / This tool requires an active paid subscription.",
                "upgrade_url": "/cart",
            },
            status_code=403,
        )

    return None  # الوصول مسموح
