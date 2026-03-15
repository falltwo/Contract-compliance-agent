# 合約／採購法遵審閱助理（Contract & Compliance Review Agent）

> **競賽導向**：本專案以「2026 智慧創新大賞」AI 應用類為目標，定位為 **合約與採購法遵審閱助理** —— 結合 RAG、多工具 Agent 路由與合約／法律檢索，協助企業快速審閱合約條款、標註風險並可追溯條文出處。
>
> 以 **RAG + LangGraph** 為核心，支援知識庫問答、**合約風險分析**（含司法院法條查詢）、網路搜尋、網頁擷取、圖表與 Eval 框架；前端為 Streamlit 多對話介面。

---

## 快速開始

- **環境變數與 API Key（必填）**
  - 複製 `.env.example` 為 `.env`，填入真實 key（勿 commit 上傳）：
    - **必填**：`PINECONE_API_KEY`、`PINECONE_INDEX`、`GOOGLE_API_KEY`
    - **合約＋法條查詢**：`TAVILY_API_KEY`（用於司法院／網路搜尋）
    - 選用：`FIRECRAWL_API_KEY`、`GROQ_API_KEY`（Eval 用）

- **最短步驟**
  1. `cp .env.example .env`，填入 API key。
  2. 將合約或文件放入 `data/`，建好 Pinecone index。
  3. 執行：
     ```bash
     uv run rag_ingest.py
     uv run streamlit run streamlit_app.py
     ```
  4. 在瀏覽器開啟 Streamlit，上傳合約後可問：「請審閱這份合約的風險條款」「合約風險評估並查相關法條」等。

