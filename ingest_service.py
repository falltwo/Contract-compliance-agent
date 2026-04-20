"""與 Streamlit／FastAPI 共用的上傳灌入邏輯：chunk → embed → Pinecone upsert → `sources_registry` → BM25 append。

與 `rag_ingest.py`（離線掃 `data/`）差異：本模組為**上傳位元組**，且可帶 `chat_id`（來源路徑 `uploaded/{chat_id}/{filename}`）。"""
from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from rag_common import append_bm25_corpus, chunk_text, embed_texts, stable_id
from sources_registry import update_registry_on_ingest

ALLOWED_SUFFIXES = (".txt", ".md", ".pdf", ".docx")


def sanitize_upload_filename(name: str) -> str:
    """防止路徑穿越：只保留最後一段檔名，並拒絕含 `..` 或空字串。"""
    base = Path(name).name.strip()
    if not base or base in (".", "..") or ".." in base:
        raise ValueError("檔名不合法")
    lower = base.lower()
    if not any(lower.endswith(s) for s in ALLOWED_SUFFIXES):
        raise ValueError(f"不支援的副檔名（允許：{', '.join(ALLOWED_SUFFIXES)}）")
    return base


def _extract_text_from_bytes(name: str, raw: bytes) -> str:
    lower_name = name.lower()
    if lower_name.endswith(".pdf"):
        try:
            reader = PdfReader(BytesIO(raw))
            pages_text: list[str] = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t:
                    pages_text.append(t)
            return "\n\n".join(pages_text)
        except Exception:
            return ""
    if lower_name.endswith(".docx"):
        try:
            from docx import Document

            doc = Document(BytesIO(raw))
            parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(parts)
        except Exception:
            return ""
    return raw.decode("utf-8", errors="ignore")


def ingest_file_items(
    items: list[tuple[str, bytes]],
    *,
    embed_client: Any,
    index: Any,
    index_dim: int,
    embed_model: str,
    chat_id: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """
    自 (filename, raw_bytes) 列表灌入；與原 `streamlit_app.ingest_uploaded_files` 行為一致。

    回傳 (chunk 總數, 本次更新的來源摘要列表（供 API 回應）)。
    """
    all_sources: list[str] = []
    all_texts: list[str] = []
    all_chunk_indexes: list[int] = []
    all_ids: list[str] = []

    for original_name, raw in items:
        try:
            name = sanitize_upload_filename(original_name)
        except ValueError:
            continue
        lower_name = name.lower()
        if not (lower_name.endswith(".txt") or lower_name.endswith(".md") or lower_name.endswith(".pdf") or lower_name.endswith(".docx")):
            continue

        text = _extract_text_from_bytes(name, raw)
        if not text.strip():
            continue
        parts = chunk_text(text)
        if chat_id:
            source = f"uploaded/{chat_id}/{name}"
        else:
            source = f"uploaded/{name}"

        for i, part in enumerate(parts):
            all_sources.append(source)
            all_texts.append(part)
            all_chunk_indexes.append(i)
            all_ids.append(stable_id(source, i, part))

    if not all_texts:
        return 0, []

    vectors = embed_texts(
        embed_client,
        all_texts,
        model=embed_model,
        output_dimensionality=index_dim,
    )

    batch_size = 100
    for i in range(0, len(all_texts), batch_size):
        to_upsert = []
        for j in range(i, min(len(all_texts), i + batch_size)):
            metadata = {
                "text": all_texts[j],
                "source": all_sources[j],
                "chunk_index": all_chunk_indexes[j],
                # 綁定 embedding 版本，避免 EMBED_MODEL 變更後新舊向量混用造成相似度崩潰
                "embed_model": str(embed_model or ""),
                "embed_dim": int(index_dim or 0),
            }
            if chat_id is not None:
                metadata["chat_id"] = chat_id
            to_upsert.append((all_ids[j], vectors[j], metadata))
        index.upsert(vectors=to_upsert)

    source_counts = Counter(all_sources)
    new_entries = [
        {"source": s, "chunk_count": c, "chat_id": chat_id}
        for s, c in source_counts.items()
    ]
    update_registry_on_ingest(new_entries)
    append_bm25_corpus(
        [
            {
                "id": all_ids[j],
                "text": all_texts[j],
                "source": all_sources[j],
                "chunk_index": all_chunk_indexes[j],
                "chat_id": chat_id,
            }
            for j in range(len(all_texts))
        ]
    )
    return len(all_texts), new_entries


def ingest_uploaded_files(
    *,
    embed_client: Any,
    index: Any,
    index_dim: int,
    embed_model: str,
    uploaded_files: list[Any],
    chat_id: str | None = None,
) -> int:
    """Streamlit `st.file_uploader` 物件列表（`.name` / `.getvalue()`）。"""
    items: list[tuple[str, bytes]] = []
    for uf in uploaded_files:
        name = getattr(uf, "name", "uploaded")
        items.append((name, uf.getvalue()))
    n, _ = ingest_file_items(
        items,
        embed_client=embed_client,
        index=index,
        index_dim=index_dim,
        embed_model=embed_model,
        chat_id=chat_id,
    )
    return n
