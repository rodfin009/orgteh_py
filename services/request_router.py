from fastapi.responses import JSONResponse, StreamingResponse
from services.limits import check_request_allowance
from services.providers import (
    smart_chat_stream, 
    acquire_provider_slot, 
    MODEL_MAPPING, 
    HIDDEN_MODELS
)

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
            status_code=404
        )

    # 2. فحص الحدود والأولوية (Limits Check)
    # هذه الدالة لا تنتظر، هي فقط تقرأ من قاعدة البيانات وتعطي قراراً
    allowed, is_priority = await check_request_allowance(email, model_id)

    if not allowed:
        return JSONResponse(
            {"error": "Quota limit reached. Please upgrade your plan or wait until renewal."}, 
            status_code=429
        )

    # 3. التنفيذ الذكي (Execution & Queuing)
    try:
        # هنا يتم اتخاذ قرار الانتظار بناءً على حمل النظام
        # إذا كان الطلب is_priority=True سيدخل حتى لو السيرفر مزدحم قليلاً
        # إذا كان is_priority=False سينتظر حتى يخف الضغط
        await acquire_provider_slot(is_priority=is_priority)

        # البدء في المعالجة والبث
        return StreamingResponse(
            smart_chat_stream(payload, email), 
            media_type="text/event-stream"
        )

    except Exception as e:
        # هذا يحدث في حال وجود ضغط شديد جداً وفشل الانتظار (Timeout)
        return JSONResponse(
            {"error": "System is currently at maximum capacity. Please try again in a few seconds."}, 
            status_code=503
        )