- **合約審閱建議環境變數**（見 [五、進階與備註](#五進階與備註)）
  - `RAG_USE_HISTORY_FOR_QUERY=1`、`RAG_MULTI_QUERY=1`、`RAG_MAX_HISTORY_TURNS=12` 可提升多輪審閱與交叉比對效果。

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B?logo=streamlit)](https://streamlit.io)

---

## 目錄

- [專案概述](#一專案概述)
- [技術架構](#二技術架構)
  - [技術棧](#21-技術棧)
  - [主要模組](#22-主要模組)
  - [支援的工具](#23-支援的工具supported_tools)
  - [整體問答流程（Workflow）](#24-整體問答流程workflow)
  - [RAG 類型與切片方式](#25-rag-類型與切片方式)
- [功能總覽](#三功能總覽)
- [使用方式](#四使用方式)
- [進階與備註](#五進階與備註)
- [開發與測試](#六開發與測試)
- [建議可增加的擴充](#七建議可增加的擴充)
- [更新記錄（Recent Updates）](#更新記錄recent-updates)

---

## 一、專案概述

本專案為 **「合約／採購法遵審閱助理」**，在知識庫問答與多工具 Agent 基礎上，強化合約與法律檢索：

- **合約審閱**：上傳合約或採購文件至知識庫，以自然語言提問；系統可自動路由至 **contract_risk_agent**（條款風險、建議調整）或 **contract_risk_with_law_search**（合約 + 司法院法條查詢 + 風險評估 + AI 自檢）。
- **多工具路由**：依問題意圖選擇知識庫 RAG、網路搜尋、網頁擷取、圖表、財報／ESG／資料分析專家等；**tw_law_web_search** 針對臺灣法規（優先 judicial.gov.tw）。
- **Streamlit** 提供多對話、嚴格／非嚴格模式、上傳灌入、檢索片段與 **Eval 運行記錄／批次結果** 檢視。

---

## 二、技術架構

### 2.1 技術棧

| 類別 | 選型 | 說明 |
|------|------|------|
| **LLM / Embedding** | Google **Gemini** | 對話預設 `gemini-3.1-flash-lite-preview`（Gemini 3.1 Flash Lite），向量化 `gemini-embedding-001`；Eval 可選 **Groq**（`llama-3.3-70b-versatile`）避開免費額度限制。 |
| **向量庫** | **Pinecone** | 存文件切片向量與 metadata，支援高維檢索。 |
| **RAG 流程** | **LangGraph** | StateGraph：檢索 → 過濾／去重／MMR 或 LLM rerank → 生成。 |
| **前端** | **Streamlit** | 聊天介面、多對話、上傳灌入、圖表、**Eval 運行記錄**與**Eval 批次結果**檢視。 |
| **網路搜尋** | **Tavily** | 即時資訊、新聞（需 `TAVILY_API_KEY`）。 |
| **網頁擷取** | **Firecrawl** | 單頁擷取、關鍵字搜尋擷取、整站爬取（需 `FIRECRAWL_API_KEY`）。 |
| **圖表** | **ECharts** | `echarts_tools` + `streamlit-echarts`；可選 ECharts MCP 產 PNG。 |
| **環境** | **uv** + **python-dotenv** | Python 3.13+，依賴與執行由 `pyproject.toml` 管理。 |

### 2.2 主要模組

| 模組 | 職責 |
|------|------|
| **rag_common.py** | 共用：chunk、format_context、Pinecone/Gemini 初始化、embed；回傳 chat client（可 Groq）+ embed client（Gemini）+ index。 |
| **llm_client.py** | 共用 LLM 客戶端：`get_chat_client_and_model()`，依 `EVAL_USE_GROQ` + `GROQ_API_KEY` 回傳 Gemini 或 Groq 適配。 |
| **rag_graph.py** | RAG 核心：檢索、可選 dedup／MMR／LLM rerank、依檢索內容生成答案；`retrieve_only` 供 research／專家共用。 |
| **agent_router.py** | 總管 Agent：依問題決定工具（rag_search、research、web_search、Firecrawl、圖表、公司工具、專家等），執行並回傳答案。 |
| **rag_ingest.py** | 離線灌入：掃描 `data/` 的 txt／md／pdf，切 chunk、embed、寫入 Pinecone，更新來源註冊表。 |
| **sources_registry.py** | 來源註冊表（JSON），記錄每個 source 與 chunk 數，供 `list_sources` 使用。 |
| **streamlit_app.py** | Streamlit 介面：多對話、嚴格／非嚴格模式、上傳灌入、檢索片段與圖表、**Eval 運行記錄**、**Eval 批次結果**。 |
| **company_tools.py** | 公司工具：財報指標計算、日期解析、季度計畫表。 |
| **expert_agents.py** | 專家子 Agent：財報／營運（financial_report_agent）、ESG／法遵（esg_agent）、資料分析（data_analyst_agent）、合約法遵審閱（contract_risk_agent）。 |
| **echarts_tools.py** / **echarts_mcp_client.py** | 圖表 option 產生與可選 MCP PNG 輸出。 |
| **firecrawl_tools.py** | Firecrawl API 封裝（scrape、search、crawl、map）。 |
| **eval_log.py** | 問答運行記錄（可選寫入每筆問題／答案／tool／延遲）。 |
| **eval/run_eval.py** | Eval 批次腳本：讀題集、呼叫 `route_and_answer`、寫入結果與核心指標；可加 `--groq`。 |

### 2.3 支援的工具（SUPPORTED_TOOLS）

| 分類 | 工具 |
|------|------|
| **知識庫** | `rag_search`、`research`、`list_sources`、`search_similar`、`summarize_source` |
| **網路／網頁** | `web_search`、`scrape_url`、`firecrawl_search` |
| **合約／法律** | `contract_risk_agent`、`contract_risk_with_law_search`、`tw_law_web_search` |
| **圖表** | `create_chart`、`analyze_and_chart` |
| **公司工具** | `financial_metrics`、`parse_dates_from_text`、`generate_quarterly_plan` |
| **專家** | `financial_report_agent`、`esg_agent`、`data_analyst_agent`、`contract_risk_agent` |
| **合約＋法條** | `contract_risk_with_law_search`（RAG → 抽法條 → Tavily 司法院／網路 → Firecrawl 對比 → 自檢 → 免責聲明） |
| **對話** | `small_talk`、`ask_web_vs_rag` |

- **contract_risk_agent**：合約／採購條款風險分析，依知識庫檢索產出條款類型、風險等級與建議。
- **contract_risk_with_law_search**：合約 RAG → 抽出法條字號 → 查司法院／網路 → 整合風險評估與法條重點，並附 AI 自檢。
- **tw_law_web_search**：臺灣法規查詢，優先搜尋 `site:judicial.gov.tw`。

### 2.4 整體問答流程（Workflow）

使用者送出一則問題後，系統依下列流程處理（`agent_router.route_and_answer`）：

```
使用者問題
    │
    ├─ 圖表確認？（上一輪問過「需要幫我生成圖表嗎？」且本輪回覆「要」）
    │       → 直接執行 analyze_and_chart 產圖
    │
    ├─ 澄清回覆？（上一輪問過「知識庫還是網路？」且本輪回覆「網路」或「知識庫」）
    │       → 依回覆執行 firecrawl_search 或 rag_search
    │
    ├─ 嚴格模式？（側欄勾選「嚴格只根據知識庫回答」）
    │       → 直接 run_rag（rag_search），不做工具選擇
    │
    └─ 一般模式：
            │
            ├─ Firecrawl 意圖層：規則或可選 LLM 判斷「擷取單頁 / 搜尋並擷取」
            │       → 命中則執行 scrape_url 或 firecrawl_search
            │
            ├─ 台灣法律／司法院意圖（tw_law_intent）→ tw_law_web_search
            │
            ├─ 合約審閱＋法條查詢（contract_risk_with_law_intent）：問題含「審閱合約」「合約風險」或「合約／契約／租賃＋分析／檢查／評估」等
            │       → contract_risk_with_law_search（RAG → 抽法條 → Tavily 查司法院／網路 → Firecrawl 法條對比 → LLM 風險評估 → AI 自檢 → 免責聲明）
            │
            └─ 否則 → LLM 工具路由（_decide_tool）
                    → 依選出之 tool 執行（rag_search / research / web_search / 圖表 / 公司工具 / 專家等）
                    → 若為 ask_web_vs_rag，回傳追問「知識庫還是網路？」，下一輪再依回覆執行
```

**RAG 子流程**（`rag_graph.run_rag`，LangGraph 兩節點）：

```
StateGraph(RAGState)
    entry → retrieve → generate → END
```

- **retrieve**：embed 問題 → Pinecone 向量檢索（`internal_top_k`，預設 20）→ 依 `RAG_MIN_SCORE` 過濾 → 可選 dedup（`RAG_DEDUP_ENABLED`）→ **MMR** 或 **LLM rerank** 取前 `rerank_top_n` → 組 context / sources / chunks。
- **generate**：依 context + 對話歷史 + strict／非 strict 的 system prompt，呼叫 LLM 生成答案。

**合約審閱流程（簡述）**：使用者上傳合約後，問「請審閱這份合約」→ Router 可選 **contract_risk_agent**（僅知識庫）或 **contract_risk_with_law_search**（知識庫 + 抽法條 + 查司法院 + 整合評估 + AI 自檢）。嚴格模式勾選時，一律走 RAG 僅依知識庫回答。

### 2.5 RAG 類型與切片方式

| 項目 | 說明 |
|------|------|
| **RAG 類型** | **檢索增強生成**：以**純向量檢索**為主（Pinecone cosine similarity）。先多取 `RAG_INTERNAL_TOP_K` 筆，再經**過濾**（`RAG_MIN_SCORE`）、可選**去重**（dedup）、**重排序**（MMR 或 LLM rerank）後取前 N 筆組 context，由 LLM 依 context 與對話歷史生成答案。目前未使用 hybrid（向量 + 關鍵字/BM25），程式註解中預留可擴充。 |
| **切片策略** | 實作於 `rag_common.chunk_text()`，**段落優先 + 長度滑窗**：<br>1. **依空白行**切出段落（`\n\s*\n+`）。<br>2. **依標題合併**：若段落首行符合標題 pattern（`#` 或 `一、二、…`），則前一段落群先結算為一 block，再開始新 block，避免標題與內文被拆開。<br>3. **區塊內滑窗**：每個 block 若超過 `chunk_size`，以 **chunk_size=900、overlap=150** 字元做滑窗切片（相鄰 chunk 保留 150 字元重疊）。<br>4. 灌入時每個 chunk 寫入 Pinecone，metadata 含 `source`、`chunk_index`、`text`。 |
| **環境變數（切片／檢索）** | `chunk_size` / `overlap` 在程式內預設 900／150；檢索相關見 **五、進階與備註**（`RAG_INTERNAL_TOP_K`、`RAG_RERANK_TOP_N`、`RAG_MMR_LAMBDA`、`RAG_DEDUP_ENABLED`、`RAG_MIN_SCORE`）。 |

---

## 三、功能總覽

- **知識庫問答**：rag_search（嚴格僅依檢索）、research（先知識庫再補網路）、list_sources、search_similar、summarize_source。
- **網路與網頁**：Tavily 一般搜尋、Firecrawl 單頁擷取與關鍵字搜尋擷取；意圖可由規則或可選 LLM 判斷。
- **圖表**：依使用者描述或資料畫 ECharts；analyze_and_chart 從知識庫檢索後分析並可確認後產圖，可選 ECharts MCP 產 PNG。
- **公司工具**：財報指標計算、從文字解析日期、產生季度計畫表。
- **專家**：財報／營運、ESG／法遵、資料分析（報表摘要／數據趨勢）、合約法遵審閱（條款、風險、民法／消保法）專用回答。
- **合約審閱＋法條查詢**：問題為審閱合約、合約風險或分析／檢查契約時，走 **contract_risk_with_law_search**：RAG 取合約 → 抽法條字號 → **Tavily** 查司法院／網路條文與實務 → **Firecrawl** 擷取法條對比（選用）→ LLM 產出風險摘要、法條重點、建議、法條字號清單與來源列表 → **AI 自檢**（一致性與具體建議）→ 文末強制附加**免責聲明**。需 `TAVILY_API_KEY`（與選用 `FIRECRAWL_API_KEY`）。
- **前端**：多對話、嚴格／非嚴格模式、上傳並灌入文件、檢索片段與圖表展示、**清空資料庫**按鈕；側欄可切換「對話」「Eval 運行記錄」「Eval 批次結果」。

---

## 四、使用方式

### 快速開始

```bash
# 1. 在專案根目錄建立 .env，填入必填項（見下方 4.1）
# 2. 建立 Pinecone index 並灌入文件（可選：先放檔案到 data/ 再執行）
uv run rag_ingest.py
# 3. 啟動 Streamlit
uv run streamlit run streamlit_app.py
```

### 4.1 環境準備

在專案根目錄建立 `.env`。

**必填：**

| 變數 | 說明 |
|------|------|
| `PINECONE_API_KEY` | Pinecone API 金鑰 |
| `PINECONE_INDEX` | Index 名稱（預設 `agent-index`） |
| `GOOGLE_API_KEY` | Google / Gemini API 金鑰 |

**選用：**

| 變數 | 說明 |
|------|------|
| `TAVILY_API_KEY` | 網路搜尋（Tavily）；**合約審閱＋法條查詢**時用於查司法院／網路條文與實務。 |
| `FIRECRAWL_API_KEY` | 網頁擷取／搜尋；**合約審閱＋法條查詢**時用於法條對比擷取（選用）。 |
| `USE_ECHARTS_MCP=1` | 圖表 PNG 輸出（需本機 Node.js 18+） |
| `EVAL_LOG_ENABLED=1` | 寫入 Eval 運行記錄，路徑 `EVAL_LOG_PATH`（預設 `eval_runs.jsonl`） |
| `GROQ_API_KEY` | Eval 時以 `--groq` 使用 Groq；**勿在 .env 寫 `EVAL_USE_GROQ=1`**（否則 Streamlit 也會用 Groq） |

### 4.2 建立 Index 與灌入

- 確認 Pinecone 連線與 index 存在（可執行 `uv run create_assistant.py` 或依既有方式建立）。
- 將 txt／md／pdf 放入 `data/`，執行：
  ```bash
  uv run rag_ingest.py
  ```
  或在 Streamlit「上傳並灌入文件」中上傳後按「灌入到向量庫」。

### 4.3 啟動問答（Streamlit）

```bash
uv run streamlit run streamlit_app.py
```

在瀏覽器中使用聊天、側欄設定與 Eval 檢視。

### 4.4 Eval 批次執行與檢視

**執行 Eval（專案根目錄）：**

```bash
uv run python eval/run_eval.py
# 合約／法律專用題集（contract_risk_agent、contract_risk_with_law_search、tw_law_web_search）：
uv run python eval/run_eval.py --eval-set eval/eval_set_contract.json
# 使用 Groq（需 GROQ_API_KEY）：
uv run python eval/run_eval.py --groq
```

可選參數：`--eval-set eval/eval_set.json`（預設）或 `eval/eval_set_contract.json`、`--out-dir eval/runs`、`--top-k 5`。

**輸出：** `eval/runs/run_<timestamp>_results.jsonl`、`run_<timestamp>_metrics.json`（routing 準確率、Tool 成功率、Latency P50/P95）。

**網頁檢視：** Streamlit 側欄選「**Eval 批次結果**」→ 選擇一次 run → 查看各題問題、預期／實際 Tool、回答與指標。

---

## 五、進階與備註

- **合約審閱場景建議**：`RAG_USE_HISTORY_FOR_QUERY=1`（多輪指代）、`RAG_MULTI_QUERY=1`（交叉比對脈絡）、`RAG_MAX_HISTORY_TURNS=12`（或至少 6）；需法條查詢時請設定 `TAVILY_API_KEY`。
- **檢索**：`RAG_DEDUP_ENABLED=1` 去重；`RAG_MMR_LAMBDA` 啟用 MMR；`RAG_INTERNAL_TOP_K`、`RAG_RERANK_TOP_N`、`RAG_MIN_SCORE` 等見程式或 .env。

  - **Dedup（去重）**：檢索回多筆片段後，先依內容 hash 或文字相似度（>0.98）剔除重複或幾乎相同的片段，避免同一段內容重複出現在 context 裡、浪費 token 並干擾回答。
  - **MMR（Maximal Marginal Relevance）**：在去重後的候選中，依「與問題的相關性」與「與已選片段的差異度」做加權挑選，逐步選出前 N 筆。λ（`RAG_MMR_LAMBDA`，建議 0.6）控制「相關性 vs 多樣性」：愈高愈偏相關，愈低愈偏多樣，可減少選到多筆講同一件事的片段。
- **Embedding 維度**：`EMBED_DIM` 須與 Pinecone index 維度一致。
- **Eval 結果目錄**：可由環境變數 `EVAL_RUNS_DIR` 覆寫（預設 `eval/runs`）。
- **Logging**：`agent_router`、`rag_graph`、`sources_registry`、`rag_ingest` 使用標準 `logging`；非預期錯誤會記錄 `warning`／`exception`。

---

## 六、開發與測試

- **依賴與執行**：**uv**（`pyproject.toml`，Python ≥3.13）。
- **測試**：`uv run pytest`（需安裝 dev 依賴：`uv sync --extra dev`）；測試位於 `tests/`。

---

## 七、建議可增加的擴充

以下為針對本專案可再強化的方向，依**體驗與功能**、**穩定性與維運**、**安全與合規**、**法遵／合約**四類整理，可依優先序擇項實作。

### 7.1 體驗與功能

| 項目 | 說明 |
|------|------|
| **對話匯出** | 支援將單一對話或全部對話匯出為 Markdown／PDF，方便存檔或分享（含問題、回答、來源列表與時間戳）。 |
| **檢索片段可複製** | 在「查看檢索片段」區塊為每個 chunk 提供一鍵複製或「引用此段」按鈕，方便撰寫報告時引用。 |
| **合約審閱結果匯出** | 合約審閱＋法條查詢完成後，提供「下載報告」按鈕（MD／PDF），內含風險摘要、法條清單、來源、自檢與免責聲明。 |
| **對話標題可編輯** | 側欄對話列表支援手動重新命名標題，而非僅用首句問題前 20 字。 |
| **快捷提示（prompt 範本）** | 側欄或輸入框旁提供常用問法（如「審閱這份合約並查法條」「列出知識庫來源」），一鍵填入。 |
| **Streaming 回答** | 若 LLM 支援，改為串流輸出回答文字，減少長時間等待感。 |

### 7.2 穩定性與維運

| 項目 | 說明 |
|------|------|
| **健康檢查** | 提供簡易 health endpoint 或 CLI（如 `uv run python -c "from rag_common import get_clients_and_index; get_clients_and_index()"`），確認 Pinecone、Gemini、.env 是否正常。 |
| **環境檢查頁** | 在 Streamlit 側欄或獨立頁顯示：各 API key 是否已設定（不顯示實際 key）、Pinecone index 維度、目前 Chat／Embed 模型，方便除錯。 |
| **請求逾時與重試** | 對 Tavily、Firecrawl、Gemini 等外部 API 設定合理 timeout；Streamlit 對話路徑可考慮對 429／暫時錯誤做重試或友善提示（Eval 與 embed 已有部分重試）。 |
| **清空資料庫確認** | 「清空資料庫」按鈕改為二次確認（例如輸入「確認清空」或勾選），避免誤觸。 |
| **日誌與追蹤** | 重要操作（灌入、清空、合約審閱流程）寫入結構化 log（含 tool、chat_id、耗時）；可選整合 LangSmith／OpenTelemetry 做 trace。 |

### 7.3 安全與合規

| 項目 | 說明 |
|------|------|
| **輸入長度與格式** | 對使用者問題與上傳檔名做長度與字元檢查，避免過長或異常字元導致錯誤或濫用。 |
| **上傳檔案類型與大小** | 除副檔名外，可依 magic bytes 檢查真實檔案類型；單檔與單次上傳總大小限制可設為可配置（如 .env）。 |
| **敏感資訊** | 若對話或灌入內容可能含個資／機密，可加註說明或提供「不記錄此對話」選項、本機-only 模式等（依需求取捨）。 |

### 7.4 法遵／合約相關

| 項目 | 說明 |
|------|------|
| **法條正則擴充** | 目前 `_LAW_REF_PATTERN` 已含民法、消保法、租賃住宅市場發展及管理條例等；可再擴充「第 N 條之 M」「第 N 條第 M 項」等 pattern，或支援更多法規名稱。 |
| **全國法規資料庫 API** | 若有穩定 API，可整合「法條字號 → 條文內容」直接查詢，減少依賴 Tavily 搜尋結果不穩定性。 |
| **合約審閱 Eval 題集** | 在 `eval/eval_set.json` 或另建題集，加入合約審閱／法條查詢範例題與預期 tool（`contract_risk_with_law_search`），方便回歸測試與比對。 |
| **多語言免責聲明** | 若需服務英文或其他語系使用者，可將免責聲明改為依介面語系或參數選擇不同語句。 |

以上項目可依團隊資源與優先序挑選實作，並在「更新記錄」或本節補充實際完成項目。

---

## 更新記錄（Recent Updates）

以下為專案分析後整理之架構要點與近期變更，便於新成員或 fork 後快速對齊現況。

### 專案架構摘要

- **定位**：RAG + 多工具 Agent + Streamlit 示範；以知識庫問答為核心，依問題自動路由至檢索、網路搜尋、網頁擷取、圖表、公司工具或專家子 Agent。
- **技術棧**：Gemini（LLM + embedding）、Pinecone、LangGraph（RAG StateGraph）、Streamlit；可選 Tavily、Firecrawl、ECharts MCP。
- **資料流**：使用者問題 → `agent_router` 決定工具 → 執行（如 `rag_graph.run_rag`、專家、web_search 等）→ 回傳答案與來源；RAG 為 retrieve → 過濾／去重／MMR 或 rerank → generate。
- **文件**：`docs/` 內含 RAG 記憶與上下文、前端設計分析、專案與負責區塊完整度分析等；根目錄另有 `competition_notes_contract_ai.md` 競賽／合約 AI 筆記。

### 近期更新（main 分支）

| 項目 | 說明 |
|------|------|
| **合約／法律工具** | **tw_law_web_search**（臺灣法規，優先 judicial.gov.tw）、**contract_risk_with_law_search**（合約 RAG + 法條抽取 + 司法院查詢 + 風險評估 + AI 自檢）；意圖層 `tw_law_intent`、`contract_risk_with_law_intent` 強制路由。 |
| **RAG 與 Agent** | `rag_graph.py` 雙 Prompt（調查員→判官）、多查詢檢索、rerank；`agent_router.py` 工具路由與 Firecrawl 意圖完善。 |
| **專家** | **contract_risk_agent**、**data_analyst_agent**、財報／ESG 專家；合約專家具 15 年法務審查 prompt 與 strict／顧問雙模式。 |
| **前端與設定** | `streamlit_app.py` 多對話、Eval 檢視；`assets/custom.css`；`.env.example` 含 RAG 記憶、多查詢、Gavel 等；競賽筆記 `competition_notes_contract_ai.md`。 |

---

## 結語

本專案以 **合約／採購法遵審閱** 為核心場景，整合 **RAG（Gemini + Pinecone + LangGraph）**、**多工具 Agent 路由**（含合約風險、法條查詢、司法院檢索）、**網路搜尋與網頁擷取（Tavily、Firecrawl）**、**ECharts 圖表** 與 **專家子 Agent**，並以 **Streamlit** 提供多對話、上傳灌入與 **Eval 運行記錄／批次結果** 檢視；適合作為競賽作品與「知識庫 + 合約審閱 + 法條檢索 + 評估」的延伸開發基礎。

---

## 授權（License）

本專案採用 **MIT License**，詳見 `LICENSE`。









