import json
import logging
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

logger = logging.getLogger(__name__)
from google import genai
from google.genai import types

from company_tools import financial_metrics, generate_quarterly_plan, parse_dates_from_text
from echarts_mcp_client import call_echarts_mcp, use_echarts_mcp
from expert_agents import (
    CONTRACT_RISK_STRICT_SYSTEM,
    contract_risk_agent,
    data_analyst_agent,
    esg_agent,
    financial_report_agent,
)
from echarts_tools import create_chart_option
from contract_risk_parser import parse_risk_cards
from firecrawl_tools import scrape_url, search_and_scrape
from intent_detector import (
    _extract_url_from_text,
    contract_risk_agent_intent,
    contract_risk_with_law_intent,
    firecrawl_intent,
    tw_law_intent,
)
from llm_client import get_model_for_stage, get_timeout_for_stage
from progress import emit_progress
from rag_graph import retrieve_only, run_rag, search_similar, summarize_source
from sources_registry import list_sources

# ---------- Tool 註冊：支援的 tool 名稱集中管理，_decide_tool 與執行分支皆依此為準 ----------
SUPPORTED_TOOLS = frozenset({
    "rag_search",
    "research",
    "small_talk",
    "list_sources",
    "search_similar",
    "summarize_source",
    "web_search",
    "scrape_url",
    "firecrawl_search",
    "ask_web_vs_rag",
    "create_chart",
    "analyze_and_chart",
    "financial_metrics",
    "parse_dates_from_text",
    "generate_quarterly_plan",
    "financial_report_agent",
    "esg_agent",
    "data_analyst_agent",
    "contract_risk_agent",
    "tw_law_web_search",
    "contract_risk_with_law_search",
})


def _timeout_kwargs(client: Any, stage: str) -> dict[str, Any]:
    timeout_sec = get_timeout_for_stage(stage)
    if not timeout_sec:
        return {}
    if getattr(client, "_supports_request_timeout", False):
        return {"request_timeout_sec": timeout_sec}
    return {}


def firecrawl_intent_with_llm(question: str) -> Tuple[str, Dict[str, Any]] | None:
    """當規則無法判斷時，用一次 LLM 只回答「要不要用 Firecrawl、用哪個、參數」。
    需設 FIRECRAWL_USE_LLM_GATE=1 才會被呼叫。回傳 (tool_name, tool_args) 或 None。
    """
    if os.getenv("FIRECRAWL_USE_LLM_GATE", "").strip().lower() not in ("1", "true", "yes"):
        return None
    client, model = _init_llm_client()
    router_model = get_model_for_stage("router", model)
    url = _extract_url_from_text(question)
    system = (
        "你只負責判斷：使用者是否要「擷取網頁內容」或「搜尋網路並擷取內容」？\n"
        "若「是」且問題中有明確 URL，回傳 JSON：{\"use\": \"scrape_url\", \"url\": \"該 URL\"}\n"
        "若「是」且是關鍵字/主題（例如新聞、某主題），回傳 JSON：{\"use\": \"firecrawl_search\", \"query\": \"關鍵字或主題\"}\n"
        "若「否」（例如只是問知識庫、一般聊天），回傳：{\"use\": \"none\"}\n"
        "禁止輸出其他文字，只輸出一段 JSON。"
    )
    prompt = f"使用者說：{question}\n\n請輸出上述 JSON："
    try:
        out = client.models.generate_content(
            model=router_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system),
            **_timeout_kwargs(client, "router"),
        )
        text = (out.text or "").strip()
        data = json.loads(text)
        use = (data.get("use") or "").strip().lower()
        if use == "scrape_url":
            u = (data.get("url") or url or "").strip()
            if u:
                return ("scrape_url", {"url": u, "only_main_content": True})
        if use == "firecrawl_search":
            query = (data.get("query") or question).strip()
            if query:
                return ("firecrawl_search", {"query": query, "limit": 5})
    except Exception as e:
        logger.debug("firecrawl_llm_gate failed: %s", e, exc_info=True)
    return None


def _format_firecrawl_scrape_result(raw: Any, max_chars: int = 30000) -> str:
    """將 Firecrawl scrape 回傳值轉成可顯示的字串（優先 markdown）。"""
    if isinstance(raw, str):
        return raw[:max_chars] + ("…" if len(raw) > max_chars else "")
    if isinstance(raw, dict):
        md = raw.get("markdown") or raw.get("content") or raw.get("data", {}).get("markdown")
        if isinstance(md, str) and md.strip():
            return md[:max_chars] + ("…" if len(md) > max_chars else "")
        title = raw.get("metadata", {}).get("title") if isinstance(raw.get("metadata"), dict) else None
        if title:
            return f"# {title}\n\n(內容未擷取到 markdown，請檢查 API 回傳)"
    return str(raw)[:max_chars] + ("…" if len(str(raw)) > max_chars else "")


# ---------- 合約審閱＋法條查詢流程：從合約抽出法條字號 → 查司法院／爬蟲對比 → 整合評估 ----------
_CONTRACT_DISCLAIMER = (
    "本分析僅供內部風險初步檢視與參考，不能視為正式法律意見，重要合約仍應由執業律師審閱。"
)
# 合約／法律審閱場景常見之法規（擴展覆蓋率，避免 router 靜默漏抽）。
# 條號支援 "第 N 條"、"第 N-M 條"（如勞基法 84-1）、選填 "第 N 項 第 N 款"。
_LAW_NAME_ALTERNATION = (
    r"民法"
    r"|消費者保護法|消保法"
    r"|政府採購法|政採法"
    r"|行政程序法|民事訴訟法|刑事訴訟法|行政訴訟法"
    r"|消防法|自來水法"
    r"|租賃住宅市場發展及管理條例"
    # 合約常涉：勞動、公司、智財、個資、證交、不動產、稅務
    r"|勞動基準法|勞基法|勞動契約法|勞工退休金條例|性別工作平等法|職業安全衛生法"
    r"|公司法|企業併購法|證券交易法|證交法|公平交易法|公平法"
    r"|著作權法|專利法|商標法|營業秘密法"
    r"|個人資料保護法|個資法"
    r"|土地法|不動產經紀業管理條例|平均地權條例|都市計畫法|建築法|土地徵收條例"
    r"|加值型及非加值型營業稅法|所得稅法|遺產及贈與稅法"
    r"|政府資訊公開法|電子簽章法|消費者債務清理條例"
)
_LAW_REF_PATTERN = re.compile(
    rf"({_LAW_NAME_ALTERNATION})"
    r"\s*第\s*(\d+(?:[-−–]\d+)?)\s*條"
    r"(?:\s*第\s*\d+\s*項)?(?:\s*第\s*\d+\s*款)?"
)


