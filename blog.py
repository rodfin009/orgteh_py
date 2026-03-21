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

def update_blog_post_ar(slug: str, title_ar: str, content_ar: str, summary_ar: str):
    conn = _get_conn()
    if not conn:
        raise RuntimeError("[BlogDB] No DB connection")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE blog_posts SET title_ar=%s, content_ar=%s, summary_ar=%s WHERE slug=%s LIMIT 1
            """, (title_ar, content_ar, summary_ar, slug))
        logger.info(f"[BlogDB] AR updated: {slug}")
    except Exception as e:
        logger.error(f"[BlogDB] update_blog_post_ar: {e}")
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

GENERATION_MODEL = "moonshotai/kimi-k2-instruct-0905"
EMBED_MODEL      = "nvidia/llama-nemotron-embed-1b-v2"
RERANK_MODEL     = "nvidia/llama-nemotron-rerank-1b-v2"
_RERANK_THRESHOLD = 20

class _HTMLTextExtractor(HTMLParser):
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
    entries = []

    try:
        from services.providers import MODELS_METADATA
        for m in MODELS_METADATA:
            short_key = m.get("short_key", "")
            if not short_key:
                continue
            desc_from_file = _read_model_description_en(short_key)
            full_desc = desc_from_file or f"{m.get('name','')} AI model by {m.get('provider','')}."
            modalities = m.get("modalities", [])
            if modalities:
                modality_str = ", ".join(modalities)
                full_desc = f"[MODALITIES: {modality_str}] " + full_desc

            input_fmt = m.get("input_format", {})
            if input_fmt:
                fmt_note = input_fmt.get("note", "")
                img_fmt  = input_fmt.get("image", "")
                aud_fmt  = input_fmt.get("audio", "")
                fmt_parts = []
                if img_fmt:  fmt_parts.append(f"Image format: {img_fmt}")
                if aud_fmt:  fmt_parts.append(f"Audio format: {aud_fmt}")
                if fmt_note: fmt_parts.append(f"Note: {fmt_note}")
                if fmt_parts:
                    full_desc += " [INPUT FORMAT: " + " | ".join(fmt_parts) + "]"
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
        _TOOL_API_SNIPPETS = {
        'orgteh-semantic-embed': "Semantic Core V2 — Vector Embeddings. API call: POST https://orgteh.com/api/tools/execute/orgteh-semantic-embed with headers {Authorization: Bearer YOUR_KEY} and body {text_input: 'your text', truncate: 'NONE'}. Returns {embedding: [float, ...], model: 'nemotron-embed-1b'}.",
        'orgteh-web-scraper': "Web Scraper Tool. API call: POST https://orgteh.com/api/tools/execute/orgteh-web-scraper with headers {Authorization: Bearer YOUR_KEY} and body {url: 'https://example.com', format: 'markdown'}. Returns {content: 'scraped text', title: '...', url: '...'}.",
        'orgteh-finance-rss': "Finance RSS Tool — live market news. API call: POST https://orgteh.com/api/tools/execute/orgteh-finance-rss with headers {Authorization: Bearer YOUR_KEY} and body {limit: 3, lang: 'en', time_filter: '1d', scrape_content: 'true'}. Returns list of {title, url, content, published_at}.",
        'orgteh-news-general': "General News RSS Tool. API call: POST https://orgteh.com/api/tools/execute/orgteh-news-general with headers {Authorization: Bearer YOUR_KEY} and body {limit: 3, lang: 'en', time_filter: '1d'}. Returns list of {title, url, summary, source}.",
        'orgteh-vision-ocr': "Vision OCR Tool — extract text from images. API call: POST https://orgteh.com/api/tools/execute/orgteh-vision-ocr with headers {Authorization: Bearer YOUR_KEY} and body {image_url: 'https://...', lang: 'en'}. Returns {text: 'extracted text', confidence: 0.99}.",
        }
        for tool_id, tool in TOOLS_DB.items():
            base_desc = (tool.get("desc_en") or tool.get("name_en") or tool_id)[:600]
            api_snippet = _TOOL_API_SNIPPETS.get(tool_id, "")
            full_desc = (base_desc + " " + api_snippet).strip()[:1800]
            entries.append({
                "type":      "tool",
                "short_key": tool_id,
                "name":      tool.get("name_en", tool_id),
                "provider":  "Orgteh",
                "link_tpl":  f"/{{lang}}/accesory/{tool_id}",
                "desc":      full_desc,
            })
    except Exception as e:
        logger.error(f"[Catalog] tools load error: {e}")

    n_models = sum(1 for e in entries if e["type"] == "model")
    n_tools  = sum(1 for e in entries if e["type"] == "tool")
    logger.info(f"[Catalog] Built {len(entries)} entries ({n_models} models, {n_tools} tools)")
    return entries

def _catalog_hash(entries: list[dict]) -> str:
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
\
\
\
\
\
\
\
\
\

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
    if not relevant:
        return "(No specific Orgteh models are particularly relevant to this topic.)"

    lines = [
        f"=== ORGTEH PLATFORM — RECOMMENDED RESOURCES FOR THIS ARTICLE ===",
        f"Article language: {lang.upper()} — ALL markdown links MUST start with /{lang}/",
        "",
        "MODEL SELECTION BY MODALITY — read [MODALITIES: ...] tag on each model below and choose accordingly:",
        "• If article topic involves audio/video/speech/sound → choose models with 'audio' or 'video' in modalities",
        "• If article topic involves images/vision/screenshots → choose models with 'images' in modalities",
        "• If article topic involves code/programming → choose models with 'code' in modalities",
        "• If article topic involves reasoning/math → choose models with 'reasoning' in modalities",
        "Each model entry shows its exact modalities — match them to the article's subject matter.",
        "",
        "IMPORTANT — ORGTEH TOOLS API FORMAT:",
        "When writing code examples that use Orgteh TOOLS (not chat models), use this pattern:",
        "  import requests",
        "  response = requests.post(",
        '      "https://orgteh.com/api/tools/execute/{tool_id}",',
        '      headers={"Authorization": "Bearer YOUR_ORGTEH_API_KEY", "Content-Type": "application/json"},',
        '      json={...tool_params...}',
        "  )",
        "DO NOT use openai client or nvidia endpoints for tools — tools use their own REST endpoint.",
        "Each TOOL entry below includes its exact API call format in the Description.",
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
\
\
\
\
\
\
\
\
\
\
\

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
            temperature=0.45, top_p=0.85, max_tokens=512, stream=False,
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
\
\
\
\

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
            temperature=0.0, top_p=1.0, max_tokens=10, stream=False,
        )
        raw = resp.choices[0].message.content.strip().upper()
        result = "simple" if "SIMPLE" in raw else "complex"
        logger.info(f"[Demo] Paper classified as: {result.upper()}")
        return result
    except Exception as e:
        logger.warning(f"[Demo] Classification failed: {e} — defaulting to complex")
        return "complex"

async def _run_simple_demo(paper: dict, relevant_models: list[dict]) -> dict:
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
            temperature=0.20, top_p=0.90, max_tokens=400, stream=False,
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
            "Practical AI research for developers: agents, prompting, RAG, tool use, LLM APIs"
        )
        passages = [f"{p['title']}. {p['abstract'][:600]}" for p in top]
        indices  = _rerank(rerank_query, passages)
        if indices:
            top = [top[i] for i in indices if i < len(top)]

    top = top[:15]
    numbered = "\n".join(
        f"{i+1}. [{p['arxiv_id']}] score={p.get('_pre_score',0)}\n"
        f"   Title: {p['title']}\n"
        f"   Abstract: {p['abstract'][:350]}..."
        for i, p in enumerate(top)
    )

    try:
        from services.providers import MODELS_METADATA
        platform_models = ", ".join(
            f"{m.get('name','')} ({', '.join(m.get('modalities',[]))})"
            for m in MODELS_METADATA if m.get("name")
        )
    except Exception:
        platform_models = "Text LLM APIs, Embedding models"

    try:
        from tools.registry import TOOLS_DB
        platform_tools = ", ".join(
            t.get("name_en", tid) for tid, t in TOOLS_DB.items()
        )
    except Exception:
        platform_tools = "Web Scraper, OCR, Semantic Embed, Finance RSS, News RSS"

    prompt = f"""You are a technical blog editor for Orgteh — an AI API platform for developers.

