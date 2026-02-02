from datetime import datetime, timedelta
from database import get_user_by_email, activate_subscription as db_activate
from services.limits import get_limits_for_new_subscription, PLAN_NAME_MAP

PLAN_DETAILS = {
    "Free Tier": { "key": "free_tier", "name_en": "Free Tier", "is_perpetual": True, "has_overdraft": False },
    "Chat Agents": { "key": "chat_agents", "name_en": "Chat Agents Bundle", "is_perpetual": False, "has_overdraft": True },
    "Nexus Global": { "key": "nexus_global", "name_en": "Nexus Global", "is_perpetual": False, "has_overdraft": True },
    "DeepSeek V3": { "key": "deepseek", "name_en": "DeepSeek V3", "is_perpetual": False, "has_overdraft": True },
    "Kimi k2": { "key": "kimi", "name_en": "Kimi k2", "is_perpetual": False, "has_overdraft": True },
    "Mistral Large": { "key": "mistral", "name_en": "Mistral Large", "is_perpetual": False, "has_overdraft": True },
    "Gemma 3": { "key": "gemma", "name_en": "Gemma 3", "is_perpetual": False, "has_overdraft": True },
    "Llama 3.2": { "key": "llama", "name_en": "Llama 3.2", "is_perpetual": False, "has_overdraft": True }
}

def calculate_days_duration(period: str) -> int:
    if period == "weekly": return 7
    if period == "monthly": return 30
    if period == "yearly": return 365
    return 0

def get_user_subscription_status(email: str):
    user = get_user_by_email(email)
    if not user: return None

    plan_name = user.get("plan", "Free Tier")
    sub_end = user.get("subscription_end")
    plan_info = PLAN_DETAILS.get(plan_name, PLAN_DETAILS["Free Tier"])

    status = {
        "plan_name": plan_name,
        "is_active": True,
        "days_left": "∞",
        "is_perpetual": plan_info["is_perpetual"],
        "has_overdraft": plan_info["has_overdraft"]
    }

    if not plan_info["is_perpetual"] and sub_end:
        try:
            expiry = datetime.fromisoformat(sub_end)
            now = datetime.utcnow()
            if expiry > now:
                status["is_active"] = True
                status["days_left"] = (expiry - now).days
            else:
                status["is_active"] = False
                status["days_left"] = 0
                status["plan_name"] = f"{plan_name} (Expired)"
        except: pass
    return status

def perform_upgrade(email: str, plan_name: str, period: str) -> bool:
    if plan_name not in PLAN_DETAILS: return False
    plan_info = PLAN_DETAILS[plan_name]
    days = calculate_days_duration(period)
    if days == 0 and not plan_info["is_perpetual"]: return False

    new_limits = get_limits_for_new_subscription(plan_info["key"], period)
    success = db_activate(email, plan_name, days, new_limits)
    return success