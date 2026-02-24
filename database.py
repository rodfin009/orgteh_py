import os
import json
from datetime import datetime, timedelta
from upstash_redis import Redis

UPSTASH_URL = os.environ.get("UPSTASH_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_TOKEN")

try:
    if UPSTASH_URL and UPSTASH_TOKEN:
        redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
        print("Connected to Upstash Redis.")
    else:
        from redis import Redis as StandardRedis
        redis = StandardRedis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            decode_responses=True
        )
        print("Connected to Local Redis.")

except Exception as e:
    print(f"Warning: Redis connection failed. {e}")
    redis = None

def update_global_stats(latency_ms, tokens, model_key=None, is_error=False, is_internal=False, is_blocked=False):
    """
    تحديث الإحصائيات العامة مع دعم الحظر (Blocked) وفصله عن الأخطاء.
    """
    if not redis: return
    today_str = str(datetime.utcnow().date())
    global_key = f"global_stats:{today_str}"
    try:
        stats = redis.get(global_key)
        if stats: stats = json.loads(stats) if isinstance(stats, str) else stats
        else:
            stats = {
                "total_requests": 0, "total_tokens": 0, 
                "latency_sum": 0, "errors": 0, "blocked": 0, "internal_ops": 0,
                "models": {} 
            }

        # التأكد من وجود حقل blocked للبيانات القديمة
        if "blocked" not in stats: stats["blocked"] = 0

        stats["total_requests"] = stats.get("total_requests", 0) + 1

        # إذا كان محظوراً، لا نحسب توكنز ولا سرعة، ونزيد عداد الحظر فقط
        if is_blocked:
            stats["blocked"] = stats.get("blocked", 0) + 1
        else:
            # الطلبات غير المحظورة (سواء نجحت أو فشلت فنياً)
            stats["total_tokens"] = stats.get("total_tokens", 0) + tokens
            stats["latency_sum"] = stats.get("latency_sum", 0) + latency_ms

            if is_error: 
                stats["errors"] = stats.get("errors", 0) + 1

            if is_internal: 
                stats["internal_ops"] = stats.get("internal_ops", 0) + 1

            # تحديث إحصائيات الموديل الفردي (فقط إذا لم يكن محظوراً)
            if model_key:
                if "models" not in stats: stats["models"] = {}
                m_stats = stats["models"].get(model_key, {"reqs": 0, "lat_sum": 0})
                m_stats["reqs"] += 1
                m_stats["lat_sum"] += latency_ms
                stats["models"][model_key] = m_stats

        redis.set(global_key, json.dumps(stats))
    except Exception as e: print(f"Global Stats Update Error: {e}")

def get_global_stats():
    if not redis: return {}
    today_str = str(datetime.utcnow().date())
    try:
        stats = redis.get(f"global_stats:{today_str}")
        if stats: return json.loads(stats) if isinstance(stats, str) else stats
    except: pass
    return {
        "total_requests": 0, "total_tokens": 0, 
        "latency_sum": 0, "errors": 0, "blocked": 0, "internal_ops": 0, 
        "models": {}
    }

def get_user_by_email(email):
    if not redis: return None
    try:
        user_json = redis.get(f"user:{email}")
        if user_json: return json.loads(user_json) if isinstance(user_json, str) else user_json
    except: pass
    return None

def create_user_record(email, password_hash, api_key):
    if not redis: return False
    from services.limits import get_limits_for_new_subscription 

    default_limits = get_limits_for_new_subscription("free_tier")

    user_data = {
        "email": email, "password": password_hash, "api_key": api_key,
        "created_at": datetime.utcnow().isoformat(), "plan": "Free Tier",
        "subscription_end": None,
        "active_plans": [], # المصفوفة الجديدة لدعم الباقات التراكمية
        "subscription_history": [], # سجل كامل لجميع الاشتراكات
        "limits": default_limits, 
        "usage": {
            "date": str(datetime.utcnow().date()),
            "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0, "unified_extra": 0,
            "total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "internal_ops": 0
        }
    }
    try:
        redis.set(f"user:{email}", json.dumps(user_data))
        redis.set(f"api_key:{api_key}", email)
        return True
    except: return False