ORGTEH PLATFORM (what developers can actually use via API):
Models: {platform_models}
Tools: {platform_tools}

YOUR TASK:
From the papers below, select {select_count} paper(s) that would make great blog articles for developers who use LLM APIs daily.

GOOD ARTICLE TOPICS (pick papers that fit one of these angles):
- Building AI agents / autonomous systems with LLMs
- Prompt engineering techniques that improve model output
- RAG, retrieval, memory systems for LLMs
- LLM evaluation, benchmarking, and reliability
- Tool use, function calling, structured outputs
- Code generation and developer productivity with AI
- Multi-model workflows and chaining
- LLM reasoning strategies (chain-of-thought, self-consistency, etc.)
- Fine-tuning concepts a developer can apply via API

REJECT papers that are ONLY about:
- How to train/architect neural networks from scratch
- Internal model math (attention mechanisms, weight initialization, etc.)
- Image/video/audio/3D generation or diffusion models
- Robotics, autonomous driving, medical imaging
- Hardware benchmarks or chip design
- Anything requiring infrastructure beyond a simple API call

KEY TEST: "Can a developer reading this article immediately try something new using the Orgteh API?"
If yes → select. If no → skip.

You may select papers that cover the SAME theme and combine them into one article angle.

PAPERS:
{numbered}

