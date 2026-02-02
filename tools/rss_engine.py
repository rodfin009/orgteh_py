import feedparser
import asyncio
import random
import requests
from datetime import datetime, timedelta
from dateutil import parser
from gnews import GNews
import trafilatura
from concurrent.futures import ThreadPoolExecutor

# --- 1. قاعدة بيانات المصادر (تم توسيعها لضمان الدقة وعدم الانقطاع) ---
RSS_DB = {
    "finance": {
        "en": [
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
            "http://feeds.reuters.com/reuters/businessNews",
            "https://finance.yahoo.com/news/rssindex",
            "https://feeds.marketwatch.com/marketwatch/topstories/",
            "https://www.investing.com/rss/news.rss",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://cointelegraph.com/rss",
            "https://fortune.com/feed",
            "https://www.businessinsider.com/rss",
            "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
            "https://www.wsj.com/xml/rss/3_7014.xml",
            "https://www.ft.com/?format=rss",
            "https://www.economist.com/finance-and-economics/rss.xml"
        ],
        "ar": [
            "https://www.cnbcarabia.com/rss/latest-news",
            "https://www.aljazeera.net/aljazeerarss/eb261fd4-02d9-48bb-a02e-9d8a39626388/4c264a92-74d3-4a1d-8422-777648348b64",
            "https://www.alarabiya.net/.mrss/aswaq.xml",
            "https://www.skynewsarabia.com/rss/business",
            "https://www.independentarabia.com/rss/business",
            "https://aawsat.com/feed/business",
            "https://www.maaal.com/feed",
            "https://www.argaam.com/ar/rss/news",
            "https://www.aleqt.com/rss/business.xml",
            "https://www.emaratalyoum.com/business/rss",
            "https://www.okaz.com.sa/rss/economy",
            "https://www.al-jazirah.com/rss/economy.xml"
        ]
    },
    "general": {
        "en": [
            "http://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://www.theguardian.com/world/rss",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "http://rss.cnn.com/rss/edition_world.rss",
            "https://feeds.washingtonpost.com/rss/world",
            "https://www.rt.com/rss/news/",
            "https://www.dw.com/en/top-stories/rss-10021",
            "https://www.france24.com/en/rss",
            "https://news.google.com/rss",
            "https://feeds.npr.org/1001/rss.xml",
            "https://www.cbsnews.com/latest/rss/main"
        ],
        "ar": [
            "https://www.aljazeera.net/aljazeerarss/3c66e3fb-a5e0-4790-91be-ddb05ec17198/4e8984cc-2802-4161-a06f-633008447883",
            "https://www.alarabiya.net/.mrss/services/rss.xml",
            "https://www.skynewsarabia.com/rss/middle-east",
            "https://www.bbc.com/arabic/index.xml",
            "https://aawsat.com/feed",
            "https://www.independentarabia.com/rss",
            "https://www.france24.com/ar/rss",
            "https://arabic.rt.com/rss/",
            "https://www.dw.com/ar/all-content/rss-9106",
            "https://news.google.com/rss?hl=ar&gl=SA&ceid=SA:ar",
            "https://www.youm7.com/rss/SectionRss?SectionID=65",
            "https://www.elwatannews.com/home/rss"
        ]
    }
}

# --- 2. دوال مساعدة للوقت وتجهيز الهيدرز ---
def get_safe_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive"
    }

def is_date_valid(date_obj, time_filter):
    """
    Filters articles based on the time_filter.
    """
    if not date_obj: return True 

    # توحيد التوقيت (إزالة Timezone للمقارنة)
    now = datetime.utcnow()
    if date_obj.tzinfo is not None:
        date_obj = date_obj.replace(tzinfo=None)

    diff = now - date_obj

    if time_filter == "1h":
        return diff <= timedelta(hours=1)
    elif time_filter == "1d":
        return diff <= timedelta(days=1)
    elif time_filter == "1m":
        return diff <= timedelta(days=30)
    elif time_filter == "1y":
        return diff <= timedelta(days=365)

    return True 

def parse_date_string(date_str):
    try:
        return parser.parse(date_str)
    except:
        return None

# --- 3. المحرك الجديد لسحب المحتوى (Trafilatura Engine) ---
async def fetch_full_content_trafilatura(url):
    """
    Uses Trafilatura to fetch and extract main text content.
    """
    try:
        loop = asyncio.get_running_loop()

        def run_sync_scrape():
            # استخدام Download مع Headers لتجنب الحظر
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None

            # استخراج النص
            result = trafilatura.extract(
                downloaded, 
                include_comments=False, 
                include_tables=False, 
                no_fallback=False
            )
            return result

        content = await loop.run_in_executor(None, run_sync_scrape)

        if content and len(content) > 200:
            return content
        return None

    except Exception as e:
        print(f"Trafilatura Error for {url}: {e}")
        return None

