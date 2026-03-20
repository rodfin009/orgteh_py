"""
blog.py — نظام المدونة الكامل لـ Orgteh
===========================================
يحتوي على:
  1. blog_db      — طبقة قاعدة البيانات (TiDB الأساسي + Redis للمؤقت فقط)
  2. blog_catalog — بناء كتالوج RAG تلقائي من ملفات النماذج وقاعدة الأدوات
  3. blog_gen     — محرك التوليد (arxiv → AI → AR/EN → حفظ)
  4. blog_router  — راوتر FastAPI (الصفحات + API)

توزيع التخزين:
  TiDB (5GB) — كل البيانات الدائمة:
    • blog_posts          → المقالات الكاملة
    • blog_catalog_embeds → تضمينات النماذج/الأدوات للـ RAG
    • blog_article_embeds → تضمينات المقالات للـ semantic dedup

  Redis (250MB) — فقط المؤقت وسريع الوصول:
    • blog:seen_arxiv_ids → SET صغير للفحص السريع (< 75KB)
    • blog:catalog_hash   → string لـ invalidation check (< 20 bytes)

نظام RAG للكتالوج:
  - المصدر: ملفات HTML في static/models_translation/ + TOOLS_DB
  - التضمين: nvidia/llama-nemotron-embed-1b-v2
  - التخزين: TiDB (UPSERT تلقائي)
  - الاسترجاع: cosine similarity → top-K أكثر صلة
  - Hash Invalidation: يعيد البناء عند إضافة نماذج جديدة
"""

import os
import re
import json
import math
import hashlib
import logging
import asyncio
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

# ============================================================================
# SECTION 1 — DATABASE LAYER
# ============================================================================

def _get_conn():
    from database import get_db_connection
    return get_db_connection()


def _redis():
    try:
        from database import redis
        return redis
    except Exception:
        return None