Reply ONLY with a JSON array of the arxiv IDs you selected: ["2501.xxxxx", ...]
No explanation. Raw JSON only."""

    try:
        client = _build_nvidia_client()
        comp   = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.05, top_p=0.95, max_tokens=300, stream=False,
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
                return selected[:select_count]

        logger.warning("[Select] LLM returned no valid IDs — retrying with simpler prompt")
        simple_prompt = (
            f"From these AI research papers, pick {select_count} that a developer can "
            f"immediately apply using a text LLM API (prompting, agents, RAG, tool use). "
            f"Avoid papers about training internals, image/video generation, or hardware. "
            f"Reply ONLY with a JSON array of arxiv IDs.\n\n{numbered}"
        )
        comp2  = client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": simple_prompt}],
            temperature=0.0, top_p=1.0, max_tokens=300, stream=False,
        )
        raw2   = comp2.choices[0].message.content.strip()
        match2 = re.search(r"\[.*?\]", raw2, re.DOTALL)
        if match2:
            ids2      = json.loads(match2.group())
            selected2 = [p for p in top if p["arxiv_id"] in ids2]
            if selected2:
                logger.info(f"[Select] Retry chose: {[p['arxiv_id'] for p in selected2]}")
                return selected2[:select_count]

        logger.error("[Select] Both attempts failed — no suitable papers found in this batch")
        return []

    except Exception as e:
        logger.error(f"[Select] LLM selection: {e}")
        return []

async def generate_article_en(paper: dict, seo_keywords: list[str], catalog_ctx: str, demo_result: dict = None) -> Optional[str]:
    kw_str = ", ".join(seo_keywords[:15])
    demo_type = demo_result.get("type", "") if demo_result else ""

    if demo_type == "live":
        demo_instruction = f"""REAL LIVE DEMO — INCLUDE THIS IN THE ARTICLE:
   We actually ran this on Orgteh API using {demo_result['model_name']}.
   Concept: {demo_result['concept']}
   In "## Integrating with Orgteh":
   "We ran a quick test using [{demo_result['model_name']}](/en/models/{demo_result['model_key']}) on Orgteh API."
   Show these blocks:
   Input:
   ```
   {demo_result['prompt'][:400]}
   ```
   Output from {demo_result['model_name']}:
   ```
   {demo_result['response'][:500]}
   ```
   Then add 2-3 sentences analyzing what the output shows."""
    elif demo_type == "note":
        demo_instruction = f"""COMPLEX PAPER — In "## Integrating with Orgteh":
   "{demo_result['suggestion']}"
   Explain briefly what a developer would need to build to implement the full system."""
    else:
        demo_instruction = """In "## Integrating with Orgteh", recommend the relevant models/tools with clear explanation."""

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

=== ORGTEH MODELS & TOOLS — CONTEXT ONLY (DO NOT REPRODUCE) ===
⚠️ The block below is PRIVATE CONTEXT for your reference ONLY.
NEVER copy, quote, print, or reproduce any part of this block in the article output.
Use the information inside it ONLY to write the "## Integrating with Orgteh" section naturally.

{catalog_ctx}

=== END CONTEXT — DO NOT INCLUDE ANYTHING ABOVE THIS LINE IN THE ARTICLE ===

=== ARTICLE REQUIREMENTS ===
1. WORD COUNT: write at least 1500 words. Each section must be fully developed with concrete examples and depth.
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

5. SECTION-SPECIFIC RULES:

   "## Introduction":
   - First paragraph ≤160 chars (used as meta description)
   - Hook: why this research matters RIGHT NOW for developers

   "## What This Research Found":
   - Explain the core idea in plain language — no formulas
   - Use analogies if helpful
   - Include ONE Mermaid diagram here or in Practical Applications:
     * ENGLISH labels only — never Arabic or any non-Latin text in nodes
     * Use simple: A[Label] --> B[Label] syntax only
     * NO curly braces {{}} in node labels
     * Example: flowchart LR 
  A[Input] --> B[Process] --> C[Output]

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
   - Use exact markdown links (already provided above with /en/ prefix)
   - {demo_instruction}

   "## Key Takeaways":
   - 3-5 concrete actionable points
   - End with a call-to-action toward Orgteh

6. NO inline citations — source card is added automatically by the website

7. Tone: conversational, like a senior engineer who READ the paper and TESTED the ideas

DO NOT:
- Copy sentences from the abstract verbatim
- Add preamble before the # H1 title
- Use /ar/ links (English article only)
- Write the code without using Orgteh API

Start directly with the # H1 title."""

    key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
    if key:
        key = key.split(",")[0].strip()

    payload = {
        "model": GENERATION_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.29, "top_p": 0.70, "max_tokens": 16384, "stream": True,
    }

    content = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0, read=1800.0)) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            content += delta
                    except Exception:
                        pass
        logger.info(f"[BlogGen] EN done: {len(content.split())} words")
        result = content.strip()
        if not result:
            return None
        non_latin = sum(1 for c in result if ord(c) > 0x2E7F)
        if non_latin > len(result) * 0.30:
            logger.error(f"[BlogGen] EN rejected: non-Latin ratio {non_latin/len(result):.0%}")
            return None
        return result
    except Exception as e:
        logger.error(f"[BlogGen] EN error: {type(e).__name__}: {e}")
        return content.strip() if len(content) > 500 else None


