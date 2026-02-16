import os
import json
import sys
import signal
import subprocess
import traceback
import time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

# Import authentication service
from services.auth import (
    LoginRequest, SendVerificationRequest, RegisterRequest,
    handle_send_verification, handle_register, handle_login, handle_logout,
    get_current_user_email, get_auth_context
)
from database import (
    get_user_by_email, get_user_by_api_key, get_global_stats, update_user_usage_struct
)
from services.subscriptions import get_user_subscription_status
from services.limits import get_user_limits_and_usage, check_trial_allowance
from services.providers import MODELS_METADATA, HIDDEN_MODELS, smart_chat_stream, acquire_provider_slot
from services.request_router import handle_chat_request
from customer_service import router as customer_service_router
from tools import router as tools_router
from tools.registry import TOOLS_DB 
from code_processor import process_code_merge_stream, CODE_HUB_MODELS_INFO

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "super-secret-key-change-in-production")

app = FastAPI(title="Nexus API Marketplace", docs_url=None, redoc_url=None)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False) 

if not os.path.exists("templates"): os.makedirs("templates")
if not os.path.exists("static"): os.makedirs("static")

# Mount static files correctly for Vercel/Production
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(customer_service_router)

# ============================================================================
# TEMPLATE CONTEXT HELPER WITH LANGUAGE SUPPORT
# ============================================================================

def get_template_context(request: Request, lang: str = "ar"):
    """Get template context with auth info and active language"""
    context = get_auth_context(request)
    # Ensure lang is valid, default to ar
    if lang not in ["ar", "en"]:
        lang = "ar"
    context["lang"] = lang
    return context

# ============================================================================
# PAGE ROUTES - SEO OPTIMIZED
# ============================================================================

@app.get("/auth", response_class=HTMLResponse)
@app.get("/{lang}/auth", response_class=HTMLResponse)
async def auth_page(request: Request, lang: Optional[str] = "ar"):
    """New dedicated authentication page"""
    if get_current_user_email(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/", response_class=HTMLResponse)
@app.get("/{lang}/", response_class=HTMLResponse)
async def home(request: Request, lang: Optional[str] = "ar"): 
    return templates.TemplateResponse("index.html", get_template_context(request, lang))

@app.get("/pricing", response_class=HTMLResponse)
@app.get("/{lang}/pricing", response_class=HTMLResponse)
async def pricing(request: Request, lang: Optional[str] = "ar"):
    context = get_template_context(request, lang)
    if context["is_logged_in"]:
        sub_status = get_user_subscription_status(context["user_email"])
        context["current_plan"] = sub_status["plan_name"] if sub_status else "Free Tier"
    return templates.TemplateResponse("pricing.html", context)

@app.get("/models", response_class=HTMLResponse)
@app.get("/{lang}/models", response_class=HTMLResponse)
async def models_page(request: Request, lang: Optional[str] = "ar"):
    """
    SEO Optimized Models Page.
    Handles /ar/models and /en/models as distinct SEO entries but uses the same file.
    """
    context = get_template_context(request, lang)
    context["models"] = context["models_metadata"]
    return templates.TemplateResponse("models.html", context)

# ============================================================================
# GITHUB OAUTH ROUTES
# ============================================================================

from github_integration import (
    handle_github_login, 
    handle_github_callback,
    handle_github_logout,
    get_github_context,
    one_click_deploy,
    DeployRequest,
    is_github_connected
)

@app.get("/auth/github/login")
async def github_login(request: Request):
    """Start GitHub OAuth flow"""
    return await handle_github_login(request)

@app.get("/auth/github/callback")
async def github_callback(request: Request, code: str, state: str):
    """GitHub OAuth callback"""
    return await handle_github_callback(request, code, state)

@app.post("/auth/github/logout")
async def github_logout(request: Request):
    """Disconnect GitHub"""
    return await handle_github_logout(request)

# ============================================================================
# ONE-CLICK DEPLOY API
# ============================================================================

@app.post("/api/deploy/github")
async def deploy_to_github(request: Request, deploy_data: DeployRequest):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, 401)

    if not is_github_connected(request):
        return JSONResponse({
            "error": "GitHub not connected",
            "connect_url": "/auth/github/login"
        }, 403)

    from github_integration import get_github_token
    token = get_github_token(request)

    try:
        body = await request.json()
        files = body.get("files", [])

        if not files:
            return JSONResponse({"error": "No files provided"}, 400)

        result = await one_click_deploy(
            access_token=token,
            project_name=deploy_data.repo_name,
            files=files,
            description=deploy_data.description,
            is_private=deploy_data.is_private,
            enable_pages=deploy_data.enable_pages
        )

        return JSONResponse(result)

    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/github/repos")
