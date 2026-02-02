import os
import uvicorn
import json
import secrets
import bcrypt
import sys
from datetime import datetime

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

# --- Database & Services Imports ---
from database import (
    get_user_by_email, create_user_record, 
    get_user_by_api_key, get_global_stats
)
from services.subscriptions import get_user_subscription_status
from services.limits import get_user_limits_and_usage
from services.providers import MODELS_METADATA, HIDDEN_MODELS
from services.request_router import handle_chat_request
from customer_service import router as customer_service_router
from tools import router as tools_router
from tools.registry import TOOLS_DB 
from code_processor import process_code_merge_stream, CODE_HUB_MODELS_INFO

# --- V2 Import (CORRECTED PATH) ---
try:
    print(">>> [INIT] Attempting to import Advanced V2 Orchestrator...", flush=True)
    from advanced_code_processor.orchestrator import run_advanced_orchestration
    print(">>> [INIT] SUCCESS: Advanced V2 Module Loaded.", flush=True)
except ImportError as e:
    print(f">>> [FATAL ERROR] Could not import V2 Module: {e}", flush=True)
    raise e 

SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key")

app = FastAPI(title="Nexus API Marketplace", docs_url=None, redoc_url=None)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False) 

if not os.path.exists("templates"): os.makedirs("templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(customer_service_router)
app.include_router(tools_router, prefix="/api") 
app.include_router(tools_router)                 
app.include_router(tools_router, prefix="/v1")   

# --- Helpers ---
def get_current_user_email(request: Request):
    return request.session.get("user_email")

def generate_nexus_key():
    return f"nx-{secrets.token_hex(16)}"

def get_template_context(request: Request):
    user_email = get_current_user_email(request)
    user_key = None
    if user_email:
         u = get_user_by_email(user_email)
         if u: user_key = u.get("api_key")

    return {
        "request": request,
        "is_logged_in": user_email is not None,
        "user_email": user_email,
        "user_api_key": user_key,
        "models_metadata": [m for m in MODELS_METADATA if m["id"] not in HIDDEN_MODELS]
    }

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request): 
    return templates.TemplateResponse("index.html", get_template_context(request))

@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    context = get_template_context(request)
    if context["is_logged_in"]:
        sub_status = get_user_subscription_status(context["user_email"])
        context["current_plan"] = sub_status["plan_name"] if sub_status else "Free Tier"
    return templates.TemplateResponse("pricing.html", context)

@app.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    context = get_template_context(request)
    context["models"] = context["models_metadata"]
    return templates.TemplateResponse("models.html", context)

# === V1 ROUTE ===
@app.get("/code-hub", response_class=HTMLResponse)
async def code_hub_page(request: Request):
    context = get_template_context(request)
    context["mode"] = "standard" 
    merged_models = []
    for m in MODELS_METADATA:
        if m["id"] in HIDDEN_MODELS: continue
        model_entry = m.copy()
        if m["id"] in CODE_HUB_MODELS_INFO:
            model_entry.update(CODE_HUB_MODELS_INFO[m["id"]])
        else:
            model_entry.update({"desc_en": "General AI", "desc_ar": "ذكاء اصطناعي عام", "badge_en": "", "badge_ar": ""})
        merged_models.append(model_entry)

    context["models"] = merged_models
    context["tools"] = list(TOOLS_DB.values())
    return templates.TemplateResponse("code_hub/index.html", context)

# === V2 ROUTE ===
@app.get("/advanced-mode", response_class=HTMLResponse)
async def advanced_mode_page(request: Request):
    email = get_current_user_email(request)
    if not email: return RedirectResponse("/?auth=required")

    context = get_template_context(request)
    context["mode"] = "advanced" 
    merged_models = []
    for m in MODELS_METADATA:
        if m["id"] in HIDDEN_MODELS: continue
        model_entry = m.copy()
        if m["id"] in CODE_HUB_MODELS_INFO:
            model_entry.update(CODE_HUB_MODELS_INFO[m["id"]])
        else:
            model_entry.update({"desc_en": "General AI", "desc_ar": "ذكاء اصطناعي عام", "badge_en": "", "badge_ar": ""})
        merged_models.append(model_entry)

    context["models"] = merged_models
    context["tools"] = list(TOOLS_DB.values())
    return templates.TemplateResponse("code_hub/index.html", context)

@app.get("/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request): 
    return templates.TemplateResponse("enterprise.html", get_template_context(request))

@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request): 
    return templates.TemplateResponse("docs.html", get_template_context(request))

@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    stats = get_global_stats()
    context = get_template_context(request)
    context.update({
        "stats": stats,
        "last_update": datetime.utcnow().isoformat(),
        "active_nodes": 5
    })
    return templates.TemplateResponse("performance.html", context)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    email = get_current_user_email(request)
    if not email: return RedirectResponse("/")
    sub_status = get_user_subscription_status(email)
    limits, usage = get_user_limits_and_usage(email)
    context = get_template_context(request)
    def calc_pct(used, limit): return min(100, (used/limit)*100) if limit > 0 else 0
    context.update({
        "api_key": context["user_api_key"],
        "plan_name": sub_status["plan_name"],
        "is_active_sub": sub_status["is_active"], 
        "is_perpetual": sub_status["is_perpetual"],
        "days_left": sub_status["days_left"],
        "usage": usage,
        "limits": limits,
        "pct_deepseek": calc_pct(usage.get("deepseek",0), limits.get("deepseek",0)),
        "pct_kimi": calc_pct(usage.get("kimi",0), limits.get("kimi",0)),
        "pct_mistral": calc_pct(usage.get("mistral",0), limits.get("mistral",0)),
        "pct_llama": calc_pct(usage.get("llama",0), limits.get("llama",0)),
        "pct_gemma": calc_pct(usage.get("gemma",0), limits.get("gemma",0)),
        "pct_extra": calc_pct(usage.get("unified_extra",0), limits.get("unified_extra",0)),
    })
    return templates.TemplateResponse("insights.html", context)

