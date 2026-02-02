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
    # إرجاع هيكل فارغ افتراضي
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
    from services.limits import get_limits_for_new_subscription # تجنب الاستيراد الدائري

    default_limits = get_limits_for_new_subscription("free_tier")

    user_data = {
        "email": email, "password": password_hash, "api_key": api_key,
        "created_at": datetime.utcnow().isoformat(), "plan": "Free Tier",
        "subscription_end": None,
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

def activate_subscription(email, plan_name, days, limits_dict):
    if not redis: return False
    user = get_user_by_email(email)
    if not user: return False
    expiry_date = datetime.utcnow() + timedelta(days=days)
    user["plan"] = plan_name
    user["limits"] = limits_dict
    user["subscription_end"] = expiry_date.isoformat()
    try: redis.set(f"user:{email}", json.dumps(user)); return True
    except: return False

def update_user_usage_struct(email, usage_data):
    if not redis: return False
    user = get_user_by_email(email)
    if user:
        user["usage"] = usage_data
        try: redis.set(f"user:{email}", json.dumps(user)); return True
        except: return False
    return False

def track_request_metrics(email, latency_ms, tokens, model_key=None, is_error=False, is_internal=False, is_blocked=False):
    """
    تسجيل المقاييس للمستخدم وللنظام العام.
    """
    update_global_stats(latency_ms, tokens, model_key, is_error, is_internal, is_blocked)
    if not redis: return False
    user = get_user_by_email(email)
    if not user: return False

    usage = user.get("usage", {})
    today_str = str(datetime.utcnow().date())

    if usage.get("date") != today_str:
        usage = {
            "date": today_str,
            "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0, "unified_extra": 0,
            "total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "internal_ops": 0
        }

    usage["total_requests"] = usage.get("total_requests", 0) + 1

    # لا نحتسب استهلاك التوكنز أو زمن الاستجابة في سجل المستخدم إذا كان محظوراً
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