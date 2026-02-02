import time
import requests
import concurrent.futures
import json
import os
import random
import threading
import csv
from datetime import datetime

# --- IMPORT SYSTEM MODULES ---
# تأكد من أن هذه الملفات في نفس المجلد أو المسار الصحيح
from database import (
    create_user_record, 
    get_user_by_email, 
    update_user_usage_struct, 
    redis
)
from services.subscriptions import perform_upgrade, PLAN_DETAILS
from services.limits import get_limits_for_new_subscription

# --- CONFIGURATION ---
BASE_URL = "http://localhost:8000/v1/chat/completions" # نستخدم نقطة النهاية المعيارية
REPORT_FILE = "nexus_audit_live.csv"

# المستخدمين الخاصين (يتم جلبهم من Redis)
VIP_EMAILS = ["owidiwo@gmail.com", "rodfin0202@gmail.com"]
SIMULATED_USERS_COUNT = 50 

# تعريف الخطط والفترات لتوزيعها
PLANS = list(PLAN_DETAILS.keys()) # ['Free Tier', 'Chat Agents', 'Nexus Global', 'DeepSeek V3', ...]
PERIODS = ["weekly", "monthly", "yearly"]

# الموديلات المستهدفة لكل خطة (لضمان اختبار الخطة الصحيحة)
# إذا كانت الخطة تدعم كل شيء، نختار موديل عشوائي أو ثقيل
PLAN_TARGET_MODELS = {
    "Free Tier": "meta/llama-3.2-3b-instruct",
    "Chat Agents": "google/gemma-3n-e4b-it", # موديل داخل الباقة
    "Nexus Global": "deepseek-ai/deepseek-v3.2",
    "DeepSeek V3": "deepseek-ai/deepseek-v3.2",
    "Kimi k2": "moonshotai/kimi-k2-thinking",
    "Mistral Large": "mistralai/mistral-large-3-675b-instruct-2512",
    "Gemma 3": "google/gemma-3n-e4b-it",
    "Llama 3.2": "meta/llama-3.2-3b-instruct"
}

# قفل للكتابة في الملف
csv_lock = threading.Lock()

# --- HELPER FUNCTIONS ---

def setup_csv():
    """تهيئة ملف التقرير برؤوس الأعمدة"""
    headers = [
        "Timestamp", "User_Email", "User_Type", "Plan", "Period", 
        "Target_Model", "Request_Num", "Plan_Limit", 
        "Status_Code", "Latency_ms", "Result_Desc"
    ]
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

