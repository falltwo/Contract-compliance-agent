"""RAG 共用模組：chunk、format_context、Pinecone/Gemini 初始化、embed。

供 rag_graph、rag_ingest、streamlit_app 使用，避免重複實作。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pinecone import Pinecone


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


def get_clients_and_index() -> tuple[Any, genai.Client, Any, int, str, str, str]:
    """初始化 chat client（可為 Groq）+ Gemini embed client + Pinecone index。
    回傳 (chat_client, embed_client, index, dim, llm_model, embed_model, index_name)。
    embed_client 一律為 Gemini，供 embed_query / embed_texts 使用。
    """
    load_dotenv()

    from llm_client import get_chat_client_and_model

    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "agent-index")
    google_api_key = os.getenv("GOOGLE_API_KEY")
    embed_model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    dim_env = os.getenv("EMBED_DIM")

    if not pinecone_api_key:
        raise RuntimeError("缺少環境變數 PINECONE_API_KEY（請放在 .env）")
    if not google_api_key:
        raise RuntimeError("缺少環境變數 GOOGLE_API_KEY（請放在 .env）")

    chat_client, llm_model = get_chat_client_and_model()
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

    index = pc.Index(index_name)
    return chat_client, embed_client, index, dim, llm_model, embed_model, index_name


def embed_query(
    client: genai.Client,
    text: str,
    *,
    model: str,
    output_dimensionality: int,
) -> list[float]:
    """單一查詢的 embedding。"""
    cfg = types.EmbedContentConfig(output_dimensionality=output_dimensionality)
    res = client.models.embed_content(model=model, contents=text, config=cfg)
    embeddings = getattr(res, "embeddings", None)
    if not embeddings:
        raise RuntimeError("Gemini 回傳的 embeddings 為空")
    vec = getattr(embeddings[0], "values", None)
    if vec is None:
        raise RuntimeError("Gemini 回傳的 embedding 為空")
    return list(vec)


def embed_texts(
    client: genai.Client,
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
