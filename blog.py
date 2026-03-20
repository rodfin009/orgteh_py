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
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
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
            for _col_sql in [
                "ALTER TABLE blog_posts ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
                "ALTER TABLE blog_posts ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
            ]:
                try:
                    cur.execute(_col_sql)
                    logger.info(f"[BlogDB] Applied migration: {_col_sql[:60]}…")
                except Exception:
                    pass
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
            INSERT IGNORE INTO blog_posts
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
                   summary_en, summary_ar, published_at, updated_at
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
            cur.execute("SELECT slug, published_at, updated_at FROM blog_posts ORDER BY published_at DESC")
            rows = cur.fetchall()
        result = []
        for r in rows:
            pub = r["published_at"]
            pub = pub.strftime("%Y-%m-%d") if hasattr(pub, "strftime") else str(pub)[:10]
            upd = r.get("updated_at") or r["published_at"]
            upd = upd.strftime("%Y-%m-%d") if hasattr(upd, "strftime") else str(upd)[:10]
            result.append({"slug": r["slug"], "published_at": pub, "updated_at": upd})
        return result
    except Exception as e:
        logger.error(f"[BlogDB] get_all_slugs: {e}")
        return []
    finally:
        conn.close()

def delete_blog_post(slug: str) -> bool:
    """يحذف مقالة من DB بالـ slug."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM blog_posts WHERE slug=%s LIMIT 1", (slug,))
            deleted = cur.rowcount > 0
        logger.info(f"[BlogDB] delete_blog_post: slug={slug} deleted={deleted}")
        return deleted
    except Exception as e:
        logger.error(f"[BlogDB] delete_blog_post: {e}")
        return False
    finally:
        conn.close()

_CATALOG_HASH_KEY  = "blog:catalog_hash_v3"
_CATALOG_TOP_K     = 10

GENERATION_MODEL = "mistralai/mistral-large-3-675b-instruct-2512"
EMBED_MODEL      = "nvidia/llama-nemotron-embed-1b-v2"
RERANK_MODEL     = "nvidia/llama-nemotron-rerank-1b-v2"
_RERANK_THRESHOLD = 20

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
    return re.sub(r"\s+", " ", text).strip()[:3000]

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
        match = re.search(
            r'class="model-lang-content lang-en[^"]*">(.*?)</div>\s*(?:<div class="model-lang-content|<script)',
            raw, re.DOTALL | re.IGNORECASE
        )
        if match:
            return _strip_html(match.group(1))
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

    try:
        from services.providers import MODELS_METADATA
        for m in MODELS_METADATA:
            short_key = m.get("short_key", "")
            if not short_key:
                continue
            desc_from_file = _read_model_description_en(short_key)
            full_desc = desc_from_file or f"{m.get('name','')} AI model by {m.get('provider','')}."
            entries.append({
                "type":      "model",
                "short_key": short_key,
                "name":      m.get("name", short_key),
                "provider":  m.get("provider", ""),
                "link_tpl":  f"/{{lang}}/models/{short_key}",
                "desc":      full_desc[:2000],
            })
    except Exception as e:
        logger.error(f"[Catalog] models load error: {e}")

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
            input=[text[:3000]], model=EMBED_MODEL,
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

def _rerank(query: str, passages: list[str], top_k: int = None) -> list[int]:
    """
    يُعيد مؤشرات passages مرتبة من الأكثر صلة للأقل.
    يستخدم NVIDIA Rerank API المباشر (ليس OpenAI compatible).

    الاستخدام:
      indices = _rerank("موضوع المقالة", ["وصف نموذج 1", "وصف نموذج 2", ...])
      # indices[0] = رقم النموذج الأنسب

    Fallback: إذا فشل API يُرجع الترتيب الأصلي بدون تغيير.
    """
    if not passages:
        return list(range(len(passages)))

    import requests as _req

    key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
    if key:
        key = key.split(",")[0].strip()
    if not key:
        logger.warning("[Rerank] No API key — skipping rerank")
        return list(range(len(passages)))

    payload = {
        "model": RERANK_MODEL,
        "query": {"text": query[:1000]},
        "passages": [{"text": p[:2000]} for p in passages],
    }
    if top_k:
        payload["truncate"] = "END"

    try:
        resp = _req.post(
            f"https://ai.api.nvidia.com/v1/retrieval/{RERANK_MODEL}/reranking",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        rankings = data.get("rankings", [])
        if not rankings:
            return list(range(len(passages)))

        indices = [r["index"] for r in rankings]
        logger.info(f"[Rerank] Reranked {len(passages)} passages → top={indices[:5]}")
        return indices

    except Exception as e:
        logger.warning(f"[Rerank] API failed ({e}) — using original order")
        return list(range(len(passages)))

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

    if r:
        try:
            cached_hash = r.get(_CATALOG_HASH_KEY)
            if isinstance(cached_hash, bytes):
                cached_hash = cached_hash.decode()
            if cached_hash == cur_hash:
                stored = _load_catalog_from_tidb()
                if stored:
                    logger.info(f"[Catalog] TiDB hit — {len(stored)} entries (hash={cur_hash})")
                    return stored
        except Exception as e:
            logger.warning(f"[Catalog] Hash check: {e}")

    logger.info(f"[Catalog] Building embeddings for {len(entries)} entries…")
    stored = []
    for entry in entries:
        embed_text = f"{entry['name']} ({entry['provider']}). {entry['desc']}"
        emb = _embed_text(embed_text, input_type="passage")
        stored.append({**entry, "embedding": emb, "catalog_hash": cur_hash})

    _save_catalog_to_tidb(stored, cur_hash)

    if r:
        try:
            r.set(_CATALOG_HASH_KEY, cur_hash)
            r.expire(_CATALOG_HASH_KEY, 86_400 * 7)
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
                    entry.get("link_tpl", ""), entry.get("desc", "")[:3000],
                    emb_json, cur_hash,
                ))
        logger.info(f"[Catalog] Saved {len(stored)} entries to TiDB (hash={cur_hash})")
    except Exception as e:
        logger.error(f"[Catalog] TiDB save: {e}")
    finally:
        conn.close()

def retrieve_relevant_catalog(query_text: str, top_k: int = _CATALOG_TOP_K) -> list[dict]:
    """
    يسترجع أكثر top_k نموذج/أداة صلةً بالمقالة.

    المرحلة 1 — cosine (دائماً):
      يُرتّب كل الكتالوج بسرعة عبر التشابه الرياضي
      → يأخذ أفضل min(top_k × 3, len(catalog)) مرشحاً

    المرحلة 2 — Rerank (تلقائي عند المرشحون > _RERANK_THRESHOLD):
      يفهم السياق الدلالي العميق ويُعيد الترتيب بدقة أعلى
      → يُطبَّق فقط عند الحاجة (لا تكلفة غير ضرورية)

    Fallback: أول top_k من القائمة إذا فشل الـ embedding.
    """
    catalog   = ensure_catalog_embeddings()
    query_emb = _embed_text(query_text[:1500], input_type="query")

    if not query_emb:
        logger.warning("[Catalog] Query embed failed — using fallback")
        return list(catalog[:top_k])

    scored = [(_cosine(query_emb, e.get("embedding", [])), e) for e in catalog]
    scored.sort(key=lambda x: x[0], reverse=True)

    candidate_count = min(top_k * 3, len(scored))
    candidates = [e for _, e in scored[:candidate_count]]

    logger.info(
        f"[Catalog] Cosine top-{candidate_count}: {[e['name'] for e in candidates[:6]]}… "
        f"scores: {[round(s, 3) for s, _ in scored[:6]]}"
    )

    if candidate_count > _RERANK_THRESHOLD:
        logger.info(f"[Catalog] {candidate_count} candidates > threshold {_RERANK_THRESHOLD} → running Rerank")
        passages = [
            (e['name'] + " (" + e.get('provider', '') + "). " + e.get('desc', '')[:1500])
            for e in candidates
        ]
        indices   = _rerank(query_text[:800], passages, top_k=top_k)
        reranked  = [candidates[i] for i in indices[:top_k] if i < len(candidates)]
        if reranked:
            logger.info(f"[Catalog] After Rerank top-{top_k}: {[e['name'] for e in reranked]}")
            return reranked
        logger.warning("[Catalog] Rerank returned empty — falling back to cosine result")

    top = candidates[:top_k]
    logger.info(f"[Catalog] Final top-{top_k}: {[e['name'] for e in top]}")
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
        lines.append(f"  Description: {e['desc'][:1800]}")
        lines.append("")

    lines += [
        "HOW TO USE IN ARTICLE:",
        "• In the 'Integrating with Orgteh' section, recommend 1–3 of the above",
        "• Choose only what GENUINELY relates to the article's specific topic",
        "• Write recommendations as natural prose — not a bullet list",
        f"• ⚠️  COPY THE EXACT LINK SHOWN ABOVE — do NOT shorten or modify the URL",
        f"• ✅ CORRECT example:  [DeepSeek V3.2](/{lang}/models/deepseek)",
        f"• ❌ WRONG example:    [DeepSeek V3.2](/{lang}/models)   ← missing the model key!",
        f"• ❌ WRONG example:    [DeepSeek V3.2](/{lang}/models/)  ← missing the model key!",
        f"• The model key is the last part of the URL — it is REQUIRED",
        f"• ⚠️  NEVER use /{'ar' if lang=='en' else 'en'}/ links — this is a {lang.upper()} article",
    ]
    return "\n".join(lines)

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
    فلترة دلالية batch لمنع تكرار المواضيع.

    المرحلة 1 — cosine (دائماً):
      يجد الأوراق المشبوهة بسرعة (threshold = 0.87)

    المرحلة 2 — Rerank للتحقق (عند وجود مقالات منشورة كثيرة > _RERANK_THRESHOLD):
      يتحقق هل الورقة "مكررة دلالياً فعلاً" بدقة أعلى
      يقلل False Positives: cosine أحياناً يعتبر ورقتين متشابهتين وهما مختلفتان

    Returns: set of arxiv_ids التي تُعتبر مكررة
    """
    if not embeddings_map:
        return set()
    stored = _load_article_embeddings()
    if not stored:
        return set()

    cosine_suspects: dict[str, float] = {}
    for arxiv_id, emb in embeddings_map.items():
        if not emb:
            continue
        best_score = max(
            (_cosine(emb, item.get("embedding", [])) for item in stored),
            default=0.0
        )
        if best_score >= threshold:
            cosine_suspects[arxiv_id] = best_score

    if not cosine_suspects:
        return set()

    if len(stored) > _RERANK_THRESHOLD:
        logger.info(f"[Dedup] {len(stored)} stored articles — running Rerank verification on {len(cosine_suspects)} suspects")
        confirmed_dups: set = set()
        for arxiv_id in cosine_suspects:
            paper_text = next(
                (f"{arxiv_id}" for _ in [None]),
                arxiv_id
            )
            similar_stored = [
                item for item in stored
                if _cosine(embeddings_map.get(arxiv_id, []), item.get("embedding", [])) >= (threshold - 0.05)
            ]
            if not similar_stored:
                confirmed_dups.add(arxiv_id)
                continue

            passages = [item.get("arxiv_id", "") for item in similar_stored[:10]]
            indices  = _rerank(arxiv_id, [p for p in passages], top_k=1)

            if indices:
                confirmed_dups.add(arxiv_id)
                logger.info(f"[Dedup] Rerank confirmed duplicate: {arxiv_id} (cosine={cosine_suspects[arxiv_id]:.3f})")
            else:
                logger.info(f"[Dedup] Rerank rejected false positive: {arxiv_id} (cosine={cosine_suspects[arxiv_id]:.3f})")

        return confirmed_dups

    logger.info(f"[Dedup] {len(stored)} stored ≤ threshold — using cosine only, {len(cosine_suspects)} duplicates found")
    return set(cosine_suspects.keys())

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
            temperature=0.31, max_tokens=500, stream=False,
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

        payload = [{"keywords": [topic], "language_code": "en", "location_code": 2840}]
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

        scored = []
        for item in results:
            kw     = item.get("keyword", "")
            volume = item.get("search_volume") or 0
            comp   = item.get("competition_index") or 100
            if kw and volume > 50:
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
    llm_kws = _get_seo_keywords_llm(paper_title, paper_abstract)

    autocomplete_kws, dataforseo_kws = await asyncio.gather(
        _get_seo_keywords_autocomplete(paper_title.split(":")[0].strip()[:60]),
        _get_seo_keywords_dataforseo(paper_title.split(":")[0].strip()[:60]),
        return_exceptions=True,
    )
    if isinstance(autocomplete_kws, Exception):
        autocomplete_kws = []
    if isinstance(dataforseo_kws, Exception):
        dataforseo_kws = []

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

