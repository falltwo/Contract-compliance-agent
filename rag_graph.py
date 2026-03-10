from __future__ import annotations

import hashlib
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, List, TypedDict

logger = logging.getLogger(__name__)

from google import genai
from google.genai import types
from langgraph.graph import END, StateGraph

from rag_common import embed_query as _embed_query_common
from rag_common import format_context as _format_context_common
from rag_common import get_clients_and_index


class RAGState(TypedDict):
    question: str
    top_k: int
    context: str
    sources: List[str]
    chunks: List[dict[str, Any]]
    answer: str
    history: List[dict[str, Any]]
    strict: bool


def _init_clients_and_index() -> tuple[Any, genai.Client, Any, int, str, str, str]:
    """共用的環境初始化：委派給 rag_common。回傳 (chat_client, embed_client, index, dim, llm_model, embed_model, index_name)。"""
    return get_clients_and_index()


# Dedup：相同 hash 或與已保留項相似度 > 此門檻即視為重複並移除
_DEDUP_HIGH_SIMILARITY_THRESHOLD = 0.98


def _normalize_text_for_dedup(text: str) -> str:
    """將文字正規化供 dedup 比對（空白壓縮、strip）。"""
    return " ".join((text or "").strip().split())


def _text_similarity(a: str, b: str) -> float:
    """兩段文字相似度 [0, 1]，用於高相似度 dedup 與 MMR。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _get_text(m: dict[str, Any]) -> str:
    """從 match 的 metadata 取出 text。"""
    return (m.get("metadata") or {}).get("text") or ""


def _dedup_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup：hash 值一樣的直接砍掉；與已保留項相似度 > 0.98 的也砍掉。"""
    seen_hashes: set[str] = set()
    kept: list[dict[str, Any]] = []
    for m in matches:
        text = _get_text(m).strip()
        if not text:
            continue
        norm = _normalize_text_for_dedup(text)
        h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        if h in seen_hashes:
            continue
        # 與已保留項相似度極高（>0.98）則視為重複
        too_similar = False
        for k in kept:
            sim = _text_similarity(text, _get_text(k))
            if sim >= _DEDUP_HIGH_SIMILARITY_THRESHOLD:
                too_similar = True
                break
        if too_similar:
            continue
        seen_hashes.add(h)
        kept.append(m)
    return kept


def _mmr_select(
    matches: list[dict[str, Any]],
    top_n: int = 5,
    lambda_: float = 0.6,
) -> list[dict[str, Any]]:
    """MMR：從剩餘結果中選出最具代表性的 Top N。λ 權重 relevance，1-λ 權重 diversity。

    MMR(d) = λ * rel(d) - (1-λ) * max(sim(d, s) for s in selected)
    rel(d) 使用 Pinecone 的 score（先正規化到 [0,1]）；doc-doc sim 用文字相似度。
    """
    if not matches or top_n <= 0:
        return []
    top_n = min(top_n, len(matches))
    # 正規化 relevance：Pinecone 可能為 cosine，若已有 [0,1] 則不變
    scores = []
    for m in matches:
        s = m.get("score")
        if s is None:
            s = 0.0
        elif not isinstance(s, (int, float)):
            s = 0.0
        scores.append(float(s))
    min_s, max_s = min(scores), max(scores)
    if max_s > min_s:
        rel = [(s - min_s) / (max_s - min_s) for s in scores]
    else:
        rel = [1.0] * len(scores)

    selected: list[int] = []
    remaining = list(range(len(matches)))
    texts = [_get_text(matches[i]) for i in range(len(matches))]

    # 第一個選 relevance 最高
    best_idx = max(remaining, key=lambda i: rel[i])
    selected.append(best_idx)
    remaining.remove(best_idx)

    while len(selected) < top_n and remaining:
        best_mmr = -1.0
        best_i = remaining[0]
        for i in remaining:
            max_sim_to_selected = 0.0
            for j in selected:
                sim = _text_similarity(texts[i], texts[j])
                if sim > max_sim_to_selected:
                    max_sim_to_selected = sim
            mmr = lambda_ * rel[i] - (1.0 - lambda_) * max_sim_to_selected
            if mmr > best_mmr:
                best_mmr = mmr
                best_i = i
        selected.append(best_i)
        remaining.remove(best_i)

    return [matches[i] for i in selected]


def _rerank_with_llm(
    client: genai.Client,
    llm_model: str,
    question: str,
    matches: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    """使用 Gemini 對檢索結果做 rerank，僅保留最相關的前 top_n 筆。

    若解析失敗，會回傳原本的前 top_n 筆，確保不會壞掉整個流程。
    """
    if not matches or top_n <= 0:
        return []

    top_n = min(top_n, len(matches))

    # 準備候選片段摘要，避免 prompt 過長
    desc_blocks: list[str] = []
    for i, m in enumerate(matches, start=1):
        md = m.get("metadata") or {}
        text = (md.get("text") or "").strip()
        if not text:
            continue
        snippet = text[:500]
        desc_blocks.append(f"候選 {i}：\n{snippet}")

    if not desc_blocks:
        return matches[:top_n]

    system = (
        "你是一個檢索結果重排器，請依照與問題的相關程度，由高到低排序候選片段。\n"
        "只需輸出前 N 個編號，以逗號分隔，例如：1,3,2\n"
        "禁止輸出任何解釋或多餘文字。"
    )

    prompt = (
        f"問題：{question}\n\n"
        f"N = {top_n}\n\n"
        "以下是多個候選片段：\n\n"
        + "\n\n".join(desc_blocks)
        + "\n\n請根據與問題的相關性，輸出前 N 個候選的編號（1 開始），以逗號分隔："
    )

    out = client.models.generate_content(
        model=llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    text = (out.text or "").strip()

    # 從輸出中擷取編號
    nums = re.findall(r"\d+", text)
    order: list[int] = []
    for n in nums:
        idx = int(n)
        if 1 <= idx <= len(matches) and idx not in order:
            order.append(idx)
        if len(order) >= top_n:
            break

    if not order:
        return matches[:top_n]

    # 依照模型輸出的順序重排
    return [matches[i - 1] for i in order]


_GRAPH = None


def _build_graph():
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH

    chat_client, embed_client, index, index_dim, llm_model, embed_model, index_name = _init_clients_and_index()

    def retrieve(state: RAGState) -> RAGState:
        question = state["question"]
        top_k = state.get("top_k") or int(os.getenv("TOP_K", "5"))
        history = state.get("history", [])
        strict = bool(state.get("strict", False))

        qvec = _embed_query_common(
            embed_client,
            question,
            model=embed_model,
            output_dimensionality=index_dim,
        )

        # 內部實際檢索的 top_k，預設至少抓 20 筆，方便後續用 LLM rerank
        # 未來若有專有名詞、數字、法規條號等「精確匹配」需求，可在此改為 hybrid：
        # 例如 RAG_USE_HYBRID=1 時併用向量 + 關鍵字/BM25，再合併結果做 rerank。
        internal_top_k = max(top_k, int(os.getenv("RAG_INTERNAL_TOP_K", "20")))
        res = index.query(vector=qvec, top_k=internal_top_k, include_metadata=True)
        raw_matches = res.get("matches", []) or []

        # 依 score 做基本過濾，避免非常不相關的片段
        min_score_env = os.getenv("RAG_MIN_SCORE")
        min_score = float(min_score_env) if min_score_env is not None else 0.0
        filtered_matches: list[dict[str, Any]] = []
        for m in raw_matches:
            score = m.get("score")
            if score is None or score >= min_score:
                filtered_matches.append(m)

        # 可選：依文字內容去重，避免多段幾乎重複（可設 RAG_DEDUP_ENABLED=1 啟用）
        if os.getenv("RAG_DEDUP_ENABLED", "").strip().lower() in ("1", "true", "yes"):
            filtered_matches = _dedup_matches(filtered_matches)

        # 若沒有任何通過門檻的片段，回傳空 context 讓生成端明確回應「不知道」
        if not filtered_matches:
            return {
                "question": question,
                "top_k": top_k,
                "context": "(無檢索內容)",
                "sources": [],
                "chunks": [],
                "answer": state.get("answer", ""),
                "history": history,
                "strict": strict,
            }

        rerank_top_n = min(max(top_k, 1), int(os.getenv("RAG_RERANK_TOP_N", "5")))
        mmr_lambda_env = os.getenv("RAG_MMR_LAMBDA", "").strip()
        if mmr_lambda_env:
            # MMR：λ 約 0.5～0.7，從剩餘結果選出最具代表性的 Top N
            try:
                lam = float(mmr_lambda_env)
                lam = max(0.0, min(1.0, lam))
            except ValueError:
                lam = 0.6
            best_matches = _mmr_select(filtered_matches, top_n=rerank_top_n, lambda_=lam)
        else:
            # 使用 LLM 做 rerank
            best_matches = _rerank_with_llm(
                chat_client,
                llm_model,
                question,
                filtered_matches,
                top_n=rerank_top_n,
            )

        context, sources, cleaned_chunks = _format_context_common(best_matches)

        if not context:
            context = "(無檢索內容)"

        return {
            "question": question,
            "top_k": top_k,
            "context": context,
            "sources": sources,
            "chunks": cleaned_chunks,
            "answer": state.get("answer", ""),
            "history": history,
            "strict": strict,
        }

    def generate(state: RAGState) -> RAGState:
        question = state["question"]
        context = state.get("context") or ""
        history = state.get("history", [])
        strict = bool(state.get("strict", False))

        # 將對話歷史整理成簡單文字，方便模型理解上下文
        history_blocks: list[str] = []
        for turn in history:
            role = turn.get("role", "user")
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            label = "使用者" if role == "user" else "助理"
            history_blocks.append(f"{label}：{content}")
        history_text = "\n".join(history_blocks)

        if strict:
            system = (
                "你是一個嚴謹的助理，只能根據提供的「檢索內容」與對話歷史回答問題。\n"
                "規則：\n"
                "1) 若檢索內容與歷史不足以回答，請明確說不知道，不要亂猜，也不要補充外部世界知識。\n"
                "2) 優先引用檢索內容中的原句或重述其要點。\n"
                "3) 在回答內可以用 [1]、[2] 這種編號標記關鍵句的來源，\n"
                "   並在回答最後用條列列出每個編號對應的來源（source#chunk）。"
            )
        else:
            system = (
                "你是一個嚴謹的助理，回答時應優先根據提供的「檢索內容」與對話歷史。\n"
                "規則：\n"
                "1) 若檢索內容不足以完全回答，可以適度補充一般常識，但要清楚標示哪些部分是推論。\n"
                "2) 優先引用檢索內容中的原句或重述其要點。\n"
                "3) 在回答內可以用 [1]、[2] 這種編號標記關鍵句的來源，\n"
                "   並在回答最後用條列列出每個編號對應的來源（source#chunk）。"
            )

        if history_text:
            prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
        else:
            prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

        out = chat_client.models.generate_content(
            model=llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system),
        )

        answer = (out.text or "").strip()
        return {
            "question": question,
            "top_k": state.get("top_k", int(os.getenv("TOP_K", "5"))),
            "context": context,
            "sources": state.get("sources", []),
            "chunks": state.get("chunks", []),
            "answer": answer,
            "history": history,
            "strict": strict,
        }

    builder = StateGraph(RAGState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)

    builder.set_entry_point("retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)

    _GRAPH = builder.compile()
    return _GRAPH


def run_rag(
    question: str,
    top_k: int | None = None,
    history: list[dict[str, Any]] | None = None,
    strict: bool = False,
) -> RAGState:
    """對外公開：使用 LangGraph 跑一次 RAG 流程。

    history：前文對話紀錄（role/user, content）
    strict：是否嚴格只依據檢索內容與歷史回答
    """
    graph = _build_graph()
    state: RAGState = {
        "question": question,
        "top_k": top_k or int(os.getenv("TOP_K", "5")),
        "context": "",
        "sources": [],
        "chunks": [],
        "answer": "",
        "history": list(history or []),
        "strict": strict,
    }
    result_raw = graph.invoke(state)
    # LangGraph 回傳一般 dict，這裡做輕微正規化，確保型別完整
    result: RAGState = {
        "question": result_raw.get("question", question),
        "top_k": result_raw.get("top_k", state["top_k"]),
        "context": result_raw.get("context", ""),
        "sources": result_raw.get("sources", []),
        "chunks": result_raw.get("chunks", []),
        "answer": result_raw.get("answer", ""),
         # 歷史與 strict 主要由呼叫端管理，這裡還是回傳以利除錯/觀察
        "history": result_raw.get("history", state["history"]),
        "strict": bool(result_raw.get("strict", state["strict"])),
    }
    return result


def retrieve_only(
    question: str,
    top_k: int = 5,
) -> tuple[str, list[str], list[dict[str, Any]], float | None]:
    """僅做檢索、不生成。與主 RAG 共用同一套流程：多取 internal_top_k → 過濾 → 可選 dedup → LLM rerank。

    供 Research Agent 判斷是否要補網搜；回傳 (context, sources, chunks, top_score)。無結果時 top_score 為 None。
    """
    chat_client, embed_client, index, index_dim, llm_model, embed_model, _index_name = _init_clients_and_index()
    qvec = _embed_query_common(
        embed_client,
        question,
        model=embed_model,
        output_dimensionality=index_dim,
    )
    # 與主線 RAG 一致：多取幾筆再過濾、可選 dedup、LLM rerank（未來可加 hybrid 併用關鍵字/BM25）
    internal_top_k = max(top_k, int(os.getenv("RAG_INTERNAL_TOP_K", "20")))
    res = index.query(vector=qvec, top_k=internal_top_k, include_metadata=True)
    raw_matches = res.get("matches", []) or []
    if not raw_matches:
        return "(無檢索內容)", [], [], None

    min_score_env = os.getenv("RAG_MIN_SCORE")
    min_score = float(min_score_env) if min_score_env is not None else 0.0
    filtered_matches: list[dict[str, Any]] = []
    for m in raw_matches:
        score = m.get("score")
        if score is None or score >= min_score:
            filtered_matches.append(m)
    if not filtered_matches:
        return "(無檢索內容)", [], [], None

    top_score = filtered_matches[0].get("score")
    if top_score is not None and not isinstance(top_score, (int, float)):
        top_score = None

    if os.getenv("RAG_DEDUP_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        filtered_matches = _dedup_matches(filtered_matches)
    if not filtered_matches:
        return "(無檢索內容)", [], [], top_score

    rerank_top_n = min(max(top_k, 1), int(os.getenv("RAG_RERANK_TOP_N", "5")))
    mmr_lambda_env = os.getenv("RAG_MMR_LAMBDA", "").strip()
    if mmr_lambda_env:
        try:
            lam = float(mmr_lambda_env)
            lam = max(0.0, min(1.0, lam))
        except ValueError:
            lam = 0.6
        best_matches = _mmr_select(filtered_matches, top_n=rerank_top_n, lambda_=lam)
    else:
        best_matches = _rerank_with_llm(
            chat_client,
            llm_model,
            question,
            filtered_matches,
            top_n=rerank_top_n,
        )
    context, sources, cleaned = _format_context_common(best_matches)
    return context, sources, cleaned, top_score


def search_similar(
    query_text: str,
    top_k: int = 10,
) -> tuple[list[str], list[dict[str, Any]]]:
    """語意搜尋：依使用者提供的文字找出知識庫中最相關的段落。

    不回傳生成答案，只回傳 (sources, chunks) 供呼叫端組裝成回答。
    """
    chat_client, embed_client, index, index_dim, _llm_model, embed_model, _index_name = _init_clients_and_index()
    qvec = _embed_query_common(
        embed_client,
        query_text,
        model=embed_model,
        output_dimensionality=index_dim,
    )
    res = index.query(vector=qvec, top_k=top_k, include_metadata=True)
    matches = res.get("matches", []) or []
    _context, sources, cleaned = _format_context_common(matches)
    return sources, cleaned


def summarize_source(
    source: str,
    max_chunks: int = 50,
) -> str:
    """對單一來源（某份文件）做摘要：依 source 過濾取回 chunks，組文後用 LLM 總結。"""
    if not (source or source.strip()):
        return "未指定來源（source 為空）。"
    source = source.strip()
    chat_client, embed_client, index, index_dim, llm_model, embed_model, _index_name = _init_clients_and_index()
    # 用 source 名稱當查詢向量，搭配 metadata filter 只取該來源的 chunks
    qvec = _embed_query_common(
        embed_client,
        source,
        model=embed_model,
        output_dimensionality=index_dim,
    )
    try:
        res = index.query(
            vector=qvec,
            top_k=max_chunks,
            include_metadata=True,
            filter={"source": {"$eq": source}},
        )
    except Exception as e:
        logger.warning("summarize_source query failed for source=%r: %s", source, e, exc_info=True)
        return f"查詢該來源時發生錯誤：{e!s}"
    matches = res.get("matches", []) or []
    if not matches:
        return f"知識庫中找不到來源「{source}」，或該來源尚無內容。"
    # 依 chunk_index 排序後組文
    with_index = [(m.get("metadata") or {}, m) for m in matches]
    with_index.sort(key=lambda x: x[0].get("chunk_index", 0))
    parts: list[str] = []
    for md, _m in with_index:
        text = (md.get("text") or "").strip()
        if text:
            parts.append(text)
    full_text = "\n\n".join(parts)
    if not full_text:
        return f"來源「{source}」的內容為空，無法摘要。"
    # 避免超過模型 context 上限，只取前一段
    max_chars = int(os.getenv("RAG_SUMMARY_MAX_CHARS", "80000"))
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n[... 後略 ...]"
    system = (
        "你是一個文件摘要助理。請根據以下「單一來源」的完整內容，撰寫一份簡潔摘要。\n"
        "摘要應涵蓋：主旨、關鍵數字或事實、重要結論或風險（若有）。使用條列或短段即可。"
    )
    prompt = f"## 來源\n{source}\n\n## 內容\n{full_text}\n\n請產出上述來源的摘要："
    out = chat_client.models.generate_content(
        model=llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return (out.text or "").strip() or "無法產生摘要。"


if __name__ == "__main__":
    q = input("請輸入問題：").strip()
    if not q:
        raise SystemExit(0)
    out = run_rag(q)
    print("=== 回答 ===\n")
    print(out.get("answer", ""))

