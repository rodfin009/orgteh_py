# tools/router.py
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse

from tools.registry import TOOLS_DB
from tools import rss_engine
from tools import nvidia_engine
from tools import scraper_engine

router = APIRouter()


@router.get("/tools")
async def get_tools():
    return list(TOOLS_DB.values())


@router.post("/tools/execute/{tool_id}")
async def execute_tool_endpoint(
    tool_id: str,
    request: Request,
    file_input: UploadFile = File(None),
):
    if tool_id not in TOOLS_DB:
        return JSONResponse({"error": "Tool not found"}, status_code=404)

    params = {}
    try:
        form_data = await request.form()
        params.update(form_data)
    except Exception:
        pass

    if not params:
        try:
            params.update(await request.json())
        except Exception:
            pass

    try:
        if tool_id == "orgteh-finance-rss":
            return await rss_engine.execute_hybrid_news(
                category="finance",
                limit=int(params.get("limit", 5)),
                lang=params.get("lang", "en"),
                time_filter=params.get("time_filter", "1d"),
                scrape_content=params.get("scrape_content", "true"),
            )

        elif tool_id == "orgteh-news-general":
            return await rss_engine.execute_hybrid_news(
                category="general",
                limit=int(params.get("limit", 3)),
                lang=params.get("lang", "en"),
                time_filter=params.get("time_filter", "1d"),
                scrape_content=params.get("scrape_content", "true"),
            )

        elif tool_id == "orgteh-vision-ocr":
            if not file_input:
                return JSONResponse({"error": "file_input is required"}, status_code=400)
            return await nvidia_engine.execute_ocr(file_input)

        elif tool_id == "orgteh-semantic-embed":
            text = params.get("text_input")
            if not text:
                return JSONResponse({"error": "text_input is required"}, status_code=400)
            return await nvidia_engine.execute_embedding(
                text_input=text,
                truncate=params.get("truncate", "NONE"),
            )

        elif tool_id == "orgteh-web-scraper":
            url = params.get("url")
            if not url:
                return JSONResponse({"error": "url is required"}, status_code=400)
            return await scraper_engine.execute_scrape(
                url=url,
                mode=params.get("mode", "smart"),
                extract=params.get("extract", "all"),
                timeout=int(params.get("timeout", 15)),
            )

        else:
            return JSONResponse(
                {"error": "Tool execution logic not implemented"},
                status_code=501,
            )

    except Exception as e:
        print(f"[Tool Error] {tool_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
