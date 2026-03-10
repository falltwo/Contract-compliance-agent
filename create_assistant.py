import os

from dotenv import load_dotenv
from pinecone import Pinecone


def main() -> None:
    load_dotenv()

    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少環境變數 PINECONE_API_KEY（請放在 .env）")

    index_name = os.getenv("PINECONE_INDEX", "agent-index")

    pc = Pinecone(api_key=api_key)

    # 只做連線檢查：若 index 不存在就清楚提示（避免直接 404）
    existing = {i["name"] for i in pc.list_indexes().get("indexes", [])}
    if index_name not in existing:
        raise RuntimeError(
            f'Pinecone index "{index_name}" 不存在。請先在 Pinecone 建立，或設定 PINECONE_INDEX 指向既有 index。'
        )

    _ = pc.Index(index_name)
    print(f'已連線 Pinecone index：{index_name}')


if __name__ == "__main__":
    main()