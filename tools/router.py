# tools/router.py
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import json

# استيراد تعريفات الأدوات
from tools.registry import TOOLS_DB
# استيراد المحركات الفعلية
from tools import rss_engine
from tools import nvidia_engine

router = APIRouter()

@router.get("/tools")
async def get_tools():
    """Returns the list of available tools definitions."""
    return list(TOOLS_DB.values())

@router.post("/tools/execute/{tool_id}")
async def execute_tool_endpoint(
    tool_id: str,
    request: Request,
    file_input: UploadFile = File(None)
):
    """
    Unified endpoint to execute any tool.
    Handles both JSON bodies, Form Data, and File Uploads.
    """
    if tool_id not in TOOLS_DB:
        return JSONResponse({"error": "Tool not found"}, status_code=404)

    # 1. Parse Parameters (Supports JSON, Form, and Query)
    params = {}

    # Try getting form data first (for files/multipart)
    try:
        form_data = await request.form()
        params.update(form_data)
    except:
        pass

    # Try getting JSON body if no form data or strictly JSON
    if not params:
        try:
            json_body = await request.json()
            params.update(json_body)
        except:
            pass

    # 2. Route Logic based on Tool ID
    try:
        # --- A. FINANCIAL NEWS ---
        if tool_id == "orgteh-finance-rss":
            return await rss_engine.execute_hybrid_news(
                category="finance",
                limit=int(params.get("limit", 5)),
                lang=params.get("lang", "en"),
                time_filter=params.get("time_filter", "1d"),
                scrape_content=params.get("scrape_content", "true")
            )

        # --- B. GENERAL NEWS ---
        elif tool_id == "orgteh-news-general":
            return await rss_engine.execute_hybrid_news(
                category="general",
                limit=int(params.get("limit", 3)),
                lang=params.get("lang", "en"),
                time_filter=params.get("time_filter", "1d"),
                scrape_content=params.get("scrape_content", "true")
            )

        # --- C. VISION / OCR ---
        elif tool_id == "orgteh-vision-ocr":
            if not file_input:
                return JSONResponse({"error": "File input required for OCR"}, 400)
            return await nvidia_engine.execute_ocr(file_input)

        # --- D. SEMANTIC EMBEDDING ---
        elif tool_id == "orgteh-semantic-embed":
            text = params.get("text_input")
            if not text:
                return JSONResponse({"error": "text_input is required"}, 400)
            return await nvidia_engine.execute_embedding(
                text_input=text, 
                truncate=params.get("truncate", "NONE")
            )

        else:
            return JSONResponse({"error": "Tool execution logic not implemented"}, 501)

    except Exception as e:
        print(f"Tool Execution Error: {str(e)}")
        return JSONResponse({"error": str(e)}, 500)