def _classify_paper_complexity(paper: dict) -> str:
    """
    يُصنّف الورقة إلى SIMPLE أو COMPLEX.
    يعتمد على LLM لأنه أدق من keywords.
    يُرجع: "simple" أو "complex"
    """
    prompt = f"""Classify this AI research paper as SIMPLE or COMPLEX for live API demos.

SIMPLE = can be demonstrated with ONE single API call (one prompt → one response):
  - Prompting techniques (CoT, few-shot, ReAct reasoning on one question)
  - Text classification, summarization, translation
  - Single-turn Q&A improvements
  - Evaluation of a single model output
  - Simple RAG with one retrieval step

COMPLEX = requires multiple files, services, or API calls:
  - Multi-agent systems
  - Fine-tuning or model training
  - Multi-step pipelines (>2 steps)
  - Vector databases or embedding pipelines
  - Code generation + execution loops
  - Multi-modal systems (image+text together)
  - Distributed or federated systems

Paper: {paper['title']}
Abstract: {paper['abstract'][:350]}

Reply with ONLY one word: SIMPLE or COMPLEX"""

    try:
        client = _build_nvidia_client()
        resp   = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=10, stream=False,
        )
        raw = resp.choices[0].message.content.strip().upper()
        result = "simple" if "SIMPLE" in raw else "complex"
        logger.info(f"[Demo] Paper classified as: {result.upper()}")
        return result
    except Exception as e:
        logger.warning(f"[Demo] Classification failed: {e} — defaulting to complex")
        return "complex"

