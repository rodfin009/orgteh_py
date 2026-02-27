import os
import json
import ssl
import pymysql
from datetime import datetime, timedelta

# ============================================================================
# REDIS CONFIGURATION
# ============================================================================
UPSTASH_URL = os.environ.get("UPSTASH_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_TOKEN")

try:
    if UPSTASH_URL and UPSTASH_TOKEN:
        from upstash_redis import Redis
        redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
        print("✅ Connected to Upstash Redis.")
    else:
        from redis import Redis as StandardRedis
        redis = StandardRedis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            decode_responses=True
        )
        print("✅ Connected to Local Redis.")
except Exception as e:
    print(f"⚠️ Warning: Redis connection failed. {e}")
    redis = None

# ============================================================================
# TiDB / MySQL CONFIGURATION
# ============================================================================
TIDB_HOST = os.environ.get("TIDB_HOST", "gateway01.us-east-1.prod.aws.tidbcloud.com")
TIDB_PORT = int(os.environ.get("TIDB_PORT", 4000))
TIDB_USER = os.environ.get("TIDB_USER", "root")
TIDB_PASSWORD = os.environ.get("TIDB_PASSWORD", "")
TIDB_NAME = os.environ.get("TIDB_NAME", "test")

def get_db_connection():
    """إنشاء اتصال آمن مع قاعدة بيانات TiDB Serverless"""
    try:
        return pymysql.connect(
            host=TIDB_HOST,
            port=TIDB_PORT,
            user=TIDB_USER,
            password=TIDB_PASSWORD,
            database=TIDB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            ssl={"ssl_cert_reqs": ssl.CERT_NONE}  # ضروري لتخطي مشاكل شهادات SSL في Serverless
        )
    except Exception as e:
        print(f"❌ TiDB Connection Error: {e}")
        return None

