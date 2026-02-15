import os
import json
import sys
import signal
import subprocess
import traceback
import time
from datetime import datetime
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(customer_service_router)

# ============================================================================
# TEMPLATE CONTEXT HELPER
# ============================================================================

def get_template_context(request: Request):
    """Get template context with auth info"""
    return get_auth_context(request)

# ============================================================================
# PAGE ROUTES - MUST BE DEFINED BEFORE API ROUTERS
# ============================================================================

@app.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request):
    """New dedicated authentication page"""
    if get_current_user_email(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("auth.html", get_template_context(request))

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
# ============================================================================
# GITHUB OAUTH ROUTES (Add to main.py)
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
    """
    One-click deploy to GitHub
    Creates repo, pushes files, enables Pages
    """
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, 401)

    # Check GitHub connection
    if not is_github_connected(request):
        return JSONResponse({
            "error": "GitHub not connected",
            "connect_url": "/auth/github/login"
        }, 403)

    # Get GitHub token
    from github_integration import get_github_token
    token = get_github_token(request)

    # Get files from request body (sent as JSON)
    try:
        body = await request.json()
        files = body.get("files", [])

        if not files:
            return JSONResponse({"error": "No files provided"}, 400)

        # Perform deployment
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
    """Get user's GitHub repositories"""
    email = get_current_user_email(request)
    if not email or not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected"}, 403)

    from github_integration import get_github_token, GitHubDeployer

    token = get_github_token(request)
    deployer = GitHubDeployer(token)
    repos = deployer.get_repos()

    return {"repos": repos}

# ============================================================================
# UPDATE TEMPLATE CONTEXT
# ============================================================================

def get_template_context(request: Request):
    """Enhanced context with GitHub info"""
    context = get_auth_context(request)
    context.update(get_github_context(request))
    return context


# ============================================================================
# FIX: TOOLS PAGE ROUTES - MUST BE DEFINED BEFORE TOOLS ROUTER
# ============================================================================
@app.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    """Tools marketplace page"""
    context = get_template_context(request)
    context["tools"] = list(TOOLS_DB.values())
    context["active_tool"] = None
    return templates.TemplateResponse("tools.html", context)

@app.get("/tools/{tool_id}", response_class=HTMLResponse)
async def tool_detail_page(request: Request, tool_id: str):
    """Individual tool detail page with playground"""
    context = get_template_context(request)
    context["tools"] = list(TOOLS_DB.values())
    context["active_tool"] = TOOLS_DB.get(tool_id)
    return templates.TemplateResponse("tools.html", context)

# ============================================================================
# FIX: REGISTER TOOLS ROUTER AFTER PAGE ROUTES WITH PREFIXES ONLY
# ============================================================================
# Removed the duplicate registration without prefix to avoid conflict with /tools page
app.include_router(tools_router, prefix="/api") 
app.include_router(tools_router, prefix="/v1")   

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

@app.get("/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request): 
    return templates.TemplateResponse("enterprise.html", get_template_context(request))

@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request): 
    return templates.TemplateResponse("docs.html", get_template_context(request))

@app.get("/policy", response_class=HTMLResponse)
async def policy_page(request: Request):
    return templates.TemplateResponse("policy.html", get_template_context(request))

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
    if not email: 
        return RedirectResponse("/auth")
    sub_status = get_user_subscription_status(email)
    limits, usage = get_user_limits_and_usage(email)
    context = get_template_context(request)

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
# AUTHENTICATION API ROUTES (Using auth.py service)
# ============================================================================

@app.post("/auth/send-verification")
async def send_verification(request: Request, data: SendVerificationRequest):
    """Send verification code to email after Turnstile validation"""
    return await handle_send_verification(request, data)

@app.post("/auth/register")
async def register(request: Request, data: RegisterRequest):
    """Register new user with verification code"""
    return await handle_register(request, data)

@app.post("/auth/login")
async def login(request: Request, data: LoginRequest):
    """Login existing user"""
    return await handle_login(request, data)

@app.post("/auth/logout")
async def logout(request: Request):
    """Logout user"""
    return await handle_logout(request)

# ============================================================================
# FIX: TRIAL CHAT ENDPOINT (Free daily trials - doesn't deduct from subscription)
# ============================================================================

@app.post("/api/chat/trial")
async def trial_chat_endpoint(request: Request):
    """
    Free trial endpoint for testing models in the UI.
    - 10 free trials per day per model for ALL users (including free tier)
    - Does NOT deduct from subscription limits
    - Only tracked in global system performance stats (not user dashboard)
    """
    try:
        data = await request.json()
        email = get_current_user_email(request)

        if not email: 
            return JSONResponse({"error": "Unauthorized"}, 401)

        model_id = data.get("model_id")

        # Check if model is available
        if model_id in HIDDEN_MODELS:
            return JSONResponse(
                {"error": {"message": f"Model '{model_id}' unavailable.", "code": "model_unavailable"}}, 
                status_code=404
            )

        # Check trial allowance (10 free trials per day per model)
        print(f"[DEBUG] Checking trial allowance for {email} / model: {model_id}")  # DEBUG
        allowed = await check_trial_allowance(email, model_id)  # FIX: Pass model_id

        if not allowed:
            print(f"[DEBUG] Trial limit reached for {email} / model: {model_id}")  # DEBUG
            return JSONResponse(
                {"error": "Daily trial limit reached (10 msgs). Please upgrade or use API."}, 
                status_code=429
            )

        print(f"[DEBUG] Trial allowed for {email} / model: {model_id}, proceeding...")  # DEBUG

        # Build payload for the model
        payload = {
            "model": model_id,
            "messages": data.get("messages", [{"role": "user", "content": data.get("message")}]),
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", False)
        }

        if "extra_params" in data: 
            payload.update(data["extra_params"])
        if "frequency_penalty" in data: 
            payload["frequency_penalty"] = float(data["frequency_penalty"])
        if "presence_penalty" in data: 
            payload["presence_penalty"] = float(data["presence_penalty"])

        # For DeepSeek thinking mode
        if payload["model"] == "deepseek-ai/deepseek-v3.1":
             if "chat_template_kwargs" not in payload: 
                 payload["chat_template_kwargs"] = {"thinking": True}

        # Execute without deducting from subscription (skip check_request_allowance)
        # Just acquire a slot and stream the response
        await acquire_provider_slot(is_priority=False)

        # FIX: Pass is_trial=True to ensure usage is NOT deducted from user subscription
        print(f"[DEBUG] Calling smart_chat_stream with is_trial=True")  # DEBUG
        return StreamingResponse(
            smart_chat_stream(payload, email, is_trial=True), 
            media_type="text/event-stream"
        )

    except Exception as e:
        print(f"[DEBUG] Error in trial endpoint: {e}")  # DEBUG
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================================
# FIX: INTERNAL CHAT - Now supports is_trial flag for models page
# ============================================================================