# --- 4. دوال الجلب المتزامن (تتم إدارتها داخل ThreadPool) ---
def fetch_gnews_sync(category, limit, lang, time_filter, period_map):
    """
    دالة متزامنة لجلب أخبار جوجل، سيتم تشغيلها في خيط منفصل لتجنب تعليق السيرفر
    """
    results = []
    try:
        gnews_period = period_map.get(time_filter)
        # تخصيص الدولة لتحسين النتائج العربية
        country = 'EG' if lang == 'ar' else 'US' 
        if lang == 'ar': country = 'SA' # السعودية للأخبار المالية أفضل

        google_news = GNews(language=lang, country=country, period=gnews_period, max_results=limit)

        topic = 'BUSINESS' if category == 'finance' else 'WORLD'
        g_results = google_news.get_news_by_topic(topic)

        # التأكد من أن النتائج ليست فارغة
        if not g_results:
            g_results = google_news.get_top_news()

        for item in g_results:
            pub_date_str = item.get('published date')
            pub_date_obj = parse_date_string(pub_date_str)

            if time_filter != 'all' and not is_date_valid(pub_date_obj, time_filter):
                continue

            results.append({
                "title": item.get('title'),
                "link": item.get('url'),
                "date": pub_date_str,
                "summary": item.get('description', 'Click to read more...'),
                "source": item.get('publisher', {}).get('title', 'Google News'),
                "is_scraped": False
            })
            if len(results) >= limit: break

    except Exception as e:
        print(f"GNews Sync Error: {e}")

    return results

def fetch_rss_fallback_sync(category, lang, limit, time_filter, existing_count):
    """
    دالة متزامنة لجلب RSS التقليدي كخيار احتياطي
    """
    fallback_items = []
    try:
        sources = RSS_DB.get(category, {}).get(lang, [])
        random.shuffle(sources) # خلط المصادر للتنويع

        for url in sources:
            if len(fallback_items) + existing_count >= limit: break

            try:
                # استخدام requests مع timeout وسياق آمن
                resp = requests.get(url, headers=get_safe_headers(), timeout=4)
                if resp.status_code != 200: continue

                feed = feedparser.parse(resp.content)

                for entry in feed.entries:
                    if len(fallback_items) + existing_count >= limit: break

                    pub_date = entry.get('published') or entry.get('updated') or str(datetime.now())
                    date_obj = parse_date_string(pub_date)

                    if time_filter != 'all':
                         if date_obj and not is_date_valid(date_obj, time_filter): continue

                    fallback_items.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": pub_date,
                        "summary": (entry.get('summary', '') or entry.get('description', ''))[:300] + "...",
                        "source": feed.feed.get('title', 'RSS Source'),
                        "is_scraped": False
                    })
            except Exception as e:
                continue # تخطي المصدر المعطوب والانتقال للتالي

    except Exception as e:
        print(f"RSS Fallback Error: {e}")

    return fallback_items

# --- 5. المنفذ الرئيسي (المعدل) ---
async def execute_hybrid_news(category, limit, lang, time_filter, scrape_content):
    period_map = {"1h": "1h", "1d": "1d", "1m": "1m", "1y": "1y", "all": None}

    collected_items = []
    loop = asyncio.get_running_loop()

    # استخدام ThreadPoolExecutor لتشغيل العمليات الثقيلة دون إيقاف السيرفر
    with ThreadPoolExecutor(max_workers=4) as executor:

        # --- PHASE 1: GNews (Async Wrapper) ---
        try:
            # تشغيل GNews في خيط منفصل
            gnews_items = await loop.run_in_executor(
                executor, 
                fetch_gnews_sync, 
                category, limit, lang, time_filter, period_map
            )
            collected_items.extend(gnews_items)
        except Exception as e:
            print(f"Critical GNews Executor Error: {e}")

        # --- PHASE 2: RSS Fallback (إذا لم نصل للحد المطلوب) ---
        if len(collected_items) < limit:
            try:
                rss_items = await loop.run_in_executor(
                    executor,
                    fetch_rss_fallback_sync,
                    category, lang, limit, time_filter, len(collected_items)
                )
                collected_items.extend(rss_items)
            except Exception as e:
                 print(f"Critical RSS Executor Error: {e}")

    # --- PHASE 3: Content Scraping (With Trafilatura) ---
    # هذه المرحلة أصلاً Async لذا لا تحتاج لتعديل كبير، فقط التحقق
    if scrape_content == "true" and collected_items:
        tasks = []
        for item in collected_items:
            tasks.append(fetch_full_content_trafilatura(item['link']))

        results = await asyncio.gather(*tasks)

        for i, text in enumerate(results):
            if text:
                collected_items[i]['full_content'] = text
                collected_items[i]['is_scraped'] = True
            else:
                collected_items[i]['full_content'] = collected_items[i]['summary'] + "\n\n(Click link to read full coverage)"
                collected_items[i]['is_scraped'] = False

    # التأكد من إرجاع مصفوفة فارغة بدلاً من الفشل الكامل
    return {
        "status": "success",
        "count": len(collected_items),
        "language": lang,
        "category": category,
        "time_filter": time_filter,
        "items": collected_items
    }