async def _run_simple_demo(paper: dict, relevant_models: list[dict]) -> dict:
    """
    يُشغّل demo حقيقي للأوراق البسيطة.
    خطوتان فقط:
      1. LLM يصمم prompt مناسب (≤150 كلمة)
      2. نُرسله فعلاً للنموذج ونلتقط الرد
    """
    demo_model_id   = "deepseek-ai/deepseek-v3.2"
    demo_model_name = "DeepSeek V3.2"
    demo_model_key  = "deepseek"

    for entry in relevant_models:
        if entry.get("type") == "model" and entry.get("short_key"):
            try:
                from services.providers import MODELS_METADATA
                match = next((m for m in MODELS_METADATA if m.get("short_key") == entry["short_key"]), None)
                if match:
                    demo_model_id   = match["id"]
                    demo_model_name = match["name"]
                    demo_model_key  = entry["short_key"]
                    break
            except Exception:
                pass

    design_prompt = f"""Design a SHORT demo prompt (max 120 words) for this research paper.

Paper: {paper['title']}
Abstract: {paper['abstract'][:300]}

The demo must:
- Be answerable in ONE API call
- Demonstrate the paper's CORE concept clearly
- Be interesting for developers

Reply with ONLY two parts separated by |||:
PART 1: One sentence describing what this demo shows
PART 2: The actual prompt to send to the AI

Example:
Showing chain-of-thought on a logic puzzle|||Solve step by step: If all A are B, and some B are C, can we conclude some A are C? Show your reasoning.

Reply ONLY in this format. Nothing else."""

    try:
        client  = _build_nvidia_client()
        resp    = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": design_prompt}],
            temperature=0.31, max_tokens=300, stream=False,
        )
        raw = resp.choices[0].message.content.strip()
        if "|||" not in raw:
            return {}
        parts   = raw.split("|||", 1)
        concept = parts[0].strip()
        prompt  = parts[1].strip()
        if not prompt or len(prompt) < 15:
            return {}
    except Exception as e:
        logger.warning(f"[Demo] Prompt design failed: {e}")
        return {}

    try:
        nvidia_key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
        if nvidia_key:
            nvidia_key = nvidia_key.split(",")[0].strip()
        if not nvidia_key:
            return {}

        from openai import OpenAI
        demo_client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=nvidia_key)
        demo_resp   = demo_client.chat.completions.create(
            model=demo_model_id,
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant. Be concise and practical."},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.31, max_tokens=500, stream=False,
        )
        response_text = demo_resp.choices[0].message.content.strip()
        if not response_text or len(response_text) < 20:
            return {}

        logger.info(f"[Demo] ✅ Simple demo captured using {demo_model_name}")
        return {
            "type":       "live",
            "model_name": demo_model_name,
            "model_key":  demo_model_key,
            "prompt":     prompt,
            "response":   response_text,
            "concept":    concept,
        }
    except Exception as e:
        logger.warning(f"[Demo] Live call failed: {e}")
        return {}

def _build_complex_note(relevant_models: list[dict]) -> dict:
    """
    للأوراق المعقدة: يبني نص اقتراح بدلاً من التجربة الفعلية.
    يختار أنسب النماذج من الكتالوج ويقترح استخدامها.
    """
    model_mentions = []
    for entry in relevant_models[:2]:
        if entry.get("type") == "model":
            key  = entry.get("short_key", "")
            name = entry.get("name", "")
            if key and name:
                model_mentions.append(f"[{name}](/en/models/{key})")

    if model_mentions:
        models_str = " and ".join(model_mentions)
        suggestion = (
            f"While this pipeline requires multiple components to build fully, "
            f"you can start experimenting with the core concepts using {models_str} "
            f"on Orgteh API — no infrastructure setup required."
        )
    else:
        suggestion = (
            "You can start experimenting with the core concepts of this research "
            "using models available on [Orgteh API](/en/models) — "
            "access multiple frontier models through a single endpoint."
        )

    return {
        "type":       "note",
        "suggestion": suggestion,
    }

async def run_live_demo(paper: dict, relevant_models: list[dict]) -> dict:
    """
    نقطة الدخول الرئيسية:
      1. صنّف الورقة (SIMPLE / COMPLEX)
      2. SIMPLE  → شغّل demo حقيقي
      3. COMPLEX → ابنِ اقتراح نصي فقط
    """
    complexity = _classify_paper_complexity(paper)

    if complexity == "simple":
        result = await _run_simple_demo(paper, relevant_models)
        if result:
            return result
        logger.info("[Demo] Simple demo failed — falling back to note")

    return _build_complex_note(relevant_models)

def select_papers_with_llm(papers: list[dict], select_count: int = 3) -> list[dict]:
    top = sorted(papers, key=lambda p: p.get("_pre_score", 0), reverse=True)[:30]

    if len(top) > _RERANK_THRESHOLD:
        rerank_query = (
            "Practical AI research paper for software developers: "
            "LLM agents, RAG, prompt engineering, code generation, tool use, multimodal AI"
        )
        passages  = [f"{p['title']}. {p['abstract'][:600]}" for p in top]
        indices   = _rerank(rerank_query, passages)
        if indices:
            top = [top[i] for i in indices if i < len(top)]
            logger.info(f"[Select] Papers reranked: {[p['arxiv_id'] for p in top[:5]]}")

    top = top[:15]
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
            temperature=0.10, max_tokens=200, stream=False,
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

