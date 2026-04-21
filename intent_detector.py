"""意圖偵測（規則優先）：從使用者問題判斷是否需走特定 tool 路徑。

從 `agent_router.py` 抽出，降低該檔負擔並方便獨立測試。
僅包含純規則（re/str 比對），不呼叫 LLM；LLM gate（`firecrawl_intent_with_llm`）
仍留在 agent_router.py。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple


def _extract_url_from_text(text: str) -> str | None:
    """從文字中擷取第一個 http(s) URL。"""
    if not text or not text.strip():
        return None
    m = re.search(r"https?://[^\s<>)\]]+", text.strip())
    return m.group(0).rstrip(".,;:)") if m else None


# ---------- Firecrawl 意圖 ----------
_FIRECRAWL_SCRAPE_PHRASES = (
    "爬這個網頁", "擷取這個網頁", "抓這個網頁", "抓取網頁",
    "擷取此 url", "擷取此 url", "擷取這個 url", "爬這個 url",
    "幫我爬", "幫我擷取", "把這頁", "這則連結", "這個連結",
)
_FIRECRAWL_SEARCH_PHRASES = (
    "搜尋並擷取", "擷取網路", "從網路搜尋並擷取",
    "搜尋網路並擷取", "網路搜尋並擷取", "找.*新聞", "網路上的.*新聞",
)


def firecrawl_intent(question: str) -> Tuple[str, Dict[str, Any]] | None:
    """判斷是否應使用 Firecrawl 以及用哪一個 tool（scrape_url / firecrawl_search）。"""
    if not question or not question.strip():
        return None
    q = question.strip()
    url = _extract_url_from_text(q)

    if url:
        for phrase in _FIRECRAWL_SCRAPE_PHRASES:
            if phrase in q or phrase.replace("網頁", "連結") in q:
                return ("scrape_url", {"url": url, "only_main_content": True})
        if "擷取" in q or "爬" in q or "抓" in q:
            return ("scrape_url", {"url": url, "only_main_content": True})

    for phrase in _FIRECRAWL_SEARCH_PHRASES:
        if re.search(phrase, q):
            query = q
            for p in ("搜尋並擷取", "擷取網路", "從網路搜尋並擷取", "搜尋網路並擷取", "網路搜尋並擷取"):
                query = query.replace(p, "").strip()
            if re.match(r"^找.*新聞$", q):
                query = re.sub(r"^找\s*", "", q).strip()
            if query:
                return ("firecrawl_search", {"query": query, "limit": 5})
            break

    if re.search(r"(台灣|全球|最新).*新聞", q) or "網路上的" in q or "網路上找" in q:
        return ("firecrawl_search", {"query": q, "limit": 5})

    return None


# ---------- 台灣法律／司法院檢索意圖 ----------
_TW_LAW_PHRASES = (
    "司法院法學資料檢索",
    "法學資料檢索系統",
    "lawsearch.judicial",
    "judicial.gov.tw",
    "搜尋司法院",
    "去搜尋司法院",
    "查司法院",
    "查詢.*法律條",
    "查法律條文",
    "查.*法第.*條",
    "根據文件.*條例",
    "根據文件.*法條",
)


def tw_law_intent(question: str) -> Tuple[str, Dict[str, Any]] | None:
    """使用者明確要查司法院／法律條文時，強制走 tw_law_web_search。"""
    if not question or not question.strip():
        return None
    q = question.strip()
    for phrase in _TW_LAW_PHRASES:
        if re.search(phrase, q):
            return ("tw_law_web_search", {"query": q})
    return None


def contract_risk_with_law_intent(question: str) -> Tuple[str, Dict[str, Any]] | None:
    """使用者明確要求「查法條／法律／司法院」時，走合約＋法條查詢流程。"""
    if not question or not question.strip():
        return None
    q = question.strip()
    contract_terms = ("合約", "契約", "契約書", "租賃契約", "採購", "標案")
    law_terms = ("法條", "法律", "條文", "民法", "消保法", "消費者保護法", "政府採購法", "條例", "司法院")
    law_action_terms = ("查", "查詢", "依據", "引用", "比對", "對照")
    if any(term in q for term in contract_terms) and any(term in q for term in law_terms):
        return ("contract_risk_with_law_search", {})
    if any(term in q for term in contract_terms) and "法規" in q and any(term in q for term in law_action_terms):
        return ("contract_risk_with_law_search", {})
    return None


def contract_risk_agent_intent(question: str) -> Tuple[str, Dict[str, Any]] | None:
    """合約／採購審閱類問句，一律走含法條查詢流程以自動附上司法院連結。"""
    if not question or not question.strip():
        return None
    q = question.strip()
    contract_terms = ("合約", "契約", "契約書", "採購", "租賃契約", "租賃", "標案")
    action_terms = ("審閱", "分析", "檢查", "評估", "看看", "幫我看", "條款", "有什麼", "有哪些", "風險", "不利")
    if any(t in q for t in contract_terms) and any(k in q for k in action_terms):
        return ("contract_risk_with_law_search", {})
    return None
