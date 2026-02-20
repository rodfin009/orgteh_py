import os
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from services.auth import (
    LoginRequest, SendVerificationRequest, RegisterRequest,
    handle_send_verification, handle_register, handle_login, handle_logout,
    get_current_user_email, get_auth_context
)
from database import (
    get_user_by_email, get_user_by_api_key, get_global_stats
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

app = FastAPI(title="Orgteh Infra", docs_url=None, redoc_url=None)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False) 

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

if not STATIC_DIR.exists(): STATIC_DIR.mkdir()
if not TEMPLATES_DIR.exists(): TEMPLATES_DIR.mkdir()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(customer_service_router)

# ============================================================================
# HEALTH CHECK ENDPOINT (CRITICAL FOR PREVENTING VERCEL COLD STARTS)
# ============================================================================
@app.get("/api/health")
async def health_check():
    """
    Ping this endpoint using cron-job.org every 5 minutes to keep the
    Vercel Serverless Function warm and drastically reduce TTFB/FCP for real users.
    """
    return JSONResponse({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})

@app.get("/debug/static-files")
async def check_static_files():
    file_structure = []
    if STATIC_DIR.exists():
        for root, dirs, files in os.walk(str(STATIC_DIR)):
            for name in files:
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, str(BASE_DIR))
                file_structure.append(rel_path)
    else:
        file_structure.append("STATIC DIRECTORY NOT FOUND AT: " + str(STATIC_DIR))

    return {
        "base_dir": str(BASE_DIR),
        "static_dir": str(STATIC_DIR),
        "found_files": file_structure[:100]  
    }

def get_template_context(request: Request, lang: str = "en"):
    context = get_auth_context(request)
    if lang not in ["ar", "en"]:
        lang = "en"
    context["lang"] = lang
    return context

@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    return RedirectResponse("/en/")

@app.get("/{lang}/", response_class=HTMLResponse)
async def home(request: Request, lang: str): 
    return templates.TemplateResponse("index.html", get_template_context(request, lang))

@app.get("/login", response_class=HTMLResponse)
async def login_redirect(request: Request): 
    lang_cookie = request.cookies.get("preferred_lang")
    if lang_cookie in ["ar", "en"]:
        return RedirectResponse(f"/{lang_cookie}/auth")
    return RedirectResponse("/en/auth")

@app.get("/{lang}/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/register", response_class=HTMLResponse)
async def register_redirect(request: Request):
    lang_cookie = request.cookies.get("preferred_lang")
    lang = lang_cookie if lang_cookie in ["ar", "en"] else "en"
    return RedirectResponse(f"/{lang}/auth?tab=register")

@app.get("/{lang}/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/profile", response_class=HTMLResponse)
async def profile_redirect(): return RedirectResponse("/en/profile")

@app.get("/{lang}/profile", response_class=HTMLResponse)
async def profile_page(request: Request, lang: str):
    email = get_current_user_email(request)
    if not email: return RedirectResponse(f"/{lang}/login")

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

@app.get("/accesory", response_class=HTMLResponse)
async def accesory_redirect(): return RedirectResponse("/en/accesory")

@app.get("/{lang}/accesory", response_class=HTMLResponse)
async def accesory_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["tools"] = list(TOOLS_DB.values())
    context["active_tool"] = None
    return templates.TemplateResponse("tools.html", context)

@app.get("/{lang}/accesory/{tool_id}", response_class=HTMLResponse)
async def accesory_detail_page(request: Request, lang: str, tool_id: str):
    context = get_template_context(request, lang)
    tool = TOOLS_DB.get(tool_id)
    if not tool:
        return RedirectResponse(f"/{lang}/accesory")
    context["active_tool"] = tool
    context["tools"] = list(TOOLS_DB.values())
    return templates.TemplateResponse("tools.html", context)

@app.get("/cart", response_class=HTMLResponse)
async def cart_redirect(): return RedirectResponse("/en/cart")

@app.get("/{lang}/cart", response_class=HTMLResponse)
async def cart_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    if context["is_logged_in"]:
        sub_status = get_user_subscription_status(context["user_email"])
        context["current_plan"] = sub_status["plan_name"] if sub_status else "Free Tier"
    return templates.TemplateResponse("pricing.html", context)

@app.get("/contacts", response_class=HTMLResponse)
async def contacts_redirect(): return RedirectResponse("/en/contacts")