async def get_user_repos(request: Request):
    email = get_current_user_email(request)
    if not email or not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected"}, 403)

    from github_integration import get_github_token, GitHubDeployer

    token = get_github_token(request)
    deployer = GitHubDeployer(token)
    repos = deployer.get_repos()

    return {"repos": repos}

# ============================================================================
# TOOLS PAGE ROUTES
# ============================================================================
@app.get("/tools", response_class=HTMLResponse)
@app.get("/{lang}/tools", response_class=HTMLResponse)
async def tools_page(request: Request, lang: Optional[str] = "ar"):
    """Tools marketplace page"""
    context = get_template_context(request, lang)
    context["tools"] = list(TOOLS_DB.values())
    context["active_tool"] = None
    return templates.TemplateResponse("tools.html", context)

@app.get("/tools/{tool_id}", response_class=HTMLResponse)
@app.get("/{lang}/tools/{tool_id}", response_class=HTMLResponse)
async def tool_detail_page(request: Request, tool_id: str, lang: Optional[str] = "ar"):
    """Individual tool detail page with playground"""
    context = get_template_context(request, lang)
    context["tools"] = list(TOOLS_DB.values())
    context["active_tool"] = TOOLS_DB.get(tool_id)
    return templates.TemplateResponse("tools.html", context)

# Register API routers
app.include_router(tools_router, prefix="/api") 
app.include_router(tools_router, prefix="/v1")   

@app.get("/code-hub", response_class=HTMLResponse)
@app.get("/{lang}/code-hub", response_class=HTMLResponse)
async def code_hub_page(request: Request, lang: Optional[str] = "ar"):
    context = get_template_context(request, lang)
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

@app.get("/enterprise", response_class=HTMLResponse)
@app.get("/{lang}/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request, lang: Optional[str] = "ar"): 
    return templates.TemplateResponse("enterprise.html", get_template_context(request, lang))

@app.get("/docs", response_class=HTMLResponse)
@app.get("/{lang}/docs", response_class=HTMLResponse)
async def docs_page(request: Request, lang: Optional[str] = "ar"): 
    return templates.TemplateResponse("docs.html", get_template_context(request, lang))

@app.get("/policy", response_class=HTMLResponse)
@app.get("/{lang}/policy", response_class=HTMLResponse)
async def policy_page(request: Request, lang: Optional[str] = "ar"):
    return templates.TemplateResponse("policy.html", get_template_context(request, lang))

@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    stats = get_global_stats()
    context = get_template_context(request) # Performance page usually standardized
    context.update({
        "stats": stats,
        "last_update": datetime.utcnow().isoformat(),
        "active_nodes": 5
    })
    return templates.TemplateResponse("performance.html", context)

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/{lang}/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, lang: Optional[str] = "ar"):
    email = get_current_user_email(request)
    if not email: 
        return RedirectResponse("/auth")
    sub_status = get_user_subscription_status(email)
    limits, usage = get_user_limits_and_usage(email)
    context = get_template_context(request, lang)

    def calc_pct(used, limit): 
        return min(100, (used/limit)*100) if limit > 0 else 0

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

# ============================================================================
# AUTHENTICATION API ROUTES
# ============================================================================

@app.post("/auth/send-verification")
async def send_verification(request: Request, data: SendVerificationRequest):
    return await handle_send_verification(request, data)

@app.post("/auth/register")
async def register(request: Request, data: RegisterRequest):
    return await handle_register(request, data)

@app.post("/auth/login")
async def login(request: Request, data: LoginRequest):
    return await handle_login(request, data)

