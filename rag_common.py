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


from llm_client import _normalize_ollama_base_url

# 模組載入時 load 一次；後續 os.getenv 直接讀 process env，避免每次呼叫重跑 dotenv。
load_dotenv()


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


_HEADING_PATTERN = re.compile(
    r"^(?:#+\s+|[一二三四五六七八九十百]+、|第\s*[0-9一二三四五六七八九十百]+\s*條)"
)

# 合約條文條號正則：支援中文數字及阿拉伯數字
_ARTICLE_NUM_RE = r"[一二三四五六七八九十百千萬\d]+"
_ARTICLE_SPLIT_RE = re.compile(
    rf"(?m)^第\s*{_ARTICLE_NUM_RE}\s*條",
    re.UNICODE,
)
_ARTICLE_HEADER_RE = re.compile(
    rf"第\s*({_ARTICLE_NUM_RE})\s*條\s*([^\n]*)",
    re.UNICODE,
)


def chunk_contract_by_article(
    text: str,
    max_article_chars: int = 3000,
    sub_overlap: int = 200,
) -> list[str]:
    """合約專用切片：每條條款為一個完整 chunk。

    - 依「第X條」邊界切分，保證條文內容不被攔腰截斷
    - 前言、附件、簽署欄等非條文部分各自成 chunk
    - 超長條款（> max_article_chars）再做子切片，但每段保留條款首行標題
    - 若文件不含條文結構（如純文字合約）則自動退回 chunk_text()

    Args:
        text: 合約全文
        max_article_chars: 單一條款 chunk 字元上限（預設 3000）
        sub_overlap: 子切片 overlap（預設 200）

    Returns:
        每個元素為一個完整條款（或條款片段）的字串列表
    """
    splits = list(_ARTICLE_SPLIT_RE.finditer(text))
    if len(splits) < 2:
        # 條文數量不足，退回通用切法（如純文字備忘錄）
        return chunk_text(text)

    result: list[str] = []

    # 前言（第一條之前的文字）
    preamble = text[: splits[0].start()].strip()
    if preamble:
        # 前言可能包含合約基本資訊，一律完整保留
        result.append(preamble)

    # 逐條處理
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        article_text = text[start:end].strip()
        if not article_text:
            continue

        if len(article_text) <= max_article_chars:
            result.append(article_text)
        else:
            # 超長條款：子切片，每段都帶條款標題
            hm = _ARTICLE_HEADER_RE.match(article_text)
            header = hm.group(0).strip() if hm else article_text.splitlines()[0].strip()

            pos = 0
            first_piece = True
            while pos < len(article_text):
                end_pos = min(len(article_text), pos + max_article_chars)
                piece = article_text[pos:end_pos].strip()
                if piece:
                    if not first_piece:
                        piece = f"{header}（續）\n{piece}"
                    result.append(piece)
                    first_piece = False
                if end_pos >= len(article_text):
                    break
                pos = max(0, end_pos - sub_overlap)

    return [r for r in result if r.strip()]


def is_contract_text(text: str, min_articles: int = 3) -> bool:
    """判斷文字是否為合約格式（含足夠數量的條文標號）。"""
    return len(_ARTICLE_SPLIT_RE.findall(text)) >= min_articles


def chunk_text(text: str, *, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """先依段落/標題/合約條款切大區塊，再在區塊內做長度切片。

    針對合約文件：`第X條`、`## 一、`、`一、` 皆視為區塊邊界，
    避免條款內容跨越 chunk 被拆散；若條款長度超過 chunk_size，
    續段會補上條款首行讓語意脈絡不遺失。
    """
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    if chunk_size <= overlap:
        raise ValueError("chunk_size 必須大於 overlap")

    # 條款標記若出現在段落中間（沒有空行），強制在其前方插入空行以獨立成段
    normalized = re.sub(
        r"(?<=\n)(?=\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*條|[一二三四五六七八九十百]+、|#+\s+))",
        "\n",
        cleaned,
    )

    raw_blocks = re.split(r"\n\s*\n+", normalized)
    blocks: list[str] = []
    current: list[str] = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        first_line = lines[0].strip() if lines else ""
        if _HEADING_PATTERN.match(first_line) and current:
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
        first_line = blk.splitlines()[0].strip()
        has_header = bool(_HEADING_PATTERN.match(first_line))
        start = 0
        first_piece = True
        while start < len(blk):
            end = min(len(blk), start + chunk_size)
            piece = blk[start:end].strip()
            if piece:
                if has_header and not first_piece:
                    piece = f"{first_line}（續）\n{piece}"
                chunks_out.append(piece)
                first_piece = False
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
        # 以 httpx_options.timeout 綁定 embed 呼叫逾時，避免 Gemini API hang 拖死整個 RAG 流程
        try:
            gemini_timeout_sec = float(os.getenv("GEMINI_EMBED_TIMEOUT_SEC", "60") or "60")
        except ValueError:
            gemini_timeout_sec = 60.0
        try:
            embed_client = genai.Client(
                api_key=google_api_key,
                http_options=types.HttpOptions(timeout=int(gemini_timeout_sec * 1000)),  # ms
            )
        except Exception:
            # 某些 genai 版本 HttpOptions 欄位不同；退回不帶 timeout，並留 log
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "genai.Client 不支援 HttpOptions.timeout，fallback 無 timeout；"
                "建議升級 google-genai 以啟用 embedding timeout",
            )
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
    p = os.getenv("BM25_CORPUS_PATH", "bm25_corpus.json")
    return Path(p)