def init_blog_tables():
    """
    ينشئ ثلاثة جداول في TiDB — كل التخزين الدائم هنا:
      1. blog_posts          — المقالات الكاملة (EN + AR)
      2. blog_catalog_embeds — تضمينات النماذج/الأدوات للـ RAG
      3. blog_article_embeds — تضمينات المقالات للـ semantic dedup

    Redis يبقى فقط لـ:
      - blog:seen_arxiv_ids → SET صغير للفحص السريع (< 75KB)
      - blog:catalog_hash   → string صغير لـ invalidation check
    """
    conn = _get_conn()
    if not conn:
        logger.warning("[BlogDB] No DB connection — skipping init")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS blog_posts (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                slug         VARCHAR(120)  UNIQUE NOT NULL,
                arxiv_id     VARCHAR(60)   UNIQUE NOT NULL,
                arxiv_url    TEXT,
                title_en     TEXT          NOT NULL,
                title_ar     TEXT          NOT NULL,
                content_en   LONGTEXT      NOT NULL,
                content_ar   LONGTEXT      NOT NULL,
                summary_en   TEXT,
                summary_ar   TEXT,
                seo_keywords TEXT,
                published_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS blog_catalog_embeds (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                short_key    VARCHAR(80)   UNIQUE NOT NULL,
                entry_type   VARCHAR(10)   NOT NULL,
                name         VARCHAR(200)  NOT NULL,
                provider     VARCHAR(100),
                link_tpl     VARCHAR(200),
                description  TEXT,
                embedding    MEDIUMTEXT    NOT NULL,
                catalog_hash VARCHAR(20),
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS blog_article_embeds (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                arxiv_id     VARCHAR(60)   UNIQUE NOT NULL,
                embedding    MEDIUMTEXT    NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        logger.info("[BlogDB] All tables ready")
    except Exception as e:
        logger.error(f"[BlogDB] init error: {e}")
    finally:
        conn.close()


def save_blog_post(data: dict) -> int:
    conn = _get_conn()
    if not conn:
        raise RuntimeError("[BlogDB] No DB connection")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO blog_posts
              (slug, arxiv_id, arxiv_url,
               title_en, title_ar,
               content_en, content_ar,
               summary_en, summary_ar,
               seo_keywords, published_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                data["slug"], data["arxiv_id"], data.get("arxiv_url", ""),
                data["title_en"], data["title_ar"],
                data["content_en"], data["content_ar"],
                data.get("summary_en", ""), data.get("summary_ar", ""),
                data.get("seo_keywords", "[]"),
                data.get("published_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
            ))
            return cur.lastrowid
    except Exception as e:
        logger.error(f"[BlogDB] save_blog_post: {e}")
        raise
    finally:
        conn.close()


def get_posts(page: int = 1, limit: int = 12) -> tuple[list[dict], int]:
    conn = _get_conn()
    if not conn:
        return [], 0
    try:
        offset = (page - 1) * limit
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM blog_posts")
            row = cur.fetchone()
            total = row["cnt"] if row else 0
            cur.execute("""
            SELECT id, slug, title_en, title_ar,
                   summary_en, summary_ar, published_at
            FROM blog_posts ORDER BY published_at DESC LIMIT %s OFFSET %s
            """, (limit, offset))
            posts = cur.fetchall()
        return list(posts), total
    except Exception as e:
        logger.error(f"[BlogDB] get_posts: {e}")
        return [], 0
    finally:
        conn.close()


def get_post_by_slug(slug: str) -> Optional[dict]:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM blog_posts WHERE slug=%s LIMIT 1", (slug,))
            return cur.fetchone()
    except Exception as e:
        logger.error(f"[BlogDB] get_post_by_slug: {e}")
        return None
    finally:
        conn.close()


def get_all_arxiv_ids() -> list[str]:
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT arxiv_id FROM blog_posts")
            return [r["arxiv_id"] for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[BlogDB] get_all_arxiv_ids: {e}")
        return []
    finally:
        conn.close()


def get_all_slugs_with_dates() -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slug, published_at FROM blog_posts ORDER BY published_at DESC")
            rows = cur.fetchall()
        result = []
        for r in rows:
            pub = r["published_at"]
            pub = pub.strftime("%Y-%m-%d") if hasattr(pub, "strftime") else str(pub)[:10]
            result.append({"slug": r["slug"], "published_at": pub})
        return result
    except Exception as e:
        logger.error(f"[BlogDB] get_all_slugs: {e}")
        return []
    finally:
        conn.close()


# ============================================================================
# SECTION 2 — AUTOMATIC CATALOG RAG SYSTEM
# ============================================================================
#
# المبدأ: بدلاً من كتالوج يدوي، نقرأ أوصاف النماذج مباشرة من ملفات HTML
# الموجودة في static/models_translation/ وأوصاف الأدوات من TOOLS_DB.
# ثم نضمّد كل إدخال ونخزّنه في Redis.
# عند توليد مقالة، نسترجع فقط الأكثر صلة عبر cosine similarity.
#
# طبقات التوسع (Scalability Layers):
#   L1 — Hash Invalidation: يعيد بناء التضمينات تلقائياً عند تغيير النماذج
#   L2 — Top-K Retrieval: يُحقن فقط top-4 مهما بلغ عدد النماذج (10 أو 500)
#   L3 — Fallback نصي: لو فشل الـ embedding يُرسل أسماء فقط
#   L4 — TTL: التضمينات تنتهي بعد 30 يوم وتُعاد إذا طُلبت
# ============================================================================

_CATALOG_HASH_KEY  = "blog:catalog_hash_v3"  # Redis: string صغير للـ invalidation فقط
_CATALOG_TOP_K     = 4   # عدد النماذج/الأدوات المُحقنة في كل مقالة

GENERATION_MODEL = "meta/llama-3.1-405b-instruct"
EMBED_MODEL      = "nvidia/llama-nemotron-embed-1b-v2"


class _HTMLTextExtractor(HTMLParser):
    """يسحب النص الخام من HTML — stdlib فقط، بدون مكتبات خارجية."""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "table", "thead", "tbody", "tr", "th", "td"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "table", "thead", "tbody", "tr", "th", "td"):
            self._skip = False
            self._parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self._parts.append(t)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(raw: str) -> str:
    p = _HTMLTextExtractor()
    try:
        p.feed(raw)
        text = p.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", text).strip()[:1500]


def _get_static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _read_model_description_en(short_key: str) -> str:
    """
    يقرأ الوصف الإنجليزي للنموذج من ملف HTML المقابل له.
    هذا نفس الملف الذي تستخدمه صفحة النماذج — مصدر موحد واحد.
    """
    path = _get_static_dir() / "models_translation" / f"{short_key}.html"
    if not path.exists():
        return ""
    try:
        raw = path.read_text(encoding="utf-8")
        # استخرج فقط القسم الإنجليزي lang-en
        match = re.search(
            r'class="model-lang-content lang-en[^"]*">(.*?)</div>\s*(?:<div class="model-lang-content|<script)',
            raw, re.DOTALL | re.IGNORECASE
        )
        if match:
            return _strip_html(match.group(1))
        # fallback: اسحب كل النص (يحذف الجداول والكود تلقائياً)
        return _strip_html(raw)
    except Exception as e:
        logger.warning(f"[Catalog] Cannot read {short_key}.html: {e}")
        return ""


def _build_catalog_entries() -> list[dict]:
    """
    يبني قائمة موحدة لكل النماذج والأدوات مع أوصافها النصية.
    تلقائية بالكامل — تتحدث عند إضافة أي نموذج أو أداة جديدة.
    """
    entries = []

    # ── النماذج من providers.py ──────────────────────────────────────────
    try:
        from services.providers import MODELS_METADATA
        for m in MODELS_METADATA:
            short_key = m.get("short_key", "")
            if not short_key:
                continue
            desc_from_file = _read_model_description_en(short_key)
            # نبني نص التضمين من الوصف الحقيقي + البيانات الأساسية
            full_desc = desc_from_file or f"{m.get('name','')} AI model by {m.get('provider','')}."
            entries.append({
                "type":      "model",
                "short_key": short_key,
                "name":      m.get("name", short_key),
                "provider":  m.get("provider", ""),
                "link_tpl":  f"/{{lang}}/models/{short_key}",
                # نقتصر على 900 حرف لتوفير context window
                "desc":      full_desc[:900],
            })
    except Exception as e:
        logger.error(f"[Catalog] models load error: {e}")

    # ── الأدوات من tools/registry.py ────────────────────────────────────
    try:
        from tools.registry import TOOLS_DB
        for tool_id, tool in TOOLS_DB.items():
            desc = (tool.get("desc_en") or tool.get("name_en") or tool_id)[:800]
            entries.append({
                "type":      "tool",
                "short_key": tool_id,
                "name":      tool.get("name_en", tool_id),
                "provider":  "Orgteh",
                "link_tpl":  f"/{{lang}}/accesory/{tool_id}",
                "desc":      desc,
            })
    except Exception as e:
        logger.error(f"[Catalog] tools load error: {e}")

    n_models = sum(1 for e in entries if e["type"] == "model")
    n_tools  = sum(1 for e in entries if e["type"] == "tool")
    logger.info(f"[Catalog] Built {len(entries)} entries ({n_models} models, {n_tools} tools)")
    return entries


def _catalog_hash(entries: list[dict]) -> str:
    """MD5 من قائمة المفاتيح — يُستخدم للكشف عن تغييرات."""
    ids = sorted(e["short_key"] for e in entries)
    return hashlib.md5(json.dumps(ids).encode()).hexdigest()[:12]


def _build_nvidia_client():
    from openai import OpenAI
    raw = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", "no-key"))
    key = raw.split(",")[0].strip()
    return OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key)


def _embed_text(text: str, input_type: str = "passage") -> list[float]:
    try:
        client = _build_nvidia_client()
        resp   = client.embeddings.create(
            input=[text[:2000]], model=EMBED_MODEL,
            encoding_format="float",
            extra_body={"input_type": input_type, "truncate": "END"},
        )
        return resp.data[0].embedding
    except Exception as e:
        logger.error(f"[Catalog] embed error: {e}")
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def ensure_catalog_embeddings() -> list[dict]:
    """
    يضمن وجود تضمينات حديثة في TiDB.
    Redis يُستخدم فقط للـ hash check (أسرع من query TiDB في كل طلب).
    يعيد البناء إذا:
      - لم تكن التضمينات موجودة في TiDB
      - تغيرت قائمة النماذج (hash مختلف)
    """
    entries  = _build_catalog_entries()
    cur_hash = _catalog_hash(entries)
    r        = _redis()

    # ── فحص الـ hash في Redis أولاً (microseconds) ──────────────────────
    if r:
        try:
            cached_hash = r.get(_CATALOG_HASH_KEY)
            if isinstance(cached_hash, bytes):
                cached_hash = cached_hash.decode()
            if cached_hash == cur_hash:
                # الـ hash متطابق — اقرأ من TiDB
                stored = _load_catalog_from_tidb()
                if stored:
                    logger.info(f"[Catalog] TiDB hit — {len(stored)} entries (hash={cur_hash})")
                    return stored
        except Exception as e:
            logger.warning(f"[Catalog] Hash check: {e}")

    # ── بناء التضمينات (يحدث عند أول تشغيل أو تغيير النماذج) ─────────────
    logger.info(f"[Catalog] Building embeddings for {len(entries)} entries…")
    stored = []
    for entry in entries:
        embed_text = f"{entry['name']} ({entry['provider']}). {entry['desc']}"
        emb = _embed_text(embed_text, input_type="passage")
        stored.append({**entry, "embedding": emb, "catalog_hash": cur_hash})

    # ── حفظ في TiDB (المخزن الدائم) ─────────────────────────────────────
    _save_catalog_to_tidb(stored, cur_hash)

    # ── حفظ الـ hash فقط في Redis (للفحص السريع) ────────────────────────
    if r:
        try:
            r.set(_CATALOG_HASH_KEY, cur_hash)
            r.expire(_CATALOG_HASH_KEY, 86_400 * 7)  # 7 أيام
        except Exception as e:
            logger.warning(f"[Catalog] Redis hash save: {e}")

    return stored


def _load_catalog_from_tidb() -> list[dict]:
    """يقرأ جميع تضمينات الكتالوج من TiDB."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT short_key, entry_type, name, provider, link_tpl,
                   description, embedding, catalog_hash
            FROM blog_catalog_embeds
            ORDER BY id ASC
            """)
            rows = cur.fetchall()
        result = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"]) if row["embedding"] else []
            except Exception:
                emb = []
            result.append({
                "short_key":    row["short_key"],
                "type":         row["entry_type"],
                "name":         row["name"],
                "provider":     row["provider"] or "",
                "link_tpl":     row["link_tpl"] or "",
                "desc":         row["description"] or "",
                "embedding":    emb,
                "catalog_hash": row["catalog_hash"] or "",
            })
        return result
    except Exception as e:
        logger.error(f"[Catalog] TiDB load: {e}")
        return []
    finally:
        conn.close()


def _save_catalog_to_tidb(stored: list[dict], cur_hash: str):
    """يحفظ/يحدّث تضمينات الكتالوج في TiDB (UPSERT)."""
    conn = _get_conn()
    if not conn:
        logger.error("[Catalog] No DB connection for save")
        return
    try:
        with conn.cursor() as cur:
            for entry in stored:
                emb_json = json.dumps(entry.get("embedding", []))
                cur.execute("""
                INSERT INTO blog_catalog_embeds
                  (short_key, entry_type, name, provider, link_tpl,
                   description, embedding, catalog_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  entry_type   = VALUES(entry_type),
                  name         = VALUES(name),
                  provider     = VALUES(provider),
                  link_tpl     = VALUES(link_tpl),
                  description  = VALUES(description),
                  embedding    = VALUES(embedding),
                  catalog_hash = VALUES(catalog_hash),
                  updated_at   = CURRENT_TIMESTAMP
                """, (
                    entry["short_key"], entry["type"],
                    entry["name"],      entry.get("provider", ""),
                    entry.get("link_tpl", ""), entry.get("desc", "")[:2000],
                    emb_json, cur_hash,
                ))
        logger.info(f"[Catalog] Saved {len(stored)} entries to TiDB (hash={cur_hash})")
    except Exception as e:
        logger.error(f"[Catalog] TiDB save: {e}")
    finally:
        conn.close()


def retrieve_relevant_catalog(query_text: str, top_k: int = _CATALOG_TOP_K) -> list[dict]:
    """
    يسترجع أكثر top_k نموذج/أداة صلةً بالمقالة عبر cosine similarity.
    Fallback: أول top_k من القائمة إذا فشل الـ embedding.
    """
    catalog   = ensure_catalog_embeddings()
    query_emb = _embed_text(query_text[:1500], input_type="query")

    if not query_emb:
        logger.warning("[Catalog] Query embed failed — using fallback")
        return [e for e in catalog[:top_k]]

    scored = [((_cosine(query_emb, e.get("embedding", []))), e) for e in catalog]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [e for _, e in scored[:top_k]]
    logger.info(f"[Catalog] Retrieved: {[e['name'] for e in top]} — scores: {[round(s,3) for s,_ in scored[:top_k]]}")
    return top


def _format_catalog_prompt(relevant: list[dict], lang: str) -> str:
    """
    يبني نص البرومبت للـ LLM من النتائج المسترجعة.
    الروابط صحيحة تلقائياً بناءً على اللغة.
    """
    if not relevant:
        return "(No specific Orgteh models are particularly relevant to this topic.)"

    lines = [
        f"=== ORGTEH PLATFORM — RECOMMENDED RESOURCES FOR THIS ARTICLE ===",
        f"Article language: {lang.upper()} — ALL markdown links MUST start with /{lang}/",
        "",
    ]
    for e in relevant:
        link = e["link_tpl"].replace("{lang}", lang)
        kind = "MODEL" if e["type"] == "model" else "TOOL"
        lines.append(f"[{kind}] {e['name']}  →  markdown link: [{e['name']}]({link})")
        lines.append(f"  Description: {e['desc'][:700]}")
        lines.append("")

    lines += [
        "HOW TO USE IN ARTICLE:",
        "• In the 'Integrating with Orgteh' section, recommend 1–3 of the above",
        "• Choose only what GENUINELY relates to the article's specific topic",
        "• Write recommendations as natural prose — not a bullet list",
        f"• Use exact markdown links shown above (they already use /{lang}/)",
        f"• ⚠️  NEVER use /{'ar' if lang=='en' else 'en'}/ links — this is a {lang.upper()} article",
    ]
    return "\n".join(lines)


# ============================================================================
# SECTION 3 — GENERATION ENGINE
# ============================================================================
#
# آلية البحث والانتقاء — 4 مراحل:
#
#  Phase 1 — الجلب متعدد المصادر (Parallel Fetch)
#    * arxiv: 4 queries متخصصة تعمل معاً في نفس الوقت
#    * HuggingFace Daily Papers: أبرز أوراق اليوم (API مجاني)
#
#  Phase 2 — الفلترة السريعة (Fast Filter)
#    * Exact dedup: Redis SET + TiDB arxiv_ids
#    * Pre-scoring: نُرتّب الأوراق قبل إرسالها للـ LLM
#
#  Phase 3 — الفلترة الدلالية (Semantic Filter)
#    * Batch embedding + cosine vs المقالات المنشورة
#
#  Phase 4 — الاختيار الذكي (LLM Selection)
#    * النموذج يرى أفضل 15 ورقة مرتبة بالـ score
#    * يختار العدد المطلوب بمعايير عملية واضحة
# ============================================================================

# 4 queries متخصصة لتنويع المواضيع
_ARXIV_QUERIES = [
    "cat:cs.AI+AND+(LLM+agents+OR+autonomous+agents+OR+agentic+AI+OR+tool+use+OR+function+calling)",
    "cat:cs.AI+cat:cs.IR+AND+(RAG+OR+retrieval+augmented+OR+knowledge+base+OR+long+context+OR+memory)",
    "cat:cs.CL+AND+(prompt+engineering+OR+chain+of+thought+OR+instruction+tuning+OR+few+shot)",
    "cat:cs.CV+cat:cs.CL+AND+(multimodal+LLM+OR+vision+language+OR+code+generation+OR+LLM+evaluation)",
]

_HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"


async def _fetch_arxiv_one(client: httpx.AsyncClient, query: str, per_query: int = 15) -> list[dict]:
    url = (
        "http://export.arxiv.org/api/query"
        f"?search_query={query}"
        "&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={per_query}"
    )
    try:
        resp = await client.get(url, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[Fetch] arxiv query failed: {e}")
        return []
    papers = []
    try:
        root = ET.fromstring(resp.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            raw_id   = entry.findtext("atom:id", "", ns)
            arxiv_id = raw_id.split("/abs/")[-1].strip()
            title    = (entry.findtext("atom:title",   "", ns) or "").strip().replace("\n", " ")
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
            published = entry.findtext("atom:published", "", ns) or ""
            if title and abstract:
                papers.append({
                    "arxiv_id": arxiv_id, "title": title, "abstract": abstract,
                    "published": published, "url": raw_id.strip(), "source": "arxiv",
                })
    except Exception as e:
        logger.warning(f"[Fetch] arxiv parse: {e}")
    return papers


async def _fetch_hf_daily(client: httpx.AsyncClient) -> list[dict]:
    """HuggingFace Daily Papers — مختارة يدوياً، مجانية بدون مفتاح."""
    try:
        resp = await client.get(_HF_PAPERS_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[Fetch] HF daily failed: {e}")
        return []
    papers = []
    for item in data[:30]:
        try:
            p        = item.get("paper", {})
            arxiv_id = p.get("id", "")
            title    = p.get("title", "").strip()
            abstract = p.get("summary", "").strip()
            if not (arxiv_id and title and abstract):
                continue
            papers.append({
                "arxiv_id":  arxiv_id,
                "title":     title,
                "abstract":  abstract,
                "published": p.get("publishedAt", "")[:10],
                "url":       f"https://arxiv.org/abs/{arxiv_id}",
                "source":    "hf_daily",
                "upvotes":   item.get("totalUpvotes", 0),
            })
        except Exception:
            continue
    logger.info(f"[Fetch] HF Daily: {len(papers)} papers")
    return papers


async def fetch_all_papers() -> list[dict]:
    """يجلب من جميع المصادر بشكل متوازٍ ويُزيل التكرار."""
    async with httpx.AsyncClient() as client:
        tasks   = [_fetch_arxiv_one(client, q) for q in _ARXIV_QUERIES]
        tasks.append(_fetch_hf_daily(client))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_ids: set[str] = set()
    merged:   list[dict] = []
    for batch in results:
        if isinstance(batch, Exception):
            continue
        for p in batch:
            aid = p.get("arxiv_id", "")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                merged.append(p)

    logger.info(f"[Fetch] Total unique: {len(merged)} papers from all sources")
    return merged


def _score_paper(paper: dict) -> float:
    """
    نقاط مسبقة لكل ورقة قبل الإرسال للـ LLM:
      +2.0  من HuggingFace (مختارة يدوياً)
      +0.1/upvote على HF (حتى +2.0)
      +1.0  نُشرت خلال 7 أيام
      +0.5  نُشرت خلال 14 يوماً
      +0.3  ملخص مفصّل (> 200 كلمة)
      +0.3  يحتوي كلمات عملية
    """
    score = 0.0
    now   = datetime.utcnow()

    if paper.get("source") == "hf_daily":
        score += 2.0
        score += min(paper.get("upvotes", 0) * 0.1, 2.0)

    try:
        pub_str = paper.get("published", "")[:10]
        if pub_str:
            pub      = datetime.strptime(pub_str, "%Y-%m-%d")
            age_days = (now - pub).days
            if age_days <= 7:
                score += 1.0
            elif age_days <= 14:
                score += 0.5
    except Exception:
        pass

    if len(paper.get("abstract", "").split()) > 200:
        score += 0.3

    practical = {"agent","rag","retrieval","prompt","code","tool","build",
                 "implement","pipeline","workflow","deploy","instruction","fine-tun"}
    text = (paper.get("title","") + " " + paper.get("abstract","")[:300]).lower()
    score += min(sum(1 for kw in practical if kw in text) * 0.1, 0.5)

    return round(score, 3)


def _batch_semantic_dedup(embeddings_map: dict, threshold: float = 0.87) -> set:
    """
    فلترة دلالية batch: يجلب كل التضمينات مرة واحدة من TiDB
    ثم يقارن دفعةً واحدة — أكفأ بكثير من query لكل ورقة.
    """
    if not embeddings_map:
        return set()
    stored = _load_article_embeddings()
    if not stored:
        return set()
    duplicates: set = set()
    for arxiv_id, emb in embeddings_map.items():
        if not emb:
            continue
        for item in stored:
            if _cosine(emb, item.get("embedding", [])) >= threshold:
                duplicates.add(arxiv_id)
                break
    return duplicates


# ─── Redis dedup — arxiv IDs ─────────────────────────────────────────────────

def get_seen_arxiv_ids() -> set:
    r = _redis()
    if not r:
        return set()
    try:
        members = r.smembers("blog:seen_arxiv_ids") or set()
        return {(m.decode() if isinstance(m, bytes) else m) for m in members}
    except Exception:
        return set()


def mark_arxiv_id_seen(arxiv_id: str):
    r = _redis()
    if not r:
        return
    try:
        r.sadd("blog:seen_arxiv_ids", arxiv_id)
        r.expire("blog:seen_arxiv_ids", 63_072_000)
    except Exception as e:
        logger.error(f"[BlogGen] Redis sadd: {e}")


# ─── Semantic dedup — مقالات منشورة (TiDB) ───────────────────────────────────

def _load_article_embeddings() -> list[dict]:
    """يقرأ جميع تضمينات المقالات من TiDB."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT arxiv_id, embedding FROM blog_article_embeds")
            rows = cur.fetchall()
        result = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"]) if row["embedding"] else []
            except Exception:
                emb = []
            if emb:
                result.append({"arxiv_id": row["arxiv_id"], "embedding": emb})
        return result
    except Exception as e:
        logger.error(f"[ArticleEmbed] load: {e}")
        return []
    finally:
        conn.close()


def is_semantic_duplicate(embedding: list[float], threshold: float = 0.87) -> bool:
    if not embedding:
        return False
    for item in _load_article_embeddings():
        if _cosine(embedding, item.get("embedding", [])) >= threshold:
            return True
    return False


def store_article_embedding(arxiv_id: str, embedding: list[float]):
    """يحفظ تضمين المقالة في TiDB (UPSERT)."""
    conn = _get_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO blog_article_embeds (arxiv_id, embedding)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE embedding = VALUES(embedding)
            """, (arxiv_id, json.dumps(embedding)))
    except Exception as e:
        logger.error(f"[ArticleEmbed] store: {e}")
    finally:
        conn.close()


# ─── SEO keywords — نظام ثلاثي الطبقات ───────────────────────────────────────
#
#  Layer 1 (الأقوى): LLM يولّد كلمات SEO مدروسة بناءً على موضوع المقالة
#                    → مجاني، يفهم السياق، يعطي long-tail keywords
#  Layer 2 (تكميلي): Google Autocomplete → ما يبحث عنه الناس فعلاً
#                    → مجاني، لا يحتاج API key
#  Layer 3 (احترافي): DataForSEO → حجم البحث + صعوبة الكلمة (اختياري)
#                    → مدفوع، يُفعَّل بوضع DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD
# ─────────────────────────────────────────────────────────────────────────────

_DATAFORSEO_LOGIN    = os.environ.get("DATAFORSEO_LOGIN", "")
_DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")


def _get_seo_keywords_llm(paper_title: str, paper_abstract: str) -> list[str]:
    """
    Layer 1: يطلب من النموذج توليد كلمات SEO مدروسة.
    النموذج يفهم موضوع المقالة ويختار كلمات ذات نية بحث حقيقية.
    """
    prompt = f"""You are an SEO specialist for an AI developer platform.
Generate a list of high-value SEO keywords for a blog post about the following research topic.

TOPIC: {paper_title}
CONTEXT: {paper_abstract[:400]}

REQUIREMENTS:
- 20 keywords total
- Mix of: short-tail (1-2 words) + long-tail (3-5 words)
- Focus on: developer intent (how to, tutorial, example, guide, API, code)
- Include variations: "LLM agents", "AI agents tutorial", "build AI agents Python", etc.
- Avoid overly academic phrases — use what developers actually search for
- Include at least 5 question-form keywords ("how to ...", "what is ...", "best way to ...")

Reply ONLY with a JSON array of strings.
Example: ["AI agents tutorial", "how to build LLM agents", "RAG implementation guide"]
No explanation. No markdown fences. Raw JSON array only."""

    try:
        client = _build_nvidia_client()
        comp   = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=400, stream=False,
        )
        raw   = comp.choices[0].message.content.strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            kws = json.loads(match.group())
            if isinstance(kws, list):
                return [str(k).strip() for k in kws if k][:20]
    except Exception as e:
        logger.warning(f"[SEO-L1] LLM keywords failed: {e}")
    return []


async def _get_seo_keywords_autocomplete(topic: str) -> list[str]:
    """
    Layer 2: Google Autocomplete — يكمّل Layer 1 بكلمات شائعة فعلاً.
    """
    keywords: set[str] = set()
    queries = [topic, f"how to {topic}", f"{topic} tutorial", f"{topic} python"]
    async with httpx.AsyncClient(timeout=8) as client:
        for q in queries[:3]:
            try:
                enc  = urllib.parse.quote(q)
                url  = (
                    f"https://suggestqueries.google.com/complete/search"
                    f"?client=chrome&q={enc}&hl=en"
                )
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                data = resp.json()
                if isinstance(data, list) and len(data) > 1:
                    keywords.update(str(s).strip() for s in data[1][:6] if s)
            except Exception:
                pass
    return list(keywords)


async def _get_seo_keywords_dataforseo(topic: str) -> list[str]:
    """
    Layer 3: DataForSEO Keyword Suggestions API (اختياري).
    يُفعَّل تلقائياً إذا كانت بيانات الدخول موجودة في البيئة.
    يعطي: keyword + monthly_searches + competition_level
    نُرجع فقط الكلمات ذات البحث المرتفع (volume > 100) والمنافسة المنخفضة.
    الوثائق: https://docs.dataforseo.com/v3/keywords_data/google_ads/keywords_for_keywords/live/
    """
    if not _DATAFORSEO_LOGIN or not _DATAFORSEO_PASSWORD:
        return []

    try:
        import base64
        credentials = base64.b64encode(
            f"{_DATAFORSEO_LOGIN}:{_DATAFORSEO_PASSWORD}".encode()
        ).decode()

        payload = [{"keywords": [topic], "language_code": "en", "location_code": 2840}]  # US
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.dataforseo.com/v3/keywords_data/google_ads/keywords_for_keywords/live",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = resp.json()

        results = (
            data.get("tasks", [{}])[0]
                .get("result", [{}])[0]
                .get("items", [])
        )

        # نرتّب: volume عالي + competition منخفضة = أفضل كلمات
        scored = []
        for item in results:
            kw     = item.get("keyword", "")
            volume = item.get("search_volume") or 0
            comp   = item.get("competition_index") or 100
            if kw and volume > 50:
                # score: نريد volume مرتفع وcompetition منخفض
                score = volume / (comp + 1)
                scored.append((score, kw))

        scored.sort(reverse=True)
        keywords = [kw for _, kw in scored[:12]]
        if keywords:
            logger.info(f"[SEO-L3] DataForSEO returned {len(keywords)} keywords")
        return keywords

    except Exception as e:
        logger.warning(f"[SEO-L3] DataForSEO failed: {e}")
        return []


async def get_seo_keywords(paper_title: str, paper_abstract: str = "") -> list[str]:
    """
    يجمع الثلاث طبقات ويُزيل التكرار.
    النتيجة: قائمة مرتبة — LLM أولاً (الأقوى)، ثم DataForSEO، ثم Autocomplete.
    """
    # Layer 1: LLM (متزامن — سريع)
    llm_kws = _get_seo_keywords_llm(paper_title, paper_abstract)

    # Layer 2 + 3: غير متزامن — معاً في نفس الوقت
    autocomplete_kws, dataforseo_kws = await asyncio.gather(
        _get_seo_keywords_autocomplete(paper_title.split(":")[0].strip()[:60]),
        _get_seo_keywords_dataforseo(paper_title.split(":")[0].strip()[:60]),
        return_exceptions=True,
    )
    if isinstance(autocomplete_kws, Exception):
        autocomplete_kws = []
    if isinstance(dataforseo_kws, Exception):
        dataforseo_kws = []

    # دمج الثلاث طبقات مع الحفاظ على الترتيب وإزالة التكرار
    seen: set[str] = set()
    final: list[str] = []
    for kw in (llm_kws + dataforseo_kws + autocomplete_kws):
        kw_clean = kw.strip().lower()
        if kw_clean and kw_clean not in seen:
            seen.add(kw_clean)
            final.append(kw.strip())

    logger.info(
        f"[SEO] Keywords: {len(llm_kws)} LLM + {len(dataforseo_kws)} DataForSEO "
        f"+ {len(autocomplete_kws)} Autocomplete = {len(final)} total"
    )
    return final[:25]


# ─── LLM selects papers ───────────────────────────────────────────────────────

def select_papers_with_llm(papers: list[dict], select_count: int = 3) -> list[dict]:
    # أرسل أفضل 15 فقط مرتبة بالـ pre_score
    top = sorted(papers, key=lambda p: p.get("_pre_score", 0), reverse=True)[:15]
    numbered = "\n".join(
        f"{i+1}. [{p['arxiv_id']}] score={p.get('_pre_score',0)} src={p.get('source','arxiv')}\n"
        f"   Title: {p['title']}\n"
        f"   Abstract: {p['abstract'][:400]}..."
        for i, p in enumerate(top)
    )
    prompt = f"""You are an AI content strategist for a developer platform. Select the best research papers for practical blog posts.

TARGET AUDIENCE: Software developers and AI practitioners who use LLM APIs daily.

SELECTION CRITERIA:
✅ Teaches something developers can IMMEDIATELY apply in their projects
✅ Covers: AI agents, prompt engineering, RAG, tool use, code generation, multimodal AI, LLM evaluation
✅ Has a clear practical angle — not pure theory or hardware benchmarks
✅ Novel enough to generate interesting content

PAPERS (pre-ranked by score — higher is better):
{numbered}

Select exactly {select_count} papers. Prefer higher-scored ones unless a lower-scored paper is clearly more practical.
Reply ONLY with a raw JSON array of arxiv IDs: ["2501.12345", "2501.67890"]
No explanation. No markdown. Raw JSON only."""

    try:
        client = _build_nvidia_client()
        comp   = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=200, stream=False,
        )
        raw   = comp.choices[0].message.content.strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            ids      = json.loads(match.group())
            selected = [p for p in top if p["arxiv_id"] in ids]
            if not selected:
                selected = [p for p in papers if p["arxiv_id"] in ids]
            if selected:
                logger.info(f"[Select] LLM chose: {[p['arxiv_id'] for p in selected]}")
                return selected
    except Exception as e:
        logger.error(f"[Select] LLM selection: {e}")
    return top[:select_count]


# ─── English article generation ───────────────────────────────────────────────

def generate_article_en(paper: dict, seo_keywords: list[str], catalog_ctx: str) -> Optional[str]:
    kw_str = ", ".join(seo_keywords[:15])
    system = (
        "You are a senior technical writer for Orgteh (orgteh.com), "
        "an AI API platform for developers. Write clear, practical, engaging articles."
    )
    prompt = f"""Write a comprehensive English blog post based on the research paper below.

=== PAPER ===
Title: {paper['title']}
Abstract: {paper['abstract']}
Source: {paper['url']}

=== SEO KEYWORDS (weave in naturally) ===
{kw_str}

=== ORGTEH MODELS & TOOLS — INJECT THESE INTO ARTICLE ===
{catalog_ctx}

=== ARTICLE REQUIREMENTS ===
1. Minimum 1600 words
2. Audience: developers and AI practitioners — practical, no heavy math
3. Markdown: # H1, ## H2, ### H3
4. Required sections:
   ## Introduction
   ## What This Research Discovered
   ## Why It Matters for Developers
   ## Practical Applications
   ## Implementation Guide  (include a code snippet or prompt template)
   ## Integrating with Orgteh
   ## Key Takeaways
5. "Integrating with Orgteh": use the models/tools provided above.
   Write naturally in prose. Use the exact markdown links shown — they already use /en/.
6. First paragraph after title ≤160 chars (works as meta description)
7. Code examples in fenced ``` blocks (Python preferred)
8. Tone: conversational, senior engineer explaining to a colleague

DO NOT:
- Say "arxiv" — say "recent research" or "a new study"
- Add any preamble before the # H1 title
- Use /ar/ links (English article → /en/ links only)

Start directly with the # H1 title."""

    try:
        client  = _build_nvidia_client()
        content = ""
        for chunk in client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0.72, top_p=0.9, max_tokens=4096, stream=True,
        ):
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content
        return content.strip() or None
    except Exception as e:
        logger.error(f"[BlogGen] EN generation: {e}")
        return None


