import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from document_processing import parse_path_document
from rag_common import chunk_text, embed_texts, get_clients_and_index, save_bm25_corpus, stable_id
from sources_registry import update_registry_on_ingest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source: str
    chunk_index: int
    metadata_extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class IngestRecord:
    source: str
    text: str
    metadata_extra: dict[str, Any] | None = None


def iter_text_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".pdf", ".docx"}:
            out.append(path)
    return out


def extract_text_from_docx(path: Path) -> str:
    parsed = parse_path_document(path=path, enable_ocr=False)
    return parsed.text if parsed else ""


def extract_text_from_pdf(path: Path) -> str:
    parsed = parse_path_document(path=path, enable_ocr=False)
    return parsed.text if parsed else ""


def build_chunks_from_records(records: list[IngestRecord]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for record in records:
        source = (record.source or "").strip()
        text = (record.text or "").strip()
        if not source or not text:
            continue
        for chunk_index, part in enumerate(chunk_text(text)):
            chunks.append(
                Chunk(
                    id=stable_id(source, chunk_index, part),
                    text=part,
                    source=source,
                    chunk_index=chunk_index,
                    metadata_extra=record.metadata_extra or {},
                )
            )
    return chunks


def ingest_chunks(
    chunks: list[Chunk],
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    if not chunks:
        return {"chunk_count": 0, "source_count": 0}

    _chat_client, embed_client, index, dim, _llm_model, embed_model, index_name = get_clients_and_index()
    texts = [chunk.text for chunk in chunks]
    vectors = embed_texts(
        embed_client,
        texts,
        model=embed_model,
        output_dimensionality=dim,
        batch_size=5,
        batch_delay_sec=15.0,
    )

    batch_size = 100
    for offset in range(0, len(chunks), batch_size):
        batch_chunks = chunks[offset : offset + batch_size]
        batch_vecs = vectors[offset : offset + batch_size]
        to_upsert = []
        for chunk, vector in zip(batch_chunks, batch_vecs, strict=True):
            metadata = {
                "text": chunk.text,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
                # 綁定 embedding 版本（model + dim），避免變更 EMBED_MODEL 後新舊向量混用
                "embed_model": str(embed_model or ""),
                "embed_dim": int(dim or 0),
            }
            if chat_id is not None:
                metadata["chat_id"] = chat_id
            if chunk.metadata_extra:
                metadata.update(chunk.metadata_extra)
            to_upsert.append((chunk.id, vector, metadata))
        index.upsert(vectors=to_upsert)

    source_counts = Counter(chunk.source for chunk in chunks)
    update_registry_on_ingest(
        [{"source": source, "chunk_count": count, "chat_id": chat_id} for source, count in source_counts.items()]
    )
    save_bm25_corpus(
        [
            {
                "id": chunk.id,
                "text": chunk.text,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
                "chat_id": chat_id,
            }
            for chunk in chunks
        ]
    )
    return {
        "chunk_count": len(chunks),
        "source_count": len(source_counts),
        "index_name": index_name,
    }


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    data_dir = Path(os.getenv("RAG_DATA_DIR", "data"))
    files = iter_text_files(data_dir)
    if not files:
        raise RuntimeError(f"No ingestible files found under {data_dir.resolve()}")

    chat_client, _embed_client, _index, _dim, llm_model, _embed_model, index_name = get_clients_and_index()

    records: list[IngestRecord] = []
    for path in files:
        parsed = parse_path_document(
            path=path,
            chat_client=chat_client,
            ocr_model=llm_model,
            enable_ocr=True,
        )
        if not parsed or not parsed.text.strip():
            continue

        for warning in parsed.warnings:
            logger.info("%s: %s", path.name, warning)

        records.append(IngestRecord(source=parsed.source, text=parsed.text))

    chunks = build_chunks_from_records(records)
    result = ingest_chunks(chunks, chat_id=None)
    print(f"Ingested {result['chunk_count']} chunks into {index_name} and refreshed BM25 corpus.")


if __name__ == "__main__":
    main()