def init_db():
    """إنشاء الجداول الأساسية إذا لم تكن موجودة"""
    conn = get_db_connection()
    if not conn: 
        print("⚠️ Skipping DB init: No connection")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email VARCHAR(255) PRIMARY KEY,
                password_hash VARCHAR(255),
                api_key VARCHAR(255) UNIQUE,
                data JSON
            )
            """)
        print("✅ TiDB Database initialized successfully.")
    except Exception as e:
        print(f"❌ TiDB Init Error: {e}")
    finally:
        conn.close()

# تشغيل التهيئة عند بدء تشغيل السيرفر
init_db()

# ============================================================================
# GLOBAL STATS (Write-Behind -> Redis Only)
# ============================================================================
def update_global_stats(latency_ms, tokens, model_key=None, is_error=False, is_internal=False, is_blocked=False):
    if not redis: return
    today_str = str(datetime.utcnow().date())
    global_key = f"global_stats:{today_str}"
    try:
        stats = redis.get(global_key)
        if stats: 
            stats = json.loads(stats) if isinstance(stats, str) else stats
        else:
            stats = {
                "total_requests": 0, "total_tokens": 0, 
                "latency_sum": 0, "errors": 0, "blocked": 0, "internal_ops": 0,
                "models": {} 
            }

        if "blocked" not in stats: stats["blocked"] = 0
        stats["total_requests"] = stats.get("total_requests", 0) + 1

        if is_blocked:
            stats["blocked"] = stats.get("blocked", 0) + 1
        else:
            stats["total_tokens"] = stats.get("total_tokens", 0) + tokens
            stats["latency_sum"] = stats.get("latency_sum", 0) + latency_ms
            if is_error: stats["errors"] = stats.get("errors", 0) + 1
            if is_internal: stats["internal_ops"] = stats.get("internal_ops", 0) + 1

            if model_key:
                if "models" not in stats: stats["models"] = {}
                m_stats = stats["models"].get(model_key, {"reqs": 0, "lat_sum": 0})
                m_stats["reqs"] += 1
                m_stats["lat_sum"] += latency_ms
                stats["models"][model_key] = m_stats

        redis.set(global_key, json.dumps(stats))
    except Exception as e: 
        print(f"⚠️ Global Stats Update Error: {e}")

def get_global_stats():
    if not redis: return {}
    today_str = str(datetime.utcnow().date())
    try:
        stats = redis.get(f"global_stats:{today_str}")
        if stats: return json.loads(stats) if isinstance(stats, str) else stats
    except: pass
    return {"total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "blocked": 0, "internal_ops": 0, "models": {}}

# ============================================================================
# USER OPERATIONS (Cache-Aside & Write-Through)
# ============================================================================
def get_user_by_email(email):
    """(Cache-Aside) قراءة من Redis، وإن لم يوجد من TiDB"""
    if redis:
        try:
            user_json = redis.get(f"user:{email}")
            if user_json: 
                return json.loads(user_json) if isinstance(user_json, str) else user_json
        except: pass

    # الجلب من TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
                if row and row['data']:
                    user_data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
                    if redis: redis.set(f"user:{email}", json.dumps(user_data))
                    return user_data
        except Exception as e: 
            print(f"❌ DB Read Error: {e}")
        finally: 
            conn.close()
    return None

def create_user_record(email, password_hash, api_key):
    """(Write-Through) حفظ في TiDB أولاً ثم Redis لضمان عدم ضياع الحساب"""
    from services.limits import get_limits_for_new_subscription 
    default_limits = get_limits_for_new_subscription("free_tier")

    user_data = {
        "email": email, "password": password_hash, "api_key": api_key,
        "created_at": datetime.utcnow().isoformat(), "plan": "Free Tier",
        "subscription_end": None, "active_plans": [], "subscription_history": [],
        "limits": default_limits, 
        "usage": {
            "date": str(datetime.utcnow().date()),
            "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0, "unified_extra": 0,
            "total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "internal_ops": 0
        }
    }

    # 1. Write to TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash, api_key, data) VALUES (%s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE data = %s, api_key = %s, password_hash = %s",
                    (email, password_hash, api_key, json.dumps(user_data), json.dumps(user_data), api_key, password_hash)
                )
        except Exception as e: 
            print(f"❌ TiDB Write Error: {e}")
            return False
        finally: 
            conn.close()

    # 2. Write to Redis
    if redis:
        try:
            redis.set(f"user:{email}", json.dumps(user_data))
            redis.set(f"api_key:{api_key}", email)
        except: pass
    return True

def get_user_by_api_key(api_key):
    """(Cache-Aside) قراءة عبر مفتاح الـ API"""
    if redis:
        try:
            email = redis.get(f"api_key:{api_key}")
            if email: 
                decoded_email = email.decode('utf-8') if isinstance(email, bytes) else email
                return get_user_by_email(decoded_email)
        except: pass

    # الجلب من TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT email FROM users WHERE api_key = %s", (api_key,))
                row = cur.fetchone()
                if row:
                    email = row['email']
                    if redis: redis.set(f"api_key:{api_key}", email)
                    return get_user_by_email(email)
        except Exception as e: 
            print(f"❌ DB API Key Fetch Error: {e}")
        finally: 
            conn.close()
    return None

def update_api_key(email, new_key):
    """(Write-Through) تغيير مفتاح الـ API"""
    user = get_user_by_email(email)
    if not user: return False
    old_key = user.get("api_key")
    user["api_key"] = new_key

    # 1. TiDB Update
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET api_key = %s, data = %s WHERE email = %s", 
                            (new_key, json.dumps(user), email))
        except Exception as e:
            print(f"❌ TiDB Key Update Error: {e}")
            return False
        finally: 
            conn.close()

    # 2. Redis Update
    if redis:
        try:
            if old_key: redis.delete(f"api_key:{old_key}")
            redis.set(f"api_key:{new_key}", email)
            redis.set(f"user:{email}", json.dumps(user))
        except: pass
    return True

# ============================================================================
# SUBSCRIPTIONS (Write-Through)
# ============================================================================
def add_user_subscription(email, plan_key, plan_name, period):
    user = get_user_by_email(email)
    if not user: return False

    from services.limits import get_limits_for_new_subscription
    limits_dict = get_limits_for_new_subscription(plan_key, period)
    days_to_add = 365 if period == 'yearly' else 30

    if "active_plans" not in user: user["active_plans"] = []
    existing_plan = next((p for p in user["active_plans"] if p.get("plan_key") == plan_key), None)
    now = datetime.utcnow()
    if "subscription_history" not in user: user["subscription_history"] = []

    if existing_plan:
        try:
            current_exp = datetime.fromisoformat(existing_plan["expires"])
            new_exp = current_exp + timedelta(days=days_to_add) if current_exp > now else now + timedelta(days=days_to_add)
        except: 
            new_exp = now + timedelta(days=days_to_add)
        existing_plan["expires"] = new_exp.isoformat()
        existing_plan["period"] = period
        existing_plan["limits"] = limits_dict
    else:
        new_exp = now + timedelta(days=days_to_add)
        user["active_plans"].append({
            "plan_key": plan_key, "name": plan_name, "period": period,
            "activated": now.isoformat(), "expires": new_exp.isoformat(), "limits": limits_dict
        })

    user["subscription_history"].append({
        "plan_key": plan_key, "name": plan_name, "period": period,
        "activated": now.isoformat(), "expires": new_exp.isoformat(),
        "type": "renewal" if existing_plan else "new"
    })

    active_names = [p["name"] for p in user["active_plans"] if datetime.fromisoformat(p["expires"]) > now]
    user["plan"] = " + ".join(active_names) if active_names else "Free Tier"
    expirations = [p["expires"] for p in user["active_plans"] if datetime.fromisoformat(p["expires"]) > now]
    user["subscription_end"] = max(expirations) if expirations else None

    # 1. Update TiDB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET data = %s WHERE email = %s", (json.dumps(user), email))
        except Exception as e:
            print(f"❌ TiDB Sub Update Error: {e}")
            return False
        finally: 
            conn.close()

    # 2. Update Redis
    if redis:
        try: redis.set(f"user:{email}", json.dumps(user))
        except: pass
    return True

def activate_subscription(email, plan_name, days, limits_dict):
    period = 'yearly' if days > 300 else 'monthly'
    plan_key = plan_name.lower().replace(" ", "_")
    return add_user_subscription(email, plan_key, plan_name, period)

def get_subscription_history(email):
    """إرجاع سجل الاشتراكات الكامل للمستخدم (نشطة + منتهية)"""
    user = get_user_by_email(email)
    if not user: return []

    history = user.get("subscription_history", [])
    if not history:
        history = []
        for p in user.get("active_plans", []):
            history.append({
                "plan_key": p.get("plan_key", ""), "name": p.get("name", ""),
                "period": p.get("period", ""), "activated": p.get("activated", p.get("expires", "")),
                "expires": p.get("expires", ""), "type": "new"
            })
    return sorted(history, key=lambda x: x.get("activated", ""), reverse=True)

# ============================================================================
# USAGE TRACKING (Write-Behind -> Redis Only for Speed)
# ============================================================================
def update_user_usage_struct(email, usage_data):
    if not redis: return False
    user = get_user_by_email(email)
    if user:
        user["usage"] = usage_data
        try: 
            redis.set(f"user:{email}", json.dumps(user))
            return True
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
            "date": today_str, "deepseek": 0, "kimi": 0, "mistral": 0, "llama": 0, "gemma": 0, 
            "unified_extra": usage.get("unified_extra", 0),
            "total_requests": 0, "total_tokens": 0, "latency_sum": 0, "errors": 0, "internal_ops": 0
        }

    usage["total_requests"] = usage.get("total_requests", 0) + 1
    if not is_blocked:
        usage["total_tokens"] = usage.get("total_tokens", 0) + tokens
        usage["latency_sum"] = usage.get("latency_sum", 0) + latency_ms

    if is_error: usage["errors"] = usage.get("errors", 0) + 1
    if is_internal: usage["internal_ops"] = usage.get("internal_ops", 0) + 1

    user["usage"] = usage
    try: 
        redis.set(f"user:{email}", json.dumps(user))
        return True
    except: return False

# ============================================================================
# BACKGROUND SYNC (Redis -> TiDB)
# ============================================================================
def sync_all_usage_to_db():
    """
    تقوم هذه الدالة بجلب كل بيانات المستخدمين من Redis (بما فيها الاستهلاك المحدث)
    وتحفظها في TiDB لضمان عدم ضياعها. يتم استدعاؤها عبر /api/admin/sync-db
    """
    if not redis: return {"status": "error", "message": "Redis not connected"}

    conn = get_db_connection()
    if not conn: return {"status": "error", "message": "TiDB not connected"}

    try:
        keys = redis.keys("user:*")
        updated = 0
        with conn.cursor() as cur:
            for key in keys:
                user_data = redis.get(key)
                if user_data:
                    user_dict = json.loads(user_data) if isinstance(user_data, str) else user_data
                    email = user_dict.get("email")
                    if email:
                        cur.execute("UPDATE users SET data = %s WHERE email = %s", (json.dumps(user_dict), email))
                        updated += 1
        return {"status": "success", "synced_users": updated}
    except Exception as e:
        print(f"❌ DB Sync Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

# ============================================================================
# ENTERPRISE LEADS
# ============================================================================
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