# tools/router.py
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
import json

from .registry import TOOLS_DB
from .rss_engine import execute_hybrid_news
from .nvidia_engine import execute_ocr, execute_embedding

# [هام جداً]: نحتفظ بالبادئة هنا لعزل الأدوات عن باقي صفحات الموقع
router = APIRouter(prefix="/tools", tags=["tools"])
templates = Jinja2Templates(directory="templates")

def get_user_from_session(request: Request):
    return request.session.get("user_email")

@router.get("/")
async def tools_page(request: Request):
    user_email = get_user_from_session(request)
    context = {
        "request": request,
        "is_logged_in": user_email is not None,
        "user_email": user_email,
        "tools": list(TOOLS_DB.values()),
        "active_tool": None
    }
    return templates.TemplateResponse("tools.html", context)

@router.get("/{tool_id}")
async def tool_details(request: Request, tool_id: str):
    user_email = get_user_from_session(request)
    tool = TOOLS_DB.get(tool_id)
    if not tool: return JSONResponse({"error": "Tool not found"}, 404)
    context = {
        "request": request,
        "is_logged_in": user_email is not None,
        "user_email": user_email,
        "tools": list(TOOLS_DB.values()),
        "active_tool": tool
    }
    return templates.TemplateResponse("tools.html", context)

# نقطة النهاية الخاصة بـ OCR (ملفات فقط)
@router.post("/execute/nexus-vision-ocr")
async def execute_ocr_endpoint(
    file_input: UploadFile = File(...)
):
    return await execute_ocr(file_input)

# نقطة النهاية الذكية (تقبل JSON و Form)
@router.post("/execute/{tool_id}")
async def execute_tool_endpoint(request: Request, tool_id: str):
    
    # 1. تحديد نوع البيانات المستلمة (JSON أو Form)
    content_type = request.headers.get("content-type", "")
    data = {}

    try:
        if "application/json" in content_type:
            data = await request.json()
        else:
            form = await request.form()
            data = {k: v for k, v in form.items()}
    except Exception:
        return JSONResponse({"error": "Could not parse request body"}, 400)

    # 2. استخراج المتغيرات (مع قيم افتراضية)
    # نحول القيم إلى الأنواع الصحيحة لأن Form Data تأتي دائماً كنصوص
    limit = int(data.get("limit", 5))
    lang = data.get("lang", "en")
    time_filter = data.get("time_filter", "1d")
    scrape_content = str(data.get("scrape_content", "false")).lower()
    text_input = data.get("text_input", "")
    truncate = data.get("truncate", "NONE")

    # 3. التوجيه للتنفيذ
    if tool_id == "nexus-finance-rss":
        return await execute_hybrid_news("finance", limit, lang, time_filter, scrape_content)

    elif tool_id == "nexus-news-general":
        return await execute_hybrid_news("general", limit, lang, time_filter, scrape_content)

    elif tool_id == "nexus-semantic-embed":
        return await execute_embedding(text_input, truncate)

    return JSONResponse({"error": "Unknown Tool or not supported via this endpoint"}, 400)