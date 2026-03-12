import os
import time
import asyncio
import json
import httpx
from itertools import cycle
from datetime import datetime

# استيراد تتبع المقاييس
from database import track_request_metrics, update_global_stats

# --- 1. CONFIGURATION & KEY MANAGEMENT ---

_keys_str = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
API_KEYS = [k.strip() for k in _keys_str.split(",") if k.strip()]

# FIX: Backward compatibility for code_processor.py
NVIDIA_API_KEY = API_KEYS[0] if API_KEYS else None

if not API_KEYS:
    print("CRITICAL WARNING: No NVIDIA API Keys found in environment variables!")

_keys_cycle = cycle(API_KEYS) if API_KEYS else cycle(["no-key"])

# HuggingFace Space API (for qwen-mini)
HF_BASE_URL = os.environ.get("HF_SPACE_BASE_URL", "https://riy777-qw.hf.space/v1")
HF_API_KEY  = os.environ.get("HF_TOKEN", "no-key-needed")

# الثوابت
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
RATE_LIMIT_PER_KEY = 40  
TOTAL_CAPACITY_RPM = len(API_KEYS) * RATE_LIMIT_PER_KEY 

# --- 2. GLOBAL RATE TRACKER (System Load) ---

_request_timestamps = []
_tracker_lock = asyncio.Lock()

async def get_system_load():
    global _request_timestamps
    if TOTAL_CAPACITY_RPM == 0: return 1.0 
    now = time.time()
    async with _tracker_lock:
        _request_timestamps = [t for t in _request_timestamps if now - t < 60]
        current_rpm = len(_request_timestamps)
    return current_rpm / TOTAL_CAPACITY_RPM

async def acquire_provider_slot(is_priority: bool):
    global _request_timestamps
    while True:
        load = await get_system_load()
        threshold = 0.95 if is_priority else 0.80
        if load < threshold:
            async with _tracker_lock:
                _request_timestamps.append(time.time())
            return 
        await asyncio.sleep(1.0)

def get_next_api_key():
    if not API_KEYS: return None
    return next(_keys_cycle)

# --- 3. METADATA & MODELS ---

EMERGENCY_MODEL_ID = "deepseek-ai/deepseek-v3.1" 

MODELS_METADATA = [
    {
        "id": "deepseek-ai/deepseek-v3.2", 
        "short_key": "deepseek",
        "name": "DeepSeek V3.2", 
        "provider": "DeepSeek",
        "image": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/deepseek-color.png"
    },
    {
        "id": "mistralai/mistral-large-3-675b-instruct-2512", 
        "short_key": "mistral",
        "name": "Mistral Large 3", 
        "provider": "Mistral AI",
        "image": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/mistral-color.png"
    },
    {
        "id": "moonshotai/kimi-k2-thinking", 
        "short_key": "kimi",
        "name": "Kimi K2 Thinking", 
        "provider": "Moonshot",
        "image": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/kimi-color.png"
    },
    {
        "id": "meta/llama-3.2-3b-instruct", 
        "short_key": "llama",
        "name": "Llama 3.2", 
        "provider": "Meta",
        "image": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/meta-color.png"
    },
    {
        "id": "google/gemma-3n-e4b-it", 
        "short_key": "gemma",
        "name": "Gemma 3", 
        "provider": "Google",
        "image": "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/gemma-color.png"
    },
    # ── NEW MODELS ──────────────────────────────────────────────────────────────
    {
        "id": "meta/llama-3.3-70b-instruct",
        "short_key": "llama-large",
        "name": "Llama 3.3 70B",
        "provider": "Meta",
        "image": "/static/meta.webp"
    },
    {
        "id": "minimaxai/minimax-m2.1",
        "short_key": "minimax",
        "name": "MiniMax M2.1",
        "provider": "MiniMax",
        "image": "/static/minimax.webp"
    },
    {
        "id": "qwen/qwen2.5-coder-32b-instruct",
        "short_key": "qwen-coder",
        "name": "Qwen 2.5 Coder",
        "provider": "Qwen",
        "image": "/static/qwen-coder.webp"
    },
    {
        "id": "Qwen/Qwen2.5-0.5B-Instruct",
        "short_key": "qwen-mini",
        "name": "Qwen 2.5 Mini",
        "provider": "Qwen",
        "image": "/static/qwen-coder.webp",
        "base_url": "https://riy777-qw.hf.space/v1",  # HuggingFace Space
        "use_hf": True
    },
]

MODEL_MAPPING = { m["id"]: m["short_key"] for m in MODELS_METADATA }
MODEL_MAPPING[EMERGENCY_MODEL_ID] = "deepseek" 

# خريطة المعرفات لمعرفة ما إذا كان النموذج يستخدم HF Space
HF_MODEL_IDS = {m["id"] for m in MODELS_METADATA if m.get("use_hf")}

HIDDEN_MODELS = []

def estimate_tokens(text):
    return len(text) // 4 if text else 0


def get_provider_config(model_id: str) -> tuple[str, str]:
    """
    يُعيد (base_url, api_key) للنموذج المطلوب.
    يدعم كلاً من NVIDIA API و HuggingFace Spaces.
    """
    if model_id in HF_MODEL_IDS:
        return HF_BASE_URL, HF_API_KEY
    return NVIDIA_BASE_URL, get_next_api_key() or "no-key"


# --- 4. STREAMING LOGIC (With TTFT) ---