def generate_article_en(paper: dict, seo_keywords: list[str], catalog_ctx: str, demo_result: dict = None) -> Optional[str]:
    kw_str = ", ".join(seo_keywords[:15])

    demo_type = demo_result.get("type", "") if demo_result else ""

    if demo_type == "live":
        demo_instruction = f"""REAL LIVE DEMO — INCLUDE THIS IN THE ARTICLE:
   We actually ran this on Orgteh API using {demo_result['model_name']}.

   Concept being demonstrated: {demo_result['concept']}

   In "## Integrating with Orgteh", write naturally:
   "We ran a quick test using [{demo_result['model_name']}](/en/models/{demo_result['model_key']}) on Orgteh API
   to demonstrate [concept]. Here is exactly what we sent and got back:"

   Show these two fenced blocks:
   Input:
   ```
   {demo_result['prompt'][:400]}
   ```
   Output from {demo_result['model_name']}:
   ```
   {demo_result['response'][:500]}
   ```
   Then add 2-3 sentences analyzing what the output shows about the paper's concept."""

    elif demo_type == "note":
        demo_instruction = f"""COMPLEX PAPER — DO NOT claim to have run a demo.
   In "## Integrating with Orgteh", write this naturally in prose:
   "{demo_result['suggestion']}"
   Then explain briefly what a developer would need to build to implement the full system."""

    else:
        demo_instruction = """In "## Integrating with Orgteh", recommend the relevant models/tools
   with a clear explanation of why each one fits this paper's use case."""

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
1. Minimum 1600 words — comprehensive and detailed
2. Audience: developers and AI practitioners — practical, no heavy math
3. Markdown: # H1, ## H2, ### H3
4. Required sections IN THIS ORDER:
   ## Introduction
   ## What This Research Found
   ## Why It Matters for Developers
   ## Practical Applications
   ## Implementation Guide
   ## Integrating with Orgteh
   ## Key Takeaways

5. DIAGRAM REQUIREMENT — include exactly ONE Mermaid diagram in the article:
   - Place it inside "## What This Research Found" or "## Practical Applications"
   - Use ```mermaid fenced block
   - Choose the type that best fits the paper:
     * flowchart LR  → for pipelines, architectures, workflows
     * sequenceDiagram → for agent interactions, multi-step processes
     * graph TD       → for hierarchies, decision trees
   - Keep it simple: 5-9 nodes max, clear labels in English
   - The diagram must illustrate the paper's CORE concept — not generic
   - Example for an agent paper:
     ```mermaid
     flowchart LR
       A[User Query] --> B[LLM Agent]
       B --> C(Tool Needed?)
       C -->|Yes| D[Tool Call]
       C -->|No| E[Direct Answer]
       D --> B
     ```
   - DO NOT add a diagram if the paper is purely statistical/math-heavy

6. SECTION-SPECIFIC RULES:

   "## Introduction":
   - First paragraph ≤160 chars (used as meta description)
   - Cite the source naturally: "A recent study published on arXiv..."
   - Hook: why this research matters RIGHT NOW for developers

   "## What This Research Found":
   - Explain the core idea in plain language — no formulas
   - Use analogies if helpful

   "## Implementation Guide":
   - MUST include a real Python code example that uses Orgteh API:
     ```python
     from openai import OpenAI
     client = OpenAI(
         base_url="https://orgteh.com/v1",
         api_key="YOUR_ORGTEH_API_KEY"
     )
     # ... practical implementation of the paper's concept
     ```
   - The code must implement the paper's core idea using Orgteh API
   - Add inline comments explaining what each part does

   "## Integrating with Orgteh":
   - Use the ORGTEH MODELS & TOOLS provided above
   - Explain WHICH specific model/tool and WHY it fits this use case
   - Natural prose, NOT bullet list
   - Use exact markdown links (already have /en/ prefix)
   - {demo_instruction}

   "## Key Takeaways":
   - 3-5 concrete actionable points
   - End with a call-to-action toward Orgteh

7. NO INLINE CITATIONS:
   Do NOT include any "Source:" blockquote or arXiv link inside the article body.
   The source citation is automatically appended below the article — do not duplicate it.

8. Tone: conversational, like a senior engineer who READ the paper and TESTED the ideas

DO NOT:
- Copy sentences from the abstract verbatim
- Add preamble before the # H1 title
- Use /ar/ links (English article only)
- Write the code without using Orgteh API

Start directly with the # H1 title."""

    try:
        client  = _build_nvidia_client()
        content = ""
        for chunk in client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0.65, top_p=0.72, max_tokens=8192, stream=True,
        ):
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content
        return content.strip() or None
    except Exception as e:
        logger.error(f"[BlogGen] EN generation: {e}")
        return None

def generate_article_ar(english_content: str, catalog_ctx_ar: str) -> Optional[str]:
    prompt = f"""Translate the following English blog post to Modern Standard Arabic (الفصحى المُيسَّرة).

=== ORGTEH LINKS FOR ARABIC ===
{catalog_ctx_ar}

=== TRANSLATION RULES ===
1. Translate ALL text: title, all headings, every paragraph
2. Keep ALL markdown formatting exactly as-is (##, ###, **, blockquotes >, etc.)
3. Keep code blocks EXACTLY as-is — do not translate ANY code
4. Keep ```mermaid blocks EXACTLY as-is — do NOT translate diagram labels or content
4. Keep model names, API names, arXiv, "Orgteh" in English
5. CRITICAL LINK RULE — replace /en/ with /ar/ in ALL Orgteh internal links:
   [Model Name](/en/models/key) → [اسم النموذج](/ar/models/key)
   External http/https links stay completely unchanged.
6. For the citation blockquote, translate only the label:
   > **Source:** [...] → > **المصدر:** [...]  (keep URL unchanged)
7. Translate naturally — write as if originally authored in Arabic
8. First-person phrases like "We tested on Orgteh API" → "جربنا على Orgteh API"
9. Do NOT add preamble like "إليك الترجمة" — output ONLY the translated markdown
10. CRITICAL: Do NOT include any blockquote with "المصدر:" or "Source:" — the source card is added automatically by the template
10. Technical terms: RAG, LLM, API, prompt, token, fine-tuning → keep in English

=== ENGLISH ARTICLE ===
{english_content}

