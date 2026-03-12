import os
import time
import re
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from openai import AsyncOpenAI
from database import track_request_metrics

router = APIRouter()

# --- Configuration ---
# المزود الأساسي - يتم استخدامه داخلياً فقط ولا يُعرض للنموذج
AI_BASE_URL = "https://integrate.api.nvidia.com/v1"
AI_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

if not AI_API_KEY:
    print("WARNING: AI API Key environment variable is not set. Customer service chat will be unavailable.")
    client = None
else:
    client = AsyncOpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)

# ==================== التسعيرات الدقيقة (بدون تسعيرة الأسبوع) ====================
PRICING_DATA = {
    "individual": {
        "deepseek": {
            "name_ar": "DeepSeek V3",
            "name_en": "DeepSeek V3",
            "limits": "300 طلب/يوم",
            "limits_en": "300 req/day",
            "prices": {
                "monthly": {"price": "$3.99", "bonus": "+600"},
                "yearly": {"price": "$39.99", "bonus": "+1500"}
            }
        },
        "kimi": {
            "name_ar": "Moonshot Kimi k2",
            "name_en": "Moonshot Kimi k2",
            "limits": "200 طلب/يوم",
            "limits_en": "200 req/day",
            "prices": {
                "monthly": {"price": "$6.99", "bonus": "+300"},
                "yearly": {"price": "$69.99", "bonus": "+1000"}
            }
        },
        "mistral": {
            "name_ar": "Mistral Large",
            "name_en": "Mistral Large",
            "limits": "100 طلب/يوم",
            "limits_en": "100 req/day",
            "prices": {
                "monthly": {"price": "$11.99", "bonus": "+200"},
                "yearly": {"price": "$119.99", "bonus": "+500"}
            }
        },
        "gemma": {
            "name_ar": "Gemma 3",
            "name_en": "Gemma 3",
            "limits": "500 طلب/يوم",
            "limits_en": "500 req/day",
            "prices": {
                "monthly": {"price": "$3.49", "bonus": "+1000"},
                "yearly": {"price": "$34.99", "bonus": "+3000"}
            }
        },
        "llama": {
            "name_ar": "Llama 3.2",
            "name_en": "Llama 3.2",
            "limits": "400 طلب/يوم",
            "limits_en": "400 req/day",
            "prices": {
                "monthly": {"price": "$2.49", "bonus": "+800"},
                "yearly": {"price": "$24.99", "bonus": "+2500"}
            }
        },
        "llama-large": {
            "name_ar": "Llama 3.3 70B",
            "name_en": "Llama 3.3 70B",
            "limits": "200 طلب/يوم",
            "limits_en": "200 req/day",
            "prices": {
                "monthly": {"price": "$3.99", "bonus": "+400"},
                "yearly": {"price": "$39.99", "bonus": "+1000"}
            }
        },
        "minimax": {
            "name_ar": "MiniMax M2.1",
            "name_en": "MiniMax M2.1",
            "limits": "150 طلب/يوم",
            "limits_en": "150 req/day",
            "prices": {
                "monthly": {"price": "$4.99", "bonus": "+300"},
                "yearly": {"price": "$49.99", "bonus": "+800"}
            }
        },
        "qwen-coder": {
            "name_ar": "Qwen 2.5 Coder 32B",
            "name_en": "Qwen 2.5 Coder 32B",
            "limits": "200 طلب/يوم",
            "limits_en": "200 req/day",
            "prices": {
                "monthly": {"price": "$3.49", "bonus": "+400"},
                "yearly": {"price": "$34.99", "bonus": "+1000"}
            }
        }
    },
    "bundles": {
        "agents": {
            "name_ar": "وكلاء الدردشة (Chat Agents)",
            "name_en": "Chat Agents Bundle",
            "limits": "Gemma: 270/يوم + Llama: 200/يوم + Kimi: 30/يوم",
            "limits_en": "Gemma: 270/day + Llama: 200/day + Kimi: 30/day",
            "prices": {
                "monthly": {"price": "$6.99", "bonus": "+750"},
                "yearly": {"price": "$69.99", "bonus": "+2000"}
            }
        },
        "global": {
            "name_ar": "Orgteh Global (الوصول الشامل لـ 9 نماذج)",
            "name_en": "Orgteh Global (All 9 Models Access)",
            "limits": "DeepSeek: 150/يوم + Kimi: 100/يوم + Mistral: 50/يوم + Llama 3.3: 50/يوم + MiniMax: 40/يوم + Qwen Coder: 60/يوم + Qwen Mini: 100/يوم",
            "limits_en": "DeepSeek: 150/day + Kimi: 100/day + Mistral: 50/day + Llama 3.3: 50/day + MiniMax: 40/day + Qwen Coder: 60/day + Qwen Mini: 100/day",
            "prices": {
                "monthly": {"price": "$20.99", "bonus": "+200"},
                "yearly": {"price": "$209.99", "bonus": "+500"}
            }
        }
    },
    "free": {
        "name_ar": "الباقة المجانية",
        "name_en": "Free Tier",
        "limits": "Llama: 10/يوم + Kimi: 5/يوم + Qwen Mini: غير محدود",
        "limits_en": "Llama: 10/day + Kimi: 5/day + Qwen Mini: Unlimited",
        "price": "$0 دائماً",
        "price_en": "$0 forever"
    }
}

