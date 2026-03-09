"""
agent/executor.py
─────────────────────────────────────────────────────────────────────────────
محرك التنفيذ الآمن للكود – يدعم Python, Node.js, HTML/CSS/JS
يعمل داخل بيئة معزولة مع حد أقصى للوقت وحماية من الأوامر الضارة
"""

import asyncio
import subprocess
import tempfile
import os
import sys
import json
import re
import time
from pathlib import Path
from typing import Optional

# ─── الحد الأقصى للوقت (ثوانٍ) لكل نوع تنفيذ ─────────────────────────────
EXEC_TIMEOUTS = {
    "python":     8,
    "javascript": 6,
    "typescript": 8,
    "bash":       5,
}

# ─── قائمة الأوامر والاستيرادات المحظورة ─────────────────────────────────
PYTHON_BLOCKED = [
    r"\bos\.system\b", r"\bsubprocess\b", r"\beval\(", r"\bexec\(",
    r"__import__", r"\bopen\s*\(.*['\"]w['\"]", r"\bshutil\.rmtree\b",
    r"\bos\.remove\b", r"\bos\.unlink\b", r"socket\.", r"requests\.",
    r"urllib\.request", r"httpx\.", r"aiohttp\."
]

NODE_BLOCKED = [
    r"require\s*\(\s*['\"]child_process",
    r"require\s*\(\s*['\"]fs",
    r"require\s*\(\s*['\"]os",
    r"process\.exit",
    r"eval\(",
]


def _is_safe_python(code: str) -> tuple[bool, str]:
    """يتحقق من أمان كود Python."""
    for pattern in PYTHON_BLOCKED:
        if re.search(pattern, code):
            return False, f"Blocked pattern detected: {pattern}"
    return True, ""


def _is_safe_node(code: str) -> tuple[bool, str]:
    """يتحقق من أمان كود JavaScript/Node."""
    for pattern in NODE_BLOCKED:
        if re.search(pattern, code):
            return False, f"Blocked pattern detected: {pattern}"
    return True, ""


async def execute_python(code: str, input_data: str = "", timeout: int = None) -> dict:
    """
    ينفذ كود Python في بيئة معزولة.
    Returns: { stdout, stderr, exit_code, duration_ms, safe }
    """
    timeout = timeout or EXEC_TIMEOUTS["python"]
    safe, reason = _is_safe_python(code)
    if not safe:
        return {"stdout": "", "stderr": f"[SECURITY] Code blocked: {reason}", "exit_code": -1, "duration_ms": 0, "safe": False}

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(code)
        tmp_path = f.name

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, tmp_path,
            stdin=asyncio.subprocess.PIPE if input_data else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        stdin_bytes = input_data.encode() if input_data else None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"stdout": "", "stderr": f"[TIMEOUT] Execution exceeded {timeout}s", "exit_code": -2, "duration_ms": int((time.monotonic() - start) * 1000), "safe": True}
        return {
            "stdout": stdout.decode("utf-8", errors="replace")[:8000],
            "stderr": stderr.decode("utf-8", errors="replace")[:4000],
            "exit_code": proc.returncode,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "safe": True
        }
    finally:
        try: os.unlink(tmp_path)
        except: pass


async def execute_node(code: str, timeout: int = None) -> dict:
    """
    ينفذ كود JavaScript/Node.js في بيئة معزولة.
    """
    timeout = timeout or EXEC_TIMEOUTS["javascript"]
    safe, reason = _is_safe_node(code)
    if not safe:
        return {"stdout": "", "stderr": f"[SECURITY] Code blocked: {reason}", "exit_code": -1, "duration_ms": 0, "safe": False}

    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(code)
        tmp_path = f.name

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"stdout": "", "stderr": f"[TIMEOUT] Execution exceeded {timeout}s", "exit_code": -2, "duration_ms": int((time.monotonic() - start) * 1000), "safe": True}
        return {
            "stdout": stdout.decode("utf-8", errors="replace")[:8000],
            "stderr": stderr.decode("utf-8", errors="replace")[:4000],
            "exit_code": proc.returncode,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "safe": True
        }
    finally:
        try: os.unlink(tmp_path)
        except: pass