OUTPUT: Complete Arabic markdown article only. Nothing else."""

    try:
        client  = _build_nvidia_client()
        content = ""
        for chunk in client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25, top_p=0.67, max_tokens=8192, stream=True,
        ):
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content
        return content.strip() or None
    except Exception as e:
        logger.error(f"[BlogGen] AR translation: {e}")
        return None

def _fix_model_links(content: str, relevant: list[dict], lang: str) -> str:
    """
    يُصحّح روابط النماذج المكسورة في المحتوى المولَّد.
    المشكلة: LLM أحياناً يكتب /ar/models بدون المفتاح.
    """
    if not relevant or not content:
        return content
    for entry in relevant:
        if entry.get("type") != "model":
            continue
        short_key = entry.get("short_key", "")
        name      = entry.get("name", "")
        if not short_key or not name:
            continue
        correct_link = "/" + lang + "/models/" + short_key
        wrong_bare  = "](" + "/" + lang + "/models)"
        wrong_slash = "](" + "/" + lang + "/models/)"
        if wrong_bare in content or wrong_slash in content:
            content = content.replace(wrong_bare,  "](" + correct_link + ")")
            content = content.replace(wrong_slash, "](" + correct_link + ")")
            logger.info(f"[PostProcess] Fixed bare link for {name} → {correct_link}")
    return content


def _clean_generated_content(content):
    result = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith('>') and ('Source:' in s or '\u0627\u0644\u0645\u0635\u062f\u0631:' in s):
            continue
        result.append(line)
    out = '\n'.join(result)
    while '\n\n\n' in out:
        out = out.replace('\n\n\n', '\n\n')
    return out.strip()


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

async def run_blog_generation(count: int = 3) -> dict:
    count = max(1, min(count, 5))
    logger.info(f"[BlogGen] Starting pipeline — target {count} article(s)")

    papers = await fetch_all_papers()
    if not papers:
        return {"ok": False, "error": "Failed to fetch papers from all sources"}

    seen = get_seen_arxiv_ids()
    try:
        seen |= set(get_all_arxiv_ids())
    except Exception:
        pass
    new_papers = [p for p in papers if p.get("arxiv_id") not in seen]
    logger.info(f"[Pipeline] {len(new_papers)}/{len(papers)} papers are new")
    if not new_papers:
        return {"ok": False, "error": "No new papers — all entries already published"}

    for p in new_papers:
        p["_pre_score"] = _score_paper(p)
    top_new = sorted(new_papers, key=lambda p: p["_pre_score"], reverse=True)[:30]

    embed_map: dict[str, list[float]] = {}
    for p in top_new:
        text = f"{p['title']}. {p['abstract'][:500]}"
        emb  = _embed_text(text, input_type="passage")
        if emb:
            embed_map[p["arxiv_id"]] = emb
            p["_embedding"] = emb

    semantic_dups = _batch_semantic_dedup(embed_map, threshold=0.87)
    candidates    = [p for p in top_new if p["arxiv_id"] not in semantic_dups]
    logger.info(f"[Pipeline] {len(candidates)} candidates after semantic dedup (removed {len(semantic_dups)})")

    if not candidates:
        return {"ok": False, "error": "All remaining papers are semantically similar to existing posts"}

    selected  = select_papers_with_llm(candidates, select_count=count)
    published = []

    for paper in selected:
        try:
            logger.info(f"[BlogGen] Generating: {paper['title'][:70]}")

            topic  = paper["title"].split(":")[0].strip()[:60]
            seo_kw = await get_seo_keywords(paper["title"], paper["abstract"])

            query = f"{paper['title']}. {paper['abstract'][:600]}"
            relevant   = retrieve_relevant_catalog(query, top_k=_CATALOG_TOP_K)
            catalog_en = _format_catalog_prompt(relevant, lang="en")
            catalog_ar = _format_catalog_prompt(relevant, lang="ar")

            demo_result = await run_live_demo(paper, relevant)
            if demo_result:
                logger.info(f"[BlogGen] ✅ Live demo captured: {demo_result['model_name']}")
            else:
                logger.info(f"[BlogGen] No demo — article will be written without it")

            content_en = generate_article_en(paper, seo_kw, catalog_en, demo_result=demo_result)
            if not content_en:
                logger.warning(f"[BlogGen] EN failed: {paper['arxiv_id']}")
                continue
            content_en = _fix_model_links(content_en, relevant, lang="en")
            content_en = _clean_generated_content(content_en)

            content_ar = generate_article_ar(content_en, catalog_ar)
            if not content_ar:
                logger.warning(f"[BlogGen] AR failed: {paper['arxiv_id']}")
                continue
            content_ar = _fix_model_links(content_ar, relevant, lang="ar")
            content_ar = _clean_generated_content(content_ar)

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

"""
PATCH لـ blog.py — استبدل كل شيء من السطر 1600 حتى النهاية بهذا الكود
===========================================================================
إصلاحان:
  1. ترتيب الراوتات: /api/blog/cron كانت تُطابَق مع /{lang}/blog/{slug}
     لأن FastAPI يمر على الراوتات بالترتيب → /api مطابق لـ {lang}="api"
     الحل: ضع جميع مسارات /api/ قبل /{lang}/ دائماً

  2. Cron endpoint جديد: يُرجع HTML streaming بطوابع زمنية تفصيلية
     عند الدخول من المتصفح (Accept: text/html)
     وJSON نظيف عند الاستدعاء من cron job (Accept: application/json)