@app.post("/api/chat")
async def internal_chat_ui(request: Request):
    """
    Main chat endpoint - deducts from subscription UNLESS is_trial=true
    This allows the models page to use this endpoint with trial flag
    """
    try:
        data = await request.json()
        email = get_current_user_email(request)
        if not email: 
            return JSONResponse({"error": "Unauthorized"}, 401)

        # CRITICAL FIX: Check if this is a trial request from models page
        is_trial = data.get("is_trial", False)

        if is_trial:
            print(f"[DEBUG] Trial flag detected in /api/chat for {email}")

            model_id = data.get("model_id")
            # Validate trial allowance (10 per day per model)
            allowed = await check_trial_allowance(email, model_id)  # FIX: Pass model_id
            if not allowed:
                return JSONResponse(
                    {"error": "Daily trial limit reached (10 msgs). Please upgrade or use API."}, 
                    status_code=429
                )

            # Build payload exactly like trial endpoint
            payload = {
                "model": data.get("model_id"),
                "messages": data.get("messages", [{"role": "user", "content": data.get("message")}]),
                "temperature": float(data.get("temperature", 0.5)),
                "top_p": float(data.get("top_p", 0.7)),
                "max_tokens": int(data.get("max_tokens", 1024)),
                "stream": data.get("stream", False)
            }

            if "extra_params" in data: 
                payload.update(data["extra_params"])
            if "frequency_penalty" in data: 
                payload["frequency_penalty"] = float(data["frequency_penalty"])
            if "presence_penalty" in data: 
                payload["presence_penalty"] = float(data["presence_penalty"])
            if payload["model"] == "deepseek-ai/deepseek-v3.1":
                 if "chat_template_kwargs" not in payload: 
                     payload["chat_template_kwargs"] = {"thinking": True}

            await acquire_provider_slot(is_priority=False)

            # IMPORTANT: Pass is_trial=True so it doesn't deduct from quota
            return StreamingResponse(
                smart_chat_stream(payload, email, is_trial=True), 
                media_type="text/event-stream"
            )

        # Normal mode (deducts from subscription)
        print(f"[DEBUG] Normal request in /api/chat for {email} - Will deduct from quota")

        payload = {
            "model": data.get("model_id"),
            "messages": [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", False)
        }
        if "extra_params" in data: 
            payload.update(data["extra_params"])
        if "frequency_penalty" in data: 
            payload["frequency_penalty"] = float(data["frequency_penalty"])
        if "presence_penalty" in data: 
            payload["presence_penalty"] = float(data["presence_penalty"])
        if payload["model"] == "deepseek-ai/deepseek-v3.1":
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
    if not user: 
        return JSONResponse({"error": "Invalid Nexus API Key"}, 401)
    try: 
        body = await request.json()
    except: 
        return JSONResponse({"error": "Invalid JSON Body"}, 400)
    return await handle_chat_request(user['email'], body)

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
    if not email: 
        return JSONResponse({"error": "Please login first"}, 401)
    user = get_user_by_email(email)
    user_api_key = user.get("api_key", "YOUR_API_KEY")
    try: 
        history_list = json.loads(chat_history)
    except: 
        history_list = []
    files_data = []
    if files:
        for f in files:
            if f.filename:
                try:
                    content = await f.read()
                    files_data.append({"name": f.filename, "content": content.decode('utf-8')})
                except: 
                    pass

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

@app.post("/api/generate-key")
async def get_my_key(request: Request):
    email = get_current_user_email(request)
    if not email: 
        return JSONResponse({"error": "Unauthorized"}, 401)
    user = get_user_by_email(email)
    return {"key": user.get("api_key")} if user else JSONResponse({"error": "Not found"}, 404)

# ============================================================================
# SERVER UTILITIES
# ============================================================================

def force_kill_port(port: int):
    try:
        result = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
        pids = result.stdout.strip().split("\n")
        for pid in pids:
            if pid:
                print(f">>> [SYSTEM] Killing old process on port {port} (PID: {pid})...", flush=True)
                os.kill(int(pid), signal.SIGKILL)
    except Exception as e:
        print(f">>> [SYSTEM] Warning: Could not clean up port {port}: {e}", flush=True)

if __name__ == "__main__":
    import uvicorn # استيراد محلي فقط عند التشغيل اليدوي
    force_kill_port(8000)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