def _extract_law_refs_from_text(text: str) -> List[str]:
    """從合約或檢索內容中抽出「法律名稱＋條號」，去重後回傳，用於後續查司法院。"""
    if not text or not text.strip():
        return []
    seen: set[str] = set()
    out: List[str] = []
    for m in _LAW_REF_PATTERN.finditer(text):
        name = (m.group(1) or "").strip()
        num = (m.group(2) or "").strip()
        if not name or not num:
            continue
        key = f"{name}第{num}條"
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{name}第{num}條")
    return out[:4]  # 最多查 4 條，控制延遲


def _web_search_with_urls(query: str, max_results: int = 5) -> Tuple[str, List[str]]:
    """呼叫 _web_search 並從回傳的 markdown 中擷取 URL 列表，回傳 (回答文字, URL 列表)。"""
    text = _web_search(query=query, max_results=max_results)
    urls = re.findall(r"\]\((https?://[^\)]+)\)", text)
    return text, list(dict.fromkeys(urls))  # 保持順序去重


def _crawl_law_for_comparison(law_refs: List[str], *, max_refs: int = 5) -> Tuple[str, List[str]]:
    """使用 Firecrawl 搜尋並擷取法條相關頁面，供與合約引用對比、增加可信度。回傳 (markdown 摘要, URL 列表)。"""
    urls: List[str] = []
    parts: List[str] = []
    for ref in law_refs[:max_refs]:
        query = f"{ref} 全國法規資料庫 OR 司法院"
        raw = search_and_scrape(query, limit=2)
        if isinstance(raw, str) and ("未設定" in raw or "失敗" in raw):
            continue
        if isinstance(raw, dict):
            data = raw.get("data") if isinstance(raw.get("data"), list) else raw.get("results")
            if data:
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    meta = item.get("metadata") or {}
                    title = item.get("title") or meta.get("title") or ref
                    url_h = item.get("url") or meta.get("source") or ""
                    md = item.get("markdown") or item.get("content") or ""
                    if url_h:
                        urls.append(url_h)
                    excerpt = (md[:2500] + "…") if len(str(md)) > 2500 else (md or "")
                    if excerpt:
                        parts.append(f"### {ref}\n**來源：** {title}\n{url_h}\n\n{excerpt}")
    if not parts:
        return "", urls
    return "\n\n".join(parts), list(dict.fromkeys(urls))


