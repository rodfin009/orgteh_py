import os
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.datastructures import MutableHeaders

from services.auth import (
    get_current_user_email, get_auth_context,
    templates, get_template_context,
    router as auth_router,
)
from database import (
    get_user_by_email, get_global_stats,
    hub_save_chat, hub_list_chats, hub_get_chat, hub_delete_chat,
)
from services.subscriptions import get_user_subscription_status
from services.limits import check_premium_tool_access
from services.providers import MODELS_METADATA, HIDDEN_MODELS
from services.request_router import router as chat_router
from customer_service import router as customer_service_router
from tools import router as tools_router
from tools.registry import TOOLS_DB
from code_processor import process_code_merge_stream, CODE_HUB_MODELS_INFO
from services.payments import router as payments_router
from services.widget_service import router as widget_router
from agent.routes import router as agent_v2_router, init_agent_db
from services.admin import router as admin_router, track_page_visit

# ============================================================================
# CONFIGURATION
# ============================================================================

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "super-secret-key-change-in-production")
logging.basicConfig(level=logging.INFO)
_sr_log = logging.getLogger("sr")

# ============================================================================
# OPTIMIZED STATIC FILES
# ============================================================================

class OptimizedStaticFiles(StaticFiles):
    def __init__(self, *args, cache_control="public, max-age=31536000, immutable", **kwargs):
        self.cache_control = cache_control
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith("widget-loader.js"):
            response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
            response.headers["Vary"] = "Accept-Encoding"
        elif any(path.endswith(ext) for ext in ['.css', '.js', '.webp', '.png', '.jpg', '.woff2', '.woff']):
            response.headers["Cache-Control"] = self.cache_control
            response.headers["Vary"] = "Accept-Encoding"
        if path.endswith(('.js', '.css', '.html')):
            response.headers["Vary"] = "Accept-Encoding"
        return response

# ============================================================================
# SECURITY HEADERS MIDDLEWARE  (Pure ASGI — safe for streaming)
# ============================================================================

class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = (
                    "accelerometer=(), camera=(), geolocation=(), "
                    "gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
                )
                headers["Link"] = (
                    "<https://fonts.gstatic.com>; rel=preconnect, "
                    "<https://cdnjs.cloudflare.com>; rel=preconnect, "
                    "<https://fonts.googleapis.com>; rel=preconnect"
                )
                content_type = headers.get("content-type", "")
                if any(x in content_type for x in ["ndjson", "event-stream", "octet-stream", "text/plain"]):
                    headers["X-Accel-Buffering"] = "no"
                    headers["Cache-Control"] = "no-cache, no-transform"
                    headers["Transfer-Encoding"] = "chunked"
            await send(message)

        await self.app(scope, receive, send_with_headers)

# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(title="Orgteh Infra", docs_url=None, redoc_url=None)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware,
    allow_origins=["*", "null"],   # null = srcdoc/blob iframes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False)

BASE_DIR      = Path(__file__).resolve().parent
STATIC_DIR    = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

if not STATIC_DIR.exists():    STATIC_DIR.mkdir()
if not TEMPLATES_DIR.exists(): TEMPLATES_DIR.mkdir()

app.mount("/static", OptimizedStaticFiles(directory=str(STATIC_DIR)), name="static")

