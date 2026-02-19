# tools/registry.py

TOOLS_DB = {
    # ==============================================================================
    # 1. FINANCIAL NEWS TOOL
    # ==============================================================================
    "orgteh-finance-rss": {
        "id": "orgteh-finance-rss",
        "name_en": "Global Market Pulse",
        "name_ar": "نبض الأسواق المالية",
        "type": "Finance Stream",
        "price": "20 Points / Req",
        "is_paid": True,
        "desc_en": "Real-time financial news aggregator with strict time filtering and full-text extraction.",
        "desc_ar": "مجمع أخبار مالية لحظي مع فلترة زمنية دقيقة وإمكانية سحب النص الكامل للمقالات.",
        "icon": "fa-solid fa-chart-line",
        "color": "text-green-500",
        "params": [
            {"name": "limit", "type": "number", "default": 3, "desc_en": "Count", "desc_ar": "العدد"},
            {"name": "lang", "type": "select", "options": ["en", "ar"], "default": "en", "desc_en": "Language", "desc_ar": "اللغة"},
            {"name": "time_filter", "type": "select", "options": ["1h", "1d", "1m", "1y", "all"], "default": "1d", "desc_en": "Time Range", "desc_ar": "النطاق الزمني"},
            {"name": "scrape_content", "type": "select", "options": ["true", "false"], "default": "true", "desc_en": "Fetch Full Text?", "desc_ar": "جلب النص الكامل؟"}
        ],
        "usage_python": """import requests

url = "https://orgteh.com/api/tools/execute/orgteh-finance-rss"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {
    "limit": 5, 
    "lang": "en", 
    "time_filter": "1d",
    "scrape_content": "true"
}

response = requests.post(url, headers=headers, json=payload)
print(response.json())""",

        "usage_java": """import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class Main {
    public static void main(String[] args) throws Exception {
        String jsonBody = "{\\"limit\\":5, \\"lang\\":\\"en\\", \\"time_filter\\":\\"1d\\", \\"scrape_content\\":\\"true\\"}";

        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-finance-rss"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
            .build();

        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        System.out.println(response.body());
    }
}""",

        "usage_http": """curl -X POST https://orgteh.com/api/tools/execute/orgteh-finance-rss \\
-H "Authorization: Bearer YOUR_API_KEY" \\
-H "Content-Type: application/json" \\
-d '{
    "limit": 5,
    "lang": "en",
    "time_filter": "1d",
    "scrape_content": "true"
}'""",

        "integ_title_en": "Market Summarization Agent",
        "integ_title_ar": "عميل تلخيص الأسواق",
        "integ_desc_en": "Fetch market news and feed it into your AI model for a daily summary.",
        "integ_desc_ar": "جلب أخبار السوق وتغذية نموذجك الذكي لإنشاء ملخص يومي.",

        "integ_python": """# Get News & Summarize
news_items = response.json().get('items', [])
context = ""
for item in news_items:
    text = item.get('full_content', item.get('summary'))
    context += f"Title: {item['title']}\\nBody: {text}\\n---\\n"

chat_url = "https://orgteh.com/v1/chat/completions"
chat_payload = {
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [
        {"role": "system", "content": "You are a financial analyst."},
        {"role": "user", "content": f"Analyze these news:\\n{context}"}
    ]
}

print(requests.post(chat_url, json=chat_payload, headers=headers).json())""",

        "integ_java": """String prompt = "Analyze this financial news: " + newsJsonString;

String chatBody = "{"
        + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
        + "\\"messages\\": [{\\"role\\":\\"user\\", \\"content\\": \\"" + prompt.replace("\"", "'") + "\\""
        + "}]"
        + "}";

HttpRequest chatReq = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(chatBody))
    .build();

System.out.println(client.send(chatReq, HttpResponse.BodyHandlers.ofString()).body());""",

        "integ_http": """POST https://orgteh.com/v1/chat/completions HTTP/1.1
Host: orgteh.com
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "model": "deepseek-ai/deepseek-v3.2",
  "messages": [
    {
      "role": "system",
      "content": "You are a financial analyst."
    },
    {
      "role": "user",
      "content": "Summarize this news data: [PASTE_TOOL_JSON_OUTPUT_HERE]"
    }
  ]
}"""
    },

    # ==============================================================================
    # 2. GENERAL NEWS TOOL
    # ==============================================================================
    "orgteh-news-general": {
        "id": "orgteh-news-general",
        "name_en": "World News & Events",
        "name_ar": "أخبار العالم والأحداث",
        "type": "News Stream",
        "price": "20 Points / Req",
        "is_paid": True,
        "desc_en": "Comprehensive global news coverage including politics, tech, and sports.",
        "desc_ar": "تغطية شاملة للأخبار العالمية، السياسية، التقنية والرياضية من مصادر موثوقة.",
        "icon": "fa-solid fa-earth-americas",
        "color": "text-blue-500",
        "params": [
            {"name": "limit", "type": "number", "default": 3, "desc_en": "Count", "desc_ar": "العدد"},
            {"name": "lang", "type": "select", "options": ["en", "ar"], "default": "en", "desc_en": "Language", "desc_ar": "اللغة"},
            {"name": "time_filter", "type": "select", "options": ["1h", "1d", "1m", "1y", "all"], "default": "1d", "desc_en": "Time Range", "desc_ar": "النطاق الزمني"},
            {"name": "scrape_content", "type": "select", "options": ["true", "false"], "default": "true", "desc_en": "Fetch Full Text?", "desc_ar": "جلب النص الكامل؟"}
        ],
        "usage_python": """import requests

url = "https://orgteh.com/api/tools/execute/orgteh-news-general"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {"limit": 3, "lang": "ar", "time_filter": "1d", "scrape_content": "true"}

resp = requests.post(url, json=payload, headers=headers)
print(resp.json())""",

        "usage_java": """import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class Main {
    public static void main(String[] args) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        String json = "{\\"limit\\":3, \\"lang\\":\\"ar\\", \\"time_filter\\":\\"1d\\", \\"scrape_content\\":\\"true\\"}";

        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-news-general"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());
    }
}""",

        "usage_http": """curl -X POST https://orgteh.com/api/tools/execute/orgteh-news-general \\
-H "Authorization: Bearer YOUR_API_KEY" \\
-H "Content-Type: application/json" \\
-d '{"limit": 3, "lang": "ar", "time_filter": "1d", "scrape_content": "true"}'""",

        "integ_title_en": "News Categorizer Bot",
        "integ_title_ar": "بوت تصنيف الأخبار",
        "integ_desc_en": "Aggregate news and categorize them using your AI model.",
        "integ_desc_ar": "تجميع الأخبار وتصنيفها باستخدام نموذجك الذكي.",

        "integ_python": """headlines = [item['title'] for item in resp.json()['items']]
prompt = f"Categorize these headlines into [Politics, Sports, Tech]: {headlines}"

chat_url = "https://orgteh.com/v1/chat/completions"
chat_payload = {
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [{"role": "user", "content": prompt}]
}

print(requests.post(chat_url, json=chat_payload, headers=headers).json())""",

        "integ_java": """String prompt = "Categorize these headlines: [INSERT_HEADLINES_HERE]";

String body = "{"
        + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
        + "\\"messages\\": [{\\"role\\":\\"user\\", \\"content\\":\\"" + prompt + "\\"}]"
        + "}";

HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("https://orgteh.com/v1/chat/completions"))
    .header("Authorization", "Bearer YOUR_API_KEY")
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(body))
    .build();

System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());""",

        "integ_http": """POST https://orgteh.com/v1/chat/completions HTTP/1.1
Host: orgteh.com
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "model": "deepseek-ai/deepseek-v3.2",
  "messages": [
    {"role": "user", "content": "Categorize these headlines: [PASTE_TOOL_OUTPUT_HERE]"}
  ]
}"""
    },

    # ==============================================================================
    # 3. NVIDIA OCR TOOL
    # ==============================================================================
    "orgteh-vision-ocr": {
        "id": "orgteh-vision-ocr",
        "name_en": "Orgteh Vision (OCR)",
        "name_ar": "محرك الرؤية (OCR)",
        "type": "Vision Model",
        "price": "20 Points / Req",
        "is_paid": True,
        "desc_en": "Extract text from images/documents with high precision by using Ai.",
        "desc_ar": "استخراج النصوص من الصور والمستندات بدقة عالية بإستخدام الذكاء الاصطناعي.",
        "icon": "fa-solid fa-eye",
        "color": "text-purple-500",
        "params": [
            {"name": "file_input", "type": "file", "desc_en": "Image File", "desc_ar": "ملف الصورة"}
        ],
        "usage_python": """import requests

url = "https://orgteh.com/api/tools/execute/orgteh-vision-ocr"
files = {'file_input': open('invoice.png', 'rb')}
headers = {"Authorization": "Bearer YOUR_API_KEY"}

response = requests.post(url, headers=headers, files=files)
print(response.json())""",

        "usage_java": """import okhttp3.*;
import java.io.File;

OkHttpClient client = new OkHttpClient();
File file = new File("invoice.png");

RequestBody body = new MultipartBody.Builder().setType(MultipartBody.FORM)
    .addFormDataPart("file_input", file.getName(),
        RequestBody.create(file, MediaType.parse("image/png")))
    .build();

Request request = new Request.Builder()
    .url("https://orgteh.com/api/tools/execute/orgteh-vision-ocr")
    .addHeader("Authorization", "Bearer YOUR_API_KEY")
    .post(body)
    .build();

System.out.println(client.newCall(request).execute().body().string());""",

        "usage_http": """curl -X POST https://orgteh.com/api/tools/execute/orgteh-vision-ocr \\
-H "Authorization: Bearer YOUR_API_KEY" \\
-F "file_input=@/path/to/invoice.png" """,

        "integ_title_en": "Receipt to JSON Converter",
        "integ_title_ar": "تحويل الفواتير إلى JSON",
        "integ_desc_en": "Convert unstructured OCR text into structured JSON data using your AI model.",
        "integ_desc_ar": "تحويل النص المستخرج من الصورة إلى بيانات JSON باستخدام نموذجك الذكي.",

        "integ_python": """ocr_text = response.json()['data']['text']
prompt = f"Extract date, vendor, and total from this text and return JSON:\\n{ocr_text}"

chat_url = "https://orgteh.com/v1/chat/completions"
payload = {
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [{"role": "user", "content": prompt}]
}

print(requests.post(chat_url, json=payload, headers=headers).json())""",

        "integ_java": """String extractedText = jsonResponse.getString("text");

String llmBody = "{"
    + "\\"model\\": \\"deepseek-ai/deepseek-v3.2\\","
    + "\\"messages\\": [{\\"role\\":\\"user\\", \\"content\\": \\"Extract JSON from: " + extractedText + "\\"}]"
    + "}";

// Send POST to orgteh.com/v1/chat/completions ...""",

        "integ_http": """POST https://orgteh.com/v1/chat/completions HTTP/1.1
Host: orgteh.com
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "model": "deepseek-ai/deepseek-v3.2",
  "messages": [
    {
      "role": "user", 
      "content": "Extract JSON (Date, Total, Vendor) from this OCR text: [PASTE_OCR_TEXT]"
    }
  ]
}"""
    },

    # ==============================================================================
    # 4. EMBEDDINGS TOOL
    # ==============================================================================
    "orgteh-semantic-embed": {
        "id": "orgteh-semantic-embed",
        "name_en": "Semantic Core V2",
        "name_ar": "المحرك الدلالي V2",
        "type": "Embedding",
        "price": "20 Points / 1k Tok",
        "is_paid": True,
        "desc_en": "Generate vector embeddings for semantic search and RAG applications.",
        "desc_ar": "توليد متجهات (Vectors) للبحث الدلالي وتطبيقات RAG.",
        "icon": "fa-solid fa-network-wired",
        "color": "text-orange-500",
        "params": [
            {"name": "text_input", "type": "text", "desc_en": "Input Text", "desc_ar": "النص"},
            {"name": "truncate", "type": "select", "options": ["NONE", "END"], "default": "NONE", "desc_en": "Truncate", "desc_ar": "القص"}
        ],
        "usage_python": """import requests

url = "https://orgteh.com/api/tools/execute/orgteh-semantic-embed"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
payload = {
    "text_input": "Artificial Intelligence agents",
    "truncate": "NONE"
}

resp = requests.post(url, json=payload, headers=headers)
print(resp.json())""",

        "usage_java": """import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class Main {
    public static void main(String[] args) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        String json = "{\\"text_input\\":\\"Hello World\\", \\"truncate\\":\\"NONE\\"}";

        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create("https://orgteh.com/api/tools/execute/orgteh-semantic-embed"))
            .header("Authorization", "Bearer YOUR_API_KEY")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();

        System.out.println(client.send(req, HttpResponse.BodyHandlers.ofString()).body());
    }
}""",

        "usage_http": """curl -X POST https://orgteh.com/api/tools/execute/orgteh-semantic-embed \\
-H "Authorization: Bearer YOUR_API_KEY" \\
-H "Content-Type: application/json" \\
-d '{"text_input": "Search Query", "truncate": "NONE"}'""",

        "integ_title_en": "RAG Search Pipeline",
        "integ_title_ar": "نظام البحث المعزز (RAG)",
        "integ_desc_en": "Use embedding for search, then generate answer using your AI model.",
        "integ_desc_ar": "استخدام المتجهات للبحث، ثم توليد الإجابة باستخدام نموذجك الذكي.",

        "integ_python": """query_vector = resp.json()['vector_preview'] 

# ... Search Vector DB (Pinecone/Milvus) ...
# context = results['text']

chat_url = "https://orgteh.com/v1/chat/completions"
payload = {
    "model": "deepseek-ai/deepseek-v3.2",
    "messages": [
        {"role": "user", "content": f"Answer based on context: {context}"}
    ]
}
print(requests.post(chat_url, json=payload, headers=headers).json())""",

        "integ_java": """// 1. Get Vector & Search DB
// String context = database.search(vector);

// 2. Ask your AI Model
String body = "{\\"model\\":\\"deepseek-ai/deepseek-v3.2\\", \\"messages\\":[{\\"role\\":\\"user\\", \\"content\\":\\"Answer based on: " + context + "\\"}]}";

// Send POST to /v1/chat/completions ...""",

        "integ_http": """POST https://orgteh.com/v1/chat/completions HTTP/1.1
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "model": "deepseek-ai/deepseek-v3.2",
  "messages": [
    {
      "role": "user",
      "content": "Answer this question using the provided context: [PASTE_DB_CONTEXT_HERE]"
    }
  ]
}"""
    }
}