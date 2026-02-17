import os
import time
import re
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from database import track_request_metrics

router = APIRouter()

# --- Configuration ---
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

if not NVIDIA_API_KEY:
    print("WARNING: NVIDIA_API_KEY environment variable is not set. Customer service chat will be unavailable.")
    client = None
else:
    client = AsyncOpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY)

# --- Prompts ---
SYSTEM_PROMPT_AR = """/no_think
أنت هو المساعد الذكي لخدمة عملاء منصة "Nexus API".
يجب عليك الالتزام التام بالإجابة بناءً على كلام المستخدم والمعلومات المتوفرة عن المنصة فقط.
ممنوع تماماً التحدث من تلقاء نفسك أو تقديم معلومات خارج نطاق الخدمة.
أجب مباشرة بشكل احترافي، تقني، ومختصر باللغة العربية.
"""

SYSTEM_PROMPT_EN = """/no_think
You are the AI Customer Support Agent for "Nexus API".
You must strictly answer based only on the user's input and the platform's available information.
Do not hallucinate or provide information outside the scope of the service.
Answer directly, professionally, technically, and concisely in English.
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

# --- Route ---
@router.post("/api/support/chat")
async def support_chat(request: Request):
    # 1. Start Timer (Same as process_code)
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
    # نأخذ الايميل لتسجيل الاحصائيات فقط، لكن لا نتحقق من الرصيد
    user_email = request.session.get("user_email")

    # 4. Prepare Context
    input_tokens = estimate_tokens(user_message)
    system_prompt = detect_language_prompt(user_message, requested_lang)

    async def generate_stream():
        output_tokens = 0
        try:
            # 5. Call Model (Determinstic Support Model)
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

            # 6. Log Metrics as INTERNAL (Exactly like process_code)
            # This ensures it increments 'internal_ops' NOT 'deepseek'
            if user_email:
                track_request_metrics(
                    email=user_email,
                    latency_ms=int((time.time() - start_time) * 1000),
                    tokens=input_tokens + output_tokens,
                    is_error=False,
                    is_internal=True  # <--- المفتاح السحري هنا
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