async def _stream_generate_en(paper: dict, seo_kw: list, catalog_ctx: str, demo_result: dict = None):
    kw_str    = ", ".join(seo_kw[:15])
    demo_type = demo_result.get("type", "") if demo_result else ""

    if demo_type == "live":
        demo_instruction = f"""REAL LIVE DEMO — INCLUDE THIS IN THE ARTICLE:
   We actually ran this on Orgteh API using {demo_result['model_name']}.
   Concept: {demo_result['concept']}
   In "## Integrating with Orgteh":
   "We ran a quick test using [{demo_result['model_name']}](/en/models/{demo_result['model_key']}) on Orgteh API."
   Show these blocks:
   Input:
   ```
   {demo_result['prompt'][:400]}
   ```
   Output from {demo_result['model_name']}:
   ```
   {demo_result['response'][:500]}
   ```
   Then add 2-3 sentences analyzing what the output shows."""
    elif demo_type == "note":
        demo_instruction = f"""COMPLEX PAPER — In "## Integrating with Orgteh":
   "{demo_result['suggestion']}"
   Explain briefly what a developer would need to build to implement the full system."""
    else:
        demo_instruction = """In "## Integrating with Orgteh", recommend the relevant models/tools with clear explanation."""

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

=== ORGTEH MODELS & TOOLS — CONTEXT ONLY (DO NOT REPRODUCE) ===
⚠️ The block below is PRIVATE CONTEXT for your reference ONLY.
NEVER copy, quote, print, or reproduce any part of this block in the article output.
Use the information inside it ONLY to write the "## Integrating with Orgteh" section naturally.

{catalog_ctx}

=== END CONTEXT — DO NOT INCLUDE ANYTHING ABOVE THIS LINE IN THE ARTICLE ===

=== ARTICLE REQUIREMENTS ===
1. WORD COUNT: write at least 1500 words. Each section must be fully developed with concrete examples and depth.
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

5. SECTION-SPECIFIC RULES:

   "## Introduction":
   - First paragraph ≤160 chars (used as meta description)
   - Hook: why this research matters RIGHT NOW for developers

   "## What This Research Found":
   - Explain the core idea in plain language — no formulas
   - Use analogies if helpful
   - Include ONE Mermaid diagram here or in Practical Applications:
     * ENGLISH labels only — never Arabic or any non-Latin text in nodes
     * Use simple: A[Label] --> B[Label] syntax only
     * NO quotes " or ' inside node labels — labels must be plain text
     * NO backslash-n (\n) inside nodes — one line per node only
     * NO arrow characters (→ ← ↔) inside node labels
     * NO curly braces in node labels
     * Keep node labels SHORT (max 4 words)
     * Example: flowchart LR
  A[Input Text] --> B[QE Model] --> C[RL Agent] --> D[Translation]

   "## Implementation Guide":
   - MUST include a real Python code example that uses Orgteh API:
     ```python
     from openai import OpenAI
     client = OpenAI(
         base_url="https://orgteh.com/v1",
         api_key="YOUR_ORGTEH_API_KEY"
     )
     ```
   - The code must implement the paper's core idea using Orgteh API

   "## Integrating with Orgteh":
   - Use the ORGTEH MODELS & TOOLS provided above
   - Natural prose, NOT bullet list
   - Use exact markdown links
   - {demo_instruction}

   "## Key Takeaways":
   - 3-5 concrete actionable points
   - End with a call-to-action toward Orgteh

6. NO inline citations
7. Tone: conversational, like a senior engineer who READ the paper and TESTED the ideas

DO NOT:
- Copy sentences from the abstract verbatim
- Add preamble before the # H1 title
- Use /ar/ links (English article only)
- Reproduce, print, or include ANY part of the "ORGTEH MODELS & TOOLS — CONTEXT ONLY" block
- Include any === section headers from the prompt
- Print the catalog descriptions, tool descriptions, or link templates verbatim

Start directly with the # H1 title."""

    key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
    if key:
        key = key.split(",")[0].strip()

    payload = {
        "model": GENERATION_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.29, "top_p": 0.70, "max_tokens": 16384, "stream": True,
    }

    content   = ""
    tok_count = 0
    t0        = datetime.utcnow()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0, read=1800.0)) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data  = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            content   += delta
                            tok_count += 1
                            if tok_count % 80 == 0:
                                secs  = int((datetime.utcnow() - t0).total_seconds())
                                words = len(content.split())
                                yield (
                                    "progress",
                                    f'<div class="log-line"><span class="ts">[{_ts()}]</span> '
                                    f'<span style="color:#4b5563">⏳ EN streaming… '
                                    f'{words} words / {secs}s</span></div>\n'
                                    f'<script>window.scrollTo(0,document.body.scrollHeight);</script>\n'
                                )
                    except Exception:
                        pass

        result = content.strip()
        if not result:
            yield ("error", "EN generation returned empty content")
            return
        non_latin = sum(1 for c in result if ord(c) > 0x2E7F)
        if non_latin > len(result) * 0.30:
            yield ("error", f"EN rejected: wrong language ratio {non_latin/len(result):.0%}")
            return
        yield ("done", result)

    except Exception as e:
        logger.error(f"[BlogGen] _stream_generate_en: {type(e).__name__}: {e}")
        if len(content) > 500:
            yield ("done", content.strip())
        else:
            yield ("error", str(e))

