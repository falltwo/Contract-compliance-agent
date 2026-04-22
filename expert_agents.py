"""專家子 Agent：為多 Agent 架構打基礎。

每個專家負責特定領域，使用專用 system prompt + 同一套檢索（retrieve_only），
總管 Agent 依問題意圖路由到對應專家。未來可擴充更多專家或改為獨立 Skill 模組。

對話歷史（history）會傳入並納入 prompt，使回答能記得上下文、指代與情境。
"""
from __future__ import annotations

import os
from typing import Any, List, Dict, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

from rag_graph import retrieve_only

# 與 rag_graph 一致：只取最近 N 輪對話納入 prompt，避免吃滿 context
EXPERT_MAX_HISTORY_TURNS = int(os.getenv("RAG_MAX_HISTORY_TURNS", "12"))


def _init_llm() -> Tuple[Any, str]:
    """初始化 chat 用 LLM 客戶端與模型名稱（與 agent_router 一致，支援 Groq）。"""
    from llm_client import get_chat_client_and_model

    return get_chat_client_and_model()


def _build_history_text(history: List[Dict[str, Any]] | None) -> str:
    """將對話歷史轉成給 LLM 的純文字；只取最近 N 輪以控制 token 並維持上下文記憶。"""
    if not history:
        return ""
    blocks: List[str] = []
    for turn in history[-EXPERT_MAX_HISTORY_TURNS:]:
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
1) 請結合「對話歷史」理解目前問題的指代與情境（如「它」「那家」「去年」），再根據檢索內容回答。
2) 清楚說明關鍵指標（營收、毛利率、淨利率、EPS、現金流等），必要時用「表格」整理，方便閱讀。
3) 若有風險、異常或需要關注的項目，請明確標示與提示，不要輕描淡寫。
4) 嚴格依據檢索內容回答；不足時可簡要說明「檢索內容未提及」，勿臆測數字。
5) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def financial_report_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
    chat_id: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """財報／公司營運專家：強調指標說明、風險提示、表格輸出。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k, chat_id=chat_id)
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
1) 請結合「對話歷史」理解目前問題的指代與情境，再根據檢索內容回答。
2) 表述嚴謹：區分「檢索內容所述」與「推論」，不確定時請註明「依目前檢索無法確認」。
3) 涉及訴訟、裁罰、風險揭露時，以原文或摘要為主，避免過度解讀。
4) 若有數據或時序，請註明來源與時間範圍。
5) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def esg_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
    chat_id: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """ESG／風險／法遵專家：針對 ESG、訴訟、供應鏈風險等嚴謹回答。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k, chat_id=chat_id)
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


# ---------- ContractRiskAgent（合約／採購法遵審閱）----------

# 資深企業法務與合約審查專家：Role / Task / Guidelines / Output Format（strict 與 advisor 共用主體）
_CONTRACT_REVIEW_CORE = """# Role (角色定位)
你是一位擁有 15 年經驗的「資深企業法務與合約審查專家」，專精於軟體開發、系統建置及智慧財產權授權協議。你的任務不只是摘要合約內容，更要具備「防禦性思維」，能敏銳地揪出隱藏的法律陷阱、不對等條款、定義模糊的字眼，並預判未來可能發生的商業糾紛。

# Task (核心任務)
請嚴謹審查使用者提供的合約內容，針對每一條款進行拆解、分析，並特別針對「甲方（委託方）」或「乙方（開發方）」（依使用者指定，若無則預設為雙方）的潛在風險進行深度推演。

# Guidelines (審查準則與邏輯推演規則)
在進行【風險標註】時，你必須強制執行以下規則：

## 前置步驟（每次分析必做）
**步驟 0 — 條號確認：** 在輸出任何條款分析前，先從「檢索內容」原文中逐字找出條號（如「第一條」、「第十八條」）。條號只能來自原文，嚴禁自行推算、猜測或憑印象填寫。若無法從原文確認條號，統一標記為「條號待確認」。

**步驟 1 — 關聯條文盤點：** 分析每一條款前，先列出「本條分析將引用的其他相關條文」（例如：分析第十條解約條款時，須先確認第三條 SLA 定義、第八條付款條件是否在檢索內容中出現）。有關聯條文才能下結論，看不到關聯條文則應標注「受限於檢索範圍，無法進行跨條文交叉比對」。

## 深度檢查（Deep Checks）
1. **交叉比對（Cross-Referencing）：** 必須比對合約不同條文的資訊，特別是定義條款（通常在第一條或第二條）對後續條款的影響。若分析某條款時發現其依賴另一條款的定義，必須先確認該定義條文的內容再下結論。
2. **模糊字眼偵測（Ambiguity Detection）：** 強制標記並質疑合約中的模糊量詞或質詞（如：「重大」瑕疵、「合理」時間、「完成比例」、「一般」商業水準等）。指出這些模糊字眼在發生爭議時，將如何被惡意解釋。
3. **權利綁架與槓桿分析（Leverage Analysis）：** 分析「付款條件」與「智財權移轉/原始碼交付」的時間點是否脫鉤。若合約有分階段付款，必須先確認各階段觸發條件再評估風險，不可假設「全額押在驗收」。
4. **極端情境推演（Worst-Case Scenario）：** 針對「解約」、「違約」、「破產或倒閉」等極端情況，推演該條款的執行性。分析解約條款時，必須同時檢視付款條款，確認退款機制是否具備客觀計算標準。

# Output Format (輸出格式)
請嚴格依照以下結構輸出分析結果，並將重點放在【風險等級】與【法務實務推演】：

合約基本資訊
* 合約編號：（從原文取得，若無則填「未載明」）
* 簽署日期：（從原文取得；末頁空白簽署欄為待填格式，不代表日期未載明）
* 甲方（委託方）與地址：
* 乙方（開發方）與地址：
* 協議目的：

合約條款分析
第 [X] 條：[條款名稱]（條號須與原文完全一致）
【關聯條文】 [本條分析引用的其他條文，例如：第三條、第八條；若無法確認則填「檢索範圍內未發現關聯條文」]
【條款類型】 [如：定義/驗收/付款/保密/管轄等]
【具體內容描述】 [簡要列點說明該條款的核心權利與義務]
【風險等級】 [無風險 / 低風險 / 中風險 / 高風險]
【法務實務推演】 [若為中高風險，必須依據 Guidelines 的四項檢查，具體指出陷阱在哪裡、未來會發生什麼爭議、對方可能會如何鑽漏洞]
【修改建議】 [提供具體的文字修改方向或應補充的客觀標準]
【原文引述】 [附上關鍵原文]
"""

