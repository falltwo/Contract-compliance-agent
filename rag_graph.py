from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, List, TypedDict

logger = logging.getLogger(__name__)

from google import genai
from google.genai import types
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from rag_common import embed_query as _embed_query_common
from rag_common import format_context as _format_context_common
from rag_common import get_clients_and_index


class _RAGStateRequired(TypedDict):
    question: str
    top_k: int
    context: str
    sources: List[str]
    chunks: List[dict[str, Any]]
    answer: str
    history: List[dict[str, Any]]
    strict: bool
    packaged_context: str  # 調查員產出，供判官使用


class RAGState(_RAGStateRequired, total=False):
    """RAG 圖狀態。error 為選填，retrieve 失敗時寫入，generate 會直接回覆錯誤不呼叫 LLM。"""
    error: str


# 生成回答時只取最近 N 輪對話，避免過長歷史吃滿 context 並維持「記得上下文」效果
MAX_HISTORY_TURNS = int(os.getenv("RAG_MAX_HISTORY_TURNS", "12"))


def _build_history_blocks(history: list[dict[str, Any]], max_turns: int | None = None) -> str:
    """將對話歷史轉成給 LLM 的純文字；只取最近 max_turns 輪（預設 MAX_HISTORY_TURNS）。供 package / generate 共用，避免重複邏輯。"""
    n = max_turns if max_turns is not None else MAX_HISTORY_TURNS
    blocks: list[str] = []
    for turn in (history or [])[-n:]:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label = "使用者" if role == "user" else "助理"
        blocks.append(f"{label}：{content}")
    return "\n".join(blocks)

# 雙 Prompt 架構：調查員（RAG 先打包）→ 判官（主 Agent 再判定）

# Prompt 1 — 調查員（法務檢索專家）：只輸出打包脈絡，不產出風險判定
_INVESTIGATOR_SYSTEM = """# Role
你是一個專為「資深合約審查 Agent」提供精準證據與脈絡的「法務檢索專家」。你的任務是從知識庫中精準提取與當前合約條款高度相關的資訊。

# Alignment Rules
1. 支援交叉比對：若分析「管轄法院」須同時檢索「雙方公司登記地址」；若分析「智財權歸屬」須同時檢索「付款條件」。
2. 支援模糊字眼：若條文有「重大瑕疵」「合理時間」，須檢索客觀定義或業界 SLA 作為參考底線。
3. 支援極端情境：違約／解約條款須優先檢索「損害賠償上限」「退款比例計算」之爭議案例或防禦條款。

# Output
請將檢索到的資訊打包，清晰區分為「原始條文脈絡」與「知識庫參考依據」。若欠缺某類脈絡請註明「檢索內容中未含 OOO 脈絡」。只輸出打包內容，不要輸出風險判定或審查結論。"""

# Prompt 2 — 判官（主 Agent）：僅依調查員提供的脈絡做風險判定
_JUDGE_SYSTEM_STRICT = """你是一個極度嚴謹的「合約法遵審閱助理」（判官），只能根據「調查員」提供的「原始條文脈絡」與「知識庫參考依據」以及「對話歷史」進行分析。
規則：1) 結合對話歷史準確理解目前問題所指條款、主體（甲方/乙方）或採購標的。2) 若脈絡未提及相關細節，請直接回答「檢索資料中未包含此細節」，嚴禁虛構。3) 回答須含【條款類型】【具體內容描述】【風險標註】【原文引述】。4) 關鍵處用 [1]、[2] 標註來源，文末列出 (source#chunk)。"""

_JUDGE_SYSTEM_ADVISOR = """你是一個專業的「合約審閱顧問」（判官），優先根據「調查員」提供的脈絡與「對話歷史」回答，可輔以法務常識。
規則：1) 結合對話歷史確認詢問的是哪一份合約或哪一項權利義務。2) 脈絡不足時可補充法律常識但須註明「此部分為法律常識補充，非該合約原文」。3) 以表格或條列呈現條款對比。4) 引用處標註 [編號]，文末列出 (source#chunk)。"""