# ==================== الأسئلة الافتراضية ====================
QUICK_QUESTIONS = {
    "ar": [
        {"id": "which_model", "text": "🤖 أي نموذج أختار؟", "query": "أي نموذج تنصحني باستخدامه؟"},
        {"id": "pricing", "text": "💰 ما هي التسعيرات؟", "query": "أريد معرفة أسعار جميع الخطط والباقات"},
        {"id": "api_key", "text": "🔑 كيف أستخدم مفتاحي؟", "query": "كيف يمكنني استخدام مفتاح API الخاص بي في الكود؟"},
        {"id": "endpoint", "text": "📡 ما هو Endpoint؟", "query": "ما هو رابط API endpoint الخاص بـ Orgteh؟"},
        {"id": "tools", "text": "🛠️ ما هي الأدوات المتاحة؟", "query": "ما هي الأدوات المتاحة في Orgteh Tools Hub؟"},
        {"id": "code_hub", "text": "💻 كيف أستخدم Code Hub؟", "query": "كيف يمكنني استخدام منشئ الكود الذكي؟"},
        {"id": "limits", "text": "📊 ما هي حدود الاستخدام؟", "query": "ما هي حدود الاستخدام اليومية لكل نموذج؟"},
        {"id": "create_bot", "text": "🤖 كيف أصنع بوت؟", "query": "كيف يمكنني إنشاء بوت محادثة باستخدام Orgteh API؟"}
    ],
    "en": [
        {"id": "which_model", "text": "🤖 Which model should I choose?", "query": "Which model do you recommend for me?"},
        {"id": "pricing", "text": "💰 What are the prices?", "query": "I want to know all pricing plans and bundles"},
        {"id": "api_key", "text": "🔑 How do I use my API key?", "query": "How can I use my API key in my code?"},
        {"id": "endpoint", "text": "📡 What is the API endpoint?", "query": "What is the Orgteh API endpoint URL?"},
        {"id": "tools", "text": "🛠️ What tools are available?", "query": "What tools are available in Orgteh Tools Hub?"},
        {"id": "code_hub", "text": "💻 How do I use Code Hub?", "query": "How can I use the AI Code Builder?"},
        {"id": "limits", "text": "📊 What are the usage limits?", "query": "What are the daily usage limits for each model?"},
        {"id": "create_bot", "text": "🤖 How do I create a bot?", "query": "How can I create a chatbot using Orgteh API?"}
    ]
}