async def smart_chat_stream(original_body, user_email, is_trial=False):
    """
    إضافة معامل is_trial:
    - إذا كان True: لا يتم خصم من رصيد المستخدم ولا يُحتسب في لوحة التحكم الخاصة به
    - إذا كان False: يتم التتبع العادي
    """
    print(f"[DEBUG] smart_chat_stream called with is_trial={is_trial}, user={user_email}")

    current_body = original_body.copy()
    target_model_id = current_body.get("model")
    internal_key = MODEL_MAPPING.get(target_model_id, "unknown")

    start_time = time.time()
    ttft_latency = 0

    tokens_est = 0
    for m in current_body.get("messages", []): 
        tokens_est += estimate_tokens(m.get("content", ""))

    response_tokens = 0

    # نموذج HuggingFace Space — API مخصص (ليس OpenAI-compatible)
    # الـ Space يستخدم: POST /v1/chat/stream مع {"prompt": "...", "max_tokens": N}
    # والرد SSE بصيغة: data: {"text": "..."}
    if target_model_id in HF_MODEL_IDS:
        base_url, api_key = get_provider_config(target_model_id)

        # بناء prompt نصي من messages array
        messages = current_body.get("messages", [])
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"[System]: {content}")
            elif role == "user":
                prompt_parts.append(f"[User]: {content}")
            elif role == "assistant":
                prompt_parts.append(f"[Assistant]: {content}")
        prompt_text = "\n".join(prompt_parts)

        hf_payload = {
            "prompt": prompt_text,
            "temperature": current_body.get("temperature", 0.7),
            "max_tokens": current_body.get("max_tokens", 512)
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    f"https://riy777-qw.hf.space/v1/chat/stream",
                    headers={"Content-Type": "application/json"},
                    json=hf_payload
                ) as response:
                    if response.status_code != 200:
                        raise Exception(f"HF Space Status {response.status_code}")

                    first_chunk = True
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                # إرسال [DONE] بصيغة OpenAI
                                yield b"data: [DONE]\n\n"
                                break
                            try:
                                hf_data = json.loads(data_str)
                                text_chunk = hf_data.get("text", "")
                                if not text_chunk:
                                    continue
                                if first_chunk:
                                    ttft_latency = int((time.time() - start_time) * 1000)
                                    first_chunk = False
                                # تحويل إلى صيغة OpenAI SSE
                                openai_chunk = {
                                    "id": "chatcmpl-hf",
                                    "object": "chat.completion.chunk",
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"content": text_chunk},
                                        "finish_reason": None
                                    }]
                                }
                                response_tokens += 1
                                yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n".encode()
                            except json.JSONDecodeError:
                                pass

        except Exception as e:
            error_json = json.dumps({"error": f"Provider Error: {str(e)}"}).encode()
            yield error_json
            final_latency = int((time.time() - start_time) * 1000)
            if user_email:
                if is_trial:
                    update_global_stats(final_latency, tokens_est, model_key=internal_key, is_error=True)
                else:
                    track_request_metrics(user_email, final_latency, tokens_est, model_key=internal_key, is_error=True)
            return

        final_metric_latency = ttft_latency if ttft_latency > 0 else int((time.time() - start_time) * 1000)
        if response_tokens > 0 and user_email:
            if is_trial:
                update_global_stats(final_metric_latency, tokens_est + response_tokens, model_key=internal_key)
            else:
                track_request_metrics(user_email, final_metric_latency, tokens_est + response_tokens, model_key=internal_key)
        return

    # نماذج NVIDIA — مع دعم إعادة المحاولة والطوارئ
    max_attempts = 2

    for attempt in range(max_attempts):
        current_api_key = get_next_api_key()

        try:
            if attempt == 1 and target_model_id == "deepseek-ai/deepseek-v3.2":
                print(f"[Provider] Switching to Emergency Model: {EMERGENCY_MODEL_ID}")
                current_body["model"] = EMERGENCY_MODEL_ID
                if "chat_template_kwargs" not in current_body:
                    current_body["chat_template_kwargs"] = {"thinking": True}

            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST", 
                    f"{NVIDIA_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {current_api_key}", 
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream"
                    },
                    json=current_body
                ) as response:

                    if response.status_code == 429:
                        print(f"[Provider] Key Rate Limited (429). Rotating key...")
                        raise Exception("Upstream Rate Limit (429)")

                    if response.status_code != 200:
                        raise Exception(f"Status {response.status_code}")

                    first_chunk = True
                    async for chunk in response.aiter_bytes(): 
                        if first_chunk:
                            ttft_latency = int((time.time() - start_time) * 1000)
                            first_chunk = False

                        response_tokens += 1 
                        yield chunk

                    break 

        except Exception as e:
            if attempt < max_attempts - 1:
                continue

            error_json = json.dumps({"error": f"Provider Error: {str(e)}"}).encode()
            yield error_json

            final_latency = int((time.time() - start_time) * 1000)
            if user_email:
                if is_trial:
                    print(f"[DEBUG] Trial mode - Error tracked in global stats only")
                    update_global_stats(final_latency, tokens_est, model_key=internal_key, is_error=True, is_internal=False, is_blocked=False)
                else:
                    print(f"[DEBUG] Normal mode - Error tracked in user stats")
                    track_request_metrics(user_email, final_latency, tokens_est, model_key=internal_key, is_error=True)
            return

    final_metric_latency = ttft_latency if ttft_latency > 0 else int((time.time() - start_time) * 1000)

    if response_tokens > 0 and user_email:
        if is_trial:
            print(f"[DEBUG] Trial mode - Success tracked in global stats only (NOT user dashboard)")
            update_global_stats(final_metric_latency, tokens_est + response_tokens, model_key=internal_key, is_error=False, is_internal=False, is_blocked=False)
        else:
            print(f"[DEBUG] Normal mode - Success tracked in user stats (DEDUCTED from quota)")
            track_request_metrics(user_email, final_metric_latency, tokens_est + response_tokens, model_key=internal_key, is_error=False)