@app.get("/{lang}/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request, lang: str):
    return templates.TemplateResponse("contacts.html", get_template_context(request, lang))

@app.get("/auth", response_class=HTMLResponse)
async def auth_redirect(): return RedirectResponse("/en/login")

@app.get("/{lang}/auth", response_class=HTMLResponse)
async def auth_page(request: Request, lang: str):
    if get_current_user_email(request):
        return RedirectResponse(f"/{lang}/profile")
    return templates.TemplateResponse("auth.html", get_template_context(request, lang))

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_redirect(): return RedirectResponse("/en/cart")

@app.get("/{lang}/pricing", response_class=HTMLResponse)
async def pricing(request: Request, lang: str):
    context = get_template_context(request, lang)
    if context["is_logged_in"]:
        sub_status = get_user_subscription_status(context["user_email"])
        context["current_plan"] = sub_status["plan_name"] if sub_status else "Free Tier"
    return templates.TemplateResponse("pricing.html", context)

@app.get("/models", response_class=HTMLResponse)
async def models_redirect():
    return RedirectResponse("/en/models")

@app.get("/{lang}/models", response_class=HTMLResponse)
async def models_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["models"] = context["models_metadata"]
    return templates.TemplateResponse("models.html", context)

@app.get("/{lang}/models/{model_key}", response_class=HTMLResponse)
async def model_detail_page(request: Request, lang: str, model_key: str):
    context = get_template_context(request, lang)
    model_info = next((m for m in MODELS_METADATA if m["short_key"] == model_key), None)
    if not model_info:
        return RedirectResponse(f"/{lang}/models")
    context["model"] = model_info
    context["models"] = context["models_metadata"]
    return templates.TemplateResponse("models.html", context)

@app.get("/api/model-description/{model_key}")
async def get_model_description(model_key: str, lang: str = "en"):
    try:
        file_path = STATIC_DIR / "models_translation" / f"{model_key}.html"
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            return JSONResponse({"html": html_content})
        else:
            return JSONResponse(
                {"error": f"Model description not found for: {model_key}", "path": str(file_path)}, 
                status_code=404
            )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

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
    return await handle_github_login(request)

@app.get("/auth/github/callback")
async def github_callback(request: Request, code: str, state: str):
    return await handle_github_callback(request, code, state)

@app.post("/auth/github/logout")
async def github_logout(request: Request):
    return await handle_github_logout(request)

@app.post("/api/deploy/github")
async def deploy_to_github(request: Request, deploy_data: DeployRequest):
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Unauthorized"}, 401)

    if not is_github_connected(request):
        return JSONResponse({"error": "GitHub not connected", "connect_url": "/auth/github/login"}, 403)

    from github_integration import get_github_token
    token = get_github_token(request)

    try:
        body = await request.json()
        files = body.get("files", [])
        if not files: return JSONResponse({"error": "No files provided"}, 400)

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


app.include_router(tools_router, prefix="/api") 
app.include_router(tools_router, prefix="/v1")

@app.get("/tools", response_class=HTMLResponse)
async def tools_legacy_redirect(): return RedirectResponse("/en/accesory")

@app.get("/{lang}/tools", response_class=HTMLResponse)
async def tools_legacy_lang_redirect(lang: str): return RedirectResponse(f"/{lang}/accesory")

@app.get("/code-hub", response_class=HTMLResponse)
async def code_hub_redirect(): return RedirectResponse("/en/code-hub")

@app.get("/{lang}/code-hub", response_class=HTMLResponse)
async def code_hub_page(request: Request, lang: str):
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
async def ent_redirect(): return RedirectResponse("/en/enterprise")

@app.get("/{lang}/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request, lang: str): 
    return templates.TemplateResponse("enterprise.html", get_template_context(request, lang))

@app.get("/docs", response_class=HTMLResponse)
async def docs_redirect(): return RedirectResponse("/en/docs")

@app.get("/{lang}/docs", response_class=HTMLResponse)
async def docs_page(request: Request, lang: str): 
    return templates.TemplateResponse("docs.html", get_template_context(request, lang))

@app.get("/policy", response_class=HTMLResponse)
async def policy_redirect(): return RedirectResponse("/en/policy")

@app.get("/{lang}/policy", response_class=HTMLResponse)
async def policy_page(request: Request, lang: str):
    return templates.TemplateResponse("policy.html", get_template_context(request, lang))

@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    stats = get_global_stats()
    context = get_template_context(request, "en")
    context.update({
        "stats": stats,
        "last_update": datetime.utcnow().isoformat(),
        "active_nodes": 5
    })
    return templates.TemplateResponse("performance.html", context)

@app.get("/{lang}/performance", response_class=HTMLResponse)
async def performance_page_lang(request: Request, lang: str):
    stats = get_global_stats()
    context = get_template_context(request, lang)
    context.update({
        "stats": stats,
        "last_update": datetime.utcnow().isoformat(),
        "active_nodes": 5
    })
    return templates.TemplateResponse("performance.html", context)

@app.get("/dashboard", response_class=HTMLResponse)
async def dash_redirect(): return RedirectResponse("/en/profile")

@app.get("/{lang}/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, lang: str):
    return RedirectResponse(f"/{lang}/profile")

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

@app.post("/api/set-language")
async def set_language(request: Request):
    from fastapi.responses import JSONResponse
    body = await request.json()
    lang = body.get("lang", "en")
    if lang not in ["ar", "en"]:
        lang = "en"
    response = JSONResponse({"ok": True, "lang": lang})
    response.set_cookie("preferred_lang", lang, max_age=60*60*24*365, path="/", samesite="lax")
    return response

@app.post("/api/chat/trial")
async def trial_chat_endpoint(request: Request):
    try:
        data = await request.json()
        email = get_current_user_email(request)
        if not email: return JSONResponse({"error": "Unauthorized"}, 401)

        model_id = data.get("model_id")
        if model_id in HIDDEN_MODELS:
            return JSONResponse({"error": {"message": f"Model unavailable.", "code": "model_unavailable"}}, 404)

        allowed = await check_trial_allowance(email, model_id)
        if not allowed:
            return JSONResponse({"error": "Daily trial limit reached (10 msgs)."}, 429)

        payload = {
            "model": model_id,
            "messages": data.get("messages", []),
            "temperature": float(data.get("temperature", 0.5)),
            "top_p": float(data.get("top_p", 0.7)),
            "max_tokens": int(data.get("max_tokens", 1024)),
            "stream": data.get("stream", True)
        }
        if "extra_params" in data: payload.update(data["extra_params"])

        if "deepseek" in payload["model"]:
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
                return JSONResponse({"error": "Daily trial limit reached."}, 429)

            payload = {
                "model": data.get("model_id"),
                "messages": data.get("messages", [{"role": "user", "content": data.get("message")}]),
                "temperature": float(data.get("temperature", 0.5)),
                "stream": data.get("stream", False)
            }
            if "deepseek" in payload["model"]:
                 payload["chat_template_kwargs"] = {"thinking": True}

            await acquire_provider_slot(is_priority=False)
            return StreamingResponse(smart_chat_stream(payload, email, is_trial=True), media_type="text/event-stream")

        payload = {
            "model": data.get("model_id"),
            "messages": [{"role": "user", "content": data.get("message")}],
            "temperature": float(data.get("temperature", 0.5)),
            "stream": data.get("stream", False)
        }
        if "deepseek" in payload["model"]:
             payload["chat_template_kwargs"] = {"thinking": True}

        return await handle_chat_request(email, payload)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/v1/chat/completions")
async def openai_compatible_proxy(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
         return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)
    api_key = auth_header.split(" ")[1]
    user = get_user_by_api_key(api_key)
    if not user: return JSONResponse({"error": "Invalid Orgteh API Key"}, 401)
    try: body = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, 400)
    return await handle_chat_request(user['email'], body)

@app.post("/api/process-code")
async def process_code_endpoint(
    request: Request,
    instruction: str = Form(...),
    target_model: str = Form("deepseek-ai/deepseek-v3.2"),
    target_tools: str = Form(""), 
    chat_history: str = Form("[]"),
    chat_mode: str = Form("build"),
    files: list[UploadFile] = File(default=[])
):
    if target_model in HIDDEN_MODELS: return JSONResponse({"error": "Target model unavailable"}, 400)
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Login required"}, 401)
    user = get_user_by_email(email)
    user_api_key = user.get("api_key", "YOUR_API_KEY")
    try: history_list = json.loads(chat_history)
    except: history_list = []
    files_data = []
    if files:
        for f in files:
            if f.filename:
                try: files_data.append({"name": f.filename, "content": (await f.read()).decode('utf-8')})
                except: pass

    async def event_generator():
        async for event in process_code_merge_stream(instruction, files_data, user_api_key, target_model, history_list, target_tools, chat_mode):
            if event['type'] in ['thinking', 'code', 'error']:
                yield json.dumps({"type": event['type'], "content": event['content']}, ensure_ascii=False).encode('utf-8') + b"\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

@app.post("/api/generate-key")
async def get_my_key(request: Request):
    email = get_current_user_email(request)
    if not email: return JSONResponse({"error": "Unauthorized"}, 401)
    user = get_user_by_email(email)
    return {"key": user.get("api_key")} if user else JSONResponse({"error": "Not found"}, 404)

from telegram_bot import notify_contact_form, notify_enterprise_form

@app.post("/api/support/chat")
async def support_chat(request: Request):
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        lang = body.get("lang", "en")
        system_lang_hint = body.get("system_lang", "")

        if not message:
            return JSONResponse({"error": "No message provided"}, 400)

        if lang == "ar":
            system_prompt = (
                "أنت مساعد خدمة عملاء ذكي لموقع Orgteh Infra (orgteh.com). "
                "يجب أن ترد دائماً باللغة العربية فقط مهما كانت لغة السؤال. "
                "موقعنا يوفر بنية تحتية موحدة للوصول إلى نماذج الذكاء الاصطناعي عبر API واحدة، "
                "بكمون منخفض وأسعار تنافسية. النماذج المتاحة: GPT-4o، Claude، Gemini، Llama وغيرها. "
                "الخطط: مجانية (تجريبية)، Pro، Enterprise. "
                "كن مفيداً ومختصراً وودياً. للأسئلة التقنية المعقدة أحل المستخدم لـ orgteh.com/docs"
            )
        else:
            system_prompt = (
                "You are a smart customer support assistant for Orgteh Infra (orgteh.com). "
                "Always reply in English only regardless of the user's language. "
                "Our platform provides unified infrastructure for AI models via a single API, "
                "with low latency and competitive pricing. Available models: GPT-4o, Claude, Gemini, Llama and more. "
                "Plans: Free (trial), Pro, Enterprise. "
                "Be helpful, concise and friendly. For complex technical questions, refer to orgteh.com/docs"
            )

        if system_lang_hint:
            system_prompt = system_lang_hint + " " + system_prompt

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            "max_tokens": 800,
            "stream": True
        }

        async def generate():
            try:
                async for chunk in smart_chat_stream(payload, "support@orgteh.com", is_trial=True):
                    if isinstance(chunk, bytes):
                        text = chunk.decode("utf-8", errors="ignore")
                    else:
                        text = str(chunk)
                    if text.startswith("data: "):
                        text = text[6:]
                    if text.strip() and text.strip() != "[DONE]":
                        try:
                            import json as _json
                            data = _json.loads(text)
                            content = (
                                data.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if content:
                                yield content.encode("utf-8")
                        except Exception:
                            pass
            except Exception as e:
                err_msg = "عذراً، حدث خطأ." if lang == "ar" else "Sorry, an error occurred."
                yield err_msg.encode("utf-8")

        return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

    except Exception as e:
        print(f"[Support Chat] Error: {e}")
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/github/status")
async def github_status(request: Request):
    try:
        connected = is_github_connected(request)
        context = {}
        if connected:
            email = get_current_user_email(request)
            if email:
                try:
                    from database import redis as _redis
                    if _redis:
                        gh_info = _redis.hgetall(f"github:{email}")
                        if gh_info:
                            def _dec(v):
                                return v.decode() if isinstance(v, bytes) else v
                            context = {
                                "login": _dec(gh_info.get(b"login", gh_info.get("login", ""))),
                                "avatar": _dec(gh_info.get(b"avatar", gh_info.get("avatar", "")))
                            }
                except Exception:
                    pass
        return JSONResponse({
            "connected": connected,
            "user": context if connected else None,
            "connect_url": "/auth/github/login"
        })
    except Exception as e:
        return JSONResponse({"connected": False, "error": str(e)}, 500)

@app.post("/api/contact")
async def api_contact(request: Request):
    try:
        body = await request.json()
        name    = body.get("name", "").strip()
        email   = body.get("email", "").strip()
        message = body.get("message", "").strip()

        if not name or not email or not message:
            return JSONResponse({"detail": "جميع الحقول مطلوبة."}, status_code=400)

        sent = await notify_contact_form(name, email, message)
        if sent:
            return JSONResponse({"ok": True})
        else:
            return JSONResponse({"ok": True})
    except Exception as e:
        print(f"[API /api/contact] Error: {e}")
        return JSONResponse({"detail": "خطأ في الخادم."}, status_code=500)

@app.post("/api/enterprise/contact")
async def api_enterprise_contact(request: Request):
    try:
        body = await request.json()
        project_type   = body.get("projectType", "").strip()
        volume         = body.get("volume", "").strip()
        needs          = body.get("needs", "").strip()
        contact_method = body.get("contactMethod", "").strip()
        contact_value  = body.get("contactValue", "").strip()
        description    = body.get("description", "").strip()

        if not project_type or not volume or not needs or not contact_value:
            return JSONResponse({"detail": "يرجى ملء الحقول المطلوبة."}, status_code=400)

        sent = await notify_enterprise_form(
            project_type, volume, needs,
            contact_method, contact_value, description
        )
        if sent:
            return JSONResponse({"ok": True})
        else:
            return JSONResponse({"ok": True})
    except Exception as e:
        print(f"[API /api/enterprise/contact] Error: {e}")
        return JSONResponse({"detail": "خطأ في الخادم."}, status_code=500)


if __name__ == "__main__":
    import os

    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        print("☁️ Vercel detected - Skipping local server (using Vercel Serverless)")
    else:
        try:
            import uvicorn
            port = int(os.environ.get("PORT", 8080))

            print(f"🚀 Starting Orgteh Dev Server on port {port}")
            uvicorn.run(
                "main:app",
                host="0.0.0.0",
                port=port,
                reload=True,
                log_level="info"
            )
        except ImportError:
            print("⚠️ Install uvicorn: pip install uvicorn")