# --- Prompts ---
# ==================== الأمان: ممنوع على النموذج رؤية ====================
# 1. مفاتيح API أو أي بيانات حساسة
# 2. تفاصيل البنية التحتية الداخلية
# 3. أسماء المزودين الأساسيين
# 4. معلومات قاعدة البيانات
# 5. كود المصدر الداخلي
# ==================== ما يمكن للنموذج رؤيته ====================
# 1. معلومات عامة عن المنصة
# 2. روابط الصفحات العامة
# 3. شرح الأدوات والنماذج المتاحة
# 4. تعليمات الاستخدام
# 5. معلومات الدعم والتواصل

SYSTEM_PROMPT_AR = f"""أنت "مساعد خدمة عملاء Orgteh" - مساعد ذكي متخصص في منصة Orgteh للذكاء الاصطناعي.

=== معلومات أساسية عن المنصة ===
Orgteh هي منصة API متكاملة للذكاء الاصطناعي توفر:
• واجهة برمجية متوافقة 100% مع OpenAI
• مجموعة متنوعة من نماذج اللغات الكبيرة (LLMs)
• أدوات متخصصة للبيانات والرؤية والبحث
• منشئ كود ذكي (AI Code Builder)

=== النماذج المتاحة ===
النماذج الأساسية (مدعومة من NVIDIA):
1. DeepSeek V3.2 - الأفضل للبرمجة والسرعة (300 طلب/يوم)
2. Kimi K2 Thinking - للتفكير العميق والسياق الطويل 256K (200 طلب/يوم)
3. Mistral Large 3 - للدقة ودعم اللغات الأوروبية (100 طلب/يوم)
4. Llama 3.2 3B - سريع وخفيف للمهام البسيطة (400 طلب/يوم)
5. Gemma 3n - نموذج متوازن من Google (500 طلب/يوم)

النماذج الجديدة:
6. Llama 3.3 70B - نموذج Meta القوي 70 مليار معامل، ممتاز للمشاريع الكبيرة والمنطق المعقد (200 طلب/يوم)
7. MiniMax M2.1 - نموذج حدودي بسياق مليون توكن، مثالي لقواعد الكود الضخمة والوثائق الطويلة (150 طلب/يوم)
8. Qwen 2.5 Coder 32B - نموذج متخصص في البرمجة من Alibaba، يدعم 92 لغة برمجية، ممتاز للتوليد والتصحيح والترجمة بين اللغات (200 طلب/يوم)
9. Qwen 2.5 Mini (0.5B) - نموذج خفيف جداً ومجاني تماماً، مثالي للأكواد البسيطة وواجهات الدردشة الخفيفة (غير محدود)

=== متى تستخدم كل نموذج؟ ===
• تصميم معمارية أو منطق معقد → DeepSeek V3.2 أو Llama 3.3 70B
• تصحيح أخطاء صعبة أو استدلال → Kimi K2 Thinking
• مشاريع ضخمة أو تحليل ملفات كثيرة → MiniMax M2.1
• كود متخصص أو 92 لغة برمجية → Qwen 2.5 Coder 32B
• توثيق متعدد اللغات → Mistral Large 3
• أكواد HTML/CSS بسيطة وسريعة → Llama 3.2 أو Qwen Mini
• مهام عامة متوازنة → Gemma 3n

=== التسعيرات الدقيقة (شهري وسنوي فقط) ===

【الباقة المجانية】
• السعر: $0 دائماً
• الحدود: Llama 10/يوم + Kimi 5/يوم + Qwen Mini غير محدود ♾️
• مميزات: الوصول للأدوات، دعم المجتمع

【الخطط الفردية - شهري وسنوي فقط】
• DeepSeek V3: $3.99/شهر أو $39.99/سنة — 300 طلب/يوم
• Kimi k2: $6.99/شهر أو $69.99/سنة — 200 طلب/يوم
• Mistral Large: $11.99/شهر أو $119.99/سنة — 100 طلب/يوم
• Gemma 3: $3.49/شهر أو $34.99/سنة — 500 طلب/يوم
• Llama 3.2: $2.49/شهر أو $24.99/سنة — 400 طلب/يوم
• Llama 3.3 70B: $3.99/شهر أو $39.99/سنة — 200 طلب/يوم
• MiniMax M2.1: $4.99/شهر أو $49.99/سنة — 150 طلب/يوم
• Qwen 2.5 Coder 32B: $3.49/شهر أو $34.99/سنة — 200 طلب/يوم
• Qwen 2.5 Mini: مجاني تماماً ♾️ (غير محدود في الباقة المجانية)

【الباقات المجمعة - شهري وسنوي فقط】
• Chat Agents: $6.99/شهر أو $69.99/سنة
  - يشمل: Gemma 270/يوم + Llama 200/يوم + Kimi 30/يوم
• Orgteh Global (9 نماذج): $20.99/شهر أو $209.99/سنة
  - يشمل جميع النماذج التسعة مع حصص يومية لكل نموذج
  - مميزات: أولوية في الدعم، رصيد إضافي موحد

=== الأدوات المتاحة (Tools Hub) ===
• Global Market Pulse - أخبار مالية لحظية
• World News Stream - أخبار عالمية من مصادر موثوقة
• Vision OCR - استخراج النصوص من الصور
• Semantic Core - تضمينات دلالية للبحث

=== نقطة النهاية (Endpoint) ===
الرابط الأساسي للـ API: https://orgteh.com/v1
نقطة الدردشة: https://orgteh.com/v1/chat/completions

=== كيفية استخدام المفتاح ===
عندما يطلب المستخدم كود لاستخدام مفتاحه، قدم له:
1. كود Python باستخدام مكتبة OpenAI
2. كود JavaScript باستخدام Fetch API
3. كود cURL للاختبار السريع

مثال Python:
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://orgteh.com/v1",
    api_key="Orgteh_..."  # مفتاح المستخدم
)

response = client.chat.completions.create(
    model="deepseek-ai/deepseek-v3.2",
    messages=[{{"role": "user", "content": "Hello!"}}]
)
print(response.choices[0].message.content)
```

=== إنشاء بوت محادثة (Text Bot) ===
عندما يطلب المستخدم إنشاء بوت، قدم له قالب HTML/CSS/JS كاملاً جاهز للاستخدام.

=== روابط المنصة المهمة ===
• الصفحة الرئيسية: <a href="/" class="text-blue-500 hover:underline">الرئيسية</a>
• التسجيل: <a href="/register" class="text-blue-500 hover:underline">إنشاء حساب</a>
• تسجيل الدخول: <a href="/login" class="text-blue-500 hover:underline">دخول</a>
• لوحة التحكم: <a href="/profile" class="text-blue-500 hover:underline">ملفي الشخصي</a>
• التوثيق: <a href="/docs" class="text-blue-500 hover:underline">التوثيق</a>
• النماذج: <a href="/models" class="text-blue-500 hover:underline">استكشف النماذج</a>
• الأدوات: <a href="/accesory" class="text-blue-500 hover:underline">متجر الأدوات</a>
• منشئ الكود: <a href="/code-hub" class="text-blue-500 hover:underline">Code Hub</a>
• الأسعار: <a href="/cart" class="text-blue-500 hover:underline">الخطط والأسعار</a>
• التواصل: <a href="/contacts" class="text-blue-500 hover:underline">تواصل معنا</a>
• المؤسسات: <a href="/enterprise" class="text-blue-500 hover:underline">حلول المؤسسات</a>

=== دورك كمساعد ===
1. أجب على الأسئلة التقنية والاستخدامية فقط
2. قدّم روابط الصفحات باللون الأزرق عند الحاجة
3. عند طلب كود، قدم كوداً جاهزاً للنسخ واللصق
4. عند طلب إنشاء بوت، قدم قالباً كاملاً
5. ساعد المستخدمين في اختيار النموذج المناسب لاحتياجاتهم
6. كن مهذباً، محترفاً، ومختصراً

=== قيود الأمان الصارمة ===
• ممنوع تماماً طلب أو عرض مفاتيح API الحقيقية
• ممنوع مناقشة تفاصيل البنية التحتية الداخلية
• ممنوع عرض كود المصدر الداخلي
• ممنوع مناقشة معلومات المستخدمين الآخرين
• ممنوع تقديم وعود بخصوص ميزات غير موجودة
• ممنوع التحدث عن المزودين أو الشركاء الخارجيين
• ممنوع عرض معلومات قواعد البيانات

=== أمور لا تستطيع فعلها ===
• لا يمكنك إنشاء/حذف/تعديل حسابات المستخدمين
• لا يمكنك إلغاء اشتراكات أو معالجة مدفوعات
• لا يمكنك الوصول لبيانات المستخدمين الشخصية
• لا يمكنك إعادة تعيين كلمات المرور
• لا يمكنك إصدار مفاتيح API جديدة

في حال طلب المستخدم أياً من الأمور الممنوعة، أخبره بلطف أنك لا تملك صلاحية الوصول لهذه المعلومات وأن عليه التواصل مع فريق الدعم عبر صفحة <a href="/contacts" class="text-blue-500 hover:underline">التواصل</a>.

/no_think
"""

