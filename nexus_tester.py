import requests
import sqlite3
import time
import random
import concurrent.futures
import json
import os
import statistics
import threading
import sys

# --- CONFIGURATION ---
BASE_URL = "http://localhost:8000"
DB_NAME = "smart_stress_test.db"
REPORT_FILE = "smart_analysis_report.txt"
LIVE_LOG_FILE = "live_stream_log.txt"
USERS_FILE = "saved_users.json"

# إعدادات المستخدمين
CONCURRENT_USERS = 100  # عدد المستخدمين الوهميين
REQUESTS_PER_USER = 150 # عدد الطلبات
VIP_ACCOUNTS = [
    {"email": "owidiwo@gmail.com", "password": "VipPassword123!"},
    {"email": "rodfin0202@gmail.com", "password": "VipPassword123!"}
]

# قفل
file_lock = threading.Lock()
users_file_lock = threading.Lock()

# النماذج
AVAILABLE_MODELS = [
    {"key": "deepseek", "id": "deepseek-ai/deepseek-v3.2", "limit": 150},
    {"key": "mistral",  "id": "mistralai/mistral-large-3-675b-instruct-2512", "limit": 50},
    {"key": "kimi",     "id": "moonshotai/kimi-k2-thinking", "limit": 100},
    {"key": "llama",    "id": "meta/llama-3.2-3b-instruct", "limit": 400}, # Added Llama
    {"key": "gemma",    "id": "google/gemma-3n-e4b-it", "limit": 500}      # Added Gemma
]

PROMPTS = {
    "Simple": ["Hi", "Hello", "Test", "1+1?", "Status?", "Ping", "Time?", "Echo", "Joking?"],
    "Complex": [
        "Write a Python function to implement Merge Sort.",
        "Explain the impact of Quantum Computing on cryptography.",
        "Summarize the history of the Roman Empire in 3 sentences.",
        "Debug this code snippet: 'def x(): return 1/0'",
        "Translate 'Artificial Intelligence' to 5 different languages.",
        "Generate a JSON schema for a user profile."
    ]
}