# ─── Arabic translation ───────────────────────────────────────────────────────

def generate_article_ar(english_content: str, catalog_ctx_ar: str) -> Optional[str]:
    prompt = f"""Translate the following English blog post to Modern Standard Arabic (الفصحى المُيسَّرة).

=== ORGTEH LINKS FOR ARABIC ===
{catalog_ctx_ar}

=== TRANSLATION RULES ===
1. Translate ALL text: title, all headings, every paragraph
2. Keep ALL markdown formatting exactly as-is
3. Keep code blocks, model names, API names in English
4. CRITICAL LINK RULE — replace /en/ with /ar/ in ALL Orgteh internal links:
   [Model Name](/en/models/key) → [اسم النموذج](/ar/models/key)
   External http links stay unchanged.
5. Translate naturally as if originally written in Arabic
6. Do NOT add preamble — output ONLY the translated markdown
7. Technical terms (RAG, LLM, API, prompt, token) can stay in English

=== ENGLISH ARTICLE ===
{english_content}

OUTPUT: Complete Arabic markdown article only."""

    try:
        client  = _build_nvidia_client()
        content = ""
        for chunk in client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, top_p=0.85, max_tokens=4096, stream=True,
        ):
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content
        return content.strip() or None
    except Exception as e:
        logger.error(f"[BlogGen] AR translation: {e}")
        return None


