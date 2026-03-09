import os
import json
import logging
import time
from datetime import datetime
from typing import Optional, Dict

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from agent.executor import (
    execute_python, execute_node, execute_project,
    check_html_syntax, check_python_syntax, check_js_basic
)
from agent.tg_store import (
    save_session_to_telegram,
    load_session_from_telegram,
    notify_session_complete,
    check_agent_bot_status,
)

try:
    from services.providers import NVIDIA_API_KEY, NVIDIA_BASE_URL
    from services.auth import get_current_user_email
except ImportError:
    NVIDIA_API_KEY  = os.environ.get("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    async def get_current_user_email(request): return None

router = APIRouter(prefix="/api/agent/v2", tags=["agent-v2"])
logger = logging.getLogger("agent_v2")


# ─── نماذج Pydantic ───────────────────────────────────────────────────────

class StreamRequest(BaseModel):
    messages:    list
    model:       str   = "minimaxai/minimax-m2.5"
    temperature: float = 0.1
    max_tokens:  int   = 8192
    session_id:  Optional[str] = None
    phase:       Optional[str] = None
    provider:    Optional[str] = None   # 'nvidia' | None (auto)

class ExecuteRequest(BaseModel):
    code:       str
    language:   str = "python"
    input_data: str = ""
    timeout:    Optional[int] = None
    session_id: Optional[str] = None

class ProjectExecuteRequest(BaseModel):
    files:       Dict[str, str]
    entry_point: str = "main.py"
    language:    str = "python"
    session_id:  Optional[str] = None

class CheckSyntaxRequest(BaseModel):
    code:     str
    language: str

class SaveStateRequest(BaseModel):
    session_id:         str
    title:              str  = ""
    status:             str  = "running"
    plan:               Optional[Dict] = None
    files:              Optional[Dict[str, str]] = None
    history:            Optional[list] = None
    current_task_index: int  = 0
    total_tasks:        int  = 0
    metadata:           Optional[Dict] = None


# ══════════════════════════════════════════════════════════════════════════════
#  [1] بروكسي البث
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/stream")
async def agent_stream(req: Request, body: StreamRequest):
    """يُعيد توجيه طلبات البث من المتصفح إلى NVIDIA API."""

    t_start = time.time()
    logger.info(f"[stream] phase={body.phase} model={body.model} provider={body.provider} msgs={len(body.messages)}")

    phase_params = {
        "plan":    {"temperature": 0.05, "top_p": 0.3, "max_tokens": 4096},
        "execute": {"temperature": 0.10, "top_p": 0.5, "max_tokens": 16384},
        "fix":     {"temperature": 0.05, "top_p": 0.3, "max_tokens": 16384},
        "test":    {"temperature": 0.02, "top_p": 0.2, "max_tokens": 2048},
        "chat":    {"temperature": 0.20, "top_p": 0.7, "max_tokens": 4096},
    }
    params = phase_params.get(body.phase or "execute", {})

    payload = {
        "model":       body.model,
        "messages":    body.messages,
        "temperature": params.get("temperature", body.temperature),
        "top_p":       params.get("top_p", 0.5),
        "max_tokens":  params.get("max_tokens", body.max_tokens),
        "stream":      True,
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
    }

    async def stream_generator():
        total_chars = 0
        t_first = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=10.0)
        ) as client:
            try:
                async with client.stream(
                    "POST", f"{NVIDIA_BASE_URL}/chat/completions",
                    json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        logger.error(f"[stream] API error {resp.status_code}: {err.decode()[:200]}")
                        yield f"data: {json.dumps({'type': 'error', 'content': f'API {resp.status_code}: {err.decode()[:100]}'})}\n\n"
                        return

                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            elapsed = time.time() - t_start
                            ttft = (t_first - t_start) if t_first else None
                            logger.info(f"[stream] ✅ done | model={body.model} phase={body.phase} chars={total_chars} total={elapsed:.2f}s ttft={f'{ttft:.2f}s' if ttft else 'n/a'}")
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            data    = json.loads(raw)
                            choices = data.get("choices", [])
                            if not choices: continue
                            delta   = choices[0].get("delta", {})

                            reasoning = delta.get("reasoning_content") or \
                                        (delta.get("model_extra") or {}).get("reasoning_content")
                            if reasoning:
                                yield f"data: {json.dumps({'type': 'thinking', 'content': reasoning})}\n\n"

                            content = delta.get("content", "")
                            if content:
                                if t_first is None:
                                    t_first = time.time()
                                    logger.info(f"[stream] first token | model={body.model} phase={body.phase} ttft={t_first - t_start:.2f}s")
                                total_chars += len(content)
                                yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                        except json.JSONDecodeError:
                            continue

            except httpx.TimeoutException:
                logger.warning(f"[stream] timeout | model={body.model} phase={body.phase} elapsed={time.time()-t_start:.1f}s")
                yield f"data: {json.dumps({'type': 'error', 'content': 'Connection timeout'})}\n\n"
            except Exception as e:
                logger.error(f"[stream] exception | {e}")
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)[:200]})}\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ══════════════════════════════════════════════════════════════════════════════
#  [2] تنفيذ كود مفرد
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/execute")
async def execute_code(req: Request, body: ExecuteRequest):
    lang = body.language.lower()
    if lang == "python":
        result = await execute_python(body.code, body.input_data, body.timeout)
    elif lang in ("javascript", "node", "nodejs"):
        result = await execute_node(body.code, body.timeout)
    else:
        return JSONResponse({"error": f"Language '{lang}' not supported. Use: python, javascript"}, status_code=400)

    result["passed"] = (result["exit_code"] == 0
                        and not result.get("stderr", "").startswith("[TIMEOUT]"))
    return JSONResponse(result)


