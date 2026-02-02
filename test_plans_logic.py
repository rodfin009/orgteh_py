import time
import requests
import concurrent.futures
import pandas as pd
from datetime import datetime
import sys
import json

# --- استيراد دوال قاعدة البيانات المباشرة ---
# يجب أن تكون ملفات database.py و services في نفس المجلد
from database import create_user_record, update_user_usage_struct, redis
from services.subscriptions import perform_upgrade

# --- إعدادات الاختبار ---
BASE_URL = "http://localhost:8000"

# قائمة السيناريوهات (كل سيناريو يمثل خطة وموديل محدد)
SCENARIOS = [
    {
        "email": "audit_free@nexus.com", 
        "plan": "Free Tier", 
        "target_model": "meta/llama-3.2-3b-instruct", 
        "limit": 10, 
        "desc": "Free Tier Compliance"
    },
    {
        "email": "audit_bundle@nexus.com", 
        "plan": "Chat Agents", 
        "target_model": "google/gemma-3n-e4b-it", 
        "limit": 270, 
        "desc": "Chat Agents Bundle"
    },
    {
        "email": "audit_global@nexus.com", 
        "plan": "Nexus Global", 
        "target_model": "deepseek-ai/deepseek-v3.2", 
        "limit": 150, 
        "desc": "Nexus Global Plan"
    },
    {
        "email": "audit_dp@nexus.com", 
        "plan": "DeepSeek V3", 
        "target_model": "deepseek-ai/deepseek-v3.2", 
        "limit": 300, 
        "desc": "Single Plan: DeepSeek"
    },
    {
        "email": "audit_kimi@nexus.com", 
        "plan": "Kimi k2", 
        "target_model": "moonshotai/kimi-k2-thinking", 
        "limit": 200, 
        "desc": "Single Plan: Kimi"
    },
    {
        "email": "audit_mistral@nexus.com", 
        "plan": "Mistral Large", 
        "target_model": "mistralai/mistral-large-3-675b-instruct-2512", 
        "limit": 100, 
        "desc": "Single Plan: Mistral"
    },
    {
        "email": "audit_llama@nexus.com", 
        "plan": "Llama 3.2", 
        "target_model": "meta/llama-3.2-3b-instruct", 
        "limit": 400, 
        "desc": "Single Plan: Llama"
    },
    {
        "email": "audit_gemma@nexus.com", 
        "plan": "Gemma 3", 
        "target_model": "google/gemma-3n-e4b-it", 
        "limit": 500, 
        "desc": "Single Plan: Gemma"
    }
]

# وضع الاختبار السريع: يقوم بملء العدادات في قاعدة البيانات
# بدلاً من إرسال مئات الطلبات الحقيقية لتوفير الوقت والمال
FAST_MODE = True 

