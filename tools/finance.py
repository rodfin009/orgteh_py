# tools/finance.py
import httpx
import feedparser
import asyncio
from bs4 import BeautifulSoup

# --- Configuration ---
RSS_SOURCES = {
    "en": [
        "https://feeds.bloomberg.com/markets/news.rss",
        "http://feeds.reuters.com/reuters/businessNews",
        "https://techcrunch.com/feed/"
    ],
    "ar": [
        "https://www.aljazeera.net/aljazeerarss/eb261fd4-02d9-48bb-a02e-9d8a39626388/4c264a92-74d3-4a1d-8422-777648348b64", # Al Jazeera Economy
        "https://www.cnbcarabia.com/rss/latest-news", # CNBC Arabia
        "https://www.skynewsarabia.com/rss/business", # Sky News Arabia
        "https://www.independentarabia.com/rss/business" # Independent Arabia
    ]
}

# --- Helper: Scrape Full Content ---
async def fetch_full_content(url):
    """
    Visits the URL and extracts the main paragraph text.
    Optimized for news sites.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return "Could not fetch content (Status mismatch)."

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Intelligent extraction: Look for <article> or specific classes common in news
            article = soup.find('article')
            if not article:
                # Fallback: Find all paragraphs
                paragraphs = soup.find_all('p')
            else:
                paragraphs = article.find_all('p')

            # Clean and join text
            text_content = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text()) > 50])

            if len(text_content) < 100:
                return "Content protected or not found."

            return text_content[:4000] + "..." # Limit to prevent context overflow

    except Exception as e:
        return f"Error scraping content: {str(e)}"

# --- Main Executor ---
async def execute_news_tool(limit=3, lang="en", scrape_content="false"):
    sources = RSS_SOURCES.get(lang, RSS_SOURCES['en'])
    # Pick the first primary source for now, or aggregate (simple version picks first)
    primary_url = sources[0]

    # 1. Fetch RSS Feed (Sync operation in async wrapper)
    # Feedparser is blocking, so we run it quickly or use async request + parsing
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(primary_url, timeout=8.0)
            rss_text = response.text

        feed = feedparser.parse(rss_text)

        items = []
        tasks = []

        # Prepare items
        for entry in feed.entries[:limit]:
            item = {
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", "Recent"),
                "summary": entry.get("summary", ""),
                "source": feed.feed.get("title", "News Source")
            }
            items.append(item)
            if scrape_content == "true":
                tasks.append(fetch_full_content(entry.link))

        # 2. Async Scraping (Parallel)
        if scrape_content == "true" and tasks:
            full_contents = await asyncio.gather(*tasks)
            for i, content in enumerate(full_contents):
                items[i]['full_content'] = content

        return {"status": "success", "count": len(items), "language": lang, "items": items}

    except Exception as e:
        return {"error": f"RSS Error: {str(e)}"}