def _contract_risk_with_law_search_impl(
    question: str,
    top_k: int,
    history: List[Dict[str, Any]] | None,
    chat_id: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """
    流程：1) RAG 取合約 2) 抽出法條字號 3) 逐條查司法院 4) 合併 context 後由 LLM 做風險評估。
    回傳 (answer, sources, chunks)。sources 含合約 chunk 標籤＋外部 URL。chat_id 若設定則僅檢索該對話上傳的 chunks。
    """
    emit_progress("contract_retrieve", "正在檢索合約內容…")
    context_rag, sources_rag, chunks_rag, _ = retrieve_only(question=question, top_k=max(top_k, 14), chat_id=chat_id)
    if not context_rag or context_rag.strip() == "(無檢索內容)" or not chunks_rag:
        return (
            "目前知識庫中沒有與合約相關的內容。請先上傳並灌入合約／採購文件，再使用「合約審閱（含法條查詢）」功能。",
            [],
            [],
        )

    # 補抓主文件的首頁 chunk（chunk_index=0），確保合約基本資訊（編號、日期）在 context 中
    # 只注入「被檢索到最多次」的那份文件，避免多份合約同時在知識庫時注入錯誤文件的首頁
    try:
        from collections import Counter
        from rag_common import load_bm25_corpus
        source_counts = Counter(
            c["tag"].split("#chunk")[0]
            for c in chunks_rag
            if "#chunk" in c.get("tag", "")
        )
        primary_source = source_counts.most_common(1)[0][0] if source_counts else None
        if primary_source:
            corpus = load_bm25_corpus()
            existing_tags = {c["tag"] for c in chunks_rag}
            primary_tag = f"{primary_source}#chunk0"
            if primary_tag not in existing_tags:
                for row in corpus:
                    if str(row.get("source", "")) != primary_source:
                        continue
                    if int(row.get("chunk_index", 999)) != 0:
                        continue
                    text = str(row.get("text", "")).strip()
                    if text:
                        context_rag = (
                            f"## 合約首頁資訊（主文件：{primary_source}）\n\n"
                            f"[{primary_tag}]\n{text}\n\n" + context_rag
                        )
                    break
    except Exception as exc:
        logger.warning("header chunk fetch failed: %s", exc)

    law_refs = _extract_law_refs_from_text(context_rag)
    law_sections: List[str] = []
    web_urls: List[str] = []

    # 並行查詢各法條，避免循序等待
    def _search_one(ref: str) -> Tuple[str, str, List[str]]:
        q = f"{ref} site:judicial.gov.tw"
        block, urls = _web_search_with_urls(q, max_results=3)
        return ref, block, urls

    queries_to_run: List[str] = list(law_refs) if law_refs else []
    if not queries_to_run:
        # 合約未明確引用法條，依問題動態產生一條後備查詢
        q_short = question.strip()[:40]
        queries_to_run = [f"{q_short} 相關法律條文 site:judicial.gov.tw"]

    if law_refs:
        emit_progress(
            "law_search",
            f"擷取到 {len(law_refs)} 筆法條引用，並行搜尋司法院…",
        )
    else:
        emit_progress("law_search", "合約未明示法條，依問題推導相關法規搜尋…")

    with ThreadPoolExecutor(max_workers=min(len(queries_to_run), 4)) as pool:
        futures = {pool.submit(_search_one, q): q for q in queries_to_run}
        for fut in as_completed(futures):
            try:
                ref, block, urls = fut.result()
                if block and "無法使用網路搜尋" not in block:
                    label = ref if ref in law_refs else "相關法規與實務"
                    law_sections.append(f"### {label}\n{block}")
                    web_urls.extend(urls)
            except Exception as exc:
                logger.warning("law search failed: %s", exc)

    crawl_law_text = ""  # Firecrawl 爬蟲已停用（速度考量）

    combined_context = "## 合約檢索內容（知識庫）\n\n" + context_rag
    if law_sections:
        combined_context += "\n\n## 查詢到的相關條文與實務見解（司法院／網路）\n\n" + "\n\n".join(law_sections)
    else:
        combined_context += "\n\n## 查詢到的相關條文\n（未找到對應條文，僅依合約內容評估。）"
    if web_urls:
        combined_context += "\n\n## 本次查詢使用之外部連結（請在回答的「來源列表」中一併列出）\n" + "\n".join(f"- {u}" for u in web_urls)

    # 使用與 contract_risk_agent 相同的結構化輸出格式（第X條 / 【風險等級】/ 【法務實務推演】/ 【修改建議】）
    # 並補充法條查詢結果指引，確保 Risk Cards 解析器能正常運作
    system = (
        CONTRACT_RISK_STRICT_SYSTEM
        + "\n\n# 法條查詢補充\n"
        "Context 中包含「查詢到的相關條文與實務見解」區塊（來自司法院網路搜尋）。\n"
        "請在相關條款的【法務實務推演】與【修改建議】中，引用或參考這些外部法條資料以增加可信度，"
        "並直接在文中寫出完整法條名稱（如民法第188條），系統將自動產生連結。\n"
        "回答中不需重複寫免責聲明，系統會自動於文末附加。"
    )
    history_text = ""
    if history:
        blocks = []
        for t in history[-4:]:
            role = t.get("role", "user")
            content = (t.get("content") or "").strip()
            if not content:
                continue
            label = "使用者" if role == "user" else "助理"
            blocks.append(f"{label}：{content}")
        history_text = "\n".join(blocks)

    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}\n\n{combined_context}\n\n請依上述指示產出風險評估："
    else:
        prompt = f"## 問題\n{question}\n\n{combined_context}\n\n請依上述指示產出風險評估："

    emit_progress("contract_generate", "正在產出條款風險評估…")
    client, model = _init_llm_client()
    contract_generate_model = get_model_for_stage("contract_risk_generate", model)
    contract_verify_model = get_model_for_stage("contract_risk_verify", contract_generate_model)
    out = client.models.generate_content(
        model=contract_generate_model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
        **_timeout_kwargs(client, "contract_risk_generate"),
    )
    answer = (out.text or "").strip()

    # 多一層 AI 自檢：以「回答＋來源摘要」請 LLM 驗證是否與來源一致
    # 預設關閉以節省延遲（多一次 LLM 調用，合約+法條路徑總耗時可降 5-10 秒）
    # 需要嚴格品檢時設 CONTRACT_RISK_SELF_CHECK_ENABLED=1
    self_check_enabled = os.getenv("CONTRACT_RISK_SELF_CHECK_ENABLED", "").strip().lower() in ("1", "true", "yes")
    if self_check_enabled:
        try:
            context_for_check = context_rag[:4500] + ("…" if len(context_rag) > 4500 else "")
            law_joined = "\n".join(law_sections) if law_sections else ""
            law_for_check = (law_joined[:3500] + ("…" if len(law_joined) > 3500 else "")) if law_joined else "（無外部法條內容）"
            verify_system = (
                "你是品質檢核員，只根據「風險評估回答」與「合約檢索摘要」「法條摘要」做一致性檢查。\n"
                "請用以下結構產出簡短自檢結果（每項一至三句話為限）：\n"
                "**1. 與合約檢索內容一致性**：回答中的條款描述、chunk 引用是否與合約摘要相符？若有未對應到來源的敘述請註明。\n"
                "**2. 與法條／實務見解一致性**：回答中引用的法條或實務見解是否與下方法條摘要一致？有無過度推論或與法條明顯矛盾之處？\n"
                "**3. 建議人工再確認之處**：列出建議法務或使用者再核對的項目（例如：具體金額、條號、責任歸屬）。\n"
                "**4. 具體建議**：給出 1～3 條可執行的後續建議（例如：建議委請律師審閱某條、建議與對方確認某事項、建議補充查詢某法規）。\n"
                "若整體一致、無明顯矛盾，也請簡要說明。禁止輸出與自檢無關的內容。"
            )
            verify_prompt = (
                "## 風險評估回答（待驗證）\n\n" + (answer[:6000] + "…" if len(answer) > 6000 else answer)
                + "\n\n## 合約檢索內容摘要（供對照）\n\n" + context_for_check
                + "\n\n## 法條／實務見解摘要（供對照）\n\n" + law_for_check
                + "\n\n請依指示產出上述自檢結果："
            )
            verify_out = client.models.generate_content(
                model=contract_verify_model,
                contents=verify_prompt,
                config=types.GenerateContentConfig(system_instruction=verify_system),
                **_timeout_kwargs(client, "contract_risk_verify"),
            )
            verify_text = (verify_out.text or "").strip()
            if verify_text:
                answer += "\n\n---\n\n**【AI 自檢】**\n\n" + verify_text + "\n\n*以上自檢僅供參考，不取代人工覆核與律師意見。*"
        except Exception as e:
            logger.warning("contract_risk_with_law_search: AI 自檢步驟失敗，略過: %s", e, exc_info=True)
            answer += "\n\n---\n\n**【AI 自檢】**\n（本次自檢未執行或執行失敗，建議人工核對上述內容與來源。）"

    # 文末一律附加免責聲明，確保可見且不被遺漏
    if _CONTRACT_DISCLAIMER not in answer:
        answer += "\n\n---\n\n**【免責聲明】**\n\n" + _CONTRACT_DISCLAIMER

    sources = list(sources_rag) + web_urls
    return answer, sources, chunks_rag