class PlanAuditor:
    def __init__(self, scenario):
        self.scenario = scenario
        self.email = scenario["email"]
        # إنشاء مفتاح API ثابت لهذا المستخدم
        self.api_key = f"nx-audit-{scenario['email'].split('@')[0]}" 
        self.plan = scenario["plan"]
        self.model = scenario["target_model"]
        self.limit = scenario["limit"]
        self.results = []

    def setup_user_backend(self):
        """تجهيز المستخدم في قاعدة البيانات وحقن القيم الأولية"""
        if not redis:
            print("❌ REDIS NOT CONNECTED")
            return

        # 1. تنظيف البيانات السابقة لضمان نظافة الاختبار
        redis.delete(f"user:{self.email}")
        redis.delete(f"api_key:{self.api_key}")

        # 2. إنشاء المستخدم في قاعدة البيانات
        # نمرر الـ API Key مباشرة
        create_user_record(self.email, "hashed_password_dummy", self.api_key)

        # 3. ترقية خطة المستخدم
        if self.plan != "Free Tier":
            perform_upgrade(self.email, self.plan, "monthly")

        # 4. (هام جداً) إعداد العدادات للاختبار السريع
        if FAST_MODE and self.limit > 5:
            # نبدأ العد من (الحد الأقصى - 3)
            # مثال: إذا الحد 100، نجعل المستخدم قد استهلك 97
            start_count = self.limit - 3
            print(f"   [SETUP] {self.email}: Pre-filling usage to {start_count}/{self.limit}")

            # تحديد المفتاح الداخلي للموديل في قاعدة البيانات
            internal_key = "unknown"
            if "deepseek" in self.model: internal_key = "deepseek"
            elif "mistral" in self.model: internal_key = "mistral"
            elif "kimi" in self.model: internal_key = "kimi"
            elif "llama" in self.model: internal_key = "llama"
            elif "gemma" in self.model: internal_key = "gemma"

            # حقن بيانات الاستهلاك
            fake_usage = {
                "date": str(datetime.utcnow().date()),
                internal_key: start_count,
                # --- نقطة حاسمة ---
                # نقوم بملء الرصيد الإضافي (unified_extra) بقيمة ضخمة
                # والسبب: نريد اختبار توقف الباقة عند "الحد اليومي" بالضبط
                # إذا لم نقم بهذا، سيتحول النظام للخصم من الرصيد الإضافي ولن يعطي خطأ 429
                "unified_extra": 99999, 
                "total_requests": start_count
            }
            update_user_usage_struct(self.email, fake_usage)
            self.start_loop_from = start_count
        else:
            self.start_loop_from = 0

    def run_audit(self):
        print(f"🔎 AUDIT START: {self.scenario['desc']}")
        self.setup_user_backend()

        # سنرسل طلبات تكفي للوصول للحد + محاولتين إضافيتين للتأكد من الحظر
        requests_to_send = (self.limit - self.start_loop_from) + 2

        # استخدام هيدر المصادقة الرسمي
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        for i in range(requests_to_send):
            current_req_num = self.start_loop_from + i + 1
            is_over_limit = current_req_num > self.limit

            try:
                # إرسال الطلب إلى نقطة النهاية الحقيقية (v1/chat/completions)
                res = requests.post(
                    f"{BASE_URL}/v1/chat/completions", 
                    json={
                        "model": self.model, 
                        "messages": [{"role": "user", "content": "test limit enforcement"}],
                        "max_tokens": 5 # طلب صغير جداً
                    },
                    headers=headers
                )

                status = res.status_code

                # تحليل النتيجة
                result_status = "PASS"

                # الحالة 1: لم نتجاوز الحد، لكن تم حظرنا (خطأ)
                if not is_over_limit and status != 200:
                    result_status = f"FAIL (Blocked Early - Got {status})"

                # الحالة 2: تجاوزنا الحد، لكن لم يتم حظرنا (تسريب خطير)
                elif is_over_limit and status != 429:
                    result_status = f"FAIL (Leaked Limit - Got {status})"

                # الحالة 3: تجاوزنا الحد، وتم حظرنا (نجاح)
                elif is_over_limit and status == 429:
                    result_status = "PASS (Blocked Correctly)"

                log_entry = {
                    "User": self.email,
                    "Plan": self.plan,
                    "Req_Num": current_req_num,
                    "Limit": self.limit,
                    "Expected_Status": "429" if is_over_limit else "200",
                    "Actual_Status": status,
                    "Result": result_status
                }
                self.results.append(log_entry)

                # طباعة الأخطاء فوراً
                if "FAIL" in result_status:
                    print(f"   ❌ {self.email} [Req {current_req_num}] -> {result_status}")

                # تأخير بسيط جداً لمنع مشاكل الشبكة المحلية
                time.sleep(0.05)

            except Exception as e:
                print(f"   ⚠️ Connection Error {self.email}: {e}")

        return self.results

def generate_report(all_results):
    df = pd.DataFrame(all_results)

    print("\n" + "="*50)
    print("       📊 FINAL API COMPLIANCE REPORT")
    print("="*50)

    # ملخص لكل خطة
    if not df.empty:
        summary = df.groupby(["Plan"]).apply(
            lambda x: "✅ PASS" if all("PASS" in r for r in x["Result"]) else "❌ FAIL"
        )
        print(summary)

        # عرض الأخطاء إن وجدت
        errors = df[df["Result"].str.contains("FAIL")]
        if not errors.empty:
            print("\n⚠️ DETAILED FAILURE LOG:")
            print(errors[["User", "Req_Num", "Limit", "Actual_Status", "Result"]].to_string(index=False))
        else:
            print("\n✅ SUCCESS: ALL LIMITS ENFORCED PERFECTLY.")

        # حفظ التقرير
        df.to_csv("final_audit_report.csv", index=False)
        print(f"\n📄 Report saved to 'final_audit_report.csv'")
    else:
        print("⚠️ No results generated.")

def main():
    print("🚀 STARTING API LIMIT AUDIT (Target: /v1/chat/completions)...")

    all_data = []

    # تشغيل الاختبارات بالتوازي
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(PlanAuditor(sc).run_audit) for sc in SCENARIOS]

        for f in concurrent.futures.as_completed(futures):
            all_data.extend(f.result())

    generate_report(all_data)

if __name__ == "__main__":
    main()