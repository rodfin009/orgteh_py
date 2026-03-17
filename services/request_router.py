import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.limits import check_request_allowance, check_trial_allowance
from services.providers import (
    smart_chat_stream,
    acquire_provider_slot,
    HIDDEN_MODELS,
)
from database import get_user_by_api_key, get_redis

router = APIRouter()

# ============================================================================
# CORE ROUTING FUNCTION — تُستخدم داخلياً وبواسطة endpoints أخرى
# ============================================================================

async def handle_chat_request(email: str, payload: dict):
    """
    نقطة التحكم المركزية:
    1. تتحقق من توفر الموديل.
    2. تفحص رصيد المستخدم عبر limits.py.
    3. توجه الطلب للطابور المناسب عبر providers.py.
    """
    model_id = payload.get("model")

    # 1. فحص الموديل
    if model_id in HIDDEN_MODELS:
        return JSONResponse(
            {"error": {"message": f"Model '{model_id}' unavailable.", "code": "model_unavailable"}},
            status_code=404,
        )

    # 2. فحص الحدود والأولوية
    allowed, is_priority = await check_request_allowance(email, model_id)

    if not allowed:
        return JSONResponse(
            {"error": "Quota limit reached. Please upgrade your plan or wait until renewal."},
            status_code=429,
        )

    # 3. التنفيذ الذكي
    try:
        await acquire_provider_slot(is_priority=is_priority)
        return StreamingResponse(
            smart_chat_stream(payload, email),
            media_type="text/event-stream",
        )
    except Exception as e:
        return JSONResponse(
            {"error": "System is currently at maximum capacity. Please try again in a few seconds."},
            status_code=503,
        )

# ============================================================================
# CHAT ENDPOINTS
# ============================================================================

@router.post("/api/chat/trial")
async def trial_chat_endpoint(request: Request):
    try:
        from services.auth import get_current_user_email

        data  = await request.json()
        email = get_current_user_email(request)
        if not email:
            return JSONResponse({"error": "Unauthorized"}, 401)

        model_id = data.get("model_id")
        if model_id in HIDDEN_MODELS:
            return JSONResponse(
                {"error": {"message": "Model unavailable.", "code": "model_unavailable"}}, 404
            )

        allowed = await check_trial_allowance(email, model_id)
        if not allowed:
            return JSONResponse({"error": "Daily trial limit reached (10 msgs)."}, 429)

        payload = {
            "model":       model_id,
            "messages":    data.get("messages", []),
            "temperature": float(data.get("temperature", 0.5)),
            "top_p":       float(data.get("top_p", 0.7)),
            "max_tokens":  int(data.get("max_tokens", 1024)),
            "stream":      data.get("stream", True),
        }
        if "extra_params" in data:
            payload.update(data["extra_params"])
        if "deepseek" in payload["model"] and "chat_template_kwargs" not in payload:
            payload["chat_template_kwargs"] = {"thinking": True}

        await acquire_provider_slot(is_priority=False)
        return StreamingResponse(
            smart_chat_stream(payload, email, is_trial=True),
            media_type="text/event-stream",
        )

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/chat")
async def internal_chat_ui(request: Request):
    try:
        from services.auth import get_current_user_email

        data  = await request.json()
        email = get_current_user_email(request)
        if not email:
            return JSONResponse({"error": "Unauthorized"}, 401)

        is_trial = data.get("is_trial", False)
        if is_trial:
            model_id = data.get("model_id")
            allowed  = await check_trial_allowance(email, model_id)
            if not allowed:
                return JSONResponse({"error": "Daily trial limit reached."}, 429)
            payload = {
                "model":       model_id,
                "messages":    data.get("messages", [{"role": "user", "content": data.get("message")}]),
                "temperature": float(data.get("temperature", 0.5)),
                "stream":      data.get("stream", False),
            }
            if "deepseek" in payload["model"]:
                payload["chat_template_kwargs"] = {"thinking": True}
            await acquire_provider_slot(is_priority=False)
            return StreamingResponse(
                smart_chat_stream(payload, email, is_trial=True),
                media_type="text/event-stream",
            )

        payload = {
            "model":       data.get("model_id"),
            "messages":    [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "stream":      data.get("stream", False),
        }
        if "deepseek" in payload["model"]:
            payload["chat_template_kwargs"] = {"thinking": True}

        return await handle_chat_request(email, payload)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# تم حذف الدالة @router.options("/v1/chat/completions") بالكامل لترك معالجة الـ CORS للميدل وير العام

@router.post("/v1/chat/completions")
async def openai_compatible_proxy(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)

    api_key = auth_header.split(" ")[1]
    user    = get_user_by_api_key(api_key)
    if not user:
        return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, 400)

    return await handle_chat_request(user["email"], body)


# ============================================================================
# SUPPORT CHAT ENDPOINT
# ============================================================================

@router.post("/api/support/chat")
async def support_chat(request: Request):
    try:
        _redis = get_redis()
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
                    or getattr(request.client, "host", "unknown")
        if _redis:
            rate_key = f"support_rate:{client_ip}"
            count    = _redis.incr(rate_key)
            if count == 1:
                _redis.expire(rate_key, 60)
            if count > 10:
                return JSONResponse({"error": "Rate limit exceeded. Please wait a moment."}, status_code=429)

        body    = await request.json()
        message = body.get("message", "").strip()
        lang    = body.get("lang", "en")

        if not message:
            return JSONResponse({"error": "No message provided"}, 400)

        system_prompt = (
            "أنت مساعد خدمة عملاء ذكي لموقع Orgteh Infra. "
            "يجب أن ترد دائماً باللغة العربية فقط. "
            "موقعنا يوفر بنية تحتية موحدة للوصول إلى نماذج الذكاء الاصطناعي. "
            "كن مفيداً ومختصراً وودياً."
        ) if lang == "ar" else (
            "You are a smart customer support assistant for Orgteh Infra. "
            "Always reply in English only. Be helpful, concise and friendly."
        )

        payload = {
            "model":      "gpt-4o-mini",
            "messages":   [{"role": "system", "content": system_prompt}, {"role": "user", "content": message}],
            "max_tokens": 800,
            "stream":     True,
        }

        async def generate():
            try:
                async for chunk in smart_chat_stream(payload, "support@orgteh.com", is_trial=True):
                    text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, bytes) else str(chunk)
                    if text.startswith("data: "):
                        text = text[6:]
                    if text.strip() and text.strip() != "[DONE]":
                        try:
                            content = json.loads(text).get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                yield content.encode("utf-8")
                        except Exception:
                            pass
            except Exception:
                yield ("عذراً، حدث خطأ." if lang == "ar" else "Sorry, an error occurred.").encode("utf-8")

        return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)