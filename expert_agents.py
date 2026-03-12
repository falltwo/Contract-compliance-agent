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


# ---------- ContractRiskAgent（合約／採購法遵） ----------

CONTRACT_RISK_SYSTEM = """你是合約與採購法遵領域的輔助審閱專家，主要針對「臺灣法制與一般商務實務」提供**
風險初步檢視**，不是正式法律意見。

規則：
1) 嚴格根據「檢索到的文件內容」進行分析，可結合一般契約與採購實務常識，但遇到不確定或
   文件未載明之事項，請明確標註「依目前檢索內容無法確認」。
   特別是涉及「金額、比例、期數、日期」等數值時，若表格或文字排列不清楚，嚴禁自行推算或假設，只能描述條款大致內容並提醒需人工核對數字。
2) 針對以下類型條款，盡量辨識並整理：
   - 付款條件與付款期限（預付款、尾款、分期、驗收後幾日付款等）
   - 違約金／損害賠償／責任上限（含間接、連帶責任）
   - 保固與維護義務（期限、範圍、排除條款）
   - 解約與終止條款（單方終止權、解除條件）
   - 競業禁止、排他約定、最低採購量等可能限制交易自由的條款
   - 個資／資安／保密義務
   - 政府採購相關條款（若文件出現「政府採購法」「採購法」「機關」「投標廠商」等用語）
3) 請輸出結構化結果，建議格式：
   - 先用短段落總結合約或文件的整體風險概況。
   - 接著用「表格或條列清單」列出每一類重要條款：
     - 條款類型（例如：付款條件、違約責任、解約條款…）
     - 風險等級（高／中／低，以你對一般臺灣實務的理解主觀評估）
     - 風險說明（為何可能對我方不利；如涉及數字，請偏重說明「計算方式、是否偏高／偏嚴」，不要自行計出具體金額）
     - 條文原文節錄（請節錄關鍵一句或數句）並註明來源編號（例如 [1]、[2]）
     - 建議調整方向或可供法務參考的替代表達方式（不需完全擬好條文，可用要點式）
4) 若相關條款在檢索內容中找不到，請說明「目前檢索內容未發現明確的 XXX 條款」，
   不要臆測存在與否。
5) 請特別留意檢索內容中出現的「法律名稱 + 條號」，例如「民法第 184 條」「政府採購法第 99 條」等：
   - 儘量列出檢索內容中**所有明確寫出的法條字號**，整理成一個獨立清單，放在回答接近結尾處。
   - 每一項至少包含「法律名稱」與「條號」，若文字中有款、項可一併標示（例如：民法第 184 條第 1 項）。
6) 在回答最後，請以「來源列表」條列列出對應的 source#chunk，便於追溯，例如：
   - [1] sourceA.pdf#12
   - [2] 合約樣本.md#5
7) 再次提醒：你僅提供初步風險檢視與整理，請在答案結尾加上一行：
   「本分析僅供內部風險初步檢視與參考，不能視為正式法律意見，重要合約仍應由執業律師審閱。」"""


def contract_risk_agent(
    question: str,
    top_k: int = 12,
    history: List[Dict[str, Any]] | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """合約／採購法遵專家：針對合約條款風險做結構化整理與建議。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    適用情境：審閱合約、採購文件、標案文件、內控制度等。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return (
            "目前知識庫中沒有與合約、採購或法遵相關的可用內容。"
            "請先上傳並灌入相關合約／採購／內規文件，再重新執行合約審閱。",
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
        config=types.GenerateContentConfig(system_instruction=CONTRACT_RISK_SYSTEM),
    )
    answer = (out.text or "").strip()
    return answer, sources, chunks