@app.post("/auth/logout")
async def logout(request: Request):
    return await handle_logout(request)

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.post("/api/chat/trial")
async def trial_chat_endpoint(request: Request):
    """Free trial endpoint (10 msgs/day/model) - No auth required for public usage logic"""
    try:
        data = await request.json()
        email = get_current_user_email(request)
        if not email: 
            return JSONResponse({"error": "Unauthorized"}, 401)

        model_id = data.get("model_id")
        if model_id in HIDDEN_MODELS:
            return JSONResponse({"error": {"message": f"Model '{model_id}' unavailable.", "code": "model_unavailable"}}, 404)

        allowed = await check_trial_allowance(email, model_id)
        if not allowed:
            return JSONResponse({"error": "Daily trial limit reached (10 msgs). Please upgrade or use API."}, 429)

        payload = {
            "model": model_id,
            "messages": data.get("messages", [{"role": "user", "content": data.get("message")}]),
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", False)
        }

        if "extra_params" in data: payload.update(data["extra_params"])
        if "frequency_penalty" in data: payload["frequency_penalty"] = float(data["frequency_penalty"])
        if "presence_penalty" in data: payload["presence_penalty"] = float(data["presence_penalty"])
        
        # DeepSeek thinking fix
        if payload["model"] == "deepseek-ai/deepseek-v3.1" or "deepseek-r1" in payload["model"]:
             if "chat_template_kwargs" not in payload: 
                 payload["chat_template_kwargs"] = {"thinking": True}

        await acquire_provider_slot(is_priority=False)
        return StreamingResponse(smart_chat_stream(payload, email, is_trial=True), media_type="text/event-stream")

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/chat")
async def internal_chat_ui(request: Request):
    try:
        data = await request.json()
        email = get_current_user_email(request)
        if not email: return JSONResponse({"error": "Unauthorized"}, 401)

        is_trial = data.get("is_trial", False)

        if is_trial:
            model_id = data.get("model_id")
            allowed = await check_trial_allowance(email, model_id)
            if not allowed:
                return JSONResponse({"error": "Daily trial limit reached (10 msgs)."}, 429)

            payload = {
                "model": data.get("model_id"),
                "messages": data.get("messages", [{"role": "user", "content": data.get("message")}]),
                "temperature": float(data.get("temperature", 0.5)),
                "top_p": float(data.get("top_p", 0.7)),
                "max_tokens": int(data.get("max_tokens", 1024)),
                "stream": data.get("stream", False)
            }
            if "extra_params" in data: payload.update(data["extra_params"])
            if payload["model"] == "deepseek-ai/deepseek-v3.1" or "deepseek-r1" in payload["model"]:
                 if "chat_template_kwargs" not in payload: 
                     payload["chat_template_kwargs"] = {"thinking": True}

            await acquire_provider_slot(is_priority=False)
            return StreamingResponse(smart_chat_stream(payload, email, is_trial=True), media_type="text/event-stream")

        payload = {
            "model": data.get("model_id"),
            "messages": [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", False)
        }
        if "extra_params" in data: payload.update(data["extra_params"])
        if payload["model"] == "deepseek-ai/deepseek-v3.1" or "deepseek-r1" in payload["model"]:
             if "chat_template_kwargs" not in payload: 
                 payload["chat_template_kwargs"] = {"thinking": True}

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

@app.post("/api/process-code")
async def process_code_endpoint(
    request: Request,
    instruction: str = Form(...),
    target_model: str = Form("deepseek-ai/deepseek-v3.2"),
    target_tools: str = Form(""), 
    chat_history: str = Form("[]"),
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
        async for event in process_code_merge_stream(instruction, files_data, user_api_key, target_model, history_list, target_tools):
            if event['type'] in ['thinking', 'code', 'error']:
                yield json.dumps({"type": event['type'], "content": event['content']}, ensure_ascii=False).encode('utf-8') + b"\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

@app.post("/api/generate-key")
async def get_my_key(request: Request):
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Unauthorized"}, 401)
    user = get_user_by_email(email)
    return {"key": user.get("api_key")} if user else JSONResponse({"error": "Not found"}, 404)