def _web_search(query: str, max_results: int = 8) -> str:
    """使用 Tavily 做網路搜尋；若未設定 TAVILY_API_KEY 則回傳提示訊息。"""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return (
            "目前未設定 TAVILY_API_KEY，無法使用網路搜尋。\n"
            "請在 .env 加入 TAVILY_API_KEY（可至 https://tavily.com 申請），即可啟用 web_search 工具。"
        )
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
            search_depth="basic",
        )
        answer = (response.get("answer") or "").strip()
        results = response.get("results") or []
        if not answer and not results:
            return "未找到與此查詢相關的網路結果。"
        lines = [answer] if answer else []
        if results:
            lines.append("\n**參考連結：**")
            for r in results:
                title = r.get("title") or "（無標題）"
                url = r.get("url") or ""
                content = (r.get("content") or "").strip()
                if content and len(content) > 200:
                    content = content[:200] + "…"
                if url:
                    lines.append(f"- [{title}]({url})")
                if content:
                    lines.append(f"  {content}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"網路搜尋時發生錯誤：{e!s}"


def _init_llm_client() -> Tuple[Any, str]:
    """初始化 chat 用 LLM client（可為 Gemini 或 Groq，由 EVAL_USE_GROQ + GROQ_API_KEY 決定）。"""
    from llm_client import get_chat_client_and_model

    return get_chat_client_and_model()


def _analyze_and_chart(
    client: genai.Client,
    model: str,
    question: str,
    top_k: int = 8,
    generate_chart: bool = True,
    chat_id: str | None = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """從知識庫檢索財報/文件內容，用 LLM 分析可視化項目；若 generate_chart=True 再產出一張圖。
    回傳 (answer_text, chart_option/asked_chart_confirmation 或 None)。"""
    context, sources, chunks, _ = retrieve_only(question=question, top_k=top_k, chat_id=chat_id)
    analysis_model = get_model_for_stage("analysis", model)
    if not context or context.strip() == "(無檢索內容)" or not chunks:
        return "知識庫中找不到與「財報」或「資料」相關的內容，請先灌入文件（例如 data 裡的財報）或改用 list_sources 查看有哪些來源。", None

    system = (
        "你是一位財務分析助理。請根據「檢索內容」與「使用者問題」做兩件事：\n"
        "(1) 用兩三句話列出「可分析的項目」（例如：各產品線營收、市佔率、成長率、毛利率、營收結構等）；\n"
        "(2) 從內容中抽出「最適合畫成一張圖」的數據，輸出「一段純 JSON」（不要 markdown 或其它文字）。\n"
        "**重要**：若使用者要求「各公司」「多間公司」或「比較多家」的指標（如總營收、毛利率、淨利潤、營收成長率），"
        "請產出「一張」比較圖：x 軸為各公司名稱，數列為使用者要求的指標（可選長條圖 bar 並用 series_data 表示單一指標，或多組數列）；"
        "勿只選單一公司。若檢索內容中只有一間公司資料，再產該公司圖。\n"
        "JSON 格式：\n"
        "- 若有類目+數值（如產品線+營收 或 **各公司+營收**）：{\"analysis_summary\": \"可分析項目：...\", \"chart_type\": \"bar\" 或 \"line\", \"chart_title\": \"圖表標題\", \"x_axis_data\": [\"類目1\", \"類目2\"], \"series_data\": [數值1, 數值2]}\n"
        "- 若為比例/結構（如市佔）：{\"analysis_summary\": \"...\", \"chart_type\": \"pie\", \"chart_title\": \"...\", \"pie_data\": [{\"name\": \"A\", \"value\": 10}, ...]}\n"
        "- 若內容不足以畫圖：{\"analysis_summary\": \"...\", \"chart_type\": \"none\"}\n"
        "只輸出一段 JSON，禁止在 JSON 前後加說明。"
    )
    prompt = f"## 使用者問題\n{question}\n\n## 檢索內容\n{context}\n\n請依指示輸出 analysis_summary 與圖表資料的 JSON："
    try:
        out = client.models.generate_content(
            model=analysis_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system),
            **_timeout_kwargs(client, "analysis"),
        )
        text = (out.text or "").strip()
        # 去掉可能的 markdown 代碼塊
        if "```" in text:
            for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text):
                text = block.strip()
                break
        data = json.loads(text)
    except Exception as e:
        logger.warning("analyze_and_chart: parse LLM output failed: %s", e, exc_info=True)
        return f"解析分析結果時發生錯誤：{e!s}", None

    summary = (data.get("analysis_summary") or "").strip() or "已檢索到相關內容。"
    chart_type = (data.get("chart_type") or "").strip().lower()
    if chart_type == "none" or not chart_type:
        return summary + "\n\n（目前內容不足以自動產出圖表，您可改說「畫一張長條圖，資料 [10, 20, 30]」指定數據。）", None

    # 只詢問、不產圖：回傳分析摘要並請使用者確認後再生成
    if not generate_chart:
        return summary + "\n\n需要幫我生成圖表嗎？", {"asked_chart_confirmation": True, "chart_query": question}

    chart_title = (data.get("chart_title") or "資料圖表").strip()
    try:
        if chart_type == "pie":
            pie_data = data.get("pie_data") or []
            if not pie_data:
                return summary + "\n\n（未抽出圓餅圖資料。）", None
            option = create_chart_option(chart_type="pie", data=pie_data, title=chart_title)
            return summary + "\n\n已根據上述資料產生圖表，請見下方。", {
                "chart_option": option,
                "chart_chunks": chunks,
                "chart_sources": sources,
            }
        else:
            x_axis_data = data.get("x_axis_data") or []
            series_data = data.get("series_data") or []
            if not series_data:
                return summary + "\n\n（未抽出圖表數列。）", None
            option = create_chart_option(
                chart_type=chart_type,
                data=series_data,
                title=chart_title,
                x_axis_data=x_axis_data if x_axis_data else None,
            )
        return summary + "\n\n已根據上述資料產生圖表，請見下方。", {
            "chart_option": option,
            "chart_chunks": chunks,
            "chart_sources": sources,
        }
    except Exception as e:
        logger.warning("analyze_and_chart: chart generation failed: %s", e, exc_info=True)
        return summary + f"\n\n圖表產生時發生錯誤：{e!s}", None