def _char_tokenize(text: str) -> list[str]:
    """舊版後援：中文逐字、英文/數字成詞。"""
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


_JIEBA_MODULE: Any = None
_JIEBA_IMPORT_FAILED = False

# 合約／法條常見多字術語，避免 jieba 預設辭典把它們拆碎
# （jieba 的通用辭典不含許多台灣法律專業詞彙）
_LEGAL_TERMS: tuple[str, ...] = (
    # 契約常見條款詞
    "保密義務", "保密期間", "機密資訊", "營業秘密", "智慧財產權",
    "著作財產權", "專利權", "商標權", "著作權",
    "違約責任", "違約金", "懲罰性違約金", "損害賠償", "損害賠償責任",
    "履約保證金", "押標金", "權利金", "授權金",
    "連帶責任", "連帶保證", "瑕疵擔保", "瑕疵責任",
    "不可抗力", "解除契約", "終止契約", "解除條件",
    "管轄法院", "準據法", "仲裁條款", "爭議解決",
    "付款條件", "交貨期限", "驗收標準", "驗收程序",
    "退款機制", "退貨機制",
    # 法律／法規
    "政府採購法", "消費者保護法", "個人資料保護法", "個資法",
    "公平交易法", "公司法", "勞動基準法", "勞基法",
    "民事訴訟法", "強制執行法", "仲裁法",
    "著作權法", "專利法", "商標法",
    # 爭議／救濟
    "違反契約", "契約終止", "定型化契約", "第三人利益契約",
    "債務不履行", "給付不能", "給付遲延", "不完全給付",
    "情事變更", "誠實信用原則",
)


def _apply_legal_userdict(jieba_mod: Any) -> None:
    """將合約／法律術語加入 jieba 辭典，避免被切碎。

    額外可由 `BM25_JIEBA_USERDICT` 指向使用者自訂 dict 檔（每行一個詞，格式見 jieba docs）。
    """
    for term in _LEGAL_TERMS:
        try:
            jieba_mod.add_word(term)
        except Exception:  # pragma: no cover
            pass
    user_dict = os.getenv("BM25_JIEBA_USERDICT", "").strip()
    if user_dict:
        try:
            jieba_mod.load_userdict(user_dict)
        except Exception as e:  # pragma: no cover
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "Failed to load BM25_JIEBA_USERDICT=%r: %s", user_dict, e
            )


def _get_jieba() -> Any | None:
    """Lazy 載入 jieba；若未安裝回傳 None（只警告一次）。"""
    global _JIEBA_MODULE, _JIEBA_IMPORT_FAILED
    if _JIEBA_MODULE is not None:
        return _JIEBA_MODULE
    if _JIEBA_IMPORT_FAILED:
        return None
    try:
        import jieba  # type: ignore

        # 關閉 jieba 初始化訊息，避免 Streamlit/測試輸出污染
        jieba.setLogLevel(40)  # logging.ERROR
        _apply_legal_userdict(jieba)
        _JIEBA_MODULE = jieba
        return jieba
    except Exception as e:  # pragma: no cover - 僅在缺套件時觸發
        _JIEBA_IMPORT_FAILED = True
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "jieba not available, BM25 fall back to char tokenizer: %s", e
        )
        return None