def add_user_subscription(email, plan_key, plan_name, period):
    """
    دالة جديدة لإضافة اشتراك بشكل تراكمي، سواء كان جديداً أو تمديداً لاشتراك حالي
    """
    if not redis: return False
    user = get_user_by_email(email)
    if not user: return False

    from services.limits import get_limits_for_new_subscription
    limits_dict = get_limits_for_new_subscription(plan_key, period)

    days_to_add = 365 if period == 'yearly' else 30

    if "active_plans" not in user:
        user["active_plans"] = []

    existing_plan = next((p for p in user["active_plans"] if p.get("plan_key") == plan_key), None)

    now = datetime.utcnow()

    if "subscription_history" not in user:
        user["subscription_history"] = []

    if existing_plan:
        try:
            current_exp = datetime.fromisoformat(existing_plan["expires"])
            if current_exp > now:
                new_exp = current_exp + timedelta(days=days_to_add)
            else:
                new_exp = now + timedelta(days=days_to_add)
        except:
            new_exp = now + timedelta(days=days_to_add)

        existing_plan["expires"] = new_exp.isoformat()
        existing_plan["period"] = period
        existing_plan["limits"] = limits_dict
    else:
        new_exp = now + timedelta(days=days_to_add)
        user["active_plans"].append({
            "plan_key": plan_key,
            "name": plan_name,
            "period": period,
            "activated": now.isoformat(),
            "expires": new_exp.isoformat(),
            "limits": limits_dict
        })

    # ✅ تسجيل في سجل الاشتراكات الكامل (يُضاف دائماً بغض النظر عن التمديد)
    user["subscription_history"].append({
        "plan_key": plan_key,
        "name": plan_name,
        "period": period,
        "activated": now.isoformat(),
        "expires": new_exp.isoformat(),
        "type": "renewal" if existing_plan else "new"
    })

    # توافق رجعي للأنظمة القديمة في الواجهة
    active_names = [p["name"] for p in user["active_plans"] if datetime.fromisoformat(p["expires"]) > now]
    user["plan"] = " + ".join(active_names) if active_names else "Free Tier"
    expirations = [p["expires"] for p in user["active_plans"] if datetime.fromisoformat(p["expires"]) > now]
    user["subscription_end"] = max(expirations) if expirations else None

    try: 
        redis.set(f"user:{email}", json.dumps(user))
        return True
    except: 
        return False

# للأنظمة القديمة التي لا زالت تعتمد الدالة القديمة (يتم تحويلها لتعمل بالتراكمي)
def activate_subscription(email, plan_name, days, limits_dict):
    period = 'yearly' if days > 300 else 'monthly'
    plan_key = plan_name.lower().replace(" ", "_")
    return add_user_subscription(email, plan_key, plan_name, period)

def update_user_usage_struct(email, usage_data):
    if not redis: return False
    user = get_user_by_email(email)
    if user:
        user["usage"] = usage_data
        try: redis.set(f"user:{email}", json.dumps(user)); return True
        except: return False
    return False

def track_request_metrics(email, latency_ms, tokens, model_key=None, is_error=False, is_internal=False, is_blocked=False):
    update_global_stats(latency_ms, tokens, model_key, is_error, is_internal, is_blocked)
    if not redis: return False
    user = get_user_by_email(email)
    if not user: return False

    usage = user.get("usage", {})
    today_str = str(datetime.utcnow().date())

    if usage.get("date") != today_str:
        usage = {
            "date": today_str,
            "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0, "unified_extra": usage.get("unified_extra", 0),
            "total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "internal_ops": 0
        }

    usage["total_requests"] = usage.get("total_requests", 0) + 1

    if not is_blocked:
        usage["total_tokens"] = usage.get("total_tokens", 0) + tokens
        usage["latency_sum"] = usage.get("latency_sum", 0) + latency_ms

    if is_error: usage["errors"] = usage.get("errors", 0) + 1
    if is_internal: usage["internal_ops"] = usage.get("internal_ops", 0) + 1

    user["usage"] = usage
    try: redis.set(f"user:{email}", json.dumps(user)); return True
    except: return False

def get_user_by_api_key(api_key):
    if not redis: return None
    try:
        email = redis.get(f"api_key:{api_key}")
        if email: return get_user_by_email(email if isinstance(email, str) else email.decode('utf-8'))
    except: pass
    return None

def update_api_key(email, new_key):
    if not redis: return False
    user = get_user_by_email(email)
    if not user: return False
    old_key = user.get("api_key")
    user["api_key"] = new_key
    try:
        if old_key: redis.delete(f"api_key:{old_key}")
        redis.set(f"api_key:{new_key}", email)
        redis.set(f"user:{email}", json.dumps(user))
        return True
    except: return False

def get_subscription_history(email):
    """إرجاع سجل الاشتراكات الكامل للمستخدم (نشطة + منتهية)"""
    user = get_user_by_email(email)
    if not user:
        return []
    # نعيد السجل الكامل مرتباً من الأحدث للأقدم
    history = user.get("subscription_history", [])
    # إذا لم يكن السجل موجوداً (مستخدمون قدامى)، نبنيه من active_plans
    if not history:
        history = []
        for p in user.get("active_plans", []):
            history.append({
                "plan_key": p.get("plan_key", ""),
                "name": p.get("name", ""),
                "period": p.get("period", ""),
                "activated": p.get("activated", p.get("expires", "")),
                "expires": p.get("expires", ""),
                "type": "new"
            })
    return sorted(history, key=lambda x: x.get("activated", ""), reverse=True)

def create_enterprise_lead(data):
    if not redis: return False
    lead_id = f"lead:{int(datetime.utcnow().timestamp())}"
    data['submitted_at'] = datetime.utcnow().isoformat()
    data['status'] = 'new'
    try:
        redis.set(lead_id, json.dumps(data))
        redis.lpush("enterprise_leads", lead_id)
        return True
    except: return False