"""

blog_router = APIRouter()

_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "rodfin0202@gmail.com").strip().lower()

def _is_admin(email: str) -> bool:
    if not email:
        return False
    return email.strip().lower() == _ADMIN_EMAIL

def _templates():
    from services.auth import templates
    return templates

def _ctx(request: Request, lang: str, **kwargs) -> dict:
    try:
        from services.auth import get_template_context, get_current_user_email
        ctx = get_template_context(request, lang)
        email = get_current_user_email(request)
        ctx["is_admin"] = _is_admin(email or "")
    except Exception:
        ctx = {}
        ctx["is_admin"] = False
    ctx.update({"request": request, "lang": lang, **kwargs})
    return ctx

def _safe_posts(posts: list) -> list:
    result = []
    for p in posts:
        sp = dict(p)
        if hasattr(sp.get("published_at"), "strftime"):
            sp["published_at"] = sp["published_at"].strftime("%Y-%m-%d")
        if hasattr(sp.get("updated_at"), "strftime"):
            sp["updated_at"] = sp["updated_at"].strftime("%Y-%m-%d")
        elif not sp.get("updated_at"):
            sp["updated_at"] = sp.get("published_at", "")
        result.append(sp)
    return result

_CRON_SECRET = os.environ.get("BLOG_CRON_SECRET", "")

@blog_router.get("/api/blog/posts")
async def api_blog_list(page: int = 1, limit: int = 12):
    posts, total = get_posts(page=max(1, page), limit=min(limit, 20))
    safe = []
    for p in _safe_posts(posts):
        p.pop("content_en", None)
        p.pop("content_ar", None)
        safe.append(p)
    return JSONResponse({"posts": safe, "total": total, "page": page})

@blog_router.get("/api/blog/catalog")
async def api_blog_catalog(request: Request):
    """يعرض الكتالوج الحالي (للتشخيص)."""
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    entries = _build_catalog_entries()
    return JSONResponse({
        "total":   len(entries),
        "models":  [{"name": e["name"], "key": e["short_key"], "has_file": bool(e["desc"])} for e in entries if e["type"] == "model"],
        "tools":   [{"name": e["name"], "key": e["short_key"]} for e in entries if e["type"] == "tool"],
    })

@blog_router.post("/api/admin/blog/generate")
async def admin_generate_blog(request: Request):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    count = max(1, min(int(body.get("count", 3)), 5))
    try:
        result = await run_blog_generation(count=count)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"[BlogRoute] generate: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@blog_router.post("/api/admin/blog/rebuild-catalog")
async def admin_rebuild_catalog(request: Request):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

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

@blog_router.delete("/api/admin/blog/delete/{slug}")
async def admin_delete_post(request: Request, slug: str):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    ok = delete_blog_post(slug)
    if ok:
        logger.info(f"[BlogAdmin] Deleted: {slug} by {email}")
        return JSONResponse({"ok": True, "slug": slug})
    return JSONResponse({"ok": False, "error": "Slug not found"}, status_code=404)

@blog_router.get("/api/admin/blog/edit/{slug}", response_class=HTMLResponse)
async def admin_edit_post_page(request: Request, slug: str):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return RedirectResponse("/en/login", status_code=302)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    post = get_post_by_slug(slug)
    if not post:
        return JSONResponse({"error": "Not found"}, status_code=404)
    post = dict(post)
    title_en = (post.get("title_en") or "").replace('"', '&quot;')
    title_ar = (post.get("title_ar") or "").replace('"', '&quot;')
    content_en = (post.get("content_en") or "").replace('</textarea>', '<' + '/textarea>')
    content_ar = (post.get("content_ar") or "").replace('</textarea>', '<' + '/textarea>')
    html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edit: {slug}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f0f1a;color:#e0e0e0;font-family:sans-serif;padding:24px;font-size:14px}}
  h1{{color:#a78bfa;margin-bottom:20px;font-size:18px}}
  label{{display:block;margin:14px 0 4px;color:#9ca3af;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}}
  input[type=text],textarea{{width:100%;background:#1a1a2e;border:1px solid #374151;border-radius:8px;
    color:#e0e0e0;padding:10px 12px;font-size:13px;font-family:monospace}}
  textarea{{height:320px;resize:vertical}}
  .row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:8px}}
  .btn{{padding:10px 22px;border-radius:9px;font-weight:700;cursor:pointer;border:none;font-size:13px;margin-top:16px;margin-right:8px}}
  .btn-save{{background:linear-gradient(135deg,#7c3aed,#5b21b6);color:#fff}}
  .btn-cancel{{background:rgba(255,255,255,0.07);color:#9ca3af}}
  #msg{{margin-top:12px;font-size:13px;padding:8px 12px;border-radius:6px}}
  .ok{{background:#052e16;color:#4ade80;border:1px solid #4ade80}}
  .err{{background:#2d0a0a;color:#f87171;border:1px solid #f87171}}
</style>
</head>
<body>
<h1>✏️ Edit Article — <code style="font-size:14px;color:#c4b5fd">{slug}</code></h1>
<div class="row">
  <div>
    <label>Title (EN)</label>
    <input type="text" id="title_en" value="{title_en}">
  </div>
  <div>
    <label>Title (AR)</label>
    <input type="text" id="title_ar" value="{title_ar}" dir="rtl">
  </div>
</div>
<div class="row">
  <div>
    <label>Content EN (Markdown)</label>
    <textarea id="content_en">{content_en}</textarea>
  </div>
  <div>
    <label>Content AR (Markdown)</label>
    <textarea id="content_ar" dir="rtl">{content_ar}</textarea>
  </div>
</div>
<button class="btn btn-save" onclick="savePost()">💾 Save Changes</button>
<a href="/en/blog/{slug}"><button class="btn btn-cancel" type="button">Cancel</button></a>
<div id="msg" style="display:none"></div>
<script>
async function savePost() {{
  const body = {{
    title_en:   document.getElementById('title_en').value,
    title_ar:   document.getElementById('title_ar').value,
    content_en: document.getElementById('content_en').value,
    content_ar: document.getElementById('content_ar').value,
  }};
  const msg = document.getElementById('msg');
  msg.style.display = 'none';
  try {{
    const res  = await fetch('/api/admin/blog/update/{slug}', {{
      method: 'PUT', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body)
    }});
    const data = await res.json();
    msg.style.display = 'block';
    if (data.ok) {{
      msg.className = 'ok'; msg.textContent = '✅ Saved! Reloading…';
      setTimeout(() => location.reload(), 1500);
    }} else {{
      msg.className = 'err'; msg.textContent = '❌ ' + (data.error || 'Save failed');
    }}
  }} catch(e) {{
    msg.style.display = 'block'; msg.className = 'err'; msg.textContent = '❌ Network error: ' + e.message;
  }}
}}
</script>
</body></html>"""
    from fastapi.responses import HTMLResponse as _HR
    return _HR(html_page)