def _decide_tool(
    client: genai.Client,
    model: str,
    question: str,
    history: List[Dict[str, Any]] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """讓模型決定要呼叫哪一個 tool，以及對應的參數。

    目前支援的 tool 名稱：
    - rag_search       → 問題需要根據公司文件/知識庫回答（會查 RAG 並生成答案）
    - research         → 先查知識庫，不足時再補網路搜尋，最後整合成一份答案（公司文件優先、必要時補外部）
    - small_talk       → 一般聊天、閒聊
    - list_sources     → 使用者想「列出知識庫有哪些文件/來源」或「這個對話灌入了什麼」
    - search_similar   → 使用者給一段話，想找知識庫裡「最相關的段落」或「出處」
    - summarize_source → 使用者想對「某一份文件/某一個來源」做摘要（需在 tool_args 給 source）
    - web_search       → 問題是即時資訊、新聞、外部資料，或 RAG 無法回答時用網路搜尋
    - scrape_url       → 使用者給一個網址，要「爬這個網頁」「擷取這個 URL」的內容（tool_args 需含 url，或問題中有 URL）
    - firecrawl_search → 使用者要「搜尋並擷取」網路內容（關鍵字搜尋後擷取前幾筆頁面內容）

    回傳：(tool_name, tool_args)
    """
    history = history or []

    history_blocks: list[str] = []
    for turn in history[-6:]:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label = "使用者" if role == "user" else "助理"
        history_blocks.append(f"{label}：{content}")
    history_text = "\n".join(history_blocks)

    system = (
        "你是一個工具路由器，負責決定這一輪對話要使用哪一個工具。\n"
        "目前可用的工具有：\n"
        "1) rag_search       → 問題需要根據公司文件/知識庫回答\n"
        "2) research         → 先查知識庫、不足再補網路，整合成一份答案（適合「公司文件優先、必要時查外部」）\n"
        "3) small_talk       → 一般聊天、閒聊\n"
        "4) list_sources     → 使用者想「列出知識庫有哪些文件」或「這個對話灌入了什麼」\n"
        "5) search_similar   → 使用者給一段話，想找知識庫裡「最相關的段落」或「出處」\n"
        "6) summarize_source → 使用者想對「某一份文件/某一個來源」做摘要；tool_args 需含 {\"source\": \"來源路徑\"}\n"
        "7) web_search       → 問題是即時資訊、新聞、股價、外部事件等，需用網路搜尋\n"
        "8) scrape_url       → 使用者給網址要「爬這個網頁」「擷取此 URL」；tool_args 需含 {\"url\": \"https://...\"}\n"
        "9) firecrawl_search → 使用者要「搜尋並擷取」、「從網路搜尋並擷取」某關鍵字或新聞等網路內容；tool_args 需含 {\"query\": \"關鍵字\"}。注意：問「台灣 AI 新聞」「某主題的網路內容」且明確要「擷取」時選此項，不要選 rag_search。\n"
        "10) ask_web_vs_rag → 僅在「意圖模糊」時使用：使用者問新聞、最新、某主題，但沒說要從「知識庫」還是「網路擷取」。選此項後會由系統追問使用者要哪一種，下一輪再依回覆執行。\n"
        "11) create_chart → 使用者要畫圖、可視化、圖表；tool_args 需含 {\"chart_type\": \"bar\"或\"line\"或\"pie\"或\"scatter\", \"data\": [...]}，可選 title, series_name。\n"
        "12) analyze_and_chart → 使用者要「分析財報/資料並畫圖」「看可以分析什麼並畫圖」；從知識庫檢索後自動分析並產出一張圖，tool_args 可為 {}。\n"
        "13) financial_metrics → 使用者要「算今年 vs 去年成長」「幫我算營收成長率」「財報指標計算」；tool_args 需含 {\"revenue_this_year\": 數字, \"revenue_last_year\": 數字}，可選 gross_margin_this_year, gross_margin_last_year, net_margin_this_year, net_margin_last_year, unit（如 \"億\"）。\n"
        "14) parse_dates_from_text → 使用者要「把文字中的日期解析出來」「這段話裡有哪些日期」；tool_args 需含 {\"text\": \"要解析的文字\"}，或問題即為該段文字。\n"
        "15) generate_quarterly_plan → 使用者要「產生未來 4 季的計畫表」「排程」「季度規劃」；tool_args 可含 {\"topic\": \"主題\", \"start_quarter\": \"2025Q1\", \"num_quarters\": 4}。\n"
        "16) financial_report_agent → 問題明顯是「財報、營收、毛利、淨利、法說會、公司營運、EPS、現金流」等；交給財報專家子 Agent 回答，強調指標說明與風險提示，tool_args 可為 {}。\n"
        "17) esg_agent → 問題明顯是「ESG、環境社會治理、訴訟、供應鏈風險、法遵、裁罰、風險揭露」等；交給 ESG／風險法遵專家子 Agent 回答，tool_args 可為 {}。\n"
        "18) data_analyst_agent → 使用者要「分析這份資料、報表摘要、數據趨勢、從內容裡整理數字」等；交給資料分析專家，依檢索內容做數據摘要與重點發現，tool_args 可為 {}。\n"
        "19) contract_risk_agent → 【僅知識庫】問題明顯是「審閱合約、採購文件、標案、契約條款風險」等；交給合約／採購法遵專家，依知識庫檢索產出條款類型、風險等級與建議，不查司法院或外部法條，tool_args 可為 {}。\n"
        "20) tw_law_web_search → 問題明顯是在詢問「適用的法律條文、臺灣民法／政府採購法／行政程序法等法規內容」，或提到「司法院法學資料檢索系統」「judicial.gov.tw」等，需上網查臺灣法律條文；會優先搜尋 judicial.gov.tw，tool_args 可含 {\"query\": \"關鍵字\"}。\n"
        "21) contract_risk_with_law_search → 【含法條查詢】使用者要「審閱合約且結合法條查詢」「合約風險評估並查相關法條」；系統會從知識庫取合約、抽出法條字號、上網查司法院等、整合產出風險評估與法條重點並附 AI 自檢與免責聲明，tool_args 可為 {}。與 19 差異：19 僅依知識庫，21 會查外部法條。\n"
        "請嚴格輸出一段 JSON，格式例如：\n"
        '  {\"tool\": \"rag_search\", \"tool_args\": {}}\n'
        "或 {\"tool\": \"research\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"web_search\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"scrape_url\", \"tool_args\": {\"url\": \"https://example.com\"}}\n"
        "或 {\"tool\": \"firecrawl_search\", \"tool_args\": {\"query\": \"關鍵字\"}}\n"
        "或 {\"tool\": \"ask_web_vs_rag\", \"tool_args\": {\"query\": \"使用者問的主題（用於之後執行時）\"}}\n"
        "或 {\"tool\": \"create_chart\", \"tool_args\": {\"chart_type\": \"bar\", \"data\": [10,20,30], \"title\": \"營收\"}}\n"
        "或 {\"tool\": \"analyze_and_chart\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"financial_metrics\", \"tool_args\": {\"revenue_this_year\": 100, \"revenue_last_year\": 80}}\n"
        "或 {\"tool\": \"parse_dates_from_text\", \"tool_args\": {\"text\": \"會議訂在 2025年3月15日 與 Q2 檢討\"}}\n"
        "或 {\"tool\": \"generate_quarterly_plan\", \"tool_args\": {\"topic\": \"產品上市\", \"start_quarter\": \"2025Q1\", \"num_quarters\": 4}}\n"
        "或 {\"tool\": \"financial_report_agent\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"esg_agent\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"data_analyst_agent\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"contract_risk_agent\", \"tool_args\": {}}\n"
        "或 {\"tool\": \"tw_law_web_search\", \"tool_args\": {\"query\": \"政府採購法 第 99 條 專案管理\"}}\n"
        "或 {\"tool\": \"contract_risk_with_law_search\", \"tool_args\": {}}\n"
        "禁止輸出任何解釋文字或多餘內容。"
    )

    if history_text:
        prompt = f"過去對話：\n{history_text}\n\n目前使用者問題：{question}\n\n請選擇最合適的工具並輸出 JSON："
    else:
        prompt = f"使用者問題：{question}\n\n請選擇最合適的工具並輸出 JSON："

    out = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
        **_timeout_kwargs(client, "router"),
    )
    text = (out.text or "").strip()

    try:
        data = json.loads(text)
        tool = str(data.get("tool") or "").strip()
        if tool not in SUPPORTED_TOOLS:
            raise ValueError("unsupported tool")
        args = data.get("tool_args") or {}
        if not isinstance(args, dict):
            args = {}
        return tool, args
    except Exception as e:
        logger.warning("tool decision parse failed, fallback to rag_search: %s", e, exc_info=True)
        return "rag_search", {}


# 使用者回覆「要」「好」「生成」等即視為同意產圖
_CHART_CONFIRM_PHRASES = (
    "要", "好", "生成", "幫我生成", "可以", "好呀", "好啊", "好喔", "畫", "產圖",
    "生成圖表", "幫我畫", "幫我產圖", "好請", "請生成", "要的", "要啊",
)