# --- API Endpoints ---

@app.post("/api/chat")
async def internal_chat_ui(request: Request):
    try:
        data = await request.json()
        email = get_current_user_email(request)
        if not email: return JSONResponse({"error": "Unauthorized"}, 401)
        payload = {
            "model": data.get("model_id"),
            "messages": [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", False)
        }
        if "extra_params" in data: payload.update(data["extra_params"])
        if "frequency_penalty" in data: payload["frequency_penalty"] = float(data["frequency_penalty"])
        if "presence_penalty" in data: payload["presence_penalty"] = float(data["presence_penalty"])
        if payload["model"] == "deepseek-ai/deepseek-v3.1":
             if "chat_template_kwargs" not in payload: payload["chat_template_kwargs"] = {"thinking": True}
        return await handle_chat_request(email, payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/v1/chat/completions")
async def openai_compatible_proxy(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
         return JSONResponse({"error": "Invalid Nexus API Key Format"}, 401)
    api_key = auth_header.split(" ")[1]
    user = get_user_by_api_key(api_key)
    if not user: return JSONResponse({"error": "Invalid Nexus API Key"}, 401)
    try: body = await request.json()
    except: return JSONResponse({"error": "Invalid JSON Body"}, 400)
    return await handle_chat_request(user['email'], body)

# --- V1 PROCESSING ---
@app.post("/api/process-code")
async def process_code_endpoint(
    request: Request,
    instruction: str = Form(...),
    target_model: str = Form("deepseek-ai/deepseek-v3.2"),
    target_tools: str = Form(""), 
    chat_history: str = Form("[]"),
    current_files: str = Form("{}"), 
    files: list[UploadFile] = File(default=[])
):
    if target_model in HIDDEN_MODELS:
        return JSONResponse({"error": "Target model is unavailable"}, 400)
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Please login first"}, 401)
    user = get_user_by_email(email)
    user_api_key = user.get("api_key", "YOUR_API_KEY")
    try: history_list = json.loads(chat_history)
    except: history_list = []
    files_data = []
    if files:
        for f in files:
            if f.filename:
                try:
                    content = await f.read()
                    files_data.append({"name": f.filename, "content": content.decode('utf-8')})
                except: pass

    async def event_generator():
        async for event in process_code_merge_stream(
            instruction, files_data, user_api_key, target_model, history_list, target_tools 
        ):
            if event['type'] in ['thinking', 'code', 'error']:
                yield json.dumps(
                    {"type": event['type'], "content": event['content']}, 
                    ensure_ascii=False
                ).encode('utf-8') + b"\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

# --- V2 PROCESSING (ADVANCED - FIXED) ---
@app.post("/api/v2/advanced-process")
async def advanced_process_endpoint(
    request: Request,
    instruction: str = Form(...),
    target_model: str = Form("deepseek-ai/deepseek-v3.2"), # [NEW]
    target_tools: str = Form(""),                          # [NEW]
    files_context: str = Form("{}"),
    chat_history: str = Form("[]")
):
    print(f"\n>>> [V2 ENDPOINT] Received Request: {instruction[:50]}...", flush=True)

    # 1. المصادقة وجلب المفتاح
    email = get_current_user_email(request)
    if not email: 
        print(">>> [V2 ERROR] Unauthorized user", flush=True)
        return JSONResponse({"error": "Unauthorized"}, 401)

    user = get_user_by_email(email)
    user_api_key = user.get("api_key", "nx-unknown")

    try: f_ctx = json.loads(files_context)
    except: f_ctx = {}

    try: history_list = json.loads(chat_history)
    except: history_list = []

    session_id = f"sess_{secrets.token_hex(4)}"

    # 2. تجهيز كائن الإعدادات لتمريره للمنسق
    config = {
        "api_key": user_api_key,
        "target_model": target_model,
        "target_tools": target_tools
    }

    print(f">>> [V2 ENDPOINT] Starting Orchestration Session: {session_id} | Model: {target_model}", flush=True)

    return StreamingResponse(
        # نمرر الكونفيج كـ kwargs أو كمتغير جديد
        run_advanced_orchestration(
            instruction, 
            f_ctx, 
            session_id, 
            history_list, 
            config=config # [NEW] تمرير الإعدادات
        ),
        media_type="application/x-ndjson"
    )

@app.post("/auth/register")
async def register(request: Request):
    try:
        data = await request.json()
        email, password = data.get("email"), data.get("password")
        if get_user_by_email(email): return JSONResponse({"error": "User exists"}, 400)
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        new_key = generate_nexus_key()
        if create_user_record(email, hashed, new_key):
            request.session["user_email"] = email 
            return {"message": "Registered", "key": new_key}
        return JSONResponse({"error": "DB Error"}, 500)
    except Exception as e: return JSONResponse({"error": str(e)}, 500)

@app.post("/auth/login")
async def login(request: Request):
    try:
        data = await request.json()
        user = get_user_by_email(data.get("email"))
        if not user or not bcrypt.checkpw(data.get("password").encode('utf-8'), user['password'].encode('utf-8')):
            return JSONResponse({"error": "Invalid credentials"}, 401)
        request.session["user_email"] = data.get("email")
        return {"message": "Logged in"}
    except Exception as e: return JSONResponse({"error": str(e)}, 500)

@app.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}

@app.post("/api/generate-key")
async def get_my_key(request: Request):
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Unauthorized"}, 401)
    user = get_user_by_email(email)
    return {"key": user.get("api_key")} if user else JSONResponse({"error": "Not found"}, 404)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)