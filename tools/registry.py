# tools/registry.py
# =============================================================================
# قاعدة بيانات أدوات Orgteh — تعريف كامل لكل أداة
# =============================================================================

TOOLS_DB = {

    # ==========================================================================
    # 1. FINANCIAL NEWS TOOL — مجانية ✅
    # ==========================================================================
    "orgteh-finance-rss": {
        "id": "orgteh-finance-rss",
        "name_en": "Global Market Pulse",
        "name_ar": "نبض الأسواق المالية",
        "type": "Finance Stream",
        "price": "Free",
        "is_paid": False,
        "desc_en": "Real-time financial news aggregator with strict time filtering and full-text extraction from trusted sources like Reuters, Bloomberg and Yahoo Finance.",
        "desc_ar": "مجمع أخبار مالية لحظي مع فلترة زمنية دقيقة وإمكانية سحب النص الكامل للمقالات من مصادر موثوقة كرويترز وبلومبرغ.",
        "icon": "fa-solid fa-chart-line",
        "color": "text-green-500",
        "params": [
            {"name": "limit",          "type": "number", "default": 3,    "desc_en": "Count",           "desc_ar": "العدد"},
            {"name": "lang",           "type": "select", "options": ["en","ar"], "default": "en", "desc_en": "Language", "desc_ar": "اللغة",
             "option_descriptions": ["English language content", "Arabic language content"],
             "option_descriptions_ar": ["محتوى باللغة الإنجليزية", "محتوى باللغة العربية"] },
            {"name": "time_filter",    "type": "select", "options": ["1h","1d","1m","1y","all"], "default": "1d", "desc_en": "Time Range", "desc_ar": "النطاق الزمني",
             "option_descriptions": ["Last hour", "Last 24 hours", "Last month", "Last year", "All time"],
             "option_descriptions_ar": ["الساعة الماضية", "آخر 24 ساعة", "الشهر الماضي", "السنة الماضية", "كل الوقت"] },
            {"name": "scrape_content", "type": "select", "options": ["true","false"], "default": "true", "desc_en": "Fetch Full Text?", "desc_ar": "جلب النص الكامل؟",
             "option_descriptions": ["Fetch full article content", "Fetch summary only"],
             "option_descriptions_ar": ["جلب المحتوى الكامل للمقال", "جلب الملخص فقط"] },
        ],
        "usage_python": '''\
import requests

url     = "https://orgteh.com/api/tools/execute/orgteh-finance-rss"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {
    "limit":          5,
    "lang":           "en",
    "time_filter":    "1d",
    "scrape_content": "true"
}

response = requests.post(url, headers=headers, json=payload)
data     = response.json()

for item in data.get("items", []):
    print(item["title"], "—", item["published"])
    print(item.get("full_content", item.get("summary", ""))[:200])
    print()''',

        "usage_java": '''\
import java.net.URI;
import java.net.http.*;

public class FinanceNews {
    public static void main(String[] args) throws Exception {
        String json = "{"
            + "\\"limit\\":5,"
            + "\\"lang\\":\\"en\\","
            + "\\"time_filter\\":\\"1d\\","
            + "\\"scrape_content\\":\\"true\\""
            + "}";

        HttpClient  client  = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-finance-rss"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(request, HttpResponse.BodyHandlers.ofString()).body());
    }
}''',

        "usage_http": '''\
curl -X POST https://orgteh.com/api/tools/execute/orgteh-finance-rss \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "limit": 5,
    "lang": "en",
    "time_filter": "1d",
    "scrape_content": "true"
  }' ''',

        "integ_title_en": "Market Summarization Agent",
        "integ_title_ar": "عميل تلخيص الأسواق المالية",
        "integ_desc_en":  "Fetch live market news then feed it into an AI model for an executive daily brief.",
        "integ_desc_ar":  "جلب أخبار السوق اللحظية وتغذية نموذجك الذكي لإنشاء ملخص يومي تنفيذي.",

        "integ_python": '''\
import requests

headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Step 1: Get financial news
news_resp = requests.post(
    "https://orgteh.com/api/tools/execute/orgteh-finance-rss",
    json={"limit": 5, "lang": "en", "time_filter": "1d", "scrape_content": "true"},
    headers=headers
)
items   = news_resp.json().get("items", [])
context = "\\n---\\n".join(
    f"Title: {it[\'title\']}\\n{it.get(\'full_content\', it.get(\'summary\',\'\'))[:400]}"
    for it in items
)

# Step 2: Summarize with AI
chat_resp = requests.post(
    "https://orgteh.com/v1/chat/completions",
    json={
        "model": "deepseek-ai/deepseek-v3.2",
        "messages": [
            {"role": "system", "content": "You are a senior financial analyst. Write concise executive briefings."},
            {"role": "user",   "content": f"Create a daily market brief from these news items:\\n{context}"}
        ]
    },
    headers=headers
)
print(chat_resp.json()["choices"][0]["message"]["content"])''',

        "integ_java": '''\
// Step 1: Fetch news (see usage example above)
String newsJson = newsResponse.body();

// Step 2: Build AI prompt and call model
String prompt   = "Create a market brief from: " + newsJson.replace("\\"", "\'");
String chatBody = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": [{"
    +   "\\"role\\": \\"system\\", \\"content\\": \\"You are a financial analyst.\\""
    + "},{"
    +   "\\"role\\": \\"user\\", \\"content\\": \\"" + prompt + "\\""
    + "}]}";

HttpRequest chatReq = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(chatReq, HttpResponse.BodyHandlers.ofString()).body());''',

        "integ_http": '''\
# Step 1: Get news (see usage tab)

# Step 2: Send to AI model
curl -X POST https://orgteh.com/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [
      {"role": "system", "content": "You are a financial analyst."},
      {"role": "user",   "content": "Create a daily brief: [PASTE_TOOL_OUTPUT]"}
    ]
  }\' ''',
    },


    # ==========================================================================
    # 2. GENERAL NEWS TOOL — مجانية ✅
    # ==========================================================================
    "orgteh-news-general": {
        "id": "orgteh-news-general",
        "name_en": "World News & Events",
        "name_ar": "أخبار العالم والأحداث",
        "type": "News Stream",
        "price": "Free",
        "is_paid": False,
        "desc_en": "Comprehensive global news aggregator covering politics, technology, science and sports from 50+ verified RSS sources.",
        "desc_ar": "مجمع أخبار عالمي شامل يغطي السياسة والتقنية والعلوم والرياضة من أكثر من 50 مصدر RSS موثوق.",
        "icon": "fa-solid fa-earth-americas",
        "color": "text-blue-500",
        "params": [
            {"name": "limit",          "type": "number", "default": 3,    "desc_en": "Count",           "desc_ar": "العدد"},
            {"name": "lang",           "type": "select", "options": ["en","ar"], "default": "en", "desc_en": "Language", "desc_ar": "اللغة",
             "option_descriptions": ["English language content", "Arabic language content"],
             "option_descriptions_ar": ["محتوى باللغة الإنجليزية", "محتوى باللغة العربية"] },
            {"name": "time_filter",    "type": "select", "options": ["1h","1d","1m","1y","all"], "default": "1d", "desc_en": "Time Range", "desc_ar": "النطاق الزمني",
             "option_descriptions": ["Last hour", "Last 24 hours", "Last month", "Last year", "All time"],
             "option_descriptions_ar": ["الساعة الماضية", "آخر 24 ساعة", "الشهر الماضي", "السنة الماضية", "كل الوقت"] },
            {"name": "scrape_content", "type": "select", "options": ["true","false"], "default": "true", "desc_en": "Fetch Full Text?", "desc_ar": "جلب النص الكامل؟",
             "option_descriptions": ["Fetch full article content", "Fetch summary only"],
             "option_descriptions_ar": ["جلب المحتوى الكامل للمقال", "جلب الملخص فقط"] },
        ],
        "usage_python": '''\
import requests

url     = "https://orgteh.com/api/tools/execute/orgteh-news-general"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {"limit": 5, "lang": "ar", "time_filter": "1d", "scrape_content": "true"}

response = requests.post(url, headers=headers, json=payload)
for item in response.json().get("items", []):
    print(item["title"])
    print("Source:", item.get("source"), "| Published:", item.get("published"))
    print()''',

        "usage_java": '''\
import java.net.URI;
import java.net.http.*;

public class WorldNews {
    public static void main(String[] args) throws Exception {
        String json = "{"
            + "\\"limit\\":5,"
            + "\\"lang\\":\\"ar\\","
            + "\\"time_filter\\":\\"1d\\","
            + "\\"scrape_content\\":\\"true\\""
            + "}";

        HttpClient  client  = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-news-general"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(request, HttpResponse.BodyHandlers.ofString()).body());
    }
}''',

        "usage_http": '''\
curl -X POST https://orgteh.com/api/tools/execute/orgteh-news-general \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"limit": 5, "lang": "ar", "time_filter": "1d", "scrape_content": "true"}\' ''',

        "integ_title_en": "News Categorizer & Digest Bot",
        "integ_title_ar": "بوت تصنيف وتلخيص الأخبار",
        "integ_desc_en":  "Aggregate world news and automatically categorize and summarize them using your AI model.",
        "integ_desc_ar":  "تجميع أخبار العالم وتصنيفها وتلخيصها تلقائياً باستخدام نموذجك الذكي.",

        "integ_python": '''\
import requests

headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Step 1: Get world news
news = requests.post(
    "https://orgteh.com/api/tools/execute/orgteh-news-general",
    json={"limit": 6, "lang": "en", "time_filter": "1d", "scrape_content": "false"},
    headers=headers
).json()

headlines = [it["title"] for it in news.get("items", [])]

# Step 2: Categorize and summarize with AI
chat = requests.post(
    "https://orgteh.com/v1/chat/completions",
    json={
        "model": "deepseek-ai/deepseek-v3.2",
        "messages": [{
            "role": "user",
            "content": (
                "Categorize each headline into [Politics / Tech / Science / Sports / Other] "
                "and give a one-line summary for each:\\n"
                + "\\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
            )
        }]
    },
    headers=headers
)
print(chat.json()["choices"][0]["message"]["content"])''',

        "integ_java": '''\
String headlines = "1. Headline One\\n2. Headline Two\\n3. Headline Three";
String prompt    = "Categorize into [Politics/Tech/Science/Sports]: " + headlines;
String chatBody  = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"" + prompt + "\\"}]"
    + "}";

HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());''',

        "integ_http": '''\
curl -X POST https://orgteh.com/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [{
      "role": "user",
      "content": "Categorize into [Politics/Tech/Science/Sports]: [PASTE_HEADLINES]"
    }]
  }\' ''',
    },


    # ==========================================================================
    # 3. VISION / OCR TOOL — مدفوعة 💎
    # ==========================================================================
    "orgteh-vision-ocr": {
        "id": "orgteh-vision-ocr",
        "name_en": "Orgteh Vision (OCR)",
        "name_ar": "محرك الرؤية (OCR)",
        "type": "Vision Model",
        "price": "Premium",
        "is_paid": True,
        "desc_en": "Extract text from images and scanned documents with high precision using AI-powered computer vision. Supports receipts, invoices, IDs, handwritten notes and more.",
        "desc_ar": "استخراج النصوص من الصور والمستندات الممسوحة بدقة عالية باستخدام رؤية الحاسوب الذكية. يدعم الفواتير والإيصالات والهويات والملاحظات المكتوبة بخط اليد.",
        "icon": "fa-solid fa-eye",
        "color": "text-purple-500",
        "params": [
            {"name": "file_input", "type": "file", "desc_en": "Image File (PNG/JPG/PDF)", "desc_ar": "ملف الصورة (PNG/JPG/PDF)"},
        ],
        "usage_python": '''\
import requests

url     = "https://orgteh.com/api/tools/execute/orgteh-vision-ocr"
headers = {"Authorization": "Bearer YOUR_API_KEY"}

with open("invoice.png", "rb") as f:
    response = requests.post(url, headers=headers, files={"file_input": f})

data = response.json()
print("Extracted Text:")
print(data["data"]["text"])''',

        "usage_java": '''\
import okhttp3.*;
import java.io.File;

OkHttpClient client = new OkHttpClient();
File          file   = new File("invoice.png");

RequestBody body = new MultipartBody.Builder()
    .setType(MultipartBody.FORM)
    .addFormDataPart(
        "file_input", file.getName(),
        RequestBody.create(file, MediaType.parse("image/png"))
    )
    .build();

Request request = new Request.Builder()
    .url("https://orgteh.com/api/tools/execute/orgteh-vision-ocr")
    .addHeader("Authorization", "Bearer YOUR_API_KEY")
    .post(body)
    .build();

System.out.println(client.newCall(request).execute().body().string());''',

        "usage_http": '''\
curl -X POST https://orgteh.com/api/tools/execute/orgteh-vision-ocr \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -F "file_input=@/path/to/invoice.png" ''',

        "integ_title_en": "Receipt → Structured JSON",
        "integ_title_ar": "فاتورة → بيانات JSON منظمة",
        "integ_desc_en":  "Extract raw text from any receipt/invoice image, then use AI to parse it into structured JSON.",
        "integ_desc_ar":  "استخراج النص من صورة الفاتورة ثم استخدام الذكاء الاصطناعي لتحويله إلى بيانات JSON منظمة.",

        "integ_python": '''\
import requests, json

headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Step 1: OCR
with open("receipt.png", "rb") as f:
    ocr = requests.post(
        "https://orgteh.com/api/tools/execute/orgteh-vision-ocr",
        headers=headers, files={"file_input": f}
    ).json()

raw_text = ocr["data"]["text"]

# Step 2: AI — parse to structured JSON
chat = requests.post(
    "https://orgteh.com/v1/chat/completions",
    json={
        "model": "deepseek-ai/deepseek-v3.2",
        "messages": [{
            "role": "user",
            "content": (
                "Extract fields (vendor_name, date, total_amount, currency, line_items) "
                f"from this receipt and return valid JSON:\\n\\n{raw_text}"
            )
        }],
        "response_format": {"type": "json_object"}
    },
    headers=headers
)
print(json.dumps(json.loads(chat.json()["choices"][0]["message"]["content"]), indent=2, ensure_ascii=False))''',

        "integ_java": '''\
String ocrText  = ocrJsonResponse.getJSONObject("data").getString("text");
String prompt   = "Extract JSON (vendor, date, total, items) from: " + ocrText;
String chatBody = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"" + prompt + "\\"}]"
    + "}";

HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());''',

        "integ_http": '''\
curl -X POST https://orgteh.com/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [{
      "role": "user",
      "content": "Extract JSON (vendor, date, total, items) from OCR text: [PASTE_OCR_TEXT]"
    }]
  }\' ''',
    },


    # ==========================================================================
    # 4. SEMANTIC EMBEDDING TOOL — مدفوعة 💎
    # ==========================================================================
    "orgteh-semantic-embed": {
        "id": "orgteh-semantic-embed",
        "name_en": "Semantic Core V2",
        "name_ar": "المحرك الدلالي V2",
        "type": "Embedding",
        "price": "Premium",
        "is_paid": True,
        "desc_en": "Generate high-dimensional vector embeddings for semantic search, clustering and RAG pipelines.",
        "desc_ar": "توليد متجهات عالية الأبعاد للبحث الدلالي والتجميع وخطوط RAG.",
        "icon": "fa-solid fa-network-wired",
        "color": "text-orange-500",
        "params": [
            {"name": "text_input", "type": "text",   "desc_en": "Input Text",    "desc_ar": "النص المُدخَل"},
            {"name": "truncate",   "type": "select",  "options": ["NONE","END"], "default": "NONE", "desc_en": "Truncate Mode", "desc_ar": "وضع القص",
             "option_descriptions": ["Keep full text without truncation", "Truncate from end if too long"],
             "option_descriptions_ar": ["الاحتفاظ بالنص الكامل بدون قص", "القص من النهاية إذا كان النص طويلاً"] },
        ],
        "usage_python": '''\
import requests

url     = "https://orgteh.com/api/tools/execute/orgteh-semantic-embed"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {
    "text_input": "Artificial Intelligence is transforming industries worldwide.",
    "truncate":   "NONE"
}

response = requests.post(url, headers=headers, json=payload)
data     = response.json()
print("Dimensions:", data.get("dimensions"))
print("Vector preview:", data.get("vector_preview")[:5], "...")''',

        "usage_java": '''\
import java.net.URI;
import java.net.http.*;

public class Embeddings {
    public static void main(String[] args) throws Exception {
        String json = "{"
            + "\\"text_input\\": \\"Artificial Intelligence is transforming industries.\\","
            + "\\"truncate\\": \\"NONE\\""
            + "}";

        HttpClient  client  = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-semantic-embed"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(request, HttpResponse.BodyHandlers.ofString()).body());
    }
}''',

        "usage_http": '''\
curl -X POST https://orgteh.com/api/tools/execute/orgteh-semantic-embed \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"text_input": "Artificial Intelligence is transforming industries.", "truncate": "NONE"}\' ''',

        "integ_title_en": "Semantic RAG Search Pipeline",
        "integ_title_ar": "نظام البحث الدلالي المعزز (RAG)",
        "integ_desc_en":  "Convert user queries to vectors, search your database, and generate grounded answers using an AI model.",
        "integ_desc_ar":  "تحويل استفسار المستخدم إلى متجه، البحث في قاعدة بياناتك، ثم توليد إجابة موثوقة بنموذج ذكي.",

        "integ_python": '''\
import requests

headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Step 1: Embed the user query
query   = "What is the refund policy?"
embed_r = requests.post(
    "https://orgteh.com/api/tools/execute/orgteh-semantic-embed",
    json={"text_input": query, "truncate": "NONE"},
    headers=headers
).json()
query_vec = embed_r["vector"]   # full float32 list

# Step 2: Search your vector DB (Pinecone / Milvus / pgvector ...)
# results  = pinecone_index.query(vector=query_vec, top_k=3)
context = "[Retrieved context from your vector database]"

# Step 3: Generate grounded answer
chat = requests.post(
    "https://orgteh.com/v1/chat/completions",
    json={
        "model": "deepseek-ai/deepseek-v3.2",
        "messages": [
            {"role": "system", "content": "Answer ONLY based on the provided context. If unknown, say so."},
            {"role": "user",   "content": f"Context:\\n{context}\\n\\nQuestion: {query}"}
        ]
    },
    headers=headers
)
print(chat.json()["choices"][0]["message"]["content"])''',

        "integ_java": '''\
// Step 1: Get embedding
String queryJson = "{\\"text_input\\":\\"What is the refund policy?\\", \\"truncate\\":\\"NONE\\"}";
// ... send, parse vector ...

// Step 2: Search vector DB (Pinecone / Milvus / pgvector)
String context = "[Context from vector DB]";

// Step 3: Answer with AI
String chatBody = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": ["
    +   "{\\"role\\": \\"system\\", \\"content\\": \\"Answer only from context.\\"},"
    +   "{\\"role\\": \\"user\\",   \\"content\\": \\"Context: " + context + "\\\\nQ: refund policy?\\"}"
    + "]}";

HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());''',

        "integ_http": '''\
# Step 1: Embed (see usage tab)

# Step 2: Search Pinecone
curl -X POST https://controller.YOUR_ENV.pinecone.io/query \\
  -H "Api-Key: YOUR_PINECONE_KEY" \\
  -d \'{"vector": [EMBED_VECTOR], "topK": 3, "includeMetadata": true}\'

# Step 3: Generate grounded answer
curl -X POST https://orgteh.com/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [
      {"role": "system", "content": "Answer only from context."},
      {"role": "user",   "content": "Context: [DB_RESULT]\\nQuestion: [USER_QUERY]"}
    ]
  }\' ''',
    },


    # ==========================================================================
    # 5. SMART WEB SCRAPER — مجانية ✅
    # ==========================================================================
    "orgteh-web-scraper": {
        "id": "orgteh-web-scraper",
        "name_en": "Orgteh Web Extractor",
        "name_ar": "محرك استخراج الويب",
        "type": "Web Extraction",
        "price": "Free",
        "is_paid": False,
        "desc_en": (
            "Extract structured data from any public web page in seconds. "
            "Returns clean text, links, images, metadata and Markdown — "
            "with built-in anti-block technology and intelligent content parsing."
        ),
        "desc_ar": (
            "استخراج بيانات منظمة من أي صفحة ويب عامة في ثوانٍ. "
            "يُرجع نصوصاً نظيفة وروابط وصوراً وبيانات وصفية ومحتوى بصيغة Markdown "
            "— مع تقنية تجاوز الحجب المدمجة وتحليل المحتوى الذكي."
        ),
        "icon": "fa-solid fa-spider",
        "color": "text-emerald-500",
        "params": [
            {"name": "url",     "type": "text",   "default": "https://example.com", "desc_en": "Target URL", "desc_ar": "الرابط المستهدف"},
            {"name": "mode",    "type": "select", "options": ["smart","stealth"], "default": "smart", "desc_en": "Extraction Mode", "desc_ar": "وضع الاستخراج",
             "option_descriptions": ["Fast extraction with standard headers", "Stealth mode with anti-bot bypass"],
             "option_descriptions_ar": ["استخراج سريع مع رؤوس قياسية", "وضع التخفي مع تجاوز الحماية"] },
            {"name": "extract", "type": "select", "options": ["all","text","links","images","metadata","markdown"], "default": "all", "desc_en": "Data to Extract", "desc_ar": "البيانات المراد استخراجها",
             "option_descriptions": ["Extract all available data", "Extract text content only", "Extract links only", "Extract images only", "Extract metadata only", "Extract as Markdown format"],
             "option_descriptions_ar": ["استخراج جميع البيانات المتاحة", "استخراج النصوص فقط", "استخراج الروابط فقط", "استخراج الصور فقط", "استخراج البيانات الوصفية فقط", "استخراج بصيغة Markdown"] },
            {"name": "timeout", "type": "number", "default": 15, "desc_en": "Timeout (seconds)", "desc_ar": "مهلة الانتظار (ثانية)"},
            {"name": "timeout",   "type": "number", "default": 15, "desc_en": "Timeout (seconds)", "desc_ar": "مهلة الانتظار (ثانية)"},
        ],
        "usage_python": '''\
import requests

url     = "https://orgteh.com/api/tools/execute/orgteh-web-scraper"
headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Extract everything from a page
response = requests.post(url, headers=headers, json={
    "url":       "https://news.ycombinator.com",
    "mode":      "smart",
    "extract":   "all",
    "js_render": "false",
    "timeout":   15
})
data = response.json()

print("Title:", data["data"]["metadata"]["title"])
print("Text preview:", data["data"]["text"][:400])
print("Links found:", len(data["data"]["links"]))

# JavaScript-rendered SPA (React / Vue / Angular)
spa = requests.post(url, headers=headers, json={
    "url":       "https://my-react-app.com",
    "mode":      "js",
    "extract":   "text",
    "js_render": "true",
    "timeout":   25
})
print(spa.json()["data"]["text"][:300])''',

        "usage_java": '''\
import java.net.URI;
import java.net.http.*;

public class WebScraper {
    public static void main(String[] args) throws Exception {
        String json = "{"
            + "\\"url\\":\\"https://news.ycombinator.com\\","
            + "\\"mode\\":\\"smart\\","
            + "\\"extract\\":\\"all\\","
            + "\\"js_render\\":\\"false\\","
            + "\\"timeout\\":15"
            + "}";

        HttpClient  client  = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-web-scraper"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(request, HttpResponse.BodyHandlers.ofString()).body());
    }
}''',

        "usage_http": '''\
# Basic scrape — extract everything
curl -X POST https://orgteh.com/api/tools/execute/orgteh-web-scraper \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"url":"https://news.ycombinator.com","mode":"smart","extract":"all","timeout":15}\'

# Stealth mode (anti-bot bypass)
curl -X POST https://orgteh.com/api/tools/execute/orgteh-web-scraper \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"url":"https://protected-site.com","mode":"stealth","extract":"text","timeout":20}\'

# JS rendering (React/Vue/Angular)
curl -X POST https://orgteh.com/api/tools/execute/orgteh-web-scraper \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"url":"https://spa-app.com","mode":"js","extract":"text","js_render":"true","timeout":25}\' ''',

        "integ_title_en": "Web Content Intelligence Agent",
        "integ_title_ar": "عميل تحليل محتوى المواقع",
        "integ_desc_en":  "Scrape any web page and feed its content into an AI model for analysis, summarization or Q&A.",
        "integ_desc_ar":  "كشط أي صفحة ويب وتغذية محتواها لنموذج ذكي للتحليل أو التلخيص أو الإجابة على الأسئلة.",

        "integ_python": '''\
import requests

headers = {"Authorization": "Bearer YOUR_API_KEY"}

# Step 1: Scrape target page
scrape = requests.post(
    "https://orgteh.com/api/tools/execute/orgteh-web-scraper",
    json={"url": "https://techcrunch.com/latest", "mode": "smart", "extract": "text", "timeout": 15},
    headers=headers
).json()

page_text  = scrape["data"]["text"]
page_title = scrape["data"]["metadata"]["title"]

# Step 2: AI analysis
chat = requests.post(
    "https://orgteh.com/v1/chat/completions",
    json={
        "model": "deepseek-ai/deepseek-v3.2",
        "messages": [
            {"role": "system", "content": "You are an intelligent content analyst. Be concise and structured."},
            {"role": "user",   "content": (
                f"Page: {page_title}\\nContent:\\n{page_text[:3000]}\\n\\n"
                "Tasks:\\n"
                "1. Summarize main topics (3 bullets)\\n"
                "2. Identify primary audience\\n"
                "3. List top 3 key entities (people, companies, places)"
            )}
        ]
    },
    headers=headers
)
print(chat.json()["choices"][0]["message"]["content"])''',

        "integ_java": '''\
// Step 1: Scrape
String scrapeBody = "{\\"url\\":\\"https://techcrunch.com/latest\\",\\"mode\\":\\"smart\\",\\"extract\\":\\"text\\",\\"timeout\\":15}";
// ... send request, parse data.text ...
String pageText = "extracted text here";

// Step 2: AI analysis
String prompt   = "Summarize in 3 bullets and find key entities:\\n" + pageText;
String chatBody = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"" + prompt.replace("\\"","\'") + "\\"}]"
    + "}";

HttpRequest chatReq = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(chatReq, HttpResponse.BodyHandlers.ofString()).body());''',

        "integ_http": '''\
# Step 1: Scrape (see usage tab)

# Step 2: Analyze with AI
curl -X POST https://orgteh.com/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [
      {"role": "system", "content": "You are an intelligent content analyst."},
      {"role": "user",   "content": "Summarize in 3 bullets and identify key entities:\\n[PASTE_SCRAPED_TEXT]"}
    ]
  }\' ''',
    }


}
