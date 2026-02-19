import os
import asyncio
import json
import httpx
from openai import AsyncOpenAI
import sys 

# 櫨 NEW: Import configuration from Provider Service 櫨
from services.providers import NVIDIA_API_KEY, NVIDIA_BASE_URL
# 櫨 NEW: Import Tool Registry to get tool details 櫨
from tools.registry import TOOLS_DB

# Configuration
PROJECT_HOST_URL = "https://orgteh.com/v1"

# 櫨 CODE HUB SPECIFIC CONFIGURATION 櫨
CODE_HUB_MODELS_INFO = {
    "deepseek-ai/deepseek-v3.2": {
        "desc_en": "Best Architect. Excellent at complex logic & refactoring.",
        "desc_ar": "المهندس الأفضل. ممتاز في المنطق المعقد وإعادة هيكلة الكود.",
        "badge_en": "Recommended",
        "badge_ar": "موصى به"
    },
    "mistralai/mistral-large-3-675b-instruct-2512": {
        "desc_en": "High precision. Great for documentation & multi-lingual apps.",
        "desc_ar": "دقة عالية. رائع للتوثيق والتطبيقات متعددة اللغات.",
        "badge_en": "Stable",
        "badge_ar": "مستقر"
    },
    "moonshotai/kimi-k2-thinking": {
        "desc_en": "Deep reasoning. Use for debugging hard errors.",
        "desc_ar": "تفكير عميق. استخدمه لتصحيح الأخطاء الصعبة.",
        "badge_en": "Reasoning",
        "badge_ar": "تفكير"
    },
    "meta/llama-3.2-3b-instruct": {
        "desc_en": "Fast & Lightweight. Perfect for HTML/CSS snippets.",
        "desc_ar": "سريع وخفيف. مثالي لأكواد HTML/CSS القصيرة.",
        "badge_en": "Fast",
        "badge_ar": "سريع"
    },
    "google/gemma-3n-e4b-it": {
        "desc_en": "Balanced performance for general scripting.",
        "desc_ar": "أداء متوازن للنصوص البرمجية العامة.",
        "badge_en": "New",
        "badge_ar": "جديد"
    }
}

def log_debug(msg):
    """Helper function to print clear debug logs to terminal"""
    print(f"\033[93m[DEBUG LOG]:\033[0m {msg}")
    sys.stdout.flush()