@blog_router.put("/api/admin/blog/update/{slug}")
async def admin_update_post(request: Request, slug: str):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    conn = _get_conn()
    if not conn:
        return JSONResponse({"error": "No DB connection"}, status_code=500)
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE blog_posts SET
              title_en   = %s,
              title_ar   = %s,
              content_en = %s,
              content_ar = %s
            WHERE slug = %s LIMIT 1
            """, (
                body.get("title_en", ""),
                body.get("title_ar", ""),
                body.get("content_en", ""),
                body.get("content_ar", ""),
                slug,
            ))
            updated = cur.rowcount > 0
        logger.info(f"[BlogAdmin] Updated: {slug} by {email}")
        return JSONResponse({"ok": updated, "slug": slug})
    except Exception as e:
        logger.error(f"[BlogAdmin] update: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()

def _ts() -> str:
    """Timestamp بصيغة HH:MM:SS UTC."""
    return datetime.utcnow().strftime("%H:%M:%S")

async def _run_blog_generation_verbose(count: int):
    """
    Generator يُنفّذ خط أنابيب التوليد خطوة بخطوة
    ويُرسل سجلات HTML لحظةً بلحظة.

    Yields: str  → سطر HTML جاهز للبث
            أو tuple ("__done__", result_dict) في النهاية
    """
    import traceback

    def log(icon: str, msg: str, color: str = "#e0e0e0") -> str:
        ts = _ts()
        return (
            f'<div class="log-line">'
            f'<span class="ts">[{ts}]</span> '
            f'<span class="icon">{icon}</span> '
            f'<span style="color:{color}">{msg}</span>'
            f'</div>\n'
            f'<script>window.scrollTo(0,document.body.scrollHeight);</script>\n'
        )

    def log_ok(msg):   return log("✅", msg, "#4ade80")
    def log_info(msg): return log("📌", msg, "#93c5fd")
    def log_warn(msg): return log("⚠️",  msg, "#fbbf24")
    def log_err(msg):  return log("❌", msg, "#f87171")
    def log_step(msg): return log("🔹", msg, "#c4b5fd")

    yield log_step(f"Pipeline started — target: <b>{count}</b> article(s)")
    yield log_info("Phase 1: Fetching papers from arXiv (4 queries) + HuggingFace Daily Papers…")

    try:
        papers = await fetch_all_papers()
    except Exception as e:
        yield log_err(f"fetch_all_papers() raised: {e}")
        yield ("__done__", {"ok": False, "error": str(e)})
        return

    if not papers:
        yield log_err("No papers returned from any source")
        yield ("__done__", {"ok": False, "error": "Failed to fetch papers from all sources"})
        return

    yield log_ok(f"Fetched <b>{len(papers)}</b> unique papers total")

    yield log_info("Phase 2: Exact dedup — checking Redis seen_ids + TiDB arxiv_ids…")
    seen = get_seen_arxiv_ids()
    yield log_info(f"Redis seen_ids: <b>{len(seen)}</b> entries")

    try:
        db_ids = set(get_all_arxiv_ids())
        seen |= db_ids
        yield log_info(f"TiDB published: <b>{len(db_ids)}</b> articles")
    except Exception as e:
        yield log_warn(f"Could not load TiDB arxiv_ids: {e}")

    new_papers = [p for p in papers if p.get("arxiv_id") not in seen]
    yield log_ok(f"New papers (not yet published): <b>{len(new_papers)}</b> / {len(papers)}")

    if not new_papers:
        yield log_warn("All fetched papers already published — nothing to generate")
        yield ("__done__", {"ok": False, "error": "No new papers — all entries already published"})
        return

    yield log_info("Pre-scoring papers (recency + HF upvotes + practical keywords)…")
    for p in new_papers:
        p["_pre_score"] = _score_paper(p)
    top_new = sorted(new_papers, key=lambda p: p["_pre_score"], reverse=True)[:30]
    yield log_ok(f"Top-30 pre-scored. Best score: <b>{top_new[0]['_pre_score']}</b> — «{top_new[0]['title'][:60]}…»")

    yield log_info(f"Phase 3: Semantic dedup — embedding top {len(top_new)} papers (NVIDIA embed model)…")
    embed_map: dict = {}
    failed_embeds = 0
    for p in top_new:
        text = f"{p['title']}. {p['abstract'][:500]}"
        emb  = _embed_text(text, input_type="passage")
        if emb:
            embed_map[p["arxiv_id"]] = emb
            p["_embedding"] = emb
        else:
            failed_embeds += 1

    if failed_embeds:
        yield log_warn(f"{failed_embeds} papers failed embedding (will still be considered)")
    yield log_ok(f"Embedded <b>{len(embed_map)}</b> papers")

    semantic_dups = _batch_semantic_dedup(embed_map, threshold=0.87)
    candidates    = [p for p in top_new if p["arxiv_id"] not in semantic_dups]
    yield log_ok(
        f"After semantic dedup: <b>{len(candidates)}</b> candidates "
        f"(removed {len(semantic_dups)} duplicates, threshold=0.87)"
    )

    if not candidates:
        yield log_err("All remaining papers are semantically similar to existing posts")
        yield ("__done__", {"ok": False, "error": "All remaining papers are semantically similar to existing posts"})
        return

    yield log_info(f"Phase 4: LLM selecting best {count} paper(s) from top-15 candidates…")
    try:
        selected = select_papers_with_llm(candidates, select_count=count)
    except Exception as e:
        yield log_warn(f"LLM selection failed ({e}) — using top-scored fallback")
        selected = sorted(candidates, key=lambda p: p.get("_pre_score", 0), reverse=True)[:count]

    yield log_ok(f"Selected <b>{len(selected)}</b> paper(s):")
    for i, p in enumerate(selected, 1):
        src = "🤗 HF" if p.get("source") == "hf_daily" else "📄 arXiv"
        yield log("  ", f"{i}. [{src}] {p['title'][:80]}… (score={p.get('_pre_score',0)})", "#d1d5db")

    published = []

    for idx, paper in enumerate(selected, 1):
        yield log_step(f"━━━ Article {idx}/{len(selected)}: «{paper['title'][:65]}…» ━━━")

        try:
            yield log_info("  Generating SEO keywords (LLM + Google Autocomplete)…")
            seo_kw = await get_seo_keywords(paper["title"], paper["abstract"])
            yield log_ok(f"  SEO keywords: <b>{len(seo_kw)}</b> — [{', '.join(seo_kw[:5])}…]")

            yield log_info("  RAG: retrieving relevant catalog models/tools…")
            query = f"{paper['title']}. {paper['abstract'][:600]}"
            relevant   = retrieve_relevant_catalog(query, top_k=_CATALOG_TOP_K)
            catalog_en = _format_catalog_prompt(relevant, lang="en")
            catalog_ar = _format_catalog_prompt(relevant, lang="ar")
            rel_names  = [e["name"] for e in relevant]
            yield log_ok(f"  Catalog: [{', '.join(rel_names)}]")

            yield log_info("  Running live demo (classify paper complexity → run or suggest)…")
            demo_result = await run_live_demo(paper, relevant)
            if demo_result and demo_result.get("type") == "live":
                yield log_ok(f"  Live demo ✅ — model: {demo_result.get('model_name','?')}")
            elif demo_result and demo_result.get("type") == "note":
                yield log_info("  Demo type: complex paper — written suggestion only")
            else:
                yield log_warn("  No demo captured — article will proceed without it")

            yield log_info("  Generating English article (streaming, ~1600+ words)…")
            content_en = generate_article_en(paper, seo_kw, catalog_en, demo_result=demo_result)
            if not content_en:
                yield log_err(f"  EN generation returned empty — skipping paper {paper['arxiv_id']}")
                continue
            yield log_ok(f"  EN article: <b>{len(content_en.split())}</b> words")

            yield log_info("  Translating to Arabic…")
            content_ar = generate_article_ar(content_en, catalog_ar)
            if not content_ar:
                yield log_err(f"  AR translation returned empty — skipping paper {paper['arxiv_id']}")
                continue
            yield log_ok(f"  AR article: <b>{len(content_ar.split())}</b> words")

            title_en = _extract_h1(content_en)
            title_ar = _extract_h1(content_ar)
            slug     = _make_slug(title_en)

            yield log_info(f"  Saving to DB — slug: <code>{slug}</code>")
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
            yield log_ok(
                f"  🎉 Published! "
                f'<a href="/en/blog/{slug}" target="_blank" style="color:#818cf8">'
                f'/en/blog/{slug}</a>'
            )

            if idx < len(selected):
                yield log_info("  Waiting 3s before next article…")
                await asyncio.sleep(3)

        except Exception as e:
            tb = traceback.format_exc()
            yield log_err(f"  Unexpected error on {paper.get('arxiv_id')}: {e}")
            yield log("  ", f"<pre style='color:#f87171;font-size:11px'>{tb[:600]}</pre>", "#f87171")
            logger.error(f"[BlogGen] Error on {paper.get('arxiv_id')}: {e}\n{tb}")
            continue

    result = {
        "ok":         True,
        "generated":  len(published),
        "articles":   published,
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    }
    if len(published) == 0:
        result["ok"] = False
        result["error"] = "Pipeline ran but no articles were saved"

    yield ("__done__", result)

_CRON_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orgteh Blog Cron — Live Log</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0f0f1a;
    color: #e0e0e0;
    font-family: 'Courier New', monospace;
    font-size: 13px;
    padding: 20px;
  }}
  .header {{
    border-bottom: 1px solid #7c3aed55;
    padding-bottom: 14px;
    margin-bottom: 18px;
  }}
  .header h1 {{ color: #a78bfa; font-size: 18px; margin-bottom: 4px; }}
  .header p  {{ color: #6b7280; font-size: 12px; }}
  .log-line  {{ padding: 3px 0; line-height: 1.6; }}
  .ts        {{ color: #4b5563; margin-right: 6px; }}
  .icon      {{ margin-right: 4px; }}
  code       {{ background: #1e1b4b; padding: 1px 5px; border-radius: 3px; color: #c4b5fd; }}
  pre        {{ background: #1a0000; padding: 8px; border-radius: 4px; margin: 4px 0; overflow-x: auto; }}
  a          {{ text-decoration: none; }}
  a:hover    {{ text-decoration: underline; }}
  .divider   {{ border-top: 1px solid #1e1b4b; margin: 10px 0; }}
  .result-box {{
    margin-top: 20px;
    padding: 14px 18px;
    border-radius: 8px;
    border: 1px solid;
  }}
  .result-ok  {{ border-color: #4ade80; background: #052e16; }}
  .result-err {{ border-color: #f87171; background: #2d0a0a; }}
  .result-box h2 {{ font-size: 15px; margin-bottom: 8px; }}
  .result-box pre {{ background: #00000044; padding: 10px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
<div class="header">
  <h1>🍄 Orgteh Blog — Cron Pipeline Log</h1>
  <p>Started at {start_time} UTC — count={count}</p>
</div>
<div id="log">
"""