def _bm25_tokenize(text: str) -> list[str]:
    """BM25 用分詞：預設 jieba（中文詞級匹配），`BM25_TOKENIZER=char` 可切回逐字模式。

    詞級分詞比逐字好的情境：
    - 「管轄法院」「違約責任」「智慧財產權」等多字術語可整詞命中
    - 避免高頻單字（的/之/與）稀釋相關性
    """
    if not text or not text.strip():
        return []

    mode = os.getenv("BM25_TOKENIZER", "jieba").strip().lower()
    if mode == "char":
        return _char_tokenize(text)

    jieba = _get_jieba()
    if jieba is None:
        return _char_tokenize(text)

    tokens: list[str] = []
    for tok in jieba.lcut(text, cut_all=False, HMM=True):
        tok = tok.strip()
        if not tok:
            continue
        # 跳過純標點
        if not any(c.isalnum() or _is_cjk(c) for c in tok):
            continue
        tokens.append(tok.lower() if tok.isascii() else tok)
    return tokens


def _is_cjk(c: str) -> bool:
    return "\u4e00" <= c <= "\u9fff"


def _bm25_lock_path() -> Path:
    """BM25 語料的 lock file 路徑（同目錄，後綴 .lock）。"""
    p = get_bm25_corpus_path()
    return p.with_suffix(p.suffix + ".lock") if p.suffix else p.with_name(p.name + ".lock")


class _BM25CorpusLock:
    """跨進程檔案鎖。Linux 用 fcntl.flock；Windows 退化為無鎖（只印 warning）。

    append/save 都應在此 lock 內操作，避免並發寫入互相覆蓋。
    """

    def __init__(self) -> None:
        self._fd = None

    def __enter__(self):
        try:
            import fcntl  # POSIX-only
        except ImportError:
            import logging
            logging.getLogger(__name__).warning(
                "fcntl unavailable (non-POSIX); BM25 corpus write is NOT atomic across processes"
            )
            return self
        lock_path = _bm25_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # 以 append 模式開檔，避免 truncate lock file（它只是 lock holder）
        self._fd = open(lock_path, "a+")
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            try:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None
        return False


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


def _atomic_write_json(path: Path, payload: Any) -> None:
    """同目錄寫 tmp → os.replace，避免讀者讀到半截檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def save_bm25_corpus(chunks: list[dict[str, Any]]) -> None:
    """覆寫寫入 BM25 語料檔。每筆應含 id, text, source, chunk_index，可選 chat_id。

    並發安全：以 fcntl.flock 鎖同目錄的 .lock 檔 + atomic replace。
    """
    path = get_bm25_corpus_path()
    out = []
    for c in chunks:
        out.append({
            "id": str(c.get("id", "")),
            "text": str(c.get("text", "")),
            "source": str(c.get("source", "")),
            "chunk_index": int(c.get("chunk_index", 0)),
            "chat_id": c.get("chat_id"),
        })
    with _BM25CorpusLock():
        _atomic_write_json(path, out)


def delete_source_from_bm25(source: str, chat_id: str | None = None) -> list[str]:
    """從 BM25 語料中移除指定 source 的所有 chunks。
    回傳被移除的 vector id 列表（供後續從 Pinecone 刪除）。"""
    corpus = load_bm25_corpus()
    removed_ids: list[str] = []
    remaining: list[dict[str, Any]] = []
    for c in corpus:
        is_match = (
            c.get("source") == source
            and (chat_id is None or c.get("chat_id") == chat_id)
        )
        if is_match:
            vid = str(c.get("id", ""))
            if vid:
                removed_ids.append(vid)
        else:
            remaining.append(c)
    if removed_ids:
        save_bm25_corpus(remaining)
    return removed_ids


def append_bm25_corpus(chunks: list[dict[str, Any]]) -> None:
    """將新 chunks 追加至 BM25 語料檔。並發安全：load→append→save 全部在 flock 內。"""
    with _BM25CorpusLock():
        existing = load_bm25_corpus()
        for c in chunks:
            existing.append({
                "id": str(c.get("id", "")),
                "text": str(c.get("text", "")),
                "source": str(c.get("source", "")),
                "chunk_index": int(c.get("chunk_index", 0)),
                "chat_id": c.get("chat_id"),
            })
        out = [
            {
                "id": str(c.get("id", "")),
                "text": str(c.get("text", "")),
                "source": str(c.get("source", "")),
                "chunk_index": int(c.get("chunk_index", 0)),
                "chat_id": c.get("chat_id"),
            }
            for c in existing
        ]
        _atomic_write_json(get_bm25_corpus_path(), out)


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