# ══════════════════════════════════════════════════════════════════════════════
#  [3] تنفيذ مشروع متعدد الملفات
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/execute-project")
async def execute_project_endpoint(req: Request, body: ProjectExecuteRequest):
    if not body.files:
        return JSONResponse({"error": "No files provided"}, status_code=400)
    if len(body.files) > 20:
        return JSONResponse({"error": "Too many files (max 20)"}, status_code=400)

    result = await execute_project(body.files, body.entry_point, body.language)
    result["passed"] = result.get("exit_code", -1) == 0
    return JSONResponse(result)


# ══════════════════════════════════════════════════════════════════════════════
#  [4] فحص الـ syntax
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/check-syntax")
async def check_syntax(body: CheckSyntaxRequest):
    lang = body.language.lower()
    if lang == "html":
        result = check_html_syntax(body.code)
    elif lang == "python":
        result = check_python_syntax(body.code)
    elif lang in ("javascript", "js", "typescript", "ts"):
        result = check_js_basic(body.code)
    else:
        result = {"valid": True, "errors": [], "note": f"No checker for '{lang}'"}
    return JSONResponse(result)


# ══════════════════════════════════════════════════════════════════════════════
#  [5] حفظ الحالة — تلجرام أولاً ثم Redis كفهرس
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/save-state")
async def save_state(req: Request, body: SaveStateRequest):
    """
    يحفظ الجلسة في تلجرام (ملف JSON مضغوط) ويُحدّث Redis بالمرجع الصغير.

    ما يُحفظ في تلجرام: البيانات الكاملة (plan + files + history + metadata)
    ما يُحفظ في Redis:  {file_id, title, status, updated_at} فقط — بايتات قليلة
    """
    email = get_current_user_email(req)
    if not email:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    # ─── جلب message_id القديم للحذف قبل الإرسال الجديد ──────────────────
    old_msg_id = None
    try:
        from database import redis as _redis
        if _redis:
            ref_key = f"agent_tg_ref:{email}:{body.session_id}"
            raw     = _redis.get(ref_key)
            if raw:
                old_msg_id = json.loads(raw).get("message_id")
    except Exception:
        pass

    # ─── الحفظ في تلجرام ──────────────────────────────────────────────────
    tg_result = await save_session_to_telegram(
        session_id      = body.session_id,
        user_email      = email,
        title           = body.title or "Untitled",
        status          = body.status,
        plan            = body.plan,
        files           = body.files,
        history         = body.history,
        current_task    = body.current_task_index,
        total_tasks     = body.total_tasks,
        metadata        = body.metadata,
        existing_msg_id = old_msg_id,
    )

    tg_ok = tg_result is not None

    # ─── تحديث Redis بالمرجع الصغير ──────────────────────────────────────
    if tg_ok:
        ref_data = {
            "session_id":   body.session_id,
            "title":        body.title or "Untitled",
            "status":       body.status,
            "file_id":      tg_result["file_id"],     # المفتاح للاسترجاع
            "message_id":   tg_result["message_id"],  # للحذف عند التحديث
            "file_count":   len(body.files or {}),
            "current_task": body.current_task_index,
            "total_tasks":  body.total_tasks,
            "updated_at":   datetime.utcnow().isoformat(),
        }
        try:
            from database import redis as _redis
            if _redis:
                ref_key = f"agent_tg_ref:{email}:{body.session_id}"
                _redis.setex(ref_key, 86400 * 30, json.dumps(ref_data))   # 30 يوم
                # فهرس مرتب حسب الوقت
                idx_key = f"agent_tg_idx:{email}"
                _redis.zadd(idx_key, {body.session_id: time.time()})
                _redis.expire(idx_key, 86400 * 30)
        except Exception as e:
            logger.warning(f"Redis update failed: {e}")

    # ─── إشعار الاكتمال ───────────────────────────────────────────────────
    if tg_ok and body.status == "completed":
        try:
            await notify_session_complete(
                session_id = body.session_id,
                user_email = email,
                title      = body.title or "Untitled",
                file_count = len(body.files or {}),
                task_count = body.total_tasks,
            )
        except Exception:
            pass

    return JSONResponse({
        "status":     "saved" if tg_ok else "redis_only",
        "session_id": body.session_id,
        "storage":    "telegram+redis" if tg_ok else "redis_only",
        "file_id":    tg_result["file_id"] if tg_ok else None,
        "file_size":  f"{tg_result['file_size'] / 1024:.1f} KB" if tg_ok else None,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  [6] قائمة جلسات المستخدم (Redis فقط — لحظية)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def list_sessions(req: Request, limit: int = 15):
    """
    قائمة آخر N جلسة مرتبة من الأحدث للأقدم.
    البيانات من Redis فقط (فهرس خفيف).
    لا نحتاج الاتصال بتلجرام هنا — سريع جداً.
    """
    email = get_current_user_email(req)
    if not email:
        return JSONResponse({"sessions": []})

    sessions = []
    try:
        from database import redis as _redis
        if _redis:
            idx_key     = f"agent_tg_idx:{email}"
            session_ids = _redis.zrevrange(idx_key, 0, limit - 1)

            for sid in session_ids:
                sid_str = sid.decode() if isinstance(sid, bytes) else sid
                ref_key = f"agent_tg_ref:{email}:{sid_str}"
                raw     = _redis.get(ref_key)
                if raw:
                    ref = json.loads(raw)
                    sessions.append({
                        "session_id":   ref.get("session_id"),
                        "title":        ref.get("title", "Untitled"),
                        "status":       ref.get("status", "unknown"),
                        "file_count":   ref.get("file_count", 0),
                        "current_task": ref.get("current_task", 0),
                        "total_tasks":  ref.get("total_tasks", 0),
                        "updated_at":   ref.get("updated_at"),
                        "has_data":     bool(ref.get("file_id")),
                    })
    except Exception as e:
        logger.warning(f"Sessions list error: {e}")

    return JSONResponse({"sessions": sessions})


# ══════════════════════════════════════════════════════════════════════════════
#  [7] استرجاع جلسة كاملة (من تلجرام)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/session/{session_id}")
async def get_session(req: Request, session_id: str):
    """
    الخطوة 1: Redis → file_id (مللي ثانية)
    الخطوة 2: Telegram getFile → رابط تحميل
    الخطوة 3: تحميل الملف المضغوط + فك ضغطه
    الخطوة 4: إعادة البيانات كاملة للواجهة
    """
    email = get_current_user_email(req)
    if not email:
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    # ─── الخطوة 1: Redis ─────────────────────────────────────────────────
    file_id = None
    try:
        from database import redis as _redis
        if _redis:
            ref_key = f"agent_tg_ref:{email}:{session_id}"
            raw     = _redis.get(ref_key)
            if raw:
                file_id = json.loads(raw).get("file_id")
    except Exception as e:
        logger.warning(f"Redis ref fetch: {e}")

    if not file_id:
        return JSONResponse(
            {"error": "Session reference not found. It may have expired (30 days)."},
            status_code=404
        )

    # ─── الخطوات 2-4: Telegram ────────────────────────────────────────────
    data = await load_session_from_telegram(file_id)
    if not data:
        return JSONResponse(
            {"error": "Failed to load session from Telegram. The file may have expired."},
            status_code=500
        )

    return JSONResponse(data)


# ══════════════════════════════════════════════════════════════════════════════
#  [8] حالة بوت التخزين
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/bot-status")
async def bot_status(req: Request):
    """يتحقق من حالة بوت تلجرام الخاص بتخزين جلسات الوكيل."""
    status = await check_agent_bot_status()
    return JSONResponse(status)


# ══════════════════════════════════════════════════════════════════════════════
#  [9] تهيئة — لا جداول مطلوبة
# ══════════════════════════════════════════════════════════════════════════════

async def init_agent_db():
    """
    في هذا النظام لا توجد جداول DB.
    تلجرام = قاعدة البيانات، Redis = الفهرس.
    هذه الدالة تتحقق فقط من أن البوت يعمل.
    """
    status = await check_agent_bot_status()
    if status.get("ok"):
        logger.info(f"✅ Agent Bot ready: {status.get('username')} | owner: {status.get('owner_id')}")
    else:
        logger.warning(f"⚠️  Agent Bot not configured: {status.get('error')}")
        logger.warning("   → Add AGENT_TG_BOT_TOKEN to environment variables")
        logger.warning("   → Sessions will fail silently until configured")
