import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pinecone import Pinecone


def embed_query(
    client: genai.Client,
    text: str,
    *,
    model: str = "gemini-embedding-001",
    output_dimensionality: int | None = None,
) -> list[float]:
    config = None
    if output_dimensionality is not None:
        config = types.EmbedContentConfig(output_dimensionality=output_dimensionality)
    res = client.models.embed_content(model=model, contents=text, config=config)
    return res.embeddings[0].values


def format_context(matches: list[dict]) -> str:
    blocks: list[str] = []
    for m in matches:
        md = m.get("metadata") or {}
        source = md.get("source", "unknown")
        chunk_index = md.get("chunk_index", "?")
        text = (md.get("text") or "").strip()
        if not text:
            continue
        blocks.append(f"[{source}#chunk{chunk_index}]\n{text}")
    return "\n\n---\n\n".join(blocks)


def main() -> None:
    load_dotenv()

    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "agent-index")
    google_api_key = os.getenv("GOOGLE_API_KEY")
    llm_model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    embed_model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    output_dimensionality = os.getenv("EMBED_DIM")
    dim = int(output_dimensionality) if output_dimensionality else None

    if not pinecone_api_key:
        raise RuntimeError("缺少環境變數 PINECONE_API_KEY（請放在 .env）")
    if not google_api_key:
        raise RuntimeError("缺少環境變數 GOOGLE_API_KEY（請放在 .env）")

    gemini = genai.Client(api_key=google_api_key)
    pc = Pinecone(api_key=pinecone_api_key)

    existing = {i["name"] for i in pc.list_indexes().get("indexes", [])}
    if index_name not in existing:
        raise RuntimeError(f'Pinecone index "{index_name}" 不存在（請先執行 ingest 或建立 index）')

    index_info = pc.describe_index(index_name)
    index_dim = int(index_info.get("dimension"))
    if dim is None:
        dim = index_dim
    elif dim != index_dim:
        raise RuntimeError(f"EMBED_DIM={dim} 與 Pinecone index 維度 {index_dim} 不一致，請修正後再執行。")

    index = pc.Index(index_name)

    question = input("請輸入問題：").strip()
    if not question:
        print("未輸入問題，結束。")
        return

    qvec = embed_query(gemini, question, model=embed_model, output_dimensionality=dim)

    top_k = int(os.getenv("TOP_K", "5"))
    res = index.query(vector=qvec, top_k=top_k, include_metadata=True)
    matches = res.get("matches", []) or []

    context = format_context(matches)
    if not context:
        context = "(無檢索內容)"

    system = (
        "你是一個嚴謹的助理。請根據提供的「檢索內容」回答問題。\n"
        "規則：\n"
        "1) 若檢索內容不足以回答，請明確說不知道，不要亂猜。\n"
        "2) 優先引用檢索內容中的原句或重述其要點。\n"
        "3) 回答最後用條列列出引用來源（source#chunk）。"
    )

    prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

    out = gemini.models.generate_content(
        model=llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )

    print("\n=== 回答 ===\n")
    print(out.text or "")


if __name__ == "__main__":
    main()