# ─── Markdown helpers ─────────────────────────────────────────────────────────

def _extract_h1(md: str) -> str:
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return "Untitled"


def _extract_summary(md: str, max_chars: int = 220) -> str:
    for line in md.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("---"):
            continue
        clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        clean = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", clean)
        clean = re.sub(r"`[^`]+`", "", clean).strip()
        if len(clean) > 40:
            return clean[:max_chars]
    return ""


def _make_slug(title: str) -> str:
    title  = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title  = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:75]
    suffix = datetime.utcnow().strftime("%Y%m%d%H%M")
    return f"{title}-{suffix}"


# ─── Main pipeline ────────────────────────────────────────────────────────────

async def run_blog_generation(count: int = 3) -> dict:
    count = max(1, min(count, 5))
    logger.info(f"[BlogGen] Starting pipeline — target {count} article(s)")

    # ── Phase 1: جلب متوازٍ من arxiv (4 queries) + HuggingFace ──────────────
    papers = await fetch_all_papers()
    if not papers:
        return {"ok": False, "error": "Failed to fetch papers from all sources"}

    # ── Phase 2: Exact dedup (Redis + TiDB) ──────────────────────────────────
    seen = get_seen_arxiv_ids()
    try:
        seen |= set(get_all_arxiv_ids())
    except Exception:
        pass
    new_papers = [p for p in papers if p.get("arxiv_id") not in seen]
    logger.info(f"[Pipeline] {len(new_papers)}/{len(papers)} papers are new")
    if not new_papers:
        return {"ok": False, "error": "No new papers — all entries already published"}

    # ── Pre-scoring: رتّب الأوراق الجديدة قبل أي شيء آخر ────────────────────
    for p in new_papers:
        p["_pre_score"] = _score_paper(p)
    # خذ أفضل 30 فقط للفلترة الدلالية (توفير API calls)
    top_new = sorted(new_papers, key=lambda p: p["_pre_score"], reverse=True)[:30]

    # ── Phase 3: Semantic dedup — batch فعّال ────────────────────────────────
    # 1) احسب كل التضمينات دفعةً واحدة
    embed_map: dict[str, list[float]] = {}
    for p in top_new:
        text = f"{p['title']}. {p['abstract'][:500]}"
        emb  = _embed_text(text, input_type="passage")
        if emb:
            embed_map[p["arxiv_id"]] = emb
            p["_embedding"] = emb

    # 2) batch dedup مقارنة بالمقالات المنشورة
    semantic_dups = _batch_semantic_dedup(embed_map, threshold=0.87)
    candidates    = [p for p in top_new if p["arxiv_id"] not in semantic_dups]
    logger.info(f"[Pipeline] {len(candidates)} candidates after semantic dedup (removed {len(semantic_dups)})")

    if not candidates:
        return {"ok": False, "error": "All remaining papers are semantically similar to existing posts"}

    # ── Phase 4: LLM يختار الأفضل من أفضل 15 مرتبة بالـ score ───────────────
    selected  = select_papers_with_llm(candidates, select_count=count)
    published = []

    for paper in selected:
        try:
            logger.info(f"[BlogGen] Generating: {paper['title'][:70]}")

            topic  = paper["title"].split(":")[0].strip()[:60]
            seo_kw = await get_seo_keywords(paper["title"], paper["abstract"])

            # RAG: استرجاع النماذج الأكثر صلة لهذه المقالة تحديداً
            query = f"{paper['title']}. {paper['abstract'][:600]}"
            relevant   = retrieve_relevant_catalog(query, top_k=_CATALOG_TOP_K)
            catalog_en = _format_catalog_prompt(relevant, lang="en")
            catalog_ar = _format_catalog_prompt(relevant, lang="ar")

            content_en = generate_article_en(paper, seo_kw, catalog_en)
            if not content_en:
                logger.warning(f"[BlogGen] EN failed: {paper['arxiv_id']}")
                continue

            content_ar = generate_article_ar(content_en, catalog_ar)
            if not content_ar:
                logger.warning(f"[BlogGen] AR failed: {paper['arxiv_id']}")
                continue

            title_en = _extract_h1(content_en)
            title_ar = _extract_h1(content_ar)
            slug     = _make_slug(title_en)

            save_blog_post({
                "slug":         slug,
                "arxiv_id":     paper["arxiv_id"],
                "arxiv_url":    paper["url"],
                "title_en":     title_en,
                "title_ar":     title_ar,
                "content_en":   content_en,
                "content_ar":   content_ar,
                "summary_en":   _extract_summary(content_en),
                "summary_ar":   _extract_summary(content_ar),
                "seo_keywords": json.dumps(seo_kw),
                "published_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            })

            mark_arxiv_id_seen(paper["arxiv_id"])
            if paper.get("_embedding"):
                store_article_embedding(paper["arxiv_id"], paper["_embedding"])

            published.append({"slug": slug, "title_en": title_en, "arxiv_id": paper["arxiv_id"]})
            logger.info(f"[BlogGen] Published: /{slug}")
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"[BlogGen] Error on {paper.get('arxiv_id')}: {e}")
            continue

    return {"ok": True, "generated": len(published), "articles": published}


# ============================================================================
# SECTION 4 — FASTAPI ROUTER
# ============================================================================

blog_router = APIRouter()


def _templates():
    from services.auth import templates
    return templates


def _ctx(request: Request, lang: str, **kwargs) -> dict:
    try:
        from services.auth import get_template_context
        ctx = get_template_context(request, lang)
    except Exception:
        ctx = {}
    ctx.update({"request": request, "lang": lang, **kwargs})
    return ctx


def _safe_posts(posts: list) -> list:
    result = []
    for p in posts:
        sp = dict(p)
        if hasattr(sp.get("published_at"), "strftime"):
            sp["published_at"] = sp["published_at"].strftime("%Y-%m-%d")
        result.append(sp)
    return result


@blog_router.get("/{lang}/blog", response_class=HTMLResponse)
async def blog_listing(request: Request, lang: str, page: int = 1):
    if lang not in ("en", "ar"):
        return RedirectResponse("/en/blog", status_code=301)
    page         = max(1, page)
    posts, total = get_posts(page=page, limit=12)
    total_pages  = max(1, (total + 11) // 12)
    return _templates().TemplateResponse("blog.html", _ctx(
        request, lang,
        view="list",
        posts=_safe_posts(posts),
        page=page, total_pages=total_pages, total=total,
    ))


@blog_router.get("/{lang}/blog/{slug}", response_class=HTMLResponse)
async def blog_post_page(request: Request, lang: str, slug: str):
    if lang not in ("en", "ar"):
        return RedirectResponse(f"/en/blog/{slug}", status_code=301)
    post = get_post_by_slug(slug)
    if not post:
        return _templates().TemplateResponse(
            "blog.html",
            _ctx(request, lang, view="404", posts=[], page=1, total_pages=1, total=0),
            status_code=404,
        )
    post = dict(post)
    if hasattr(post.get("published_at"), "strftime"):
        post["published_at"] = post["published_at"].strftime("%Y-%m-%d")
    summary = post.get(f"summary_{lang}") or post.get("summary_en", "")
    if len(summary) > 160:
        summary = summary[:157] + "..."
    return _templates().TemplateResponse("blog.html", _ctx(
        request, lang, view="post", post=post, meta_description=summary,
    ))


@blog_router.post("/api/admin/blog/generate")
async def admin_generate_blog(request: Request):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body  = await request.json()
    except Exception:
        body  = {}
    count = max(1, min(int(body.get("count", 3)), 5))
    try:
        result = await run_blog_generation(count=count)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"[BlogRoute] generate: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@blog_router.post("/api/admin/blog/rebuild-catalog")
async def admin_rebuild_catalog(request: Request):
    """
    يجبر إعادة بناء تضمينات الكتالوج.
    استخدمه بعد إضافة نموذج أو أداة جديدة لضمان تضمينها فوراً.
    """
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # امسح جدول TiDB + hash Redis ليُعاد البناء
    conn = _get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM blog_catalog_embeds")
        except Exception as e:
            logger.warning(f"[RebuildCatalog] TiDB clear: {e}")
        finally:
            conn.close()

    r = _redis()
    if r:
        try:
            r.delete(_CATALOG_HASH_KEY)
        except Exception:
            pass

    stored = ensure_catalog_embeddings()
    return JSONResponse({
        "ok":      True,
        "total":   len(stored),
        "models":  [e["name"] for e in stored if e["type"] == "model"],
        "tools":   [e["name"] for e in stored if e["type"] == "tool"],
    })


@blog_router.get("/api/blog/catalog")
async def api_blog_catalog(request: Request):
    """يعرض الكتالوج الحالي (للتشخيص)."""
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    entries = _build_catalog_entries()
    return JSONResponse({
        "total":   len(entries),
        "models":  [{"name": e["name"], "key": e["short_key"], "has_file": bool(e["desc"])} for e in entries if e["type"] == "model"],
        "tools":   [{"name": e["name"], "key": e["short_key"]} for e in entries if e["type"] == "tool"],
    })


@blog_router.get("/api/blog/posts")
async def api_blog_list(page: int = 1, limit: int = 12):
    posts, total = get_posts(page=max(1, page), limit=min(limit, 20))
    safe = []
    for p in _safe_posts(posts):
        p.pop("content_en", None)
        p.pop("content_ar", None)
        safe.append(p)
    return JSONResponse({"posts": safe, "total": total, "page": page})


# ─── تهيئة عند الاستيراد ─────────────────────────────────────────────────────

try:
    init_blog_tables()
except Exception as _e:
    logger.warning(f"[Blog] Table init deferred: {_e}")