# --- إدارة المستخدمين المحفوظين ---
def load_stored_users():
    if not os.path.exists(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_user_to_file(user_data):
    with users_file_lock:
        current_users = load_stored_users()
        exists = False
        for i, u in enumerate(current_users):
            if u["email"] == user_data["email"]:
                current_users[i] = user_data
                exists = True
                break
        if not exists:
            current_users.append(user_data)
        with open(USERS_FILE, "w") as f:
            json.dump(current_users, f, indent=4)

# --- إعداد قاعدة البيانات ---
def setup_files():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('DROP TABLE IF EXISTS test_logs')
    c.execute('''
        CREATE TABLE test_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            model_key TEXT,
            prompt_type TEXT,
            status INTEGER,
            latency_ms REAL,
            prompt_tokens INTEGER,
            output_tokens INTEGER,
            priority_tag TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

    with open(LIVE_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== LIVE STREAM LOG STARTED AT {time.ctime()} ===\n")
        f.write(f"{'TIME':<10} | {'USER':<20} | {'MODEL':<10} | {'STATUS':<6} | {'LATENCY':<8} | {'PRIORITY'}\n")
        f.write("-" * 90 + "\n")

def log_result(email, model_key, p_type, status, lat, p_tok, o_tok, prio):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO test_logs 
              (user_email, model_key, prompt_type, status, latency_ms, prompt_tokens, output_tokens, priority_tag) 
              VALUES (?,?,?,?,?,?,?,?)''', 
              (email, model_key, p_type, status, lat, p_tok, o_tok, prio))
    conn.commit()
    conn.close()

    time_str = time.strftime("%H:%M:%S")
    # عرض اسم أطول للمستخدمين الـ VIP لتمييزهم
    user_disp = email if "gmail" in email else email.split('@')[0][-8:]

    log_line = f"{time_str:<10} | {user_disp:<20} | {model_key:<10} | {status:<6} | {int(lat):<6} ms | {prio}\n"

    with file_lock:
        with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
            f.flush()
            os.fsync(f.fileno())
        # طباعة الـ VIP في الكونسول لتمييزهم
        if "gmail" in email:
            print(f"🌟 VIP ACT: {log_line.strip()}")

class SmartTester:
    def __init__(self, user_idx, existing_data=None, is_vip=False):
        self.session = requests.Session()
        self.is_vip = is_vip

        if existing_data:
            self.email = existing_data["email"]
            self.password = existing_data["password"]
            self.is_new = False
        else:
            self.email = f"user_{user_idx}_{int(time.time())}@stress.com"
            self.password = "TestPass123!"
            self.is_new = True

        if self.is_vip:
            print(f"💎 Initializing VIP Tester: {self.email}")
        elif self.is_new:
            print(f"🔸 Creating NEW user: {self.email}")
        else:
            print(f"🔹 Loaded existing user: {self.email}")

    def ensure_access(self):
        try:
            # 1. Login or Register
            login_res = self.session.post(f"{BASE_URL}/auth/login", json={"email": self.email, "password": self.password})

            if login_res.status_code != 200:
                reg_res = self.session.post(f"{BASE_URL}/auth/register", json={"email": self.email, "password": self.password})
                if reg_res.status_code != 200:
                    # قد يكون المستخدم موجوداً لكن كلمة المرور مختلفة، نحاول الدخول بكلمة مرور VIP
                    if self.is_vip:
                        print(f"⚠️ VIP Login Retry for {self.email}...")
                    else:
                        return False 
                self.session.post(f"{BASE_URL}/auth/login", json={"email": self.email, "password": self.password})

            # 2. Subscription Logic
            if self.is_vip:
                # --- VIP AUTO UPGRADE ---
                # نقوم بترقية الـ VIP لأعلى باقة (Nexus Global) لضمان التجربة الكاملة
                # نفترض أن هناك Endpoint للاشتراك كما في الكود السابق
                sub_res = self.session.post(f"{BASE_URL}/api/subscribe", json={"plan_id": "nexus_global"}) # or proper plan key
                if sub_res.status_code == 200:
                    print(f"✅ VIP UPGRADE SUCCESS: {self.email} is now on Nexus Global")
                else:
                    print(f"⚠️ VIP Upgrade Warning: {sub_res.text}")
            else:
                # المستخدم العادي يشترك في المجاني أو التجريبي
                self.session.post(f"{BASE_URL}/api/subscribe", json={"plan_id": "free_tier"})

            save_user_to_file({"email": self.email, "password": self.password})
            return True
        except Exception as e:
            print(f"Auth Error for {self.email}: {e}")
            return False

    def run_stress_loop(self):
        consecutive_blocks = 0

        # الـ VIP يقومون بطلبات أكثر قليلاً للتأكد من الاستقرار
        limit = REQUESTS_PER_USER + 50 if self.is_vip else REQUESTS_PER_USER

        for i in range(limit):
            target = random.choice(AVAILABLE_MODELS)
            model_key = target["key"]
            model_id = target["id"]

            is_complex = random.random() < 0.3
            p_type = "Complex" if is_complex else "Simple"
            prompt = random.choice(PROMPTS[p_type])

            # إضافة تمييز في البرومبت للـ VIP
            if self.is_vip: prompt = f"[VIP CHECK] {prompt}"

            start = time.time()
            try:
                res = self.session.post(f"{BASE_URL}/api/chat", json={"model_id": model_id, "message": prompt})
                latency = (time.time() - start) * 1000

                status = res.status_code
                p_tok = 0
                o_tok = 0
                prio = "Unknown"

                if status == 200:
                    data = res.json()
                    # التعامل مع رد الـ Streaming إذا حدث، أو JSON العادي
                    # هنا نفترض JSON للتبسيط في الاختبار، أو نقرأ الهيدرز
                    prio = "Success"
                    consecutive_blocks = 0
                elif status == 429:
                    prio = "BLOCKED"
                    consecutive_blocks += 1

                log_result(self.email, model_key, p_type, status, latency, p_tok, o_tok, prio)

                if consecutive_blocks >= 15 and not self.is_vip:
                    # الـ VIP لا يتوقفون بسهولة
                    print(f"🛑 {self.email}: Limit exhausted. Stopping.")
                    break

                # فاصل زمني بسيط جداً
                time.sleep(random.uniform(0.1, 0.5))

            except Exception as e:
                log_result(self.email, model_key, "ERROR", 500, 0, 0, 0, str(e))

def analyze_and_report():
    print("\n💾 Generating Final Analysis Report...")
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("SELECT status FROM test_logs")
        all_statuses = [r[0] for r in c.fetchall()]
        if not all_statuses:
            print("⚠️ No data to analyze.")
            return

        total = len(all_statuses)
        success = all_statuses.count(200)
        blocked = all_statuses.count(429)

        model_stats = {}
        for m in AVAILABLE_MODELS:
            key = m["key"]
            c.execute("SELECT COUNT(*) FROM test_logs WHERE model_key=? AND status=200", (key,))
            s_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM test_logs WHERE model_key=? AND status=429", (key,))
            b_count = c.fetchone()[0]
            model_stats[key] = {"success": s_count, "blocked": b_count, "limit": m["limit"]}

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("========================================================\n")
            f.write("         NEXUS FINAL REPORT (VIP INCLUDED)              \n")
            f.write("========================================================\n\n")
            f.write("1. SUMMARY\n")
            f.write(f"• Total Requests:  {total}\n")
            f.write(f"• Successful:      {success}\n")
            f.write(f"• Blocked (Quota): {blocked}\n\n")
            f.write("2. MODEL BREAKDOWN\n")
            f.write(f"{'MODEL':<10} | {'LIMIT':<6} | {'SUCCESS':<8} | {'BLOCKED':<8}\n")
            for key, stats in model_stats.items():
                f.write(f"{key.upper():<10} | {stats['limit']:<6} | {stats['success']:<8} | {stats['blocked']:<8}\n")

            # قسم خاص للـ VIP
            f.write("\n3. VIP PERFORMANCE\n")
            for vip in VIP_ACCOUNTS:
                email = vip["email"]
                c.execute("SELECT COUNT(*), AVG(latency_ms) FROM test_logs WHERE user_email=? AND status=200", (email,))
                res = c.fetchone()
                reqs = res[0]
                lat = res[1] if res[1] else 0
                f.write(f"• {email}: {reqs} Requests, Avg Latency: {int(lat)}ms\n")

        print(f"✅ Final Report Saved: {REPORT_FILE}")
        conn.close()
    except Exception as e:
        print(f"❌ Error generating report: {e}")

def main():
    setup_files()

    stored_users = load_stored_users()
    print(f"📂 Found {len(stored_users)} saved users.")

    testers = []

    # 1. تجهيز الـ VIP أولاً
    print("💎 Preparing VIP Users...")
    for vip_data in VIP_ACCOUNTS:
        t = SmartTester(0, existing_data=vip_data, is_vip=True)
        if t.ensure_access():
            testers.append(t)

    # 2. تجهيز المستخدمين الوهميين (100 مستخدم)
    print(f"🚀 Preparing {CONCURRENT_USERS} Standard Users...")
    for i in range(CONCURRENT_USERS):
        # البحث عن بيانات محفوظة إن وجدت
        existing = None
        # نحاول عدم استخدام بيانات الـ VIP للمستخدمين العاديين بالخطأ
        if i < len(stored_users):
            u = stored_users[i]
            if u["email"] not in [v["email"] for v in VIP_ACCOUNTS]:
                existing = u

        t = SmartTester(i, existing_data=existing, is_vip=False)
        if t.ensure_access():
            testers.append(t)

    print(f"🔥 STARTING STRESS TEST WITH {len(testers)} ACTIVE USERS...")

    # تشغيل الجميع معاً باستخدام ThreadPool
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(testers)) as executor:
        futures = [executor.submit(t.run_stress_loop) for t in testers]
        concurrent.futures.wait(futures)

    analyze_and_report()

if __name__ == "__main__":
    main()