async def process_code_merge_stream(
    instruction: str,
    files_data: list, 
    user_api_key: str, 
    embedding_model_id: str, 
    chat_history: list,
    target_tools: str = "" 
):
    log_debug(f"Starting Process. Target Model: {embedding_model_id}")
    log_debug(f"Target Tools IDs: {target_tools}")

    # 1. Prepare File Context
    files_context = ""
    if files_data:
        for f in files_data:
            files_context += f"\n--- ATTACHED FILE: {f['name']} ---\n{f['content']}\n"

    # 2. Prepare Tool Context
    tools_context = ""
    if target_tools:
        tools_list = target_tools.split(',')
        if tools_list:
            tools_context = "\n\n[ENABLED TOOLS DOCUMENTATION]\n"
            tools_context += "The user has explicitly enabled the following tools. You SHOULD use them if the request requires it:\n"
            for t_id in tools_list:
                tool = TOOLS_DB.get(t_id.strip())
                if tool:
                    tools_context += f"\n--- TOOL: {tool.get('name_en')} (ID: {t_id}) ---\n"
                    tools_context += f"Description: {tool.get('desc_en')}\n"
                    tools_context += "Usage Note: This API accepts FORM DATA only. Do NOT use JSON body.\n"
                    tools_context += f"Python Usage:\n{tool.get('usage_python')}\n"

    # 3. Construct System Prompt
    # [FIX]: Added 'SelectedModel' and specific instructions on how to use it in JS code.
    system_prompt = f"""
    You are 'Nexus AI', an expert Full-Stack Architect and helpful AI assistant.

    [DUAL MODE - VERY IMPORTANT]
    You operate in TWO modes based on the user's message:

    MODE A — CONVERSATION/QUESTION:
    If the user is asking a question, requesting explanation, asking for advice,
    or having a general conversation (NOT asking to build/create/generate code),
    respond naturally in plain text. Do NOT output any file separators.
    Answer conversationally in the same language the user uses.

    MODE B — CODE GENERATION:
    If the user is asking to build, create, generate, make, or modify a web app/project,
    output code files using the separator format: ### filename.ext ###

    [HOW TO DETECT THE MODE]
    - Keywords like "build", "create", "make", "generate", "اصنع", "أنشئ", "ابن", "اعمل" → MODE B
    - Keywords like "why", "how", "what", "explain", "help", "لماذا", "كيف", "ما هو", "اشرح" → MODE A
    - If ambiguous and there is existing code context → MODE A (answer about the existing code)

    [CRITICAL API CONFIGURATION — USE IN GENERATED CODE ONLY]
    BaseUrl: {PROJECT_HOST_URL}/v1
    APIKey: {user_api_key}
    SelectedModel: {embedding_model_id}

    [INSTRUCTION FOR GENERATED CODE — MODE B ONLY]
    When writing JavaScript to call the chat API:
    1. `baseURL`: MUST be exactly "{PROJECT_HOST_URL}/v1"  (this is OpenAI-compatible)
    2. `apiKey`: Use "{user_api_key}"
    3. `model`: Use the first value of "{embedding_model_id}".split(',')[0]
    4. Do NOT duplicate /v1 in the URL. The full endpoint is: {PROJECT_HOST_URL}/v1/chat/completions

    [IMPORTANT: HOW TO CALL TOOLS — MODE B ONLY]
    If the user enabled tools (like News, OCR, etc.), follow these rules in JavaScript:
    1. Endpoint: `{PROJECT_HOST_URL}/v1/tools/execute/{{TOOL_ID}}`
    2. Method: `POST`
    3. Body Format: **FormData** (Do NOT use JSON.stringify)

    --- CORRECT JS EXAMPLE FOR TOOLS ---
    const formData = new FormData();
    formData.append("text_input", "Artificial Intelligence");
    formData.append("limit", "5");
    const response = await fetch("{PROJECT_HOST_URL}/v1/tools/execute/nexus-news-general", {{
        method: "POST",
        body: formData
    }});
    ------------------------------------

    {tools_context}

    [OUTPUT RULES FOR MODE B]
    - Output strictly valid code files using separator: ### filename.ext ###
    - Design: Modern, Dark Theme (Tailwind CSS).
    - If "News" is requested, display them in a beautiful Grid Cards layout.
    - Repository description MUST include: "Built with Orgteh AI — https://orgteh.com"
    """

    # 4. Build Messages
    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat_history[-10:]:
        if msg.get('content'):
            messages.append({"role": msg.get('role', 'user'), "content": msg['content']})

    full_prompt = instruction
    if files_context: full_prompt += f"\n\n[USER ATTACHED FILES]:\n{files_context}"

    # كشف ذكي: هل الرسالة استفسار أم طلب كود؟
    code_keywords = ['build', 'create', 'make', 'generate', 'develop', 'design', 'write code',
                     'اصنع', 'أنشئ', 'ابن', 'اعمل', 'اكتب', 'طور', 'صمم', 'انشئ']
    is_code_request = any(kw in full_prompt.lower() for kw in code_keywords) or not chat_history

    if is_code_request:
        strict_instruction = (
            f"{full_prompt}\n\n"
            f"[SYSTEM REMINDER]: You are in CODE GENERATION mode. "
            f"Format all output files using exactly this separator:\n"
            f"### filename.ext ###\n(content)\n"
        )
    else:
        strict_instruction = (
            f"{full_prompt}\n\n"
            f"[SYSTEM REMINDER]: You are in CONVERSATION mode. "
            f"Answer the user's question naturally. Do NOT output file separators unless explicitly asked."
        )
    messages.append({"role": "user", "content": strict_instruction})

    log_debug(f"Messages prepared. Count: {len(messages)}")

    # 5. Call API
    http_client = httpx.AsyncClient(timeout=120.0)
    client = AsyncOpenAI(
        base_url=NVIDIA_BASE_URL, 
        api_key=NVIDIA_API_KEY,
        http_client=http_client
    )

    try:
        log_debug(">>> ATTEMPT 1: Initiating DeepSeek (Primary) with STRICT 3s TIMEOUT...")

        async def connect_and_get_first_chunk():
            log_debug("   -> Sending API Request...")
            stream = await client.chat.completions.create(
                model="deepseek-ai/deepseek-v3.1-terminus", 
                messages=messages,
                temperature=0.1, 
                top_p=0.5,
                max_tokens=8192,
                stream=True,
                extra_body={"chat_template_kwargs": {"thinking": True}}
            )
            log_debug("   -> Request Sent. Waiting for first byte...")
            iterator = stream.__aiter__()
            first_chunk = await iterator.__anext__()
            return iterator, first_chunk

        try:
            chunk_iterator, first_chunk = await asyncio.wait_for(connect_and_get_first_chunk(), timeout=3.0)
            log_debug("!!! CONNECTION ESTABLISHED & FIRST CHUNK RECEIVED !!!")

        except asyncio.TimeoutError:
            log_debug("❌ TIMEOUT: Connection or First Byte took > 3s.")
            raise Exception("Timeout: DeepSeek Primary is unresponsive")

        except Exception as e:
            log_debug(f"❌ ERROR during connection: {str(e)}")
            raise e

        # --- PROCESSING THE STREAM (If successful) ---
        delta = first_chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None) or \
                    (delta.model_extra and delta.model_extra.get("reasoning_content"))

        if reasoning: yield {"type": "thinking", "content": reasoning}
        elif delta.content: yield {"type": "code", "content": delta.content}

        async for chunk in chunk_iterator:
            if not getattr(chunk, "choices", None): continue
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None) or \
                        (delta.model_extra and delta.model_extra.get("reasoning_content"))

            if reasoning:
                yield {"type": "thinking", "content": reasoning}

            if delta.content:
                yield {"type": "code", "content": delta.content}

        log_debug("DeepSeek Primary Stream Completed Successfully ✅")

    except Exception as e:
        log_debug(f"⚠️ FALLBACK TRIGGERED! Reason: {str(e)}")
        log_debug(">>> ATTEMPT 2: Switching to DeepSeek v3.1 (Standard Fallback)...")

        try:
            backup_completion = await client.chat.completions.create(
                model="deepseek-ai/deepseek-v3.1", 
                messages=messages,
                temperature=0.2,
                top_p=0.7,
                max_tokens=16384,
                stream=True,
                extra_body={"chat_template_kwargs": {"thinking": True}} 
            )

            log_debug("DeepSeek v3.2 Fallback Connection Established. Streaming...")

            async for chunk in backup_completion:
                if not getattr(chunk, "choices", None): continue
                delta = chunk.choices[0].delta

                reasoning = getattr(delta, "reasoning_content", None) or \
                            (delta.model_extra and delta.model_extra.get("reasoning_content"))

                if reasoning:
                     yield {"type": "thinking", "content": reasoning}

                if delta.content:
                    yield {"type": "code", "content": delta.content}

            log_debug("DeepSeek v3.2 Fallback Stream Completed ✅")

        except Exception as backup_error:
             err_msg = f"FATAL ERROR: Primary: {str(e)}, Backup: {str(backup_error)}"
             log_debug(err_msg)
             yield {"type": "error", "content": err_msg}

    finally:
        await http_client.aclose()
        log_debug("Process Finished. Client Closed.")