async def execute_project(files: dict, entry_point: str = "main.py", language: str = "python") -> dict:
    """
    ينفذ مشروع متعدد الملفات في مجلد مؤقت.
    files: {"filename": "content", ...}
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # كتابة جميع الملفات
        for fname, content in files.items():
            fpath = Path(tmpdir) / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding='utf-8')

        entry_path = Path(tmpdir) / entry_point

        if language == "python":
            safe, reason = _is_safe_python("\n".join(files.values()))
            if not safe:
                return {"stdout": "", "stderr": f"[SECURITY] {reason}", "exit_code": -1}
            cmd = [sys.executable, str(entry_path)]
        elif language in ("javascript", "node"):
            safe, reason = _is_safe_node("\n".join(files.values()))
            if not safe:
                return {"stdout": "", "stderr": f"[SECURITY] {reason}", "exit_code": -1}
            cmd = ["node", str(entry_path)]
        else:
            return {"stdout": "", "stderr": f"Language '{language}' not supported for server execution", "exit_code": -1}

        timeout = EXEC_TIMEOUTS.get(language, 8)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return {"stdout": "", "stderr": f"[TIMEOUT] {timeout}s exceeded", "exit_code": -2, "duration_ms": int((time.monotonic() - start) * 1000)}
            return {
                "stdout": stdout.decode("utf-8", errors="replace")[:8000],
                "stderr": stderr.decode("utf-8", errors="replace")[:4000],
                "exit_code": proc.returncode,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        except FileNotFoundError:
            return {"stdout": "", "stderr": f"Runtime not found: {cmd[0]}", "exit_code": -1}


def check_html_syntax(html_content: str) -> dict:
    """
    يتحقق من صحة HTML الأساسية.
    Returns: { valid, errors }
    """
    errors = []
    # التحقق من الوسوم الأساسية
    required = ["<!DOCTYPE", "<html", "<head", "<body"]
    for tag in required:
        if tag.lower() not in html_content.lower():
            errors.append(f"Missing required element: {tag}")

    # التحقق من وسوم غير مغلقة (مبسط)
    open_tags = re.findall(r'<([a-zA-Z][a-zA-Z0-9]*)\b[^/]*?(?<!/)>', html_content)
    close_tags = re.findall(r'</([a-zA-Z][a-zA-Z0-9]*)>', html_content)
    void_elements = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    for tag in open_tags:
        tag_lower = tag.lower()
        if tag_lower not in void_elements:
            if open_tags.count(tag) > close_tags.count(tag) + 2:
                errors.append(f"Possible unclosed tag: <{tag_lower}>")

    return {"valid": len(errors) == 0, "errors": errors[:10]}


def check_python_syntax(code: str) -> dict:
    """يتحقق من صحة Python syntax."""
    try:
        import ast
        ast.parse(code)
        return {"valid": True, "errors": []}
    except SyntaxError as e:
        return {"valid": False, "errors": [f"SyntaxError at line {e.lineno}: {e.msg}"]}


def check_js_basic(code: str) -> dict:
    """فحص أساسي لـ JavaScript (بدون runtime)."""
    errors = []
    # فحص الأقواس المتوازنة
    if code.count('{') != code.count('}'):
        errors.append(f"Mismatched braces: {code.count('{')} open vs {code.count('}')} close")
    if code.count('(') != code.count(')'):
        errors.append(f"Mismatched parentheses: {code.count('(')} open vs {code.count(')')} close")
    if code.count('[') != code.count(']'):
        errors.append(f"Mismatched brackets: {code.count('[')} open vs {code.count(']')} close")
    return {"valid": len(errors) == 0, "errors": errors}
