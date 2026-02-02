import os
import time
import asyncio
import json
import httpx
from itertools import cycle
from datetime import datetime

# استيراد تتبع المقاييس
from database import track_request_metrics

# --- 1. CONFIGURATION & KEY MANAGEMENT ---

_keys_str = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
API_KEYS = [k.strip() for k in _keys_str.split(",") if k.strip()]

# FIX: Backward compatibility for code_processor.py
NVIDIA_API_KEY = API_KEYS[0] if API_KEYS else None

if not API_KEYS:
    print("CRITICAL WARNING: No NVIDIA API Keys found in environment variables!")

_keys_cycle = cycle(API_KEYS) if API_KEYS else cycle(["no-key"])

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
    }
]

MODEL_MAPPING = { m["id"]: m["short_key"] for m in MODELS_METADATA }
MODEL_MAPPING[EMERGENCY_MODEL_ID] = "deepseek" 

HIDDEN_MODELS = []

def estimate_tokens(text):
    return len(text) // 4 if text else 0

# --- 4. STREAMING LOGIC (With TTFT) ---

async def smart_chat_stream(original_body, user_email):
    current_body = original_body.copy()
    target_model_id = current_body.get("model")
    internal_key = MODEL_MAPPING.get(target_model_id, "unknown")

    start_time = time.time()
    ttft_latency = 0 # Time To First Token

    tokens_est = 0
    for m in current_body.get("messages", []): 
        tokens_est += estimate_tokens(m.get("content", ""))

    response_tokens = 0
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
                        # TTFT Logic: Capture time of first byte
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

            # في حالة الخطأ، الزمن هو الوقت المستغرق حتى ظهور الخطأ
            final_latency = int((time.time() - start_time) * 1000)
            if user_email:
                track_request_metrics(user_email, final_latency, tokens_est, model_key=internal_key, is_error=True)
            return

    # Use TTFT for metrics if available, else total time
    final_metric_latency = ttft_latency if ttft_latency > 0 else int((time.time() - start_time) * 1000)

    if response_tokens > 0 and user_email:
        track_request_metrics(user_email, final_metric_latency, tokens_est + response_tokens, model_key=internal_key, is_error=False)