# ── تضمين جميع الراوترات ──────────────────────────────────────────────────────
app.include_router(customer_service_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(payments_router)
app.include_router(widget_router)
app.include_router(chat_router)       # ← المحادثات: /api/chat, /api/chat/trial, /v1/chat/completions

# ── تهيئة جدول agent_sessions عند بدء التشغيل ────────────────────────────────
@app.on_event("startup")
async def _startup_init():
    try:
        await init_agent_db()
    except Exception as _e:
        logging.getLogger("startup").warning(f"agent_db init: {_e}")

# ============================================================================
# VISITOR TRACKING MIDDLEWARE
# ============================================================================

@app.middleware("http")
async def visitor_tracking_middleware(request: Request, call_next):
    await track_page_visit(request)
    return await call_next(request)

# ============================================================================
# PREMIUM TOOLS GUARD MIDDLEWARE — المنطق في services/limits.py
# ============================================================================

@app.middleware("http")
async def premium_tools_guard(request: Request, call_next):
    rejection = await check_premium_tool_access(request)
    if rejection is not None:
        return rejection
    return await call_next(request)

# ============================================================================
# HEALTH CHECK & WARM-UP
# ============================================================================

@app.get("/api/health")
async def health_check():
    return JSONResponse({
        "status":      "healthy",
        "timestamp":   datetime.utcnow().isoformat(),
        "cache":       "enabled",
        "compression": "gzip",
    })

@app.get("/api/ready")
async def readiness_check():
    return {"ready": True}

@app.post("/api/sync-db")
async def sync_db_endpoint():
    """مزامنة Redis → TiDB — تُستدعى من cron job."""
    from database import sync_all_usage_to_db
    result = sync_all_usage_to_db()
    return JSONResponse({
        "ok":        result.get("status") == "success",
        "synced":    result.get("synced_users", 0),
        "timestamp": datetime.utcnow().isoformat(),
    })

# ============================================================================
# Static Files Debug
# ============================================================================

@app.get("/debug/static-files")
async def check_static_files():
    file_structure = []
    total_size = 0
    if STATIC_DIR.exists():
        for root, dirs, files in os.walk(str(STATIC_DIR)):
            for name in files:
                full_path = os.path.join(root, name)
                rel_path  = os.path.relpath(full_path, str(BASE_DIR))
                size      = os.path.getsize(full_path)
                total_size += size
                file_structure.append({"path": rel_path, "size_kb": round(size / 1024, 2)})
    return {
        "base_dir":       str(BASE_DIR),
        "static_dir":     str(STATIC_DIR),
        "total_files":    len(file_structure),
        "total_size_mb":  round(total_size / (1024 * 1024), 2),
        "webp_files":     [f for f in file_structure if f["path"].endswith(".webp")],
        "found_files":    file_structure[:20],
    }

# ============================================================================
# ROUTES — الصفحات العامة
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    return RedirectResponse("/en/")

@app.get("/{lang}/", response_class=HTMLResponse)
async def home(request: Request, lang: str):
    return templates.TemplateResponse("index.html", get_template_context(request, lang))

# ─── Accesory / Tools ─────────────────────────────────────────────────────────

@app.get("/accesory", response_class=HTMLResponse)
async def accesory_redirect():
    return RedirectResponse("/en/accesory")

@app.get("/{lang}/accesory", response_class=HTMLResponse)
async def accesory_page(request: Request, lang: str):
    context = get_template_context(request, lang)
    context["tools"]       = list(TOOLS_DB.values())
    context["active_tool"] = None
    return templates.TemplateResponse("tools.html", context)

@app.get("/{lang}/accesory/{tool_id}", response_class=HTMLResponse)
async def accesory_detail_page(request: Request, lang: str, tool_id: str):
    context = get_template_context(request, lang)
    tool    = TOOLS_DB.get(tool_id)
    if not tool:
        return RedirectResponse(f"/{lang}/accesory")
    context["active_tool"] = tool
    context["tools"]       = list(TOOLS_DB.values())
    return templates.TemplateResponse("tools.html", context)

# ─── Cart / Pricing ───────────────────────────────────────────────────────────

@app.get("/cart", response_class=HTMLResponse)
async def cart_redirect():
    return RedirectResponse("/en/cart")

@app.get("/{lang}/cart", response_class=HTMLResponse)
async def cart_page(request: Request, lang: str):
    try:
        context = get_template_context(request, lang)
        if context.get("is_logged_in"):
            try:
                sub_status = get_user_subscription_status(context["user_email"])
                context["current_plan"] = sub_status.get("plan_name", "Free Tier") if sub_status else "Free Tier"
            except Exception:
                context["current_plan"] = "Free Tier"
        else:
            context["current_plan"] = "Free Tier"
        return templates.TemplateResponse("pricing.html", context)
    except Exception:
        return templates.TemplateResponse("pricing.html", {
            "request": request, "lang": lang, "is_logged_in": False,
            "user_email": "", "api_key": "", "current_plan": "Free Tier", "models_metadata": [],
        })

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_redirect():
    return RedirectResponse("/en/cart")

@app.get("/{lang}/pricing", response_class=HTMLResponse)
async def pricing(request: Request, lang: str):
    try:
        context = get_template_context(request, lang)
        if context.get("is_logged_in"):
            try:
                sub_status = get_user_subscription_status(context["user_email"])
                context["current_plan"] = sub_status.get("plan_name", "Free Tier") if sub_status else "Free Tier"
            except Exception:
                context["current_plan"] = "Free Tier"
        else:
            context["current_plan"] = "Free Tier"
        return templates.TemplateResponse("pricing.html", context)
    except Exception:
        return templates.TemplateResponse("pricing.html", {
            "request": request, "lang": lang, "is_logged_in": False,
            "user_email": "", "api_key": "", "current_plan": "Free Tier", "models_metadata": [],
        })

# ─── Contacts / Enterprise / Docs ─────────────────────────────────────────────

@app.get("/contacts", response_class=HTMLResponse)
async def contacts_redirect():
    return RedirectResponse("/en/contacts")

@app.get("/{lang}/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request, lang: str):
    return templates.TemplateResponse("contacts.html", get_template_context(request, lang))

@app.get("/enterprise", response_class=HTMLResponse)
async def ent_redirect():
    return RedirectResponse("/en/enterprise")

@app.get("/{lang}/enterprise", response_class=HTMLResponse)
async def enterprise_page(request: Request, lang: str):
    return templates.TemplateResponse("enterprise.html", get_template_context(request, lang))

@app.get("/docs", response_class=HTMLResponse)
async def docs_redirect():
    return RedirectResponse("/en/docs")

@app.get("/{lang}/docs", response_class=HTMLResponse)
async def docs_page(request: Request, lang: str):
    return templates.TemplateResponse("docs.html", get_template_context(request, lang))

# ─── Policy / Performance ─────────────────────────────────────────────────────

@app.get("/policy", response_class=HTMLResponse)
async def policy_redirect():
    return RedirectResponse("/en/service-policy")

@app.get("/{lang}/policy", response_class=HTMLResponse)
async def policy_old_redirect(request: Request, lang: str):
    return RedirectResponse(f"/{lang}/service-policy")

@app.get("/service-policy", response_class=HTMLResponse)
async def service_policy_redirect():
    return RedirectResponse("/en/service-policy")

@app.get("/{lang}/service-policy", response_class=HTMLResponse)
async def service_policy_page(request: Request, lang: str):
    return templates.TemplateResponse("service-policy.html", get_template_context(request, lang))

@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    stats   = get_global_stats()
    context = get_template_context(request, "en")
    context.update({"stats": stats, "last_update": datetime.utcnow().isoformat(), "active_nodes": 5})
    return templates.TemplateResponse("performance.html", context)

@app.get("/{lang}/performance", response_class=HTMLResponse)
async def performance_page_lang(request: Request, lang: str):
    stats   = get_global_stats()
    context = get_template_context(request, lang)
    context.update({"stats": stats, "last_update": datetime.utcnow().isoformat(), "active_nodes": 5})
    return templates.TemplateResponse("performance.html", context)

# ─── Models ───────────────────────────────────────────────────────────────────

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
    context    = get_template_context(request, lang)
    model_info = next((m for m in MODELS_METADATA if m.get("short_key") == model_key or model_key in m.get("id", "")), None)
    if not model_info:
        return RedirectResponse(f"/{lang}/models")
    context["model"]         = model_info
    context["models"]        = context["models_metadata"]
    context["seo_model_key"] = model_key
    return templates.TemplateResponse("models.html", context)

@app.get("/api/model-description/{model_key}")
async def get_model_description(model_key: str, lang: str = "en"):
    try:
        file_path = STATIC_DIR / "models_translation" / f"{model_key}.html"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return JSONResponse(
                {"html": html_content},
                headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400", "Vary": "Accept-Encoding"},
            )
        return JSONResponse({"error": f"Model description not found for: {model_key}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── Tools / Code Hub ─────────────────────────────────────────────────────────

app.include_router(tools_router, prefix="/api")
app.include_router(tools_router, prefix="/v1")
app.include_router(agent_v2_router)

@app.get("/tools", response_class=HTMLResponse)
async def tools_legacy_redirect():
    return RedirectResponse("/en/accesory")

@app.get("/{lang}/tools", response_class=HTMLResponse)
async def tools_legacy_lang_redirect(lang: str):
    return RedirectResponse(f"/{lang}/accesory")

@app.get("/code-hub", response_class=HTMLResponse)
async def code_hub_redirect():
    return RedirectResponse("/en/code-hub")

@app.get("/{lang}/code-hub", response_class=HTMLResponse)
async def code_hub_page(request: Request, lang: str, mode: str = "classic"):
    context = get_template_context(request, lang)
    context["mode"]     = "standard"
    context["hub_mode"] = mode
    merged_models = []
    for m in MODELS_METADATA:
        if m["id"] in HIDDEN_MODELS:
            continue
        model_entry = m.copy()
        if m["id"] in CODE_HUB_MODELS_INFO:
            model_entry.update(CODE_HUB_MODELS_INFO[m["id"]])
        else:
            model_entry.update({"desc_en": "General AI", "desc_ar": "ذكاء اصطناعي عام", "badge_en": "", "badge_ar": ""})
        merged_models.append(model_entry)
    context["models"] = merged_models
    context["tools"]  = list(TOOLS_DB.values())
    return templates.TemplateResponse("code_hub/index.html", context)

# ============================================================================
# HUB CHAT HISTORY (V1 Classic)
# ============================================================================

@app.post("/api/hub/save-chat")
async def hub_save_chat_endpoint(request: Request):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Login required"}, 401)
    try:
        body       = await request.json()
        session_id = body.get("session_id") or f"v1_{int(__import__('time').time())}"
        title      = body.get("title", "محادثة")[:80]
        history    = body.get("history", [])
        files      = body.get("files", {})
        result     = hub_save_chat(email, session_id, title, history, files)
        if not result.get("ok"):
            return JSONResponse({"error": result.get("error", "Unknown error")}, 500)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/hub/chats")
async def hub_list_chats_endpoint(request: Request):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"chats": []})
    try:
        return JSONResponse({"chats": hub_list_chats(email)})
    except Exception as e:
        return JSONResponse({"chats": [], "error": str(e)})

@app.get("/api/hub/chat/{session_id}")
async def hub_get_chat_endpoint(request: Request, session_id: str):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Login required"}, 401)
    try:
        data = hub_get_chat(email, session_id)
        if not data:
            return JSONResponse({"error": "Not found"}, 404)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.delete("/api/hub/chat/{session_id}")
async def hub_delete_chat_endpoint(request: Request, session_id: str):
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Login required"}, 401)
    try:
        hub_delete_chat(email, session_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

# ============================================================================
# CODE PROCESSOR
# ============================================================================

@app.post("/api/process-code")
async def process_code_endpoint(
    request:      Request,
    instruction:  str               = Form(...),
    target_model: str               = Form("deepseek-ai/deepseek-v3.2"),
    target_tools: str               = Form(""),
    chat_history: str               = Form("[]"),
    chat_mode:    str               = Form("build"),
    files:        list[UploadFile]  = File(default=[]),
):
    if target_model in HIDDEN_MODELS:
        return JSONResponse({"error": "Target model unavailable"}, 400)

    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Login required"}, 401)

    user         = get_user_by_email(email)
    user_api_key = user.get("api_key", "YOUR_API_KEY")

    try:
        history_list = json.loads(chat_history)
    except Exception:
        history_list = []

    files_data = []
    if files:
        for f in files:
            if f.filename:
                try:
                    files_data.append({"name": f.filename, "content": (await f.read()).decode("utf-8")})
                except Exception:
                    pass

    from telegram_bot import schedule_log_v1
    await schedule_log_v1(
        email       = email,
        instruction = instruction,
        chat_mode   = chat_mode,
        model_id    = target_model,
        files_count = len(files_data),
    )

    async def event_generator():
        async for event in process_code_merge_stream(instruction, files_data, user_api_key, target_model, history_list, target_tools, chat_mode):
            if event["type"] in ["thinking", "code", "error"]:
                yield json.dumps({"type": event["type"], "content": event["content"]}, ensure_ascii=False).encode("utf-8") + b"\n"
                await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform", "Content-Encoding": "identity"},
    )

# ============================================================================
# SUPPORT & CONTACT
# ============================================================================

from telegram_bot import handle_contact_form, handle_enterprise_form



@app.post("/api/contact")
async def api_contact(request: Request):
    body   = await request.json()
    result = await handle_contact_form(body)
    return JSONResponse({"ok": True} if result["ok"] else {"detail": result["detail"]},
                        status_code=result["status"])


@app.post("/api/enterprise/contact")
async def api_enterprise_contact(request: Request):
    body   = await request.json()
    result = await handle_enterprise_form(body)
    return JSONResponse({"ok": True} if result["ok"] else {"detail": result["detail"]},
                        status_code=result["status"])

# ─── SEO ──────────────────────────────────────────────────────────────────────

@app.get("/sitemap.xml", include_in_schema=False)
async def get_sitemap():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    urls = [{"loc": "https://orgteh.com/", "changefreq": "daily", "priority": "1.0"}]
    added_locs = {"https://orgteh.com/"}

    for route in app.routes:
        if hasattr(route, "methods") and "GET" in route.methods:
            path = route.path
            if "{lang}" in path:
                paths_to_add = []
                if "{tab}" in path:
                    for tab in ["account", "integrations", "notifications", "billing", "security"]:
                        paths_to_add.append(path.replace("{tab}", tab))
                elif "{model_key}" in path:
                    for m in MODELS_METADATA:
                        short_key = m.get("short_key")
                        if short_key:
                            paths_to_add.append(path.replace("{model_key}", short_key))
                elif "{tool_id}" in path:
                    for tid in TOOLS_DB.keys():
                        paths_to_add.append(path.replace("{tool_id}", tid))
                elif "{" not in path.replace("{lang}", ""):
                    paths_to_add.append(path)

                for p in paths_to_add:
                    for lang in ["ar", "en"]:
                        final_path = p.replace("{lang}", lang)
                        loc = f"https://orgteh.com{final_path}"
                        if loc not in added_locs:
                            urls.append({
                                "loc":        loc,
                                "changefreq": "weekly" if "models" in loc or "accesory" in loc else "monthly",
                                "priority":   "0.9" if "models" in loc else "0.8",
                            })
                            added_locs.add(loc)

    xml_content  = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        xml_content += f'  <url>\n    <loc>{url["loc"]}</loc>\n    <lastmod>{today}</lastmod>\n'
        xml_content += f'    <changefreq>{url["changefreq"]}</changefreq>\n    <priority>{url["priority"]}</priority>\n  </url>\n'
    xml_content += '</urlset>'
    return Response(content=xml_content, media_type="application/xml")


@app.get("/robots.txt", include_in_schema=False)
async def get_robots_txt():
    robots_path = BASE_DIR / "robots.txt"
    if robots_path.exists():
        return FileResponse(str(robots_path), media_type="text/plain")
    return Response(content="User-agent: *\nAllow: /\n", media_type="text/plain")

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        print("☁️ Vercel detected - Using Serverless")
    else:
        try:
            import uvicorn
            port = int(os.environ.get("PORT", 5000))
            print(f"🚀 Starting Orgteh Dev Server on port {port}")
            uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="info")
        except ImportError:
            print("⚠️ Install uvicorn: pip install uvicorn")
