"""RAG 共用模組：chunk、format_context、Pinecone/Gemini 初始化、embed、BM25 Hybrid。

供 rag_graph、rag_ingest、streamlit_app 使用，避免重複實作。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pinecone import Pinecone


def _normalize_ollama_base_url(base_url: str) -> str:
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


class _EmbeddingItem:
    def __init__(self, values: list[float]):
        self.values = values


class _EmbeddingResponse:
    def __init__(self, embeddings: list[_EmbeddingItem]):
        self.embeddings = embeddings


class OllamaEmbeddingAdapter:
    """Adapter that mimics Gemini embed response shape via Ollama OpenAI API."""

    _is_ollama_embedding_adapter = True

    def __init__(self, *, base_url: str, api_key: str = "ollama", timeout_sec: float = 240.0):
        from openai import OpenAI

        self._client = OpenAI(
            base_url=_normalize_ollama_base_url(base_url),
            api_key=api_key,
            timeout=timeout_sec,
        )

    @property
    def models(self) -> Any:
        return self

    def embed_content(self, *, model: str, contents: str | list[str], config: Any = None) -> _EmbeddingResponse:
        inputs = contents if isinstance(contents, list) else [contents]
        resp = self._client.embeddings.create(model=model, input=inputs)
        return _EmbeddingResponse([_EmbeddingItem(list(item.embedding or [])) for item in resp.data])


def chunk_text(text: str, *, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """先依段落/標題切大區塊，再在區塊內做長度切片，減少語意被拆散。"""
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    if chunk_size <= overlap:
        raise ValueError("chunk_size 必須大於 overlap")

    raw_blocks = re.split(r"\n\s*\n+", cleaned)
    blocks: list[str] = []
    current: list[str] = []
    heading_pattern = re.compile(r"^(#+\s+|[一二三四五六七八九十]+、)")

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        first_line = lines[0].strip() if lines else ""
        if heading_pattern.match(first_line) and current:
            blocks.append("\n".join(current).strip())
            current = [block]
        else:
            current.append(block)
    if current:
        blocks.append("\n".join(current).strip())

    chunks_out: list[str] = []
    for blk in blocks:
        if len(blk) <= chunk_size:
            chunks_out.append(blk)
            continue
        start = 0
        while start < len(blk):
            end = min(len(blk), start + chunk_size)
            piece = blk[start:end].strip()
            if piece:
                chunks_out.append(piece)
            if end >= len(blk):
                break
            start = max(0, end - overlap)

    return chunks_out


def stable_id(source: str, chunk_index: int, text: str) -> str:
    """產生穩定的 chunk id（同 source+index+text 必得同 id）。"""
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\n")
    h.update(str(chunk_index).encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:32]


def format_context(matches: list[dict[str, Any]]) -> tuple[str, list[str], list[dict[str, Any]]]:
    """將 Pinecone 檢索結果轉成 context 字串、sources 列表、cleaned chunks。"""
    blocks: list[str] = []
    sources: list[str] = []
    cleaned: list[dict[str, Any]] = []

    for m in matches:
        md = m.get("metadata") or {}
        source = md.get("source", "unknown")
        chunk_index = md.get("chunk_index", "?")
        text = (md.get("text") or "").strip()
        if not text:
            continue
        tag = f"{source}#chunk{chunk_index}"
        sources.append(tag)
        blocks.append(f"[{tag}]\n{text}")
        cleaned.append({"tag": tag, "text": text})

    return ("\n\n---\n\n".join(blocks), sources, cleaned)


def get_clients_and_index() -> tuple[Any, Any, Any, int, str, str, str]:
    """初始化 chat client + embed client + Pinecone index。
    回傳 (chat_client, embed_client, index, dim, llm_model, embed_model, index_name)。
    embed_client 可由 EMBEDDING_PROVIDER 切換（gemini / ollama）。
    """
    load_dotenv()

    from llm_client import get_chat_client_and_model

    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "agent-index")
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "gemini").strip().lower() or "gemini"
    google_api_key = os.getenv("GOOGLE_API_KEY")
    dim_env = os.getenv("EMBED_DIM")

    if not pinecone_api_key:
        raise RuntimeError("缺少環境變數 PINECONE_API_KEY（請放在 .env）")

    chat_client, llm_model = get_chat_client_and_model()

    if embedding_provider == "ollama":
        embed_model = os.getenv("OLLAMA_EMBED_MODEL", "snowflake-arctic-embed2:568m")
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ollama_api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        timeout_sec = float(os.getenv("OLLAMA_TIMEOUT_SEC", "240").strip() or "240")
        embed_client = OllamaEmbeddingAdapter(
            base_url=ollama_base_url,
            api_key=ollama_api_key,
            timeout_sec=timeout_sec,
        )
    else:
        if not google_api_key:
            raise RuntimeError("缺少環境變數 GOOGLE_API_KEY（請放在 .env）")
        embed_model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
        embed_client = genai.Client(api_key=google_api_key)

    pc = Pinecone(api_key=pinecone_api_key)

    existing = {i["name"] for i in pc.list_indexes().get("indexes", [])}
    if index_name not in existing:
        raise RuntimeError(f'Pinecone index "{index_name}" 不存在（請先建立 index 或改 PINECONE_INDEX）')

    index_info: dict[str, Any] = pc.describe_index(index_name)  # type: ignore[assignment]
    raw_dim = index_info.get("dimension")
    if raw_dim is None:
        raise RuntimeError("Pinecone index 描述中沒有 dimension 欄位")
    index_dim = int(raw_dim)
    if dim_env:
        dim = int(dim_env)
        if dim != index_dim:
            raise RuntimeError(f"EMBED_DIM={dim} 與 Pinecone index 維度 {index_dim} 不一致，請修正後再執行。")
    else:
        dim = index_dim

    if embedding_provider == "ollama":
        probe_res = embed_client.models.embed_content(model=embed_model, contents="dimension probe", config=None)
        probe_embeddings = getattr(probe_res, "embeddings", None) or []
        probe_dim = len(getattr(probe_embeddings[0], "values", []) or []) if probe_embeddings else 0
        if probe_dim != dim:
            raise RuntimeError(
                f"Ollama embedding model '{embed_model}' 維度為 {probe_dim}，與 Pinecone index 維度 {dim} 不一致。"
                " 請改用相同維度模型或重建 Pinecone index。"
            )

    index = pc.Index(index_name)
    return chat_client, embed_client, index, dim, llm_model, embed_model, index_name


def embed_query(
    client: Any,
    text: str,
    *,
    model: str,
    output_dimensionality: int,
) -> list[float]:
    """單一查詢的 embedding。"""
    cfg = None
    if not getattr(client, "_is_ollama_embedding_adapter", False):
        cfg = types.EmbedContentConfig(output_dimensionality=output_dimensionality)
    res = client.models.embed_content(model=model, contents=text, config=cfg)
    embeddings = getattr(res, "embeddings", None)
    if not embeddings:
        raise RuntimeError("Embedding API 回傳的 embeddings 為空")
    vec = getattr(embeddings[0], "values", None)
    if vec is None:
        raise RuntimeError("Embedding API 回傳的向量為空")
    return list(vec)


def embed_texts(
    client: Any,
    texts: list[str],
    *,
    model: str,
    output_dimensionality: int,
    batch_size: int = 16,
    batch_delay_sec: float | None = None,
    rate_limit_retry_sec: float = 60.0,
    rate_limit_max_retries: int = 5,
) -> list[list[float]]:
    """批次 embedding，適合 ingest 與上傳灌入。

    若遇 Gemini 429 限流會自動重試（等待 rate_limit_retry_sec 秒後重試，最多 rate_limit_max_retries 次）。
    可設 batch_delay_sec 或環境變數 EMBED_BATCH_DELAY_SEC 在每批之間延遲，避免觸發限流。
    """
    if not texts:
        return []
    load_dotenv()
    delay = batch_delay_sec
    if delay is None:
        try:
            delay = float(os.getenv("EMBED_BATCH_DELAY_SEC", "0"))
        except (TypeError, ValueError):
            delay = 0.0
    vectors: list[list[float]] = []
    cfg = None
    if not getattr(client, "_is_ollama_embedding_adapter", False):
        cfg = types.EmbedContentConfig(output_dimensionality=output_dimensionality)
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for attempt in range(rate_limit_max_retries):
            try:
                res = client.models.embed_content(model=model, contents=batch, config=cfg)
                vectors.extend([list(e.values) for e in res.embeddings])
                break
            except Exception as e:
                err_str = str(e).upper()
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if attempt < rate_limit_max_retries - 1:
                        time.sleep(rate_limit_retry_sec)
                        continue
                raise
        if delay > 0 and i + batch_size < len(texts):
            time.sleep(delay)
    return vectors


# ---------- BM25 Hybrid（向量 + 關鍵字檢索）---------

def get_bm25_corpus_path() -> Path:
    """BM25 語料檔路徑，可由環境變數 BM25_CORPUS_PATH 覆寫。"""
    load_dotenv()
    p = os.getenv("BM25_CORPUS_PATH", "bm25_corpus.json")
    return Path(p)


def _bm25_tokenize(text: str) -> list[str]:
    """BM25 用分詞：中文以字為單位、保留英文/數字詞，利於條號與術語匹配。"""
    if not text or not text.strip():
        return []
    # 英文/數字連續成一 token，其餘（含中文）逐字
    tokens: list[str] = []
    buf = ""
    for c in text:
        if c.isalnum() or c in "._-":
            buf += c
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            if c.strip():
                tokens.append(c)
    if buf:
        tokens.append(buf)
    return tokens


def load_bm25_corpus() -> list[dict[str, Any]]:
    """載入 BM25 語料：每筆為 {id, text, source, chunk_index, chat_id}。無檔或空則回傳 []。"""
    path = get_bm25_corpus_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("id") and c.get("text") is not None]


def save_bm25_corpus(chunks: list[dict[str, Any]]) -> None:
    """覆寫寫入 BM25 語料檔。每筆應含 id, text, source, chunk_index，可選 chat_id。"""
    path = get_bm25_corpus_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for c in chunks:
        out.append({
            "id": str(c.get("id", "")),
            "text": str(c.get("text", "")),
            "source": str(c.get("source", "")),
            "chunk_index": int(c.get("chunk_index", 0)),
            "chat_id": c.get("chat_id"),
        })
    with path.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)


def append_bm25_corpus(chunks: list[dict[str, Any]]) -> None:
    """將新 chunks 追加至 BM25 語料檔。每筆應含 id, text, source, chunk_index，可選 chat_id。"""
    existing = load_bm25_corpus()
    for c in chunks:
        existing.append({
            "id": str(c.get("id", "")),
            "text": str(c.get("text", "")),
            "source": str(c.get("source", "")),
            "chunk_index": int(c.get("chunk_index", 0)),
            "chat_id": c.get("chat_id"),
        })
    save_bm25_corpus(existing)


def build_bm25_index(corpus: list[dict[str, Any]]):
    """依語料建 BM25 索引。回傳 (bm25, tokenized_corpus, corpus_by_id)。corpus 為空則回傳 (None, [], {})。"""
    if not corpus:
        return None, [], {}
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return None, [], {c["id"]: c for c in corpus}
    tokenized = [_bm25_tokenize(c.get("text") or "") for c in corpus]
    bm25 = BM25Okapi(tokenized)
    corpus_by_id = {c["id"]: c for c in corpus}
    return bm25, tokenized, corpus_by_id


def bm25_search(
    bm25,
    corpus: list[dict[str, Any]],
    query: str,
    top_k: int = 20,
    filter_chat_id: str | None = None,
) -> list[tuple[str, float]]:
    """BM25 檢索，回傳 [(chunk_id, score), ...]。filter_chat_id 若設定則只保留該 chat 的 chunk。"""
    if bm25 is None or not corpus or not (query or "").strip():
        return []
    import numpy as np
    query_tokens = _bm25_tokenize(query.strip())
    if not query_tokens:
        return []
    scores = bm25.get_scores(query_tokens)
    indices = np.argsort(scores)[::-1]
    out: list[tuple[str, float]] = []
    for i in indices:
        if scores[i] <= 0:
            break
        c = corpus[i]
        cid = c.get("id")
        if not cid:
            continue
        if filter_chat_id is not None:
            if c.get("chat_id") != filter_chat_id:
                continue
        out.append((cid, float(scores[i])))
        if len(out) >= top_k:
            break
    return out


def merge_hybrid_rrf(
    vector_matches: list[dict[str, Any]],
    bm25_id_scores: list[tuple[str, float]],
    corpus_by_id: dict[str, dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """RRF 合併向量與 BM25 結果。回傳與 Pinecone 相同結構：{id, score, metadata: {text, source, chunk_index}}。"""
    rrf_scores: dict[str, float] = {}
    for rank, m in enumerate(vector_matches, start=1):
        mid = m.get("id")
        if not mid:
            continue
        rrf_scores[mid] = rrf_scores.get(mid, 0.0) + 1.0 / (k + rank)
    for rank, (cid, _) in enumerate(bm25_id_scores, start=1):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank)
    # 依 RRF 分數排序
    ordered_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
    vector_by_id = {m["id"]: m for m in vector_matches if m.get("id")}
    result: list[dict[str, Any]] = []
    for cid in ordered_ids:
        rrf = rrf_scores[cid]
        if cid in vector_by_id:
            m = vector_by_id[cid]
            meta = m.get("metadata") or {}
        else:
            meta = (corpus_by_id.get(cid) or {})
            meta = {"text": meta.get("text", ""), "source": meta.get("source", ""), "chunk_index": meta.get("chunk_index", 0)}
        result.append({"id": cid, "score": rrf, "metadata": meta})
    return result