# 多查詢檢索：產生輔助問句以補足交叉比對脈絡（如管轄法院 + 雙方地址）
_AUX_QUERIES_SYSTEM = """你是合約檢索輔助專家。根據「主問句」產出 1～3 個簡短的「輔助檢索問句」，用來從知識庫補足交叉比對所需的脈絡。例如：主問句為「管轄法院」時，輔助問句可含「雙方公司登記地址」「立約人地址」；主問句為「智財權歸屬」時，可含「付款條件」「原始碼交付」。只輸出 JSON 陣列，例如 ["雙方登記地址", "付款條件"]，不要其他說明。"""


def _match_key(m: dict[str, Any]) -> tuple[str, int | None]:
    """用於合併多查詢結果時的去重鍵：(source, chunk_index) 或 (id, 0)。"""
    meta = m.get("metadata") or {}
    sid = m.get("id")
    if sid:
        return (str(sid), 0)
    return (str(meta.get("source", "")), meta.get("chunk_index"))


def _generate_auxiliary_queries(
    chat_client: Any,
    llm_model: str,
    main_query: str,
    max_queries: int = 3,
) -> list[str]:
    """依主問句產出 1～max 個輔助檢索問句，供多查詢檢索合併脈絡。失敗或空則回傳空列表。"""
    if not (main_query or "").strip():
        return []
    prompt = f"主問句：{main_query.strip()}\n請輸出輔助檢索問句的 JSON 陣列："
    try:
        out = chat_client.models.generate_content(
            model=llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=_AUX_QUERIES_SYSTEM),
        )
        text = (out.text or "").strip()
        # 嘗試解析 JSON 陣列
        if "[" in text:
            start, end = text.index("["), text.rindex("]") + 1
            arr = json.loads(text[start:end])
        else:
            arr = json.loads(text)
        if not isinstance(arr, list):
            return []
        queries = [str(q).strip() for q in arr if q and str(q).strip()][:max_queries]
        return queries
    except Exception as e:
        logger.warning("Auxiliary queries generation failed: %s", e)
        return []


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

# Query Rewriting：依對話歷史將短問／代名詞問題改寫成完整檢索問句
_QUERY_REWRITE_SYSTEM = (
    "你是一個專注於合約法遵的檢索問句重寫專家。請根據提供的「對話歷史」，將使用者最新的「簡短或具代名詞的問題（如：那延期呢？、這條的罰則是什麼？）」改寫成一個完整、獨立且適合用於向量檢索的查詢語句（例如：這份合約中關於付款延期的罰則是什麼？）。如果原問題已經很完整，請直接輸出原問題。請只輸出重寫後的問句，不要加上任何解釋。"
)


def _rewrite_query_for_retrieval(
    chat_client: Any,
    llm_model: str,
    question: str,
    history: list[dict[str, Any]],
) -> str:
    """依對話歷史用 LLM 將目前問題改寫成適合向量檢索的完整問句；失敗或空則回傳原 question。"""
    if not (question or "").strip():
        return question or ""
    history = history[-MAX_HISTORY_TURNS:]
    history_blocks: list[str] = []
    for turn in history:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label = "使用者" if role == "user" else "助理"
        history_blocks.append(f"{label}：{content}")
    history_text = "\n".join(history_blocks)
    if not history_text.strip():
        return (question or "").strip()
    prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題（請改寫成完整檢索問句）\n{question}"
    try:
        out = chat_client.models.generate_content(
            model=llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=_QUERY_REWRITE_SYSTEM),
        )
        rewritten = (out.text or "").strip()
        return rewritten if rewritten else (question or "").strip()
    except Exception as e:
        logger.warning("Query rewrite failed, using original question: %s", e)
        return (question or "").strip()