async def generate_article_ar(english_content: str, catalog_ctx_ar: str) -> Optional[str]:
    prompt = f"""Translate the following English blog post to Modern Standard Arabic (الفصحى المُيسَّرة).

=== ORGTEH LINKS FOR ARABIC — CONTEXT ONLY (DO NOT REPRODUCE) ===
⚠️ The block below is PRIVATE CONTEXT for your reference ONLY.
NEVER copy, quote, print, or reproduce any part of this block in the article output.
Use the links inside it ONLY to update /en/ → /ar/ in the translated article.

{catalog_ctx_ar}

=== END CONTEXT — DO NOT INCLUDE ANYTHING ABOVE THIS LINE IN THE ARTICLE ===

=== TRANSLATION RULES ===
1. Translate ALL text: title, all headings, every paragraph
   TITLE RULE: If the English title starts with a proper noun/acronym (e.g. "3DreamBooth: ...", "WALAR: ..."),
   put the Arabic description FIRST, then the English term at the end.
   Example: "3DreamBooth: From One Photo to 360° Video" → "من صورة واحدة إلى فيديو 360°: ‪3DreamBooth‬"
   This ensures correct RTL reading order in Arabic.
1b. In the Arabic TITLE: wrap any English acronym or product name with ‪...‬ (LTR mark) — e.g. ‪VID-AD‬ or ‪WALAR‬ — so it renders correctly in RTL
2. Keep ALL markdown formatting exactly as-is
3. Keep code blocks EXACTLY as-is — do not translate ANY code
4. Keep ```mermaid blocks EXACTLY as-is
5. CRITICAL LINK RULE: replace /en/ with /ar/ in ALL Orgteh internal links
6. Translate naturally — write as if originally authored in Arabic
7. Do NOT add preamble — output ONLY the translated markdown
8. Technical terms: RAG, LLM, API, prompt, token → keep in English
9. CRITICAL: For any English acronym or product name inside Arabic text (e.g. VID-AD, GPT, WALAR), wrap it with Unicode LTR markers: ‎ENGLISH_WORD‎ — this ensures correct display direction
9. Do NOT add preamble like "إليك الترجمة"
10. CRITICAL: Do NOT include any blockquote with "المصدر:" or "Source:"

=== ENGLISH ARTICLE ===
{english_content}

OUTPUT: Complete Arabic markdown article only."""

    key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
    if key:
        key = key.split(",")[0].strip()

    payload = {
        "model": GENERATION_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.20, "top_p": 0.65, "max_tokens": 16384, "stream": True,
    }

    content = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0, read=1800.0)) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            content += delta
                    except Exception:
                        pass
        logger.info(f"[BlogGen] AR done: {len(content.split())} words")
        return content.strip() or None
    except Exception as e:
        logger.error(f"[BlogGen] AR error: {type(e).__name__}: {e}")
        return content.strip() if len(content) > 300 else None


