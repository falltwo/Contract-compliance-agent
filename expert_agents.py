"""專家子 Agent：為多 Agent 架構打基礎。

每個專家負責特定領域，使用專用 system prompt + 同一套檢索（retrieve_only），
總管 Agent 依問題意圖路由到對應專家。未來可擴充更多專家或改為獨立 Skill 模組。
"""
from __future__ import annotations

import os
from typing import Any, List, Dict, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

from rag_graph import retrieve_only


def _init_llm() -> Tuple[Any, str]:
    """初始化 chat 用 LLM 客戶端與模型名稱（與 agent_router 一致，支援 Groq）。"""
    from llm_client import get_chat_client_and_model

    return get_chat_client_and_model()


def _build_history_text(history: List[Dict[str, Any]] | None) -> str:
    """將對話歷史轉成給 LLM 的純文字。"""
    if not history:
        return ""
    blocks: List[str] = []
    for turn in history:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label = "使用者" if role == "user" else "助理"
        blocks.append(f"{label}：{content}")
    return "\n".join(blocks)


# ---------- FinancialReportAgent ----------

FINANCIAL_REPORT_SYSTEM = """你是財報與公司營運專家，專門根據檢索到的公司文件（財報、法說會、營運資料）回答問題。

規則：
1) 清楚說明關鍵指標（營收、毛利率、淨利率、EPS、現金流等），必要時用「表格」整理，方便閱讀。
2) 若有風險、異常或需要關注的項目，請明確標示與提示，不要輕描淡寫。
3) 嚴格依據檢索內容回答；不足時可簡要說明「檢索內容未提及」，勿臆測數字。
4) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def financial_report_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """財報／公司營運專家：強調指標說明、風險提示、表格輸出。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return (
            "目前知識庫中沒有與財報或營運相關的檢索結果。請先灌入財報、法說會或營運文件，或改用一般問答。",
            [],
            [],
        )

    client, model = _init_llm()
    history_text = _build_history_text(history)
    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
    else:
        prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

    out = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=FINANCIAL_REPORT_SYSTEM),
    )
    answer = (out.text or "").strip()
    return answer, sources, chunks


# ---------- ESGAgent（風險與法遵） ----------

ESG_AGENT_SYSTEM = """你是 ESG、風險與法遵專家，專門根據檢索內容回答環境、社會、公司治理、訴訟、供應鏈風險、法規遵循等問題。

規則：
1) 表述嚴謹：區分「檢索內容所述」與「推論」，不確定時請註明「依目前檢索無法確認」。
2) 涉及訴訟、裁罰、風險揭露時，以原文或摘要為主，避免過度解讀。
3) 若有數據或時序，請註明來源與時間範圍。
4) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def esg_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """ESG／風險／法遵專家：針對 ESG、訴訟、供應鏈風險等嚴謹回答。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return (
            "目前知識庫中沒有與 ESG、風險或法遵相關的檢索結果。請先灌入相關文件，或改用一般問答。",
            [],
            [],
        )

    client, model = _init_llm()
    history_text = _build_history_text(history)
    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
    else:
        prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

    out = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=ESG_AGENT_SYSTEM),
    )
    answer = (out.text or "").strip()
    return answer, sources, chunks


# ---------- DataAnalystAgent（資料分析／報表摘要）----------

DATA_ANALYST_SYSTEM = """你是資料分析師，專門根據檢索到的內容（文件、報表、表格、數字）做分析與摘要。

規則：
1) 從檢索內容中辨識數字、趨勢、比較、分布，用條列或短段整理成「資料摘要」與「重點發現」。
2) 若有表格或結構化數據，用文字摘要其含義（例如：各項占比、前幾名、成長/衰退）。
3) 區分「檢索內容明確寫出的」與「你的推論」，不確定時註明「依目前內容無法確認」。
4) 可建議「若要進一步分析可補充哪些資料」；若檢索內容不足，明確說明不足之處。
5) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def data_analyst_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """資料分析專家：針對「分析這份資料、報表摘要、數據趨勢」等問題，依檢索內容做分析摘要。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return (
            "目前知識庫中沒有可分析的資料內容。請先灌入相關文件或報表，或改用一般問答／財報專家。",
            [],
            [],
        )

    client, model = _init_llm()
    history_text = _build_history_text(history)
    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
    else:
        prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

    out = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=DATA_ANALYST_SYSTEM),
    )
    answer = (out.text or "").strip()
    return answer, sources, chunks
