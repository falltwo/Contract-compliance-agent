import logging
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from backend.api.deps import SettingsDep
from backend.schemas.ingest import (
    IngestUploadResponse,
    SourcePreviewResponse,
    SourcesListResponse,
)
from backend.services.ingest_adapter import run_ingest_upload
from rag_common import delete_source_from_bm25, load_bm25_corpus
from sources_registry import delete_source_from_registry, list_sources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["ingest"])


def _source_variants(value: str) -> set[str]:
    normalized = value.strip().replace("\\", "/")
    variants = {normalized}
    try:
        repaired = normalized.encode("latin-1").decode("utf-8")
    except UnicodeError:
        repaired = None
    if repaired:
        variants.add(repaired)
    expanded: set[str] = set()
    for item in variants:
        expanded.add(item)
        expanded.add(PurePosixPath(item).name)
    return {item for item in expanded if item}


@router.post("/ingest/upload", response_model=IngestUploadResponse)
async def post_ingest_upload(
    settings: SettingsDep,
    files: Annotated[list[UploadFile], File(..., description="One or more .txt / .md / .pdf / .docx files")],
    chat_id: Annotated[str | None, Form(description="Conversation id for metadata scoping")] = None,
) -> IngestUploadResponse:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    cid = (chat_id or "").strip() or None
    return await run_ingest_upload(files, cid, settings)


@router.get("/sources", response_model=SourcesListResponse)
def get_sources(
    chat_id: str | None = None,
) -> SourcesListResponse:
    cid = (chat_id or "").strip() or None
    return SourcesListResponse(entries=list_sources(chat_id=cid))


@router.delete("/sources")
def delete_source(
    source: Annotated[str, Query(description="完整 source 路徑（如 uploaded/<chat_id>/<filename>）")],
    chat_id: Annotated[str | None, Query(description="對話 ID（選填）")] = None,
) -> dict:
    """從知識庫（BM25 語料 + Pinecone + 來源註冊表）刪除指定來源的所有向量。"""
    normalized_source = source.strip()
    if not normalized_source:
        raise HTTPException(status_code=400, detail="source is required")

    normalized_chat_id = (chat_id or "").strip() or None

    # 1. 從 BM25 語料移除並取得對應的 vector ids
    vector_ids = delete_source_from_bm25(normalized_source, normalized_chat_id)

    # 2. 從 Pinecone 刪除（依 ID 批次刪除）
    if vector_ids:
        try:
            from backend.rag_clients import get_cached_rag_stack
            _, _, index, _, _, _, _ = get_cached_rag_stack()
            batch_size = 1000
            for i in range(0, len(vector_ids), batch_size):
                index.delete(ids=vector_ids[i : i + batch_size])
        except Exception as exc:
            logger.warning("Pinecone delete failed for source=%s: %s", normalized_source, exc)

    # 3. 從來源註冊表移除
    delete_source_from_registry(normalized_source, normalized_chat_id)

    return {
        "deleted": True,
        "source": normalized_source,
        "vector_count": len(vector_ids),
    }


@router.get("/sources/preview", response_model=SourcePreviewResponse)
def get_source_preview(
    source: Annotated[str, Query(description="Exact source id, such as uploaded/<chat_id>/<filename>")],
    chat_id: Annotated[str | None, Query(description="Optional conversation scope")] = None,
) -> SourcePreviewResponse:
    normalized_source = source.strip()
    if not normalized_source:
        raise HTTPException(status_code=400, detail="source is required")

    normalized_chat_id = (chat_id or "").strip() or None
    source_keys = _source_variants(normalized_source)
    corpus = load_bm25_corpus()
    matched = [
        row for row in corpus
        if _source_variants(str(row.get("source", ""))).intersection(source_keys)
        and (normalized_chat_id is None or row.get("chat_id") == normalized_chat_id)
    ]
    if not matched:
        raise HTTPException(status_code=404, detail="Preview source not found")

    matched.sort(key=lambda row: int(row.get("chunk_index", 0)))
    canonical_source = str(matched[0].get("source", normalized_source)).strip().replace("\\", "/")
    title = Path(canonical_source).name.replace("\\", "/")
    if "#chunk" in title:
        title = title.split("#chunk", 1)[0]
    content = "\n\n".join(str(row.get("text", "")).strip() for row in matched if str(row.get("text", "")).strip())
    if not content:
        raise HTTPException(status_code=404, detail="Preview content is empty")

    return SourcePreviewResponse(
        source=canonical_source,
        chat_id=normalized_chat_id,
        title=title,
        content=content,
        chunk_count=len(matched),
    )