def route_and_answer(
    *,
    question: str,
    top_k: int,
    history: List[Dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
    rag_scope_chat_id: str | None = None,
    original_question: str | None = None,
    clarification_reply: str | None = None,
    chart_confirmation_question: str | None = None,
    chart_confirmation_reply: str | None = None,
) -> Tuple[str, List[str], List[Dict[str, Any]], str, Optional[Dict[str, Any]]]:
    """由總管 Agent 判斷要用哪個 tool，然後回傳答案。

    目前支援：rag_search、research、small_talk、list_sources、search_similar、
    summarize_source、web_search、scrape_url、firecrawl_search、ask_web_vs_rag、create_chart、analyze_and_chart。
    chat_id：可選，用於 list_sources 時只列該對話灌入的來源。
    rag_scope_chat_id：可選，若設定則 RAG/檢索僅限該對話上傳的 chunks，避免參雜其他來源。
    original_question / clarification_reply：當上一輪回傳 ask_web_vs_rag 後，呼叫端傳入
    使用者當時的問題與本輪回覆（例如「網路」或「知識庫」），會依此直接執行對應 tool，不再追問。
    chart_confirmation_question / chart_confirmation_reply：上一輪已回「需要幫我生成圖表嗎？」，
    本輪使用者回覆（例如「要」「好」）時傳入，會依此執行產圖。
    回傳：(answer, sources, chunks, tool_name, extra)。extra 在 create_chart 時為 {"chart_option": ...}，其餘見各 tool。
    """
    history = history or []

    # 圖表確認：上一輪已問「需要幫我生成圖表嗎？」，本輪使用者說要 → 直接產圖
    if chart_confirmation_question and chart_confirmation_reply:
        reply = chart_confirmation_reply.strip()
        if reply and any(p in reply for p in _CHART_CONFIRM_PHRASES):
            client, llm_model = _init_llm_client()
            # 以使用者本輪的具體要求為準（例如「好畫出各公司的營收、毛利率」），讓檢索與圖表依此產出
            query = reply if reply else (chart_confirmation_question.strip() or "財報 財務 產品線 營收 市佔率")
            answer, extra = _analyze_and_chart(client, llm_model, query, top_k=max(top_k, 8), generate_chart=True, chat_id=rag_scope_chat_id)
            if extra and extra.get("chart_option") and use_echarts_mcp():
                ok, b64, _err = call_echarts_mcp(extra["chart_option"], width=800, height=500, output_type="png")
                if ok and b64:
                    # 保留圖表依據的檢索片段，供前端展開查看
                    out_extra = {"chart_image_base64": b64}
                    if extra.get("chart_chunks") is not None:
                        out_extra["chart_chunks"] = extra["chart_chunks"]
                    if extra.get("chart_sources") is not None:
                        out_extra["chart_sources"] = extra["chart_sources"]
                    return answer, [], [], "analyze_and_chart", out_extra
            return answer, [], [], "analyze_and_chart", extra

    # 澄清回覆：上一輪問了「知識庫還是網路」，本輪依使用者回覆直接執行
    if original_question and clarification_reply:
        reply = clarification_reply.strip().lower()
        if any(k in reply for k in ("網路", "擷取", "搜尋", "web", "firecrawl", "爬")):
            query = original_question.strip()
            limit = 5
            raw = search_and_scrape(query, limit=limit)
            if isinstance(raw, str) and ("未設定" in raw or "失敗" in raw):
                return raw, [], [], "firecrawl_search", None
            if isinstance(raw, dict):
                data = raw.get("data") if isinstance(raw.get("data"), list) else raw.get("results")
                if data:
                    parts = []
                    for i, item in enumerate(data, 1):
                        if isinstance(item, dict):
                            title = item.get("title") or (item.get("metadata") or {}).get("title") or "（無標題）"
                            url_h = item.get("url") or (item.get("metadata") or {}).get("source") or ""
                            md = item.get("markdown") or item.get("content") or ""
                            parts.append(f"## [{i}] {title}\n{url_h}\n\n{md[:4000]}{'…' if len(str(md)) > 4000 else ''}")
                        else:
                            parts.append(str(item)[:2000])
                    return "\n\n---\n\n".join(parts), [], [], "firecrawl_search", None
            return _format_firecrawl_scrape_result(raw), [], [], "firecrawl_search", None
        if any(k in reply for k in ("知識庫", "文件", "內部", "rag")):
            rag_state = run_rag(question=original_question, top_k=top_k, history=history, strict=False, chat_id=rag_scope_chat_id)
            return (
                rag_state.get("answer", "") or "",
                rag_state.get("sources", []) or [],
                rag_state.get("chunks", []) or [],
                "rag_search",
                None,
            )
        # 回覆不明確，用原始問題再走一次正常路由
        question = original_question

    # 嚴格模式：直接走 rag_search，不做路由判斷
    if strict:
        rag_state = run_rag(question=question, top_k=top_k, history=history, strict=True, chat_id=rag_scope_chat_id)
        answer = rag_state.get("answer", "") or ""
        sources = rag_state.get("sources", []) or []
        chunks = rag_state.get("chunks", []) or []
        return answer, sources, chunks, "rag_search", None

    # Firecrawl 意圖層：先判斷是否要用 Firecrawl、用哪一個（scrape_url / firecrawl_search）
    tool, tool_args = None, {}
    intent = firecrawl_intent(question)
    if intent is None:
        intent = firecrawl_intent_with_llm(question)
    if intent is not None:
        tool, tool_args = intent
    # 台灣法律／司法院檢索意圖：明確要「搜尋司法院法學資料檢索系統」等時，強制走 tw_law_web_search，確保會用網路搜尋
    if tool is None:
        intent = tw_law_intent(question)
        if intent is not None:
            tool, tool_args = intent
    # 合約審閱＋法條查詢：問題同時提到合約／審閱與法條／法律時，走「合約 RAG → 抽法條 → 查司法院 → 整合評估」
    if tool is None:
        intent = contract_risk_with_law_intent(question)
        if intent is not None:
            tool, tool_args = intent
    # 合約／採購審閱類但未命中法條查詢時，保證走 contract_risk_agent（僅知識庫），減少路由歧義
    if tool is None:
        intent = contract_risk_agent_intent(question)
        if intent is not None:
            tool, tool_args = intent
    if tool is None:
        client, llm_model = _init_llm_client()
        router_model = get_model_for_stage("router", llm_model)
        tool, tool_args = _decide_tool(client, router_model, question, history)

    if tool == "ask_web_vs_rag":
        # 意圖模糊：追問使用者要從知識庫還是網路擷取；由呼叫端（Streamlit）記住 original_question，下一輪傳回
        query_for_later = (tool_args.get("query") or question).strip()
        answer = (
            "您是想從 **知識庫** 查詢，還是從 **網路** 搜尋並擷取最新內容？\n\n"
            "請回覆「**知識庫**」或「**網路**」，我就會依您的選擇執行。"
        )
        # 回傳時用 tool_name 標記，呼叫端可依此儲存 pending_question = question（或 query_for_later）待下一輪使用
        return answer, [], [], "ask_web_vs_rag", None

    if tool == "create_chart":
        chart_type = (tool_args.get("chart_type") or "bar").strip().lower()
        data = tool_args.get("data")
        title = (tool_args.get("title") or "").strip() or None
        series_name = (tool_args.get("series_name") or "").strip() or None
        x_axis_data = tool_args.get("x_axis_data")
        if not data and question.strip():
            try:
                import json as _json
                data = _json.loads(question)
            except Exception as e:
                logger.debug("create_chart: parse question as JSON failed: %s", e)
        if not data:
            return "未提供圖表資料（請在 tool_args 給 data，或問題中給 JSON 陣列）。", [], [], "create_chart", None
        try:
            option = create_chart_option(
                chart_type=chart_type,
                data=data,
                title=title,
                series_name=series_name,
                x_axis_data=x_axis_data,
            )
            if use_echarts_mcp():
                ok, b64, err = call_echarts_mcp(option, width=800, height=500, output_type="png")
                if ok and b64:
                    return "已產生圖表（ECharts MCP），請見下方。", [], [], "create_chart", {"chart_image_base64": b64}
                if err:
                    pass  # 靜默 fallback 到內建渲染
            return "已產生圖表，請見下方。", [], [], "create_chart", {"chart_option": option}
        except Exception as e:
            logger.exception("create_chart failed")
            return f"圖表產生失敗：{e!s}", [], [], "create_chart", None

    if tool == "analyze_and_chart":
        query = (tool_args.get("query") or question).strip() or "財報 財務 產品線 營收 市佔率"
        client, llm_model = _init_llm_client()
        # 先只分析並詢問「需要幫我生成圖表嗎？」，不直接產圖；使用者確認後由 chart_confirmation 流程產圖
        answer, extra = _analyze_and_chart(client, llm_model, query, top_k=max(top_k, 8), generate_chart=False, chat_id=rag_scope_chat_id)
        return answer, [], [], "analyze_and_chart", extra

    if tool == "financial_metrics":
        rev_this = tool_args.get("revenue_this_year")
        rev_last = tool_args.get("revenue_last_year")
        gm_this = tool_args.get("gross_margin_this_year")
        gm_last = tool_args.get("gross_margin_last_year")
        nm_this = tool_args.get("net_margin_this_year")
        nm_last = tool_args.get("net_margin_last_year")
        unit = (tool_args.get("unit") or "億").strip()
        answer = financial_metrics(
            revenue_this_year=rev_this,
            revenue_last_year=rev_last,
            gross_margin_this_year=gm_this,
            gross_margin_last_year=gm_last,
            net_margin_this_year=nm_this,
            net_margin_last_year=nm_last,
            unit=unit,
        )
        return answer, [], [], "financial_metrics", None

    if tool == "parse_dates_from_text":
        text = (tool_args.get("text") or question).strip()
        answer = parse_dates_from_text(text)
        return answer, [], [], "parse_dates_from_text", None

    if tool == "generate_quarterly_plan":
        topic = (tool_args.get("topic") or "計畫").strip()
        start_q = (tool_args.get("start_quarter") or "2025Q1").strip()
        num_q = tool_args.get("num_quarters")
        try:
            num_q = int(num_q) if num_q is not None else 4
        except (TypeError, ValueError):
            num_q = 4
        num_q = max(1, min(num_q, 8))
        answer = generate_quarterly_plan(topic=topic, start_quarter=start_q, num_quarters=num_q)
        return answer, [], [], "generate_quarterly_plan", None

    if tool == "financial_report_agent":
        top_k_expert = max(top_k, int(tool_args.get("top_k") or top_k))
        answer, sources, chunks = financial_report_agent(
            question=question, top_k=top_k_expert, history=history, chat_id=rag_scope_chat_id
        )
        return answer, sources, chunks, "financial_report_agent", None

    if tool == "esg_agent":
        top_k_expert = max(top_k, int(tool_args.get("top_k") or top_k))
        answer, sources, chunks = esg_agent(
            question=question, top_k=top_k_expert, history=history, chat_id=rag_scope_chat_id
        )
        return answer, sources, chunks, "esg_agent", None

    if tool == "data_analyst_agent":
        top_k_expert = max(top_k, int(tool_args.get("top_k") or top_k))
        answer, sources, chunks = data_analyst_agent(
            question=question, top_k=top_k_expert, history=history, chat_id=rag_scope_chat_id
        )
        return answer, sources, chunks, "data_analyst_agent", None

    if tool == "contract_risk_agent":
        top_k_expert = max(top_k, int(tool_args.get("top_k") or top_k))
        answer, sources, chunks = contract_risk_agent(
            question=question, top_k=top_k_expert, history=history, strict=strict, chat_id=rag_scope_chat_id
        )
        # 從 LLM 回答 + 合約切片原文同時萃取法條引用，構建全國法規資料庫連結加入 sources
        _chunks_text = "\n".join(
            c.get("text", "") for c in chunks if isinstance(c, dict)
        )
        _law_refs = _extract_law_refs_from_text(answer + "\n" + _chunks_text)
        if _law_refs:
            _law_urls = [
                f"https://law.moj.gov.tw/LawClass/LawSearchAll.aspx?kw={urllib.parse.quote(_ref, safe='')}"
                for _ref in _law_refs
            ]
            sources = list(sources) + _law_urls
            logger.info("contract_risk_agent: appended %d law ref URLs to sources", len(_law_urls))
        risk_cards = parse_risk_cards(answer)
        extra = {"risk_cards": risk_cards} if risk_cards else None
        return answer, sources, chunks, "contract_risk_agent", extra

    if tool == "contract_risk_with_law_search":
        top_k_expert = max(top_k, int(tool_args.get("top_k") or top_k))
        answer, sources, chunks = _contract_risk_with_law_search_impl(
            question=question, top_k=top_k_expert, history=history, chat_id=rag_scope_chat_id
        )
        risk_cards = parse_risk_cards(answer)
        extra = {"risk_cards": risk_cards} if risk_cards else None
        return answer, sources, chunks, "contract_risk_with_law_search", extra

    if tool == "rag_search":
        rag_state = run_rag(question=question, top_k=top_k, history=history, strict=False, chat_id=rag_scope_chat_id)
        answer = rag_state.get("answer", "") or ""
        sources = rag_state.get("sources", []) or []
        chunks = rag_state.get("chunks", []) or []
        return answer, sources, chunks, "rag_search", None

    if tool == "research":
        # Research Agent：先 RAG，信心低或無結果再補網搜，最後 LLM 整合並標註來源
        client, llm_model = _init_llm_client()
        context_rag, sources_rag, chunks_rag, top_score = retrieve_only(question=question, top_k=top_k, chat_id=rag_scope_chat_id)
        threshold = float(os.getenv("RESEARCH_WEB_SCORE_THRESHOLD", "0.35"))
        need_web = (not chunks_rag) or (
            top_score is not None and top_score < threshold
        )
        context_web = _web_search(question, max_results=8) if need_web else ""

        system = (
            "你是一個研究助理，負責整合「知識庫檢索」與「網路搜尋」的結果回答問題。\n"
            "規則：\n"
            "1) 以知識庫內容為主；若有網路搜尋結果，可補足或對照。\n"
            "2) 在回答中明確標註哪些來自「知識庫」、哪些來自「網路」；若只有一方有內容，也請註明。\n"
            "3) 若有引用，請在句末或段落註明來源類型（知識庫 / 網路）。\n"
            "4) 若兩邊都沒有足夠內容，請誠實說明無法回答。"
        )
        parts = ["## 問題\n" + question, "\n## 知識庫檢索結果\n" + (context_rag or "(無)")]
        if context_web:
            parts.append("\n## 網路搜尋結果\n" + context_web)
        prompt = "\n".join(parts) + "\n\n請整合以上內容回答問題，並標註來源（知識庫 vs 網路）："
        out = client.models.generate_content(
            model=get_model_for_stage("research_generate", llm_model),
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system),
            **_timeout_kwargs(client, "research_generate"),
        )
        answer = (out.text or "").strip()
        return answer, sources_rag, chunks_rag, "research", None

    if tool == "list_sources":
        filter_chat_id = tool_args.get("chat_id") or chat_id
        entries = list_sources(chat_id=filter_chat_id)
        if not entries:
            answer = "目前沒有已灌入的來源。"
        else:
            lines = [f"- **{e.get('source', '?')}**（{e.get('chunk_count', 0)} 個片段）" for e in entries]
            answer = "知識庫來源如下：\n\n" + "\n".join(lines)
        return answer, [], [], "list_sources", None

    if tool == "search_similar":
        query = (tool_args.get("query") or question).strip()
        sim_top_k = int(tool_args.get("top_k") or top_k)
        sim_top_k = min(max(sim_top_k, 1), 20)
        sources, chunks = search_similar(query_text=query, top_k=sim_top_k)
        if not chunks:
            answer = "未找到與這段話相關的段落。"
        else:
            blocks = [f"**[{c['tag']}]**\n{c['text']}" for c in chunks]
            answer = "找到以下相關段落：\n\n" + "\n\n---\n\n".join(blocks)
        return answer, sources, chunks, "search_similar", None

    if tool == "summarize_source":
        source = (tool_args.get("source") or question).strip()
        max_chunks = int(tool_args.get("max_chunks") or 50)
        max_chunks = min(max(max_chunks, 1), 100)
        answer = summarize_source(source=source, max_chunks=max_chunks)
        return answer, [], [], "summarize_source", None

    if tool == "web_search":
        query = (tool_args.get("query") or question).strip()
        max_results = min(max(int(tool_args.get("max_results") or 8), 1), 20)
        answer = _web_search(query=query, max_results=max_results)
        return answer, [], [], "web_search", None

    if tool == "tw_law_web_search":
        # 針對臺灣法律／判決等問題，優先搜尋 judicial.gov.tw 網域（司法院法學資料檢索系統等）
        query = (tool_args.get("query") or question).strip()
        if query:
            query = f"{query} site:judicial.gov.tw"
        max_results = min(max(int(tool_args.get("max_results") or 8), 1), 20)
        answer = _web_search(query=query, max_results=max_results)
        return answer, [], [], "tw_law_web_search", None

    if tool == "scrape_url":
        url = (tool_args.get("url") or "").strip() or _extract_url_from_text(question)
        if not url:
            answer = "未提供網址。請在問題中貼上 URL，或使用 tool_args 傳入 {\"url\": \"https://...\"}。"
            return answer, [], [], "scrape_url", None
        only_main = bool(tool_args.get("only_main_content", True))
        raw = scrape_url(url, only_main_content=only_main)
        answer = _format_firecrawl_scrape_result(raw)
        return answer, [], [], "scrape_url", None

    if tool == "firecrawl_search":
        query = (tool_args.get("query") or question).strip()
        if not query:
            answer = "請提供搜尋關鍵字（或在問題中寫明要搜尋的內容）。"
            return answer, [], [], "firecrawl_search", None
        limit = min(max(int(tool_args.get("limit") or 5), 1), 10)
        raw = search_and_scrape(query, limit=limit)
        if isinstance(raw, str) and ("未設定" in raw or "失敗" in raw):
            answer = raw
        elif isinstance(raw, dict):
            data = raw.get("data") if isinstance(raw.get("data"), list) else raw.get("results")
            if data:
                parts = []
                for i, item in enumerate(data, 1):
                    if isinstance(item, dict):
                        meta = item.get("metadata") or {}
                        title = item.get("title") or meta.get("title") or "（無標題）"
                        url_h = item.get("url") or meta.get("source") or ""
                        md = item.get("markdown") or item.get("content") or ""
                        parts.append(f"## [{i}] {title}\n{url_h}\n\n{md[:4000]}{'…' if len(str(md)) > 4000 else ''}")
                    else:
                        parts.append(str(item)[:2000])
                answer = "\n\n---\n\n".join(parts)
            else:
                answer = _format_firecrawl_scrape_result(raw)
        else:
            answer = _format_firecrawl_scrape_result(raw)
        return answer, [], [], "firecrawl_search", None

    # small_talk 路徑：純 LLM 對話，不查知識庫
    client, llm_model = _init_llm_client()
    history_blocks: list[str] = []
    for turn in history[-10:]:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label = "使用者" if role == "user" else "助理"
        history_blocks.append(f"{label}：{content}")
    history_text = "\n".join(history_blocks)

    system = (
        "你是一個友善的中文助理，負責一般對話、說明與簡單推理。\n"
        "這個模式下你不需要引用公司內部文件，只要根據對話歷史與一般常識回答即可。\n"
        "若問題明顯需要公司內部資料才能準確回答，請先說明你只能根據一般知識推估。"
    )

    if history_text:
        prompt = f"## 對話歷史\n{history_text}\n\n## 目前問題\n{question}"
    else:
        prompt = f"## 問題\n{question}"

    out = client.models.generate_content(
        model=get_model_for_stage("small_talk", llm_model),
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
        **_timeout_kwargs(client, "small_talk"),
    )
    answer = (out.text or "").strip()
    return answer, [], [], "small_talk", None

