import os
import json
import ssl
import pymysql
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlparse

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
            ssl={"ssl_cert_reqs": ssl.CERT_NONE}
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

init_db()

# ============================================================================
# VISITORS TABLE INIT — جدول تتبع الزوار (دائم لا يُحذف عند إعادة التشغيل)
# ============================================================================

def init_visitors_table():
    conn = get_db_connection()
    if not conn:
        print("⚠️ Skipping visitors table init: No DB connection")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS site_visits (
                id             BIGINT AUTO_INCREMENT PRIMARY KEY,
                visited_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ip_address     VARCHAR(50),
                country        VARCHAR(100)  DEFAULT 'غير معروف',
                referer        VARCHAR(500),
                referer_domain VARCHAR(100),
                user_agent     VARCHAR(500),
                path           VARCHAR(500)
            )
            """)
            try:
                cur.execute("CREATE INDEX idx_visited_at ON site_visits (visited_at)")
            except Exception: pass
            try:
                cur.execute("ALTER TABLE site_visits ADD COLUMN country VARCHAR(100) DEFAULT 'غير معروف'")
            except Exception: pass
        print("✅ site_visits table initialized.")
    except Exception as e:
        print(f"❌ Visitors Table Init Error: {e}")
    finally:
        conn.close()

init_visitors_table()

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
# USER OPERATIONS (Cache-Aside & Write-Through with Self-Healing)
# ============================================================================
def get_user_by_email(email):
    user_data = None
    if redis:
        try:
            user_json = redis.get(f"user:{email}")
            if user_json:
                user_data = json.loads(user_json) if isinstance(user_json, str) else user_json
        except: pass

    # Fallback to DB if not in Redis
    if not user_data:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM users WHERE email = %s", (email,))
                    row = cur.fetchone()
                    if row and row['data']:
                        user_data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
            except Exception as e:
                print(f"❌ DB Read Error: {e}")
            finally:
                conn.close()

    # Self-Healing: Ensure Redis holds BOTH user data AND the API Key mapping
    if user_data and redis:
        try:
            redis.set(f"user:{email}", json.dumps(user_data))
            api_key = user_data.get("api_key")
            if api_key:
                redis.set(f"api_key:{api_key}", email)
        except: pass

    return user_data

def create_user_record(email, password_hash, api_key):
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

    # 1. Update Redis FIRST to guarantee API works instantly even if TiDB times out
    if redis:
        try:
            redis.set(f"user:{email}", json.dumps(user_data))
            redis.set(f"api_key:{api_key}", email)
        except: pass

    # 2. Update TiDB
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
            print(f"❌ TiDB Write Error on Create: {e}")
            # We don't return False here anymore because Redis was successfully updated
        finally:
            conn.close()

    return True

def get_user_by_api_key(api_key):
    if redis:
        try:
            email = redis.get(f"api_key:{api_key}")
            if email:
                decoded_email = email.decode('utf-8') if isinstance(email, bytes) else email
                return get_user_by_email(decoded_email)
        except: pass

    # Fallback to DB
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM users WHERE api_key = %s", (api_key,))
                row = cur.fetchone()
                if row and row['data']:
                    user_data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']

                    # Cache Healing
                    if redis and user_data:
                        email = user_data.get("email")
                        if email:
                            redis.set(f"api_key:{api_key}", email)
                            redis.set(f"user:{email}", json.dumps(user_data))
                    return user_data
        except Exception as e:
            print(f"❌ DB API Key Read Error: {e}")
        finally:
            conn.close()
    return None

def update_api_key(email, new_key):
    user = get_user_by_email(email)
    if not user: return False
    old_key = user.get("api_key")
    user["api_key"] = new_key

    # 1. Update Redis FIRST for immediate API access
    if redis:
        try:
            if old_key: redis.delete(f"api_key:{old_key}")
            redis.set(f"api_key:{new_key}", email)
            redis.set(f"user:{email}", json.dumps(user))
        except: pass

    # 2. Update TiDB asynchronously-like
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET api_key = %s, data = %s WHERE email = %s",
                            (new_key, json.dumps(user), email))
        except Exception as e:
            print(f"❌ TiDB Key Update Error: {e}")
            # Do not return False, Redis is updated and the API Key is active
        finally:
            conn.close()

    return True

# ============================================================================
# SUBSCRIPTIONS (Write-Through)
# ============================================================================
def add_user_subscription(email, plan_key, plan_name, period, limits_dict=None):
    user = get_user_by_email(email)
    if not user: return False

    from services.limits import get_limits_for_new_subscription
    # ✅ استخدم الحدود الممررة من perform_upgrade إذا كانت موجودة، وإلا احسبها من plan_key
    if limits_dict is None:
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

    # Update Redis First for fast state reflection
    if redis:
        try: redis.set(f"user:{email}", json.dumps(user))
        except: pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET data = %s WHERE email = %s", (json.dumps(user), email))
        except Exception as e:
            print(f"❌ TiDB Sub Update Error: {e}")
        finally:
            conn.close()

    return True

def activate_subscription(email, plan_name, days, limits_dict):
    from services.limits import PLAN_NAME_MAP
    period   = 'yearly' if days > 300 else 'monthly'
    # ✅ استخدم PLAN_NAME_MAP للحصول على المفتاح الصحيح بدلاً من توليده من الاسم
    plan_key = PLAN_NAME_MAP.get(plan_name, plan_name.lower().replace(" ", "_"))
    return add_user_subscription(email, plan_key, plan_name, period, limits_dict=limits_dict)

def fix_broken_subscription_limits(email: str) -> dict:
    """
    تُصلح الحدود المخزونة خطأً في active_plans بسبب plan_key غير صحيح.
    شغّلها مرة واحدة لكل مستخدم متأثر عبر endpoint مؤقت أو Telegram bot.
    """
    from services.limits import PLAN_NAME_MAP, get_limits_for_new_subscription

    user = get_user_by_email(email)
    if not user:
        return {"ok": False, "reason": "user not found"}

    fixed = []
    for plan in user.get("active_plans", []):
        plan_name   = plan.get("name", "")
        correct_key = PLAN_NAME_MAP.get(plan_name)
        if not correct_key:
            continue
        # أصلح المفتاح والحدود إذا كان المفتاح المخزون مختلفاً عن الصحيح
        old_key = plan.get("plan_key", "")
        if old_key != correct_key or not plan.get("limits"):
            plan["plan_key"] = correct_key
            plan["limits"]   = get_limits_for_new_subscription(
                correct_key, plan.get("period", "monthly")
            )
            fixed.append(f"{plan_name}: '{old_key}' → '{correct_key}'")

    if not fixed:
        return {"ok": True, "fixed": [], "message": "لا يوجد شيء يحتاج إصلاح"}

    if redis:
        try: redis.set(f"user:{email}", json.dumps(user))
        except: pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET data = %s WHERE email = %s",
                    (json.dumps(user), email)
                )
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        finally:
            conn.close()

    return {"ok": True, "fixed": fixed}


def get_subscription_history(email):
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
    """
    يحدّث بيانات الاستخدام في Redis (فوري).
    كل 5 طلبات: يزامن إلى TiDB تلقائياً لضمان عدم ضياع البيانات.
    """
    if not redis: return False
    user = get_user_by_email(email)
    if not user:
        return False

    user["usage"] = usage_data
    try:
        redis.set(f"user:{email}", json.dumps(user))
    except:
        return False

    # ── مزامنة تلقائية إلى TiDB مع كل طلب — صفر ضياع بيانات ────────────────
    total_reqs = usage_data.get("total_requests", 0)
    if total_reqs > 0:
        try:
            conn = get_db_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE users SET data = %s WHERE email = %s",
                            (json.dumps(user), email)
                        )
                except Exception as e:
                    print(f"⚠️ Auto-sync to TiDB failed (non-critical): {e}")
                finally:
                    conn.close()
        except Exception:
            pass  # صامت — Redis هو المصدر الأساسي

    return True

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

# ============================================================================
# REDIS HELPER
# ============================================================================
def get_redis():
    return redis

# ============================================================================
# USER PROFILE OPERATIONS
# ============================================================================
def update_user_profile(email: str, first_name: str, last_name: str) -> dict:
    import json as _json
    user = get_user_by_email(email)
    if not user:
        return {"error": "المستخدم غير موجود", "status": 404}

    user["first_name"] = first_name
    user["last_name"]  = last_name

    if redis:
        try: redis.set(f"user:{email}", _json.dumps(user))
        except: pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET data = %s WHERE email = %s",
                    (_json.dumps(user), email)
                )
        except Exception as e:
            return {"error": str(e), "status": 500}
        finally:
            conn.close()

    return {"message": "تم حفظ الاسم بنجاح", "status": 200}

def change_user_password(email: str, current_password: str, new_password: str,
                         validate_fn=None) -> dict:
    import bcrypt as _bcrypt
    import json as _json

    user = get_user_by_email(email)
    if not user:
        return {"error": "المستخدم غير موجود", "status": 404}

    try:
        valid = _bcrypt.checkpw(
            current_password.encode("utf-8"),
            user["password"].encode("utf-8")
        )
    except Exception:
        valid = False

    if not valid:
        return {"error": "كلمة المرور الحالية غير صحيحة", "status": 400}

    if validate_fn:
        is_valid_pw, error_msg = validate_fn(new_password)
        if not is_valid_pw:
            return {"error": error_msg, "status": 400}

    hashed = _bcrypt.hashpw(new_password.encode("utf-8"), _bcrypt.gensalt(12)).decode("utf-8")
    user["password"] = hashed

    if redis:
        try: redis.set(f"user:{email}", _json.dumps(user))
        except: pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s, data = %s WHERE email = %s",
                    (hashed, _json.dumps(user), email)
                )
        except Exception as e:
            return {"error": str(e), "status": 500}
        finally:
            conn.close()

    return {"message": "تم تغيير كلمة المرور بنجاح", "status": 200}

def delete_user_account(email: str) -> dict:
    user    = get_user_by_email(email)
    old_key = user.get("api_key") if user else None

    if redis:
        try:
            redis.delete(f"user:{email}")
            redis.delete(f"github:{email}")
            if old_key: redis.delete(f"api_key:{old_key}")
        except Exception: pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE email = %s", (email,))
        except Exception as e:
            return {"error": str(e), "status": 500}
        finally:
            conn.close()

    return {"message": "تم حذف الحساب بنجاح", "status": 200}

# ============================================================================
# HUB CHAT SESSIONS — جلسات محادثات Code Hub V1 (Redis)
# ============================================================================
import time as _time_module

def hub_save_chat(email: str, session_id: str, title: str,
                  history: list, files: dict) -> dict:
    import json as _json
    if not redis:
        return {"ok": False, "error": "Redis unavailable"}

    updated_at = int(_time_module.time())
    data = _json.dumps({
        "session_id": session_id,
        "title":      title,
        "history":    history[-40:],
        "files":      files,
        "updated_at": updated_at,
        "version":    "v1",
    }, ensure_ascii=False)

    try:
        key     = f"hub_chat:{email}:{session_id}"
        idx_key = f"hub_chat_idx:{email}"
        redis.setex(key, 60 * 60 * 24 * 30, data)
        redis.zadd(idx_key, {session_id: updated_at})
        redis.expire(idx_key, 60 * 60 * 24 * 30)
        redis.zremrangebyrank(idx_key, 0, -51)
        return {"ok": True, "session_id": session_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def hub_list_chats(email: str) -> list:
    import json as _json
    if not redis:
        return []
    try:
        idx_key = f"hub_chat_idx:{email}"
        ids     = redis.zrevrange(idx_key, 0, 49)
        chats   = []
        for sid in ids:
            sid = sid if isinstance(sid, str) else sid.decode()
            raw = redis.get(f"hub_chat:{email}:{sid}")
            if raw:
                d = _json.loads(raw)
                chats.append({
                    "session_id": d["session_id"],
                    "title":      d.get("title", "محادثة"),
                    "updated_at": d.get("updated_at", 0),
                    "msg_count":  len(d.get("history", [])),
                    "file_count": len(d.get("files", {})),
                    "version":    d.get("version", "v1"),
                })
        return chats
    except Exception:
        return []

def hub_get_chat(email: str, session_id: str) -> dict | None:
    import json as _json
    if not redis:
        return None
    try:
        raw = redis.get(f"hub_chat:{email}:{session_id}")
        return _json.loads(raw) if raw else None
    except Exception:
        return None

def hub_delete_chat(email: str, session_id: str) -> bool:
    if not redis:
        return False
    try:
        redis.delete(f"hub_chat:{email}:{session_id}")
        redis.zrem(f"hub_chat_idx:{email}", session_id)
        return True
    except Exception:
        return False

# ============================================================================
# VISITOR TRACKING & GEOLOCATION
# ============================================================================
def get_country_from_ip(ip: str) -> str:
    if not ip or ip in ["127.0.0.1", "localhost", "::1"] or ip.startswith("192.168.") or ip.startswith("10."):
        return "محلية (Local)"

    cache_key = f"geoip:{ip}"
    if redis:
        try:
            cached = redis.get(cache_key)
            if cached: return cached.decode('utf-8') if isinstance(cached, bytes) else cached
        except: pass

    try:
        req = urllib.request.Request(f"http://ip-api.com/json/{ip}?fields=country")
        with urllib.request.urlopen(req, timeout=2.0) as response:
            data = json.loads(response.read().decode('utf-8'))
            country = data.get("country", "غير معروف")
            if redis: redis.setex(cache_key, 30 * 24 * 3600, country)
            return country
    except Exception:
        return "غير معروف"

def _extract_referer_domain(referer: str) -> str:
    if not referer:
        return "مباشر"
    try:
        parsed = urlparse(referer)
        domain = parsed.netloc.lower().replace("www.", "")
        if not domain:
            return "مباشر"
        source_map = {
            "google.":      "Google", "bing.com":     "Bing", "yahoo.com":    "Yahoo",
            "t.co":         "Twitter/X", "twitter.com":  "Twitter/X", "x.com":        "Twitter/X",
            "facebook.com": "Facebook", "instagram.com":"Instagram", "linkedin.com": "LinkedIn",
            "youtube.com":  "YouTube", "reddit.com":   "Reddit", "tiktok.com":   "TikTok",
            "snapchat.com": "Snapchat", "telegram.org": "Telegram", "whatsapp.com": "WhatsApp",
        }
        for key, label in source_map.items():
            if key in domain:
                return label
        return domain
    except Exception:
        return "أخرى"

def record_visit(ip: str, referer: str, user_agent: str, path: str = "/"):
    now            = datetime.utcnow()
    today_str      = str(now.date())
    referer_domain = _extract_referer_domain(referer)
    country        = get_country_from_ip(ip)

    if redis:
        try:
            visits_key = f"visits:{today_str}"
            unique_key = f"unique_ips:{today_str}"

            raw = redis.get(visits_key)
            stats: dict = {}
            if raw:
                try: stats = json.loads(raw) if isinstance(raw, str) else raw
                except Exception: stats = {}
            if not isinstance(stats, dict): stats = {}

            stats["total"]      = stats.get("total", 0) + 1
            stats["last_visit"] = now.isoformat()

            is_new_ip = redis.sadd(unique_key, ip)
            if is_new_ip:
                stats["unique"] = stats.get("unique", 0) + 1

            redis.expire(unique_key, 7 * 24 * 3600)

            sources: dict = stats.get("sources", {})
            sources[referer_domain] = sources.get(referer_domain, 0) + 1
            stats["sources"] = sources

            hour_key = now.strftime("%H")
            hourly: dict = stats.get("hourly", {})
            hourly[hour_key] = hourly.get(hour_key, 0) + 1
            stats["hourly"] = hourly

            redis.setex(visits_key, 7 * 24 * 3600, json.dumps(stats))
        except Exception as e:
            print(f"⚠️ Redis visit record error: {e}")

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO site_visits
                       (visited_at, ip_address, country, referer, referer_domain, user_agent, path)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (now, ip[:50] if ip else "", country[:100], referer[:500] if referer else "",
                     referer_domain[:100], user_agent[:500] if user_agent else "", path[:500] if path else "/"),
                )
        except Exception as e:
            print(f"⚠️ TiDB visit insert error: {e}")
        finally:
            conn.close()

def get_visitor_stats(period: str = "24h") -> dict:
    now = datetime.utcnow()
    period_map = {
        "1h":  now - timedelta(hours=1), "24h": now - timedelta(hours=24),
        "1m":  now - timedelta(days=30), "1y":  now - timedelta(days=365),
    }
    since = period_map.get(period, now - timedelta(hours=24))

    total_visits    = 0
    unique_visits   = 0
    last_visit      = None
    sources         = {}
    countries       = {}
    daily_trend     = []
    recent_visitors = []

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as cnt, COUNT(DISTINCT ip_address) as ucnt, MAX(visited_at) as last_v "
                    "FROM site_visits WHERE visited_at >= %s", (since,)
                )
                row = cur.fetchone()
                if row:
                    total_visits  = row["cnt"] or 0
                    unique_visits = row["ucnt"] or 0
                    last_visit    = str(row["last_v"]) if row["last_v"] else None

                cur.execute(
                    "SELECT referer_domain, COUNT(*) as cnt FROM site_visits WHERE visited_at >= %s "
                    "GROUP BY referer_domain ORDER BY cnt DESC LIMIT 15", (since,)
                )
                for r in cur.fetchall():
                    sources[r["referer_domain"] or "مباشر"] = r["cnt"]

                cur.execute(
                    "SELECT country, COUNT(*) as cnt FROM site_visits WHERE visited_at >= %s "
                    "GROUP BY country ORDER BY cnt DESC LIMIT 15", (since,)
                )
                for r in cur.fetchall():
                    countries[r["country"]] = r["cnt"]

                cur.execute(
                    "SELECT ip_address, country, visited_at, path FROM site_visits "
                    "ORDER BY visited_at DESC LIMIT 20"
                )
                for r in cur.fetchall():
                    recent_visitors.append({
                        "ip": r["ip_address"], "country": r["country"],
                        "time": str(r["visited_at"]), "path": r["path"]
                    })

                trend_since = max(since, now - timedelta(days=30))
                cur.execute(
                    "SELECT DATE(visited_at) as day, COUNT(*) as cnt FROM site_visits "
                    "WHERE visited_at >= %s GROUP BY DATE(visited_at) ORDER BY day ASC", (trend_since,)
                )
                for r in cur.fetchall():
                    daily_trend.append({"date": str(r["day"]), "visits": r["cnt"]})

                # ── أكثر الصفحات زيارة ────────────────────────────────────
                top_pages = []
                cur.execute(
                    "SELECT path, COUNT(*) as cnt, COUNT(DISTINCT ip_address) as ucnt "
                    "FROM site_visits WHERE visited_at >= %s AND path NOT LIKE '/api/%%' "
                    "GROUP BY path ORDER BY cnt DESC LIMIT 30", (since,)
                )
                for r in cur.fetchall():
                    top_pages.append({
                        "path":   r["path"] or "/",
                        "visits": r["cnt"]  or 0,
                        "unique": r["ucnt"] or 0,
                    })

        except Exception as e:
            print(f"⚠️ Visitor stats DB error: {e}")
        finally:
            conn.close()

    if total_visits == 0 and redis:
        try:
            today_str = str(now.date())
            raw = redis.get(f"visits:{today_str}")
            if raw:
                stats_r = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(stats_r, dict):
                    total_visits = stats_r.get("total", 0)
                    unique_visits= stats_r.get("unique", 0)
                    last_visit   = stats_r.get("last_visit")
                    sources      = stats_r.get("sources", {})
                    countries    = stats_r.get("countries", {})
        except Exception:
            pass

    return {
        "total_visits":    total_visits,
        "unique_visits":   unique_visits,
        "last_visit":      last_visit,
        "sources":         sources,
        "countries":       countries,
        "daily_trend":     daily_trend,
        "recent_visitors": recent_visitors,
        "top_pages":       top_pages if "top_pages" in dir() else [],
        "period":          period,
    }