def log_to_csv(data):
    """كتابة سطر في التقرير بشكل فوري"""
    with csv_lock:
        with open(REPORT_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(data)
            f.flush() # إجبار الكتابة على القرص فوراً

def get_target_model_for_plan(plan_name):
    return PLAN_TARGET_MODELS.get(plan_name, "deepseek-ai/deepseek-v3.2")

# --- USER CLASS ---

class AuditUser:
    def __init__(self, email, is_vip=False, plan_idx=0):
        self.email = email
        self.is_vip = is_vip
        self.api_key = None

        # توزيع الخطط بشكل دوري
        self.plan_name = PLANS[plan_idx % len(PLANS)]
        self.period = PERIODS[plan_idx % len(PERIODS)]
        self.target_model = get_target_model_for_plan(self.plan_name)

        # تحديد الحد الخاص بالخطة والموديل
        limits_dict = get_limits_for_new_subscription(PLAN_DETAILS[self.plan_name]["key"], self.period)

        # استخراج الحد الخاص بالموديل المستهدف
        # نحتاج معرفة الـ key الداخلي للموديل (مثلاً deepseek-ai/deepseek-v3.2 -> deepseek)
        short_key = "unknown"
        if "deepseek" in self.target_model: short_key = "deepseek"
        elif "mistral" in self.target_model: short_key = "mistral"
        elif "kimi" in self.target_model: short_key = "kimi"
        elif "llama" in self.target_model: short_key = "llama"
        elif "gemma" in self.target_model: short_key = "gemma"

        self.daily_limit = limits_dict.get(short_key, 0)
        self.short_key = short_key

    def prepare_backend(self):
        """تجهيز المستخدم في قاعدة البيانات وحقن القيم"""
        if not redis:
            print(f"❌ Redis Error for {self.email}")
            return False

        # 1. جلب أو إنشاء المستخدم
        if self.is_vip:
            user_data = get_user_by_email(self.email)
            if user_data:
                self.api_key = user_data.get("api_key")
                print(f"💎 VIP Loaded: {self.email} | Plan: {self.plan_name}")
            else:
                print(f"⚠️ VIP Not Found in DB: {self.email}")
                return False
        else:
            # إنشاء مستخدم جديد وحقنه مباشرة
            self.api_key = f"nx-stress-{random.randint(10000,99999)}"
            # حذف القديم إن وجد لضمان نظافة الاختبار
            redis.delete(f"user:{self.email}")
            redis.delete(f"api_key:{self.api_key}")
            create_user_record(self.email, "pass123", self.api_key)

        # 2. تفعيل الخطة المحددة
        perform_upgrade(self.email, self.plan_name, self.period)

        # 3. تعديل الاستهلاك (Smart Fill)
        # الهدف: نريد اختبار الـ 5 طلبات الأخيرة قبل الحظر
        # مثال: الحد 100 -> نجعل الاستهلاك الحالي 95
        # مثال: الحد 0 -> نتركه 0 (سيتم حظره فوراً)

        start_usage = max(0, self.daily_limit - 5) if self.daily_limit > 5 else 0

        # تجهيز هيكل usage
        fake_usage = {
            "date": str(datetime.utcnow().date()),
            self.short_key: start_usage,
            "total_requests": start_usage,
            # --- تعطيل الرصيد الإضافي تماماً ---
            # نضع الاستهلاك الإضافي بقيمة ضخمة جداً ليتم تجاوزه دائماً
            "unified_extra": 999999, 
            "total_tokens": start_usage * 100,
            "latency_sum": 0, "errors": 0, "internal_ops": 0
        }

        # دمج الأصفار لباقي الموديلات
        for k in ["deepseek", "kimi", "mistral", "llama", "gemma"]:
            if k not in fake_usage: fake_usage[k] = 0

        update_user_usage_struct(self.email, fake_usage)

        self.current_req_counter = start_usage
        return True

    def run_stress_loop(self):
        if not self.api_key: return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # عدد المحاولات: المتبقي من الحد + 3 محاولات إضافية للتأكد من الحظر
        # مثلاً: الحد 100، بدأنا من 95. سنحاول 5 (للوصول لـ 100) + 3 (ليتم حظرنا) = 8 محاولات
        remaining = max(0, self.daily_limit - self.current_req_counter)
        loops = remaining + 3 

        # إذا كان الحد 0 (غير مسموح)، نحاول 3 مرات فقط للتأكد من الحظر
        if self.daily_limit == 0: loops = 3

        for i in range(loops):
            self.current_req_counter += 1
            expected_block = self.current_req_counter > self.daily_limit

            start_time = time.time()
            status_code = 0
            try:
                # إرسال طلب حقيقي (صغير الحجم لسرعة الاختبار)
                payload = {
                    "model": self.target_model,
                    "messages": [{"role": "user", "content": "Just saying hello."}],
                    "max_tokens": 5
                }

                resp = requests.post(BASE_URL, json=payload, headers=headers, timeout=15)
                status_code = resp.status_code

            except Exception as e:
                status_code = 500

            latency = int((time.time() - start_time) * 1000)

            # تحليل النتيجة
            result_desc = "Unknown"
            if expected_block:
                if status_code == 429: result_desc = "✅ PASS (Blocked Correctly)"
                elif status_code == 200: result_desc = "❌ FAIL (Leakage detected!)"
                else: result_desc = f"⚠️ Unexpected ({status_code})"
            else:
                if status_code == 200: result_desc = "✅ PASS (Allowed)"
                elif status_code == 429: result_desc = "❌ FAIL (Blocked Early!)"
                else: result_desc = f"⚠️ Error ({status_code})"

            # تسجيل حي
            log_data = [
                datetime.utcnow().strftime("%H:%M:%S"),
                self.email,
                "VIP" if self.is_vip else "Sim",
                self.plan_name,
                self.period,
                self.target_model,
                self.current_req_counter,
                self.daily_limit,
                status_code,
                latency,
                result_desc
            ]
            log_to_csv(log_data)

            # طباعة مختصرة للكونسول
            vip_tag = "💎" if self.is_vip else "👤"
            print(f"{vip_tag} {self.email[:15]}.. | Req: {self.current_req_counter}/{self.daily_limit} | {status_code} | {latency}ms | {result_desc}")

            # توقف إذا تم الحظر بشكل صحيح لتوفير الموارد
            if expected_block and status_code == 429:
                break

            time.sleep(0.1) # فاصل زمني بسيط

# --- MAIN EXECUTION ---

def main():
    print("🚀 INITIALIZING NEXUS AUDIT V2...")

    if not redis:
        print("❌ CRITICAL: Redis is not connected. Aborting.")
        return

    setup_csv()

    testers = []

    # 1. إعداد مستخدمي VIP
    for i, email in enumerate(VIP_EMAILS):
        tester = AuditUser(email, is_vip=True, plan_idx=i) # تنويع الخطط بينهم
        if tester.prepare_backend():
            testers.append(tester)

    # 2. إعداد المستخدمين الوهميين (50 مستخدم)
    for i in range(SIMULATED_USERS_COUNT):
        email = f"audit_user_{i+1}@simulated.com"
        # نبدأ الـ plan_idx من 2 لأن 0 و 1 أخذهم الـ VIP
        tester = AuditUser(email, is_vip=False, plan_idx=i+2)
        if tester.prepare_backend():
            testers.append(tester)

    print(f"\n🔥 STARTING STRESS TEST WITH {len(testers)} USERS")
    print(f"📄 Live Report: {REPORT_FILE}\n")
    print("-" * 60)

    # تشغيل الاختبارات بالتوازي
    # نستخدم عدد Workers معقول (مثلاً 20) حتى لا نقتل الجهاز المحلي، 
    # لكن الطلبات ستكون غير متزامنة (Async) في الباك اند.
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(t.run_stress_loop) for t in testers]
        concurrent.futures.wait(futures)

    print("\n" + "="*60)
    print("✅ AUDIT COMPLETE.")
    print(f"📂 Please check '{REPORT_FILE}' for detailed analysis.")

if __name__ == "__main__":
    main()