async def _stream_generate_ar(english_content: str, catalog_ctx_ar: str):
    prompt = f"""Translate the following English blog post to Modern Standard Arabic (الفصحى المُيسَّرة).

=== ORGTEH LINKS FOR ARABIC — CONTEXT ONLY (DO NOT REPRODUCE) ===
⚠️ The block below is PRIVATE CONTEXT for your reference ONLY.
NEVER copy, quote, print, or reproduce any part of this block in the article output.
Use the links inside it ONLY to update /en/ → /ar/ in the translated article.

{catalog_ctx_ar}

=== END CONTEXT — DO NOT INCLUDE ANYTHING ABOVE THIS LINE IN THE ARTICLE ===

=== TRANSLATION RULES ===
1. Translate ALL text: title, all headings, every paragraph
   TITLE RULE: If the English title starts with a proper noun/acronym (e.g. "3DreamBooth: ...", "WALAR: ..."),
   put the Arabic description FIRST, then the English term at the end.
   Example: "3DreamBooth: From One Photo to 360° Video" → "من صورة واحدة إلى فيديو 360°: ‪3DreamBooth‬"
   This ensures correct RTL reading order in Arabic.
1b. In the Arabic TITLE: wrap any English acronym or product name with ‪...‬ (LTR mark) — e.g. ‪VID-AD‬ or ‪WALAR‬ — so it renders correctly in RTL
2. Keep ALL markdown formatting exactly as-is
3. Keep code blocks EXACTLY as-is — do not translate ANY code
4. Keep ```mermaid blocks EXACTLY as-is
5. CRITICAL LINK RULE: replace /en/ with /ar/ in ALL Orgteh internal links
6. Translate naturally — write as if originally authored in Arabic
7. Do NOT add preamble — output ONLY the translated markdown
8. Technical terms: RAG, LLM, API, prompt, token → keep in English
9. CRITICAL: For any English acronym or product name inside Arabic text (e.g. VID-AD, GPT, WALAR), wrap it with Unicode LTR markers: ‎ENGLISH_WORD‎ — this ensures correct display direction
9. Do NOT add preamble like "إليك الترجمة"
10. CRITICAL: Do NOT include any blockquote with "المصدر:" or "Source:"

=== ENGLISH ARTICLE ===
{english_content}

OUTPUT: Complete Arabic markdown article only."""

    key = os.environ.get("NVIDIA_API_KEYS", os.environ.get("NVIDIA_API_KEY", ""))
    if key:
        key = key.split(",")[0].strip()

    payload = {
        "model": GENERATION_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.20, "top_p": 0.65, "max_tokens": 16384, "stream": True,
    }

    content   = ""
    tok_count = 0
    t0        = datetime.utcnow()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=30.0, read=1800.0)) as client:
            async with client.stream(
                "POST",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data  = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            content   += delta
                            tok_count += 1
                            if tok_count % 80 == 0:
                                secs  = int((datetime.utcnow() - t0).total_seconds())
                                words = len(content.split())
                                yield (
                                    "progress",
                                    f'<div class="log-line"><span class="ts">[{_ts()}]</span> '
                                    f'<span style="color:#4b5563">⏳ AR streaming… '
                                    f'{words} words / {secs}s</span></div>\n'
                                    f'<script>window.scrollTo(0,document.body.scrollHeight);</script>\n'
                                )
                    except Exception:
                        pass

        result = content.strip()
        if result:
            yield ("done", result)
        else:
            yield ("error", "AR generation returned empty content")

    except Exception as e:
        logger.error(f"[BlogGen] _stream_generate_ar: {type(e).__name__}: {e}")
        if len(content) > 300:
            yield ("done", content.strip())
        else:
            yield ("error", str(e))

def _fix_model_links(content: str, relevant: list[dict], lang: str) -> str:
    if not relevant or not content:
        return content
    for entry in relevant:
        short_key  = entry.get("short_key", "")
        name       = entry.get("name", "")
        entry_type = entry.get("type", "model")
        if not short_key or not name:
            continue
        if entry_type == "model":
            correct_link = "/" + lang + "/models/" + short_key
        else:
            correct_link = "/" + lang + "/accesory/" + short_key
        md_link = "[" + name + "](" + correct_link + ")"
        for wrong in ["](" + "/" + lang + "/models)", "](" + "/" + lang + "/models/)"]:
            if wrong in content:
                content = content.replace(wrong, "](" + correct_link + ")")
        if name in content and md_link not in content:
            integ = content.find("## Integrating")
            if integ > 0:
                after = content[integ:]
                bold = "**" + name + "**"
                if bold in after and "[**" + name + "**]" not in after:
                    content = content[:integ] + after.replace(bold, "[**" + name + "**](" + correct_link + ")", 1)
                elif name in after and "[" + name not in after:
                    content = content[:integ] + after.replace(name, md_link, 1)
    return content

def _extract_mermaid_blocks(content: str) -> list[str]:
    blocks = []
    in_block = False
    current = []
    for line in content.splitlines():
        if line.strip() == "```mermaid":
            in_block = True
            current = [line]
        elif in_block and line.strip() == "```":
            current.append(line)
            blocks.append("\n".join(current))
            in_block = False
            current = []
        elif in_block:
            current.append(line)
    return blocks

def _restore_mermaid_blocks(ar_content: str, en_content: str) -> str:
    en_blocks = _extract_mermaid_blocks(en_content)
    if not en_blocks:
        return ar_content
    idx = [0]
    def _replacer(m):
        block = en_blocks[idx[0]] if idx[0] < len(en_blocks) else m.group(0)
        idx[0] += 1
        return block
    result = re.sub(r"```mermaid.*?```", _replacer, ar_content, flags=re.DOTALL)
    if result == ar_content:
        ar_blocks = _extract_mermaid_blocks(ar_content)
        for i, ar_block in enumerate(ar_blocks):
            if i < len(en_blocks):
                result = result.replace(ar_block, en_blocks[i], 1)
    return result

def _sanitize_mermaid_line(line: str) -> str:
    import re as _re
    fixed = line
    fixed = _re.sub(r'\{([^}]+)\}', r'(\1)', fixed)
    fixed = _re.sub(r'>([^\]\[]+)\]', r'[\1]', fixed)
    fixed = fixed.replace('\\n', ' ').replace('\n', ' ')
    fixed = fixed.replace('→', '-').replace('←', '-').replace('↔', '-')
    def _clean_node(m):
        label = m.group(1)
        label = _re.sub(r'"([^"]*)"', r'\1', label)
        label = _re.sub(r"'([^']*)'", r'\1', label)
        label = label.replace('"', '').replace("'", '')
        if any('\u0600' <= c <= '\u06ff' for c in label):
            return '[Node]'
        return '[' + label + ']'
    fixed = _re.sub(r'\[([^\]]+)\]', _clean_node, fixed)
    return fixed

def _clean_generated_content(content):
    out_lines = []
    in_mermaid = False
    for line in content.splitlines():
        s = line.strip()
        if s == "```mermaid":
            in_mermaid = True
            out_lines.append(line)
            continue
        if in_mermaid and s == "```":
            in_mermaid = False
            out_lines.append(line)
            continue
        if in_mermaid:
            out_lines.append(_sanitize_mermaid_line(line))
            continue
        if s.startswith('>') and ('Source:' in s or '\u0627\u0644\u0645\u0635\u062f\u0631:' in s):
            continue
        out_lines.append(line)
    nl = "\n"
    result = nl.join(out_lines)
    while nl*3 in result:
        result = result.replace(nl*3, nl*2)
    return result.strip()

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

            content_en = await generate_article_en(paper, seo_kw, catalog_en, demo_result=demo_result)
            if not content_en:
                logger.warning(f"[BlogGen] EN failed: {paper['arxiv_id']}")
                continue
            content_en = _fix_model_links(content_en, relevant, lang="en")
            content_en = _clean_generated_content(content_en)

            content_ar = await generate_article_ar(content_en, catalog_ar)
            if not content_ar:
                logger.warning(f"[BlogGen] AR failed: {paper['arxiv_id']}")
                continue
            content_ar = _fix_model_links(content_ar, relevant, lang="ar")
            content_ar = _clean_generated_content(content_ar)
            content_ar = _restore_mermaid_blocks(content_ar, content_en)

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
    return datetime.utcnow().strftime("%H:%M:%S")

async def _run_blog_generation_verbose(count: int):
\
\
\
\
\
\

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
    for i, p in enumerate(top_new, 1):
        text = f"{p['title']}. {p['abstract'][:500]}"
        emb  = await asyncio.to_thread(_embed_text, text, "passage")
        if emb:
            embed_map[p["arxiv_id"]] = emb
            p["_embedding"] = emb
        else:
            failed_embeds += 1
        yield (
            f'<div class="log-line"><span class="ts">[{_ts()}]</span> '
            f'<span style="color:#4b5563">📎 Embedding {i}/{len(top_new)}: {p["title"][:55]}…</span></div>\n'
            f'<script>window.scrollTo(0,document.body.scrollHeight);</script>\n'
        )

    if failed_embeds:
        yield log_warn(f"{failed_embeds} papers failed embedding (will still be considered)")
    yield log_ok(f"Embedded <b>{len(embed_map)}</b> papers")

    yield log_info("Running semantic dedup (cosine similarity)…")
    semantic_dups = await asyncio.to_thread(_batch_semantic_dedup, embed_map, 0.87)
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
        selected = await asyncio.to_thread(select_papers_with_llm, candidates, count)
    except Exception as e:
        yield log_warn(f"LLM selection failed ({e}) — using top-scored fallback")
        selected = sorted(candidates, key=lambda p: p.get("_pre_score", 0), reverse=True)[:count]

    if not selected:
        yield log_err("No suitable papers found in this batch — all were rejected as incompatible with Orgteh platform. Try again later for a fresh batch.")
        yield ("__done__", {"ok": False, "error": "No suitable papers found — batch rejected by platform compatibility filter"})
        return

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
            relevant   = await asyncio.to_thread(retrieve_relevant_catalog, query, _CATALOG_TOP_K)
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

            yield log_info("  Generating English article (direct NVIDIA stream)…")
            t0         = datetime.utcnow()
            content_en = None
            async for event, payload_data in _stream_generate_en(paper, seo_kw, catalog_en, demo_result=demo_result):
                if event == "progress":
                    yield payload_data
                elif event == "done":
                    content_en = payload_data
                elif event == "error":
                    yield log_err(f"  EN stream error: {payload_data}")
            elapsed_en = int((datetime.utcnow() - t0).total_seconds())
            if not content_en:
                yield log_err(f"  EN generation failed after {elapsed_en}s")
                continue
            content_en = _fix_model_links(content_en, relevant, lang="en")
            content_en = _clean_generated_content(content_en)
            words_en   = len(content_en.split())
            yield log_ok(f"  EN article: <b>{words_en}</b> words in <b>{elapsed_en}s</b>")

            title_en = _extract_h1(content_en)
            slug     = _make_slug(title_en)

            yield log_info(f"  Saving EN to DB — slug: <code>{slug}</code>")
            save_blog_post({
                "slug":         slug,
                "arxiv_id":     paper["arxiv_id"],
                "arxiv_url":    paper["url"],
                "title_en":     title_en,
                "title_ar":     title_en,
                "content_en":   content_en,
                "content_ar":   "",
                "summary_en":   _extract_summary(content_en),
                "summary_ar":   "",
                "seo_keywords": json.dumps(seo_kw),
                "published_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            })
            mark_arxiv_id_seen(paper["arxiv_id"])
            if paper.get("_embedding"):
                store_article_embedding(paper["arxiv_id"], paper["_embedding"])
            yield log_ok(f'  EN saved! <a href="/en/blog/{slug}" target="_blank" style="color:#818cf8">/en/blog/{slug}</a>')

            yield log_info("  Translating to Arabic — opening fresh connection…")
            yield (
                f'<script>\n'
                f'(function(){{\n'
                f'  var slug="{slug}";\n'
                f'  var secret=new URLSearchParams(window.location.search).get("secret");\n'
                f'  var logDiv=document.getElementById("log");\n'
                f'  fetch("/api/blog/translate-ar?slug="+encodeURIComponent(slug)+"&secret="+encodeURIComponent(secret||""),{{\n'
                f'    method:"GET",credentials:"same-origin"\n'
                f'  }}).then(function(r){{\n'
                f'    var reader=r.body.getReader(),dec=new TextDecoder();\n'
                f'    function read(){{reader.read().then(function(v){{\n'
                f'      if(v.done)return;\n'
                f'      var lines=dec.decode(v.value).split("\\n");\n'
                f'      lines.forEach(function(l){{if(l){{var d=document.createElement("div");d.innerHTML=l;logDiv.appendChild(d);}}}});\n'
                f'      window.scrollTo(0,document.body.scrollHeight);\n'
                f'      read();\n'
                f'    }})}}\n'
                f'    read();\n'
                f'  }});\n'
                f'}})();\n'
                f'</script>\n'
            )

            published.append({"slug": slug, "title_en": title_en, "arxiv_id": paper["arxiv_id"], "pending_ar": True})

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

    start_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept and "text/html" not in accept

    if want_json:
        try:
            result = await run_blog_generation(count=count)
            result["triggered_at"] = datetime.utcnow().isoformat() + "Z"
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"[Cron] Error: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    PADDING = b"<!-- " + b"x" * 2048 + b" -->\n"

    async def stream_html():
        head = _CRON_HTML_HEAD.format(start_time=start_time, count=count).encode("utf-8")
        yield head + PADDING
        await asyncio.sleep(0)

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
            "X-Accel-Buffering":  "no",
            "Cache-Control":      "no-cache, no-store, no-transform",
            "Transfer-Encoding":  "chunked",
            "X-Content-Type-Options": "nosniff",
        },
    )

@blog_router.post("/api/admin/blog/clear-seen-ids")
async def admin_clear_seen_ids(request: Request):
    from services.auth import get_current_user_email
    email = get_current_user_email(request)
    if not email or not _is_admin(email):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    r = _redis()
    if not r:
        return JSONResponse({"error": "Redis unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    mode = body.get("mode", "stale")

    if mode == "all":
        r.delete("blog:seen_arxiv_ids")
        return JSONResponse({"ok": True, "mode": "all", "message": "All seen_ids cleared"})

    db_ids = set(get_all_arxiv_ids())
    members = r.smembers("blog:seen_arxiv_ids") or set()
    stale = [
        m.decode() if isinstance(m, bytes) else m
        for m in members
        if (m.decode() if isinstance(m, bytes) else m) not in db_ids
    ]
    if stale:
        r.srem("blog:seen_arxiv_ids", *stale)
    return JSONResponse({
        "ok": True,
        "mode": "stale",
        "total_in_redis": len(members),
        "in_db": len(db_ids),
        "removed_stale": len(stale),
        "remaining": len(members) - len(stale),
    })

@blog_router.get("/api/blog/translate-ar")
async def blog_translate_ar(request: Request, slug: str = "", secret: str = ""):
    if not _CRON_SECRET or secret != _CRON_SECRET:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not slug:
        return JSONResponse({"error": "slug required"}, status_code=400)

    post = get_post_by_slug(slug)
    if not post:
        return JSONResponse({"error": "slug not found"}, status_code=404)

    content_en = post.get("content_en", "")
    if not content_en:
        return JSONResponse({"error": "no EN content"}, status_code=400)

    async def stream_ar():
        from datetime import datetime as _dt
        ts = lambda: _dt.utcnow().strftime("%H:%M:%S")

        def _line(icon, msg, color="#e0e0e0"):
            return (
                f'<div class="log-line"><span class="ts">[{ts()}]</span> '
                f'<span>{icon}</span> <span style="color:{color}">{msg}</span></div>\n'
            )

        yield (_line("📌", "AR translation started (fresh connection)…", "#93c5fd")).encode()

        relevant = await asyncio.to_thread(
            retrieve_relevant_catalog,
            (post.get("title_en","") + ". " + content_en[:300]),
            _CATALOG_TOP_K
        )
        catalog_ar = _format_catalog_prompt(relevant, lang="ar")

        content_ar = None
        t0 = _dt.utcnow()
        async for event, data in _stream_generate_ar(content_en, catalog_ar):
            if event == "progress":
                yield data.encode()
            elif event == "done":
                content_ar = data
            elif event == "error":
                yield (_line("❌", f"AR error: {data}", "#f87171")).encode()
        elapsed = int((_dt.utcnow() - t0).total_seconds())

        if not content_ar:
            yield (_line("❌", f"AR translation failed after {elapsed}s", "#f87171")).encode()
            return

        content_ar = _fix_model_links(content_ar, relevant, lang="ar")
        content_ar = _clean_generated_content(content_ar)
        content_ar = _restore_mermaid_blocks(content_ar, content_en)
        title_ar   = _extract_h1(content_ar)
        summary_ar = _extract_summary(content_ar)

        try:
            update_blog_post_ar(slug, title_ar, content_ar, summary_ar)
            words_ar = len(content_ar.split())
            yield (_line("✅", f"AR done: <b>{words_ar}</b> words in <b>{elapsed}s</b> — <a href=\"/ar/blog/{slug}\" target=\"_blank\" style=\"color:#818cf8\">/ar/blog/{slug}</a>", "#4ade80")).encode()
        except Exception as e:
            yield (_line("❌", f"DB update failed: {e}", "#f87171")).encode()

    from fastapi.responses import StreamingResponse as _SR
    return _SR(
        stream_ar(),
        media_type="text/html; charset=utf-8",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-store"},
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