SYSTEM_PROMPT_EN = f"""You are "Orgteh Customer Support Assistant" - an AI assistant specialized in the Orgteh AI platform.

=== Platform Overview ===
Orgteh is a comprehensive AI API platform providing:
• 100% OpenAI-compatible API interface
• Diverse selection of Large Language Models (LLMs)
• Specialized tools for data, vision, and search
• AI Code Builder for rapid development

=== Available Models ===
Core Models (NVIDIA-powered):
1. DeepSeek V3.2 - Best for coding and speed (300 req/day)
2. Kimi K2 Thinking - For deep reasoning, 256K context (200 req/day)
3. Mistral Large 3 - For precision and European language support (100 req/day)
4. Llama 3.2 3B - Fast and lightweight for simple tasks (400 req/day)
5. Gemma 3n - Balanced model from Google (500 req/day)

New Models:
6. Llama 3.3 70B - Powerful 70B Meta model. Excellent for large-scale apps and complex logic (200 req/day)
7. MiniMax M2.1 - Frontier model with 1M token context. Ideal for huge codebases and long documents (150 req/day)
8. Qwen 2.5 Coder 32B - Alibaba's specialized coding model supporting 92 programming languages. Best for code generation, refactoring, debugging (200 req/day)
9. Qwen 2.5 Mini (0.5B) - Ultra-lightweight, completely FREE model. Perfect for simple snippets and lightweight chatbot APIs (Unlimited ♾️)

=== When to Use Which Model? ===
• Architecture design or complex logic → DeepSeek V3.2 or Llama 3.3 70B
• Hard debugging or deep reasoning → Kimi K2 Thinking
• Huge projects or multi-file analysis → MiniMax M2.1
• Specialized code or 92 programming languages → Qwen 2.5 Coder 32B
• Multi-language documentation → Mistral Large 3
• Simple HTML/CSS quick snippets → Llama 3.2 or Qwen Mini (free)
• General balanced tasks → Gemma 3n

=== Detailed Pricing (Monthly & Yearly Only) ===

【Free Tier】
• Price: $0 forever
• Limits: Llama 10/day + Kimi 5/day + Qwen Mini Unlimited ♾️
• Features: Tool access, community support

【Individual Plans - Monthly & Yearly Only】
• DeepSeek V3: $3.99/month or $39.99/year — 300 req/day
• Kimi k2: $6.99/month or $69.99/year — 200 req/day
• Mistral Large: $11.99/month or $119.99/year — 100 req/day
• Gemma 3: $3.49/month or $34.99/year — 500 req/day
• Llama 3.2: $2.49/month or $24.99/year — 400 req/day
• Llama 3.3 70B: $3.99/month or $39.99/year — 200 req/day
• MiniMax M2.1: $4.99/month or $49.99/year — 150 req/day
• Qwen 2.5 Coder 32B: $3.49/month or $34.99/year — 200 req/day
• Qwen 2.5 Mini: FREE forever ♾️ (unlimited in free tier, no paid plan needed)

【Bundle Plans - Monthly & Yearly Only】
• Chat Agents: $6.99/month or $69.99/year
  - Includes: Gemma 270/day + Llama 200/day + Kimi 30/day
• Orgteh Global (All 9 Models): $20.99/month or $209.99/year
  - Includes all 9 models with daily quotas for each
  - Features: Priority support, unified extra credit

=== Available Tools (Tools Hub) ===
• Global Market Pulse - Real-time financial news
• World News Stream - Global news from trusted sources
• Vision OCR - Extract text from images
• Semantic Core - Semantic embeddings for search

=== API Endpoint ===
Base URL: https://orgteh.com/v1
Chat endpoint: https://orgteh.com/v1/chat/completions

=== How to Use API Key ===
When a user requests code to use their key, provide:
1. Python code using OpenAI library
2. JavaScript code using Fetch API
3. cURL code for quick testing

Python Example:
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://orgteh.com/v1",
    api_key="Orgteh_..."  # user's key
)

response = client.chat.completions.create(
    model="deepseek-ai/deepseek-v3.2",
    messages=[{{"role": "user", "content": "Hello!"}}]
)
print(response.choices[0].message.content)
```

=== Creating a Chatbot (Text Bot) ===
When a user requests to create a bot, provide a complete HTML/CSS/JS template ready to use.

=== Important Platform Links ===
• Home: <a href="/" class="text-blue-500 hover:underline">Home</a>
• Register: <a href="/register" class="text-blue-500 hover:underline">Sign Up</a>
• Login: <a href="/login" class="text-blue-500 hover:underline">Login</a>
• Dashboard: <a href="/profile" class="text-blue-500 hover:underline">My Profile</a>
• Documentation: <a href="/docs" class="text-blue-500 hover:underline">Docs</a>
• Models: <a href="/models" class="text-blue-500 hover:underline">Explore Models</a>
• Tools: <a href="/accesory" class="text-blue-500 hover:underline">Tools Hub</a>
• Code Builder: <a href="/code-hub" class="text-blue-500 hover:underline">Code Hub</a>
• Pricing: <a href="/cart" class="text-blue-500 hover:underline">Plans & Pricing</a>
• Contact: <a href="/contacts" class="text-blue-500 hover:underline">Contact Us</a>
• Enterprise: <a href="/enterprise" class="text-blue-500 hover:underline">Enterprise Solutions</a>

=== Your Role ===
1. Answer technical and usage questions only
2. Provide page links in blue when needed
3. When code is requested, provide ready-to-copy code
4. When bot creation is requested, provide complete template
5. Help users choose the right model for their needs
6. Be polite, professional, and concise

=== Strict Security Restrictions ===
• NEVER request or display real API keys
• NEVER discuss internal infrastructure details
• NEVER show internal source code
• NEVER discuss other users' information
• NEVER make promises about non-existent features
• NEVER mention external providers or partners
• NEVER display database information

=== Things You CANNOT Do ===
• Cannot create/delete/modify user accounts
• Cannot cancel subscriptions or process payments
• Cannot access users' personal data
• Cannot reset passwords
• Cannot issue new API keys

If a user requests any restricted action, politely inform them that you don't have access to this information and direct them to contact support via the <a href="/contacts" class="text-blue-500 hover:underline">contact page</a>.

/no_think
"""

