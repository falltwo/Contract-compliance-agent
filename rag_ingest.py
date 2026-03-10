import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)
from google import genai
from pypdf import PdfReader

from rag_common import chunk_text, embed_texts, get_clients_and_index, stable_id
from sources_registry import update_registry_on_ingest


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source: str
    chunk_index: int


def iter_text_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".txt", ".md", ".pdf"}:
            out.append(p)
    return out


def extract_text_from_pdf(path: Path) -> str:
    """從 PDF 檔案中擷取所有頁面的文字。"""
    try:
        with path.open("rb") as f:
            reader = PdfReader(f)
            texts: list[str] = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t:
                    texts.append(t)
        return "\n\n".join(texts)
    except Exception as e:
        logger.warning("extract_text_from_pdf failed for %s: %s", path, e, exc_info=True)
        return ""


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    data_dir = Path(os.getenv("RAG_DATA_DIR", "data"))
    files = iter_text_files(data_dir)
    if not files:
        raise RuntimeError(f"找不到可灌入的檔案：{data_dir.resolve()}（支援 .txt/.md/.pdf）")

    _chat_client, embed_client, index, dim, _llm_model, embed_model, index_name = get_clients_and_index()

    chunks: list[Chunk] = []
    for f in files:
        suffix = f.suffix.lower()
        if suffix == ".pdf":
            text = extract_text_from_pdf(f)
        else:
            text = f.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        parts = chunk_text(text)
        for i, part in enumerate(parts):
            chunks.append(
                Chunk(
                    id=stable_id(str(f), i, part),
                    text=part,
                    source=str(f).replace("\\", "/"),
                    chunk_index=i,
                )
            )

    texts = [c.text for c in chunks]
    # 降低 batch_size 並在每批之間延遲，避免 Gemini 免費額度 429；遇 429 時 rag_common 會自動重試
    vectors = embed_texts(
        embed_client,
        texts,
        model=embed_model,
        output_dimensionality=dim,
        batch_size=5,
        batch_delay_sec=15.0,
    )

    # Pinecone upsert（批次）
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i : i + batch_size]
        batch_vecs = vectors[i : i + batch_size]
        to_upsert = []
        for c, v in zip(batch_chunks, batch_vecs, strict=True):
            to_upsert.append(
                (
                    c.id,
                    v,
                    {"text": c.text, "source": c.source, "chunk_index": c.chunk_index},
                )
            )
        index.upsert(vectors=to_upsert)

    source_counts = Counter(c.source for c in chunks)
    update_registry_on_ingest(
        [{"source": s, "chunk_count": n, "chat_id": None} for s, n in source_counts.items()]
    )
    print(f"已灌入完成：{len(chunks)} chunks → {index_name}")


if __name__ == "__main__":
    main()