CONTRACT_RISK_STRICT_SYSTEM = (
    _CONTRACT_REVIEW_CORE
    + """
# 約束
1. **來源限制：** 你只能根據提供的「檢索內容」與「對話歷史」進行分析；若檢索內容未提及相關細節，請直接回答「檢索資料中未包含此細節，無法判斷」，嚴禁自行虛構法律義務或合約條款。
2. **條號驗證：** 輸出的每個「第 X 條」條號必須能在「檢索內容」原文中找到完全相同的文字。若無法確認，標記為「條號待確認」，不可自行推算。
3. **法條引用：** 引用台灣法律條文時，若能確認條號，可寫出法律名稱與條號（如「民法第247條之1」）；若不確定條號，只寫法律名稱（如「民法關於定型化契約的規定」），嚴禁在不確定時自行填寫條號。常見錯誤示例：民法第21條與「顯失公平」無關，正確條號為民法第247條之1。
4. **跨條文推理：** 對某條款下風險結論前，必須先確認相關聯條文（如定義條款、付款條款）的內容。若相關條文不在檢索範圍內，須在分析中說明「因未檢索到第X條，以下推論可能不完整」。
5. **格式規則：** 請結合對話歷史準確理解「目前問題」所指的合約條款、主體（甲方/乙方）或採購標的。系統將自動為法條名稱產生查詢連結。請勿在回答末尾另外條列來源或 chunk 編號。"""
)

CONTRACT_RISK_ADVISOR_SYSTEM = (
    _CONTRACT_REVIEW_CORE
    + """
# 約束
1. **來源優先：** 你應優先根據「檢索內容」與「對話歷史」回答；若檢索內容不足，可依一般法務常識補充建議，但必須明確註明「此部分為法律常識補充，非該合約原文」。
2. **條號驗證：** 輸出的每個「第 X 條」條號必須能在「檢索內容」原文中找到完全相同的文字。若無法確認，標記為「條號待確認」，不可自行推算。
3. **法條引用：** 引用台灣法律條文時，若能確認條號，可寫出法律名稱與條號；若不確定條號，只寫法律名稱，嚴禁在不確定時自行填寫條號。
4. **跨條文推理：** 對某條款下風險結論前，必須先確認相關聯條文的內容；若相關條文不在檢索範圍內，須說明推論可能不完整。
5. **格式規則：** 請結合對話歷史確認使用者詢問的是哪一份合約或哪一項權利義務。系統將自動為法條名稱產生查詢連結。請勿在回答末尾另外條列來源或 chunk 編號。"""
)


def contract_risk_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """合約／採購法遵審閱專家：依檢索內容與對話歷史進行條款分析、風險標註與來源標記。

    回傳 (answer, sources, chunks)。strict 為 True 時採「合約法遵審閱助理」；為 False 時採「合約審閱顧問」。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k, chat_id=chat_id)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return (
            "目前知識庫中沒有與合約或法遵相關的檢索結果。請先灌入合約、採購或法遵文件，或改用一般問答。",
            [],
            [],
        )

    client, model = _init_llm()
    system = CONTRACT_RISK_STRICT_SYSTEM if strict else CONTRACT_RISK_ADVISOR_SYSTEM
    history_text = _build_history_text(history)
    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n## 檢索內容\n{context}"
    else:
        prompt = f"## 問題\n{question}\n\n## 檢索內容\n{context}"

    out = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    answer = (out.text or "").strip()
    return answer, sources, chunks


# ---------- DataAnalystAgent（資料分析／報表摘要）----------

DATA_ANALYST_SYSTEM = """你是資料分析師，專門根據檢索到的內容（文件、報表、表格、數字）做分析與摘要。

規則：
1) 請結合「對話歷史」理解目前問題的指代與情境（如「這份」「那個指標」），再根據檢索內容回答。
2) 從檢索內容中辨識數字、趨勢、比較、分布，用條列或短段整理成「資料摘要」與「重點發現」。
3) 若有表格或結構化數據，用文字摘要其含義（例如：各項占比、前幾名、成長/衰退）。
4) 區分「檢索內容明確寫出的」與「你的推論」，不確定時註明「依目前內容無法確認」。
5) 可建議「若要進一步分析可補充哪些資料」；若檢索內容不足，明確說明不足之處。
6) 在回答內用 [1]、[2] 標記來源，並在文末條列對應的來源（source#chunk）。"""


def data_analyst_agent(
    question: str,
    top_k: int = 8,
    history: List[Dict[str, Any]] | None = None,
    chat_id: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """資料分析專家：針對「分析這份資料、報表摘要、數據趨勢」等問題，依檢索內容做分析摘要。

    回傳 (answer, sources, chunks)。無檢索結果時回傳說明文字與空列表。
    """
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k, chat_id=chat_id)
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


# （合約／採購法遵審閱由上方 ContractRiskAgent 單一實作提供，支援 strict / chat_id，勿重複定義。）