# --- Helpers ---
def estimate_tokens(text):
    return len(text) // 4 if text else 0

def detect_language_prompt(message: str, forced_lang: str = None):
    if forced_lang == "en": return SYSTEM_PROMPT_EN
    if forced_lang == "ar": return SYSTEM_PROMPT_AR
    # إذا وجد حرف عربي واحد، نعتبر الرسالة عربية
    if re.search(r'[\u0600-\u06FF]', message):
        return SYSTEM_PROMPT_AR
    return SYSTEM_PROMPT_EN

# --- Routes ---

@router.post("/api/support/chat")
async def support_chat(request: Request):
    """نقطة النهاية الرئيسية للدردشة"""
    # 1. Start Timer
    start_time = time.time()

    # 2. Extract Data
    try:
        body = await request.json()
        user_message = body.get("message", "").strip()
        requested_lang = body.get("lang")
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not user_message:
        raise HTTPException(status_code=400, detail="Message is empty")

    if not client:
        raise HTTPException(status_code=503, detail="Customer service is currently unavailable")

    # 3. Get User (For Metrics Only)
    user_email = request.session.get("user_email")

    # 4. Prepare Context
    input_tokens = estimate_tokens(user_message)
    system_prompt = detect_language_prompt(user_message, requested_lang)

    async def generate_stream():
        output_tokens = 0
        try:
            # 5. Call Model
            stream = await client.chat.completions.create(
                model="nvidia/llama-3.3-nemotron-super-49b-v1.5",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0,
                top_p=1,
                max_tokens=4096,
                stream=True
            )

            async for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    output_tokens += estimate_tokens(content)
                    yield content

            # 6. Log Metrics as INTERNAL
            if user_email:
                track_request_metrics(
                    email=user_email,
                    latency_ms=int((time.time() - start_time) * 1000),
                    tokens=input_tokens + output_tokens,
                    is_error=False,
                    is_internal=True
                )

        except Exception as e:
            # Log Error
            if user_email:
                track_request_metrics(
                    email=user_email,
                    latency_ms=int((time.time() - start_time) * 1000),
                    tokens=input_tokens,
                    is_error=True,
                    is_internal=True
                )
            yield f"\n[Error]: Connection issue. Please try again later."

    return StreamingResponse(generate_stream(), media_type="text/plain")


