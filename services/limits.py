import json
from datetime import datetime
from database import get_user_by_email, update_user_usage_struct
from services.providers import MODEL_MAPPING

# --- 1. DEFINING THE TRUTH (تكوين الخطط) ---

PLAN_CONFIGS = {
    # --- Free Tier ---
    "free_tier": {
        "daily_limits": {
            "llama": 10, 
            "kimi": 5, 
            "deepseek": 0, 
            "mistral": 0, 
            "gemma": 0
        },
        "overdraft": {"forever": 0} 
    },

    # --- Bundles ---
    "chat_agents": {
        "daily_limits": {"gemma": 270, "llama": 200, "kimi": 30, "deepseek": 0, "mistral": 0},
        "overdraft": {"weekly": 250, "monthly": 750, "yearly": 2000}
    },
    "nexus_global": {
        "daily_limits": {"deepseek": 150, "kimi": 100, "mistral": 50, "llama": 0, "gemma": 0},
        "overdraft": {"weekly": 50, "monthly": 200, "yearly": 500}
    },

    # --- Individual Plans ---
    "deepseek": {
        "daily_limits": {"deepseek": 300, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0},
        "overdraft": {"weekly": 100, "monthly": 600, "yearly": 1500}
    },
    "kimi": {
        "daily_limits": {"kimi": 200, "deepseek": 0, "mistral": 0, "llama": 0, "gemma": 0},
        "overdraft": {"weekly": 75, "monthly": 300, "yearly": 1000}
    },
    "mistral": {
        "daily_limits": {"mistral": 100, "deepseek": 0, "kimi": 0, "llama": 0, "gemma": 0},
        "overdraft": {"weekly": 50, "monthly": 200, "yearly": 500}
    },
    "gemma": {
        "daily_limits": {"gemma": 500, "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0},
        "overdraft": {"weekly": 200, "monthly": 1000, "yearly": 3000}
    },
    "llama": {
        "daily_limits": {"llama": 400, "deepseek": 0, "kimi": 0, "mistral": 0, "gemma": 0},
        "overdraft": {"weekly": 150, "monthly": 800, "yearly": 2500}
    }
}

PLAN_NAME_MAP = {
    "Free Tier": "free_tier",
    "Chat Agents": "chat_agents",
    "Nexus Global": "nexus_global",
    "DeepSeek V3": "deepseek",
    "Kimi k2": "kimi",
    "Mistral Large": "mistral",
    "Gemma 3": "gemma",
    "Llama 3.2": "llama"
}

# --- 2. HELPER FUNCTIONS ---

def get_config_for_user(user):
    plan_name = user.get("plan", "Free Tier")
    config_key = PLAN_NAME_MAP.get(plan_name, "free_tier")
    return PLAN_CONFIGS.get(config_key, PLAN_CONFIGS["free_tier"])

def get_user_limits_and_usage(email):
    """
    تقوم بجلب الحدود والاستهلاك، وتتأكد من تصفير العدادات إذا بدأ يوم جديد.
    """
    user = get_user_by_email(email)
    if not user: return {}, {}

    config = get_config_for_user(user)

    # 1. Base limits from the Plan
    final_limits = config["daily_limits"].copy()

    # 2. Add extra credit (Unified Extra) from DB
    db_limits = user.get("limits", {})
    final_limits["unified_extra"] = db_limits.get("unified_extra", 0)

    # 3. Check for specific model overrides in DB
    for model in ["deepseek", "kimi", "mistral", "llama", "gemma"]:
        if model in db_limits and db_limits[model] > 0:
            final_limits[model] = db_limits[model]

    # 4. Handle Usage Structure & Date Reset
    usage = user.get("usage", {})
    today_str = str(datetime.utcnow().date())

    if usage.get("date") != today_str:
        preserved_extra_usage = usage.get("unified_extra", 0)
        usage = {
            "date": today_str,
            "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0,
            "unified_extra": preserved_extra_usage, # الرصيد الإضافي تراكمي لا يصفر يومياً
            "trial_count": 0, # تصفير عداد التجربة اليومي
            "total_requests": usage.get("total_requests", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_sum": 0, "errors": 0, "internal_ops": 0
        }
        update_user_usage_struct(email, usage)

    return final_limits, usage

async def check_request_allowance(email, model_id):
    """
    القلب النابض للحدود.
    Returns: (is_allowed: bool, is_priority: bool)

    Logic:
    1. If within Daily Limit -> Allowed + Priority (Fast Lane).
    2. If within Extra Credit -> Allowed + No Priority (Slow Lane).
    3. Else -> Not Allowed.
    """
    user = get_user_by_email(email)
    if not user: return False, False

    internal_key = MODEL_MAPPING.get(model_id)
    # إذا الموديل غير معروف، نسمح به مؤقتاً أو نرفضه (هنا نسمح به كـ Priority لتجنب التعطيل)
    if not internal_key: return True, True 

    limits, usage = get_user_limits_and_usage(email)

    # --- A. Check Daily Limit (The "Priority" Lane) ---
    daily_limit = limits.get(internal_key, 0)
    daily_usage = usage.get(internal_key, 0)

    if daily_usage < daily_limit:
        usage[internal_key] += 1
        # نقوم بتحديث الاستهلاك فوراً لمنع التلاعب (Check-and-Deduct)
        usage["total_requests"] = usage.get("total_requests", 0) + 1
        update_user_usage_struct(email, usage)
        return True, True # (Allowed, Fast Lane)

    # --- B. Check Extra Credit (The "Slow" Lane) ---
    extra_limit = limits.get("unified_extra", 0)
    extra_usage = usage.get("unified_extra", 0)

    if extra_usage < extra_limit:
        usage["unified_extra"] += 1
        usage["total_requests"] = usage.get("total_requests", 0) + 1
        update_user_usage_struct(email, usage)
        return True, False # (Allowed, Slow Lane)

    # --- C. Rejection ---
    return False, False

async def check_trial_allowance(email):
    """
    تحقق خاص للتجربة الحية (Live Demo) في الموقع.
    لا يخصم من الباقة، بحد أقصى 10 محاولات يومياً.
    """
    _, usage = get_user_limits_and_usage(email)
    trial_count = usage.get("trial_count", 0)

    if trial_count < 10:
        usage["trial_count"] = trial_count + 1
        update_user_usage_struct(email, usage)
        return True

    return False

def get_limits_for_new_subscription(plan_key, period="monthly"):
    """
    تستخدم عند إنشاء اشتراك جديد أو الترقية.
    """
    config = PLAN_CONFIGS.get(plan_key)
    if not config: return PLAN_CONFIGS["free_tier"]["daily_limits"], 0

    limits = config["daily_limits"].copy()
    overdraft = config["overdraft"].get(period, 0)
    limits["unified_extra"] = overdraft

    return limits