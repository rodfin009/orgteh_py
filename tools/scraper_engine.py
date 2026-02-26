# tools/scraper_engine.py
"""
Orgteh Web Extractor Engine
"""

import asyncio
import re
import time
from urllib.parse import urlparse, urljoin
from datetime import datetime
from fastapi.responses import JSONResponse


# ─── Fetch Layer ─────────────────────────────────────────────────────────────

async def _fetch(url: str, mode: str, timeout: int) -> tuple[int, str]:
    """
    Fetch page HTML.
    mode="smart"   → Scrapling with auto fallback to httpx
    mode="stealth" → Scrapling with stealthy headers
    """
    # ── Try Scrapling first ───────────────────────────────────────────────────
    try:
        from scrapling.fetchers import Fetcher
        fetcher = Fetcher(auto_match=True)
        loop    = asyncio.get_event_loop()
        page    = await loop.run_in_executor(
            None,
            lambda: fetcher.get(
                url,
                timeout=timeout,
                stealthy_headers=(mode == "stealth"),
            )
        )
        html = page.content if hasattr(page, "content") else str(page)
        if html and len(html) > 200:
            return 200, html
    except Exception:
        pass

    # ── Fallback: plain httpx ─────────────────────────────────────────────────
    import httpx
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "DNT":             "1",
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        verify=False,
    ) as client:
        r = await client.get(url, headers=headers)
        return r.status_code, r.text


# ─── Parse & Extract ──────────────────────────────────────────────────────────

def _parse(html: str, base_url: str, extract: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "iframe",
                     "nav", "footer", "aside", "form"]):
        tag.decompose()

    result = {}

    # ── Metadata ──────────────────────────────────────────────────────────────
    if extract in ("all", "metadata"):
        title = soup.title.string.strip() if soup.title else ""
        desc  = ""
        og    = {}
        for meta in soup.find_all("meta"):
            name    = (meta.get("name") or meta.get("property") or "").lower()
            content = meta.get("content", "")
            if name == "description":
                desc = content
            elif name.startswith("og:"):
                og[name[3:]] = content
        result["metadata"] = {"title": title, "description": desc, "og": og}

    # ── Clean Text ────────────────────────────────────────────────────────────
    if extract in ("all", "text", "markdown"):
        clean = ""
        try:
            from readability import Document
            rb    = BeautifulSoup(Document(html).summary(), "lxml")
            clean = rb.get_text(separator="\n", strip=True)
        except Exception:
            body  = soup.find("body") or soup
            clean = body.get_text(separator="\n", strip=True)
        result["text"] = re.sub(r"\n{3,}", "\n\n", clean).strip()[:8000]

    # ── Markdown ──────────────────────────────────────────────────────────────
    if extract in ("all", "markdown"):
        try:
            import markdownify
            md = markdownify.markdownify(
                html, heading_style="ATX",
                strip=["script", "style", "nav", "footer"]
            )
            result["markdown"] = re.sub(r"\n{3,}", "\n\n", md).strip()[:8000]
        except ImportError:
            result["markdown"] = result.get("text", "")

    # ── Links ─────────────────────────────────────────────────────────────────
    if extract in ("all", "links"):
        links, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:")):
                continue
            full = urljoin(base_url, href)
            if full not in seen:
                seen.add(full)
                links.append({"text": a.get_text(strip=True)[:100], "url": full})
        result["links"] = links[:50]

    # ── Images ────────────────────────────────────────────────────────────────
    if extract in ("all", "images"):
        images, seen = [], set()
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src or src in seen:
                continue
            full = urljoin(base_url, src)
            seen.add(full)
            images.append({"src": full, "alt": img.get("alt", "").strip()})
        result["images"] = images[:30]

    # ── Headings ──────────────────────────────────────────────────────────────
    if extract == "all":
        result["headings"] = [
            {"level": t.name, "text": t.get_text(strip=True)[:200]}
            for t in soup.find_all(["h1", "h2", "h3"])
            if t.get_text(strip=True)
        ][:20]

    return result


# ─── Public API ───────────────────────────────────────────────────────────────

async def execute_scrape(
    url: str,
    mode: str = "smart",
    extract: str = "all",
    timeout: int = 15,
    js_render: bool = False,   # ignored — kept for API compatibility
) -> JSONResponse:

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return JSONResponse(
                {"error": "Invalid URL — must start with http:// or https://"},
                status_code=400,
            )
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return JSONResponse({"error": "Malformed URL"}, status_code=400)

    start = time.time()
    try:
        status, html = await _fetch(url, mode, timeout)
        if not html or len(html) < 100:
            return JSONResponse(
                {"error": f"Empty response (HTTP {status})"},
                status_code=422,
            )

        data = _parse(html, base_url, extract)

        return JSONResponse({
            "status":               "success",
            "url":                  url,
            "http_status":          status,
            "mode":                 mode,
            "extract":              extract,
            "content_length_bytes": len(html),
            "elapsed_seconds":      round(time.time() - start, 3),
            "scraped_at":           datetime.utcnow().isoformat() + "Z",
            "data":                 data,
        })

    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": f"Timed out after {timeout}s", "url": url},
            status_code=408,
        )
    except Exception as e:
        return JSONResponse({"error": str(e), "url": url}, status_code=500)