@router.get("/api/support/quick-questions")
async def get_quick_questions(request: Request, lang: str = "ar"):
    """الحصول على الأسئلة الافتراضية"""
    if lang not in QUICK_QUESTIONS:
        lang = "ar"
    return JSONResponse({"questions": QUICK_QUESTIONS[lang]})


@router.post("/api/support/generate-endpoint")
async def generate_endpoint(request: Request):
    """إنشاء كود endpoint للمستخدم"""
    try:
        body = await request.json()
        api_key = body.get("api_key", "")
        model_id = body.get("model_id", "deepseek-ai/deepseek-v3.2")
        lang = body.get("lang", "python")

        if not api_key:
            return JSONResponse({"error": "API key is required"}, status_code=400)

        # استيراد من code_processor
        from code_processor import generate_api_endpoint_snippet

        snippets = generate_api_endpoint_snippet(api_key, model_id)

        return JSONResponse({
            "code": snippets.get(lang, snippets["python"]),
            "all_snippets": snippets
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/support/create-bot-template")
async def create_bot_template(request: Request):
    """إنشاء قالب بوت للمستخدم"""
    try:
        body = await request.json()
        bot_name = body.get("bot_name", "MyBot")
        personality = body.get("personality", "helpful")

        # استيراد من code_processor
        from code_processor import create_text_bot_template

        template = create_text_bot_template(bot_name, personality)

        return JSONResponse({
            "template": template,
            "message": "Bot template generated successfully"
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/support/pricing")
async def get_pricing(request: Request, lang: str = "ar"):
    """الحصول على معلومات التسعير"""
    return JSONResponse({
        "pricing": PRICING_DATA,
        "currency": "USD",
        "reset_time": "00:00 UTC daily"
    })