_CRON_HTML_FOOT_OK = """
</div>
<div class="result-box result-ok">
  <h2>✅ Pipeline completed successfully</h2>
  <pre>{result_json}</pre>
</div>
</body></html>
"""

_CRON_HTML_FOOT_ERR = """
</div>
<div class="result-box result-err">
  <h2>❌ Pipeline finished with errors</h2>
  <pre>{result_json}</pre>
</div>
</body></html>
"""

@blog_router.get("/api/blog/cron")
async def blog_cron_trigger(request: Request, secret: str = "", count: int = 3):
    """
    GET /api/blog/cron?secret=YOUR_SECRET&count=3

    • من المتصفح → يُرجع صفحة HTML streaming بسجلات لحظية تفصيلية
    • من curl / cron job → يُرجع JSON نظيف (عند إرسال Accept: application/json)

    secret  — يطابق BLOG_CRON_SECRET في متغيرات البيئة (إلزامي)
    count   — عدد المقالات (1-5، افتراضي 3)
    """
    if not _CRON_SECRET:
        logger.warning("[Cron] BLOG_CRON_SECRET not configured")
        err = {"ok": False, "error": "Set BLOG_CRON_SECRET env variable first"}
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            html = (
                "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                "<style>body{background:#0f0f1a;color:#f87171;font-family:monospace;"
                "padding:30px}</style></head><body>"
                "<h2>❌ BLOG_CRON_SECRET not configured</h2>"
                "<p>Please set the <code>BLOG_CRON_SECRET</code> environment variable in Vercel.</p>"
                "</body></html>"
            )
            from fastapi.responses import HTMLResponse
            return HTMLResponse(html, status_code=503)
        return JSONResponse(err, status_code=503)

    if not secret or secret != _CRON_SECRET:
        logger.warning("[Cron] Invalid secret")
        err = {"ok": False, "error": "Invalid secret"}
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            html = (
                "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                "<style>body{background:#0f0f1a;color:#f87171;font-family:monospace;"
                "padding:30px}</style></head><body>"
                "<h2>❌ 401 — Invalid secret</h2>"
                "</body></html>"
            )
            from fastapi.responses import HTMLResponse
            return HTMLResponse(html, status_code=401)
        return JSONResponse(err, status_code=401)

    count = max(1, min(int(count), 5))
    logger.info(f"[Cron] Triggered — {count} article(s)")

    accept     = request.headers.get("accept", "")
    is_browser = "text/html" in accept

    if not is_browser:
        try:
            result = await run_blog_generation(count=count)
            result["triggered_at"] = datetime.utcnow().isoformat() + "Z"
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"[Cron] Error: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    start_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    async def stream_html():
        yield _CRON_HTML_HEAD.format(start_time=start_time, count=count).encode("utf-8")

        final_result = {"ok": False, "error": "Generator did not complete"}

        try:
            async for item in _run_blog_generation_verbose(count=count):
                if isinstance(item, tuple) and item[0] == "__done__":
                    final_result = item[1]
                else:
                    yield item.encode("utf-8")
                await asyncio.sleep(0)

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            yield (
                f'<div class="log-line"><span style="color:#f87171">❌ Fatal error: {e}</span></div>\n'
                f'<pre style="color:#f87171;font-size:11px">{tb[:800]}</pre>\n'
            ).encode("utf-8")
            final_result = {"ok": False, "error": str(e)}

        result_json = json.dumps(final_result, ensure_ascii=False, indent=2)
        if final_result.get("ok"):
            yield _CRON_HTML_FOOT_OK.format(result_json=result_json).encode("utf-8")
        else:
            yield _CRON_HTML_FOOT_ERR.format(result_json=result_json).encode("utf-8")

    from fastapi.responses import StreamingResponse as _SR
    return _SR(
        stream_html(),
        media_type="text/html; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache, no-transform",
            "Transfer-Encoding": "chunked",
        },
    )

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
    if hasattr(post.get("updated_at"), "strftime"):
        post["updated_at"] = post["updated_at"].strftime("%Y-%m-%d")
    elif not post.get("updated_at"):
        post["updated_at"] = post.get("published_at", "")
    summary = post.get(f"summary_{lang}") or post.get("summary_en", "")
    if len(summary) > 160:
        summary = summary[:157] + "..."
    return _templates().TemplateResponse("blog.html", _ctx(
        request, lang, view="post", post=post, meta_description=summary,
    ))

try:
    init_blog_tables()
except Exception as _e:
    logger.warning(f"[Blog] Table init deferred: {_e}")