def _build_graph():
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH

    chat_client, embed_client, index, index_dim, llm_model, embed_model, index_name = _init_clients_and_index()

    def retrieve(state: RAGState) -> RAGState:
        t0 = time.perf_counter()
        question = state["question"]
        top_k = state.get("top_k") or int(os.getenv("TOP_K", "5"))
        history = state.get("history", [])
        strict = bool(state.get("strict", False))

        try:
            # Query Rewriting：依對話歷史用 LLM 將短問／代名詞改寫成完整檢索問句（RAG_USE_HISTORY_FOR_QUERY=1 建議開啟）
            query_for_embed = question
            if os.getenv("RAG_USE_HISTORY_FOR_QUERY", "").strip().lower() in ("1", "true", "yes") and history:
                query_for_embed = _rewrite_query_for_retrieval(
                    chat_client, llm_model, question, history
                )

            internal_top_k = max(top_k, int(os.getenv("RAG_INTERNAL_TOP_K", "20")))
            aux_top_k = int(os.getenv("RAG_AUX_QUERY_TOP_K", "8"))
            aux_max = int(os.getenv("RAG_AUX_QUERY_MAX", "3"))
            use_multi_query = os.getenv("RAG_MULTI_QUERY", "").strip().lower() in ("1", "true", "yes")
            filter_chat_id = state.get("chat_id")
            query_filter = {"chat_id": {"$eq": filter_chat_id}} if filter_chat_id else None

            qvec = _embed_query_common(
                embed_client,
                query_for_embed,
                model=embed_model,
                output_dimensionality=index_dim,
            )
            res = index.query(vector=qvec, top_k=internal_top_k, include_metadata=True, filter=query_filter)
            raw_matches = list(res.get("matches", []) or [])
            seen_keys = {_match_key(m) for m in raw_matches}

            if use_multi_query and query_for_embed.strip():
                aux_queries = _generate_auxiliary_queries(
                    chat_client, llm_model, query_for_embed, max_queries=aux_max
                )
                for aux_q in aux_queries:
                    if not aux_q:
                        continue
                    try:
                        qvec_aux = _embed_query_common(
                            embed_client,
                            aux_q,
                            model=embed_model,
                            output_dimensionality=index_dim,
                        )
                        res_aux = index.query(vector=qvec_aux, top_k=aux_top_k, include_metadata=True, filter=query_filter)
                        for m in res_aux.get("matches", []) or []:
                            k = _match_key(m)
                            if k not in seen_keys:
                                seen_keys.add(k)
                                raw_matches.append(m)
                    except Exception as e:
                        logger.warning("Auxiliary query failed for %r: %s", aux_q, e)
                merge_cap = int(os.getenv("RAG_MERGE_CAP", str(2 * internal_top_k)))
                if len(raw_matches) > merge_cap:
                    raw_matches = sorted(
                        raw_matches,
                        key=lambda x: (x.get("score") is not None, x.get("score") or 0.0),
                        reverse=True,
                    )[:merge_cap]

            min_score_env = os.getenv("RAG_MIN_SCORE")
            min_score = float(min_score_env) if min_score_env is not None else 0.0
            filtered_matches = [m for m in raw_matches if m.get("score") is None or m.get("score") >= min_score]

            if os.getenv("RAG_DEDUP_ENABLED", "").strip().lower() in ("1", "true", "yes"):
                filtered_matches = _dedup_matches(filtered_matches)

            if not filtered_matches:
                logger.info("rag_graph node=retrieve duration_sec=%.3f outcome=ok", time.perf_counter() - t0)
                return {
                    "question": question,
                    "top_k": top_k,
                    "context": "(無檢索內容)",
                    "sources": [],
                    "chunks": [],
                    "answer": state.get("answer", ""),
                    "history": history,
                    "strict": strict,
                    "packaged_context": "",
                }

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

            context, sources, cleaned_chunks = _format_context_common(best_matches)
            if not context:
                context = "(無檢索內容)"

            logger.info("rag_graph node=retrieve duration_sec=%.3f outcome=ok", time.perf_counter() - t0)
            return {
                "question": question,
                "top_k": top_k,
                "context": context,
                "sources": sources,
                "chunks": cleaned_chunks,
                "answer": state.get("answer", ""),
                "history": history,
                "strict": strict,
                "packaged_context": "",
            }
        except Exception as e:
            logger.exception("rag_graph node=retrieve failed")
            logger.info("rag_graph node=retrieve duration_sec=%.3f outcome=error", time.perf_counter() - t0)
            return {
                "question": question,
                "top_k": top_k,
                "context": "(無檢索內容)",
                "sources": [],
                "chunks": [],
                "answer": state.get("answer", ""),
                "history": history,
                "strict": strict,
                "packaged_context": "",
                "error": str(e)[:500],
            }

    def package(state: RAGState) -> RAGState:
        """調查員：用法務檢索專家 Prompt 將檢索內容打包成「原始條文脈絡」+「知識庫參考依據」，供判官使用。"""
        t0 = time.perf_counter()
        question = state["question"]
        context = state.get("context") or ""
        history = state.get("history", [])
        strict = bool(state.get("strict", False))
        if not context.strip() or context.strip() == "(無檢索內容)":
            logger.info("rag_graph node=package duration_sec=%.3f outcome=skip", time.perf_counter() - t0)
            return {**state, "packaged_context": context or ""}
        history_text = _build_history_blocks(history)
        if history_text:
            prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
        else:
            prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"
        try:
            out = chat_client.models.generate_content(
                model=llm_model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=_INVESTIGATOR_SYSTEM),
            )
            packaged_context = (out.text or "").strip() or context
        except Exception as e:
            logger.warning("Investigator package failed, pass through context: %s", e)
            packaged_context = context
        logger.info("rag_graph node=package duration_sec=%.3f outcome=ok", time.perf_counter() - t0)
        return {
            "question": question,
            "top_k": state.get("top_k", int(os.getenv("TOP_K", "5"))),
            "context": context,
            "sources": state.get("sources", []),
            "chunks": state.get("chunks", []),
            "answer": state.get("answer", ""),
            "history": history,
            "strict": strict,
            "packaged_context": packaged_context,
        }

    def generate(state: RAGState) -> RAGState:
        """判官：僅依調查員提供的 packaged_context 做風險判定。檢索失敗（state 含 error）時直接回覆錯誤訊息不呼叫 LLM。"""
        t0 = time.perf_counter()
        question = state["question"]
        context = state.get("context") or ""
        packaged_context = state.get("packaged_context") or context
        history = state.get("history", [])
        strict = bool(state.get("strict", False))
        err = state.get("error")

        if err:
            answer = (
                "檢索時發生錯誤，無法依知識庫回答。請稍後再試或檢查向量庫連線。\n"
                "（錯誤摘要：" + (err[:300] + "…" if len(err) > 300 else err) + "）"
            )
            logger.info("rag_graph node=generate duration_sec=%.3f outcome=error_reply", time.perf_counter() - t0)
            return {
                "question": question,
                "top_k": state.get("top_k", int(os.getenv("TOP_K", "5"))),
                "context": context,
                "sources": state.get("sources", []),
                "chunks": state.get("chunks", []),
                "answer": answer,
                "history": history,
                "strict": strict,
                "packaged_context": packaged_context,
            }

        history_text = _build_history_blocks(history)
        system = _JUDGE_SYSTEM_STRICT if strict else _JUDGE_SYSTEM_ADVISOR
        if history_text:
            prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索專家提供的脈絡（原始條文脈絡與知識庫參考依據）\n{packaged_context}"
        else:
            prompt = f"## 問題\n{question}\n\n## 檢索專家提供的脈絡（原始條文脈絡與知識庫參考依據）\n{packaged_context}"

        out = chat_client.models.generate_content(
            model=llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        answer = (out.text or "").strip()
        logger.info("rag_graph node=generate duration_sec=%.3f outcome=ok", time.perf_counter() - t0)
        return {
            "question": question,
            "top_k": state.get("top_k", int(os.getenv("TOP_K", "5"))),
            "context": context,
            "sources": state.get("sources", []),
            "chunks": state.get("chunks", []),
            "answer": answer,
            "history": history,
            "strict": strict,
            "packaged_context": packaged_context,
        }

    def _route_after_retrieve(s: RAGState) -> str:
        """無檢索內容時跳過調查員（package），直接進判官（generate），省一次 LLM 呼叫。"""
        ctx = (s.get("context") or "").strip()
        if not ctx or ctx == "(無檢索內容)":
            return "generate"
        return "package"

    builder = StateGraph(RAGState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("package", package)
    builder.add_node("generate", generate)
    builder.set_entry_point("retrieve")
    builder.add_conditional_edges("retrieve", _route_after_retrieve, {"package": "package", "generate": "generate"})
    builder.add_edge("package", "generate")
    builder.add_edge("generate", END)

    _GRAPH = builder.compile(checkpointer=MemorySaver())
    return _GRAPH


def run_rag(
    question: str,
    top_k: int | None = None,
    history: list[dict[str, Any]] | None = None,
    strict: bool = False,
    chat_id: str | None = None,
) -> RAGState:
    """對外公開：使用 LangGraph 跑一次 RAG 流程。

    history：前文對話紀錄（role/user, content）
    strict：是否嚴格只依據檢索內容與歷史回答
    chat_id：若設定，檢索僅限此對話上傳的 chunks；亦用於 Checkpointing 的 thread_id（同一對話可沿用同一 id 以支援未來重放／續跑）。
    """
    graph = _build_graph()
    state: dict[str, Any] = {
        "question": question,
        "top_k": top_k or int(os.getenv("TOP_K", "5")),
        "context": "",
        "sources": [],
        "chunks": [],
        "answer": "",
        "history": list(history or []),
        "strict": strict,
        "packaged_context": "",
    }
    if chat_id:
        state["chat_id"] = chat_id
    config = {"configurable": {"thread_id": chat_id or "default"}}
    result_raw = graph.invoke(state, config=config)
    result: RAGState = {
        "question": result_raw.get("question", question),
        "top_k": result_raw.get("top_k", state["top_k"]),
        "context": result_raw.get("context", ""),
        "sources": result_raw.get("sources", []),
        "chunks": result_raw.get("chunks", []),
        "answer": result_raw.get("answer", ""),
        "history": result_raw.get("history", state["history"]),
        "strict": bool(result_raw.get("strict", state["strict"])),
        "packaged_context": result_raw.get("packaged_context", ""),
    }
    if result_raw.get("error"):
        result["error"] = result_raw["error"]
    return result


def retrieve_only(
    question: str,
    top_k: int = 5,
    chat_id: str | None = None,
) -> tuple[str, list[str], list[dict[str, Any]], float | None]:
    """僅做檢索、不生成。與主 RAG 共用同一套流程：多取 internal_top_k → 過濾 → 可選 dedup → LLM rerank。

    供 Research Agent 判斷是否要補網搜；回傳 (context, sources, chunks, top_score)。無結果時 top_score 為 None。
    chat_id：若設定，只檢索該對話上傳的 chunks。
    """
    chat_client, embed_client, index, index_dim, llm_model, embed_model, _index_name = _init_clients_and_index()
    qvec = _embed_query_common(
        embed_client,
        question,
        model=embed_model,
        output_dimensionality=index_dim,
    )
    query_filter = {"chat_id": {"$eq": chat_id}} if chat_id else None
    internal_top_k = max(top_k, int(os.getenv("RAG_INTERNAL_TOP_K", "20")))
    res = index.query(
        vector=qvec,
        top_k=internal_top_k,
        include_metadata=True,
        filter=query_filter,
    )
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

