# Agent-DEMO（範本）

> 這個 repo 是我們組內的 **範本專案**，給組員 fork / 使用 template 後，快速建立自己的「知識庫問答 + 多工具 Agent」服務。
>
> 以 RAG 為核心的智慧問答 Agent：結合 LLM 與向量檢索，支援知識庫問答、網路搜尋、網頁擷取、圖表生成與多工具路由，並提供 Eval 框架與網頁檢視。

---



- **這個 repo 怎麼用**
  - 到 GitHub 上 **fork** 或點 `Use this template` 建立自己的專案。
  - 之後只維護自己的 repo，不要直接在這個範本上改。

- **環境變數與 API Key（超重要）**
  - 專案內的 `.env.example` 是**示範檔**，裡面都是假的值，請依照自己帳號改：
    - `PINECONE_API_KEY=your_pinecone_api_key_here`
    - `GOOGLE_API_KEY=your_google_api_key_here`
    - `TAVILY_API_KEY=your_tavily_api_key_here`
    - `FIRECRAWL_API_KEY=your_firecrawl_api_key_here`
    - `GROQ_API_KEY=your_groq_api_key_here`
  - 實際開發時：
    - 在本機建立 `.env` 或 `.env.local` 來放**真實 key**。
    - 確認 `.env` / `.env.local` 都有被列在 `.gitignore`，**不要把真實 key commit / push 上 GitHub**。

- **跑起來的最短步驟**
  1. 建立自己的 repo（fork / template）。
  2. `cp .env.example .env`，在 `.env` 裡填入自己帳號的 API key。
  3. 建好 Pinecone index，準備好要放到知識庫的檔案放在 `data/`。
  4. 在專案根目錄執行：
     ```bash
     uv run rag_ingest.py
     uv run streamlit run streamlit_app.py
     ```
  5. 瀏覽器打開 Streamlit 顯示的網址，就可以開始問問題、看檢索結果與圖表。

> 下面開始是原本比較完整的技術與架構說明，需要理解細節或要擴充功能再往下看即可。

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

---

## 一、專案概述

本專案是 **「知識庫問答 + 多工具 Agent 路由」** 的示範系統：

- 使用者可**上傳文件**建立知識庫，以**自然語言**提問
- 系統會**自動選擇**要查知識庫、查網路、擷取網頁、畫圖表或呼叫公司工具（財報計算、日期解析、季度計畫等）
- 在 **Streamlit** 網頁上完成對話、檢索結果與 **Eval 批次結果** 的展示

---

## 二、技術架構

### 2.1 技術棧

| 類別 | 選型 | 說明 |
|------|------|------|
| **LLM / Embedding** | Google **Gemini** | 對話預設 `gemini-2.5-flash`，向量化 `gemini-embedding-001`；Eval 可選 **Groq**（`llama-3.3-70b-versatile`）避開免費額度限制。 |
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
| **expert_agents.py** | 專家子 Agent：財報／營運（FinancialReportAgent）、ESG／法遵（ESGAgent）。 |
| **echarts_tools.py** / **echarts_mcp_client.py** | 圖表 option 產生與可選 MCP PNG 輸出。 |
| **firecrawl_tools.py** | Firecrawl API 封裝（scrape、search、crawl、map）。 |
| **eval_log.py** | 問答運行記錄（可選寫入每筆問題／答案／tool／延遲）。 |
| **eval/run_eval.py** | Eval 批次腳本：讀題集、呼叫 `route_and_answer`、寫入結果與核心指標；可加 `--groq`。 |

### 2.3 支援的工具（SUPPORTED_TOOLS）

| 分類 | 工具 |
|------|------|
| **知識庫** | `rag_search`、`research`、`list_sources`、`search_similar`、`summarize_source` |
| **網路／網頁** | `web_search`、`scrape_url`、`firecrawl_search` |
| **圖表** | `create_chart`、`analyze_and_chart` |
| **公司工具** | `financial_metrics`、`parse_dates_from_text`、`generate_quarterly_plan` |
| **專家** | `financial_report_agent`、`esg_agent` |
| **對話** | `small_talk`、`ask_web_vs_rag` |

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
- **專家**：財報／營運、ESG／法遵專用回答。
- **前端**：多對話、嚴格／非嚴格模式、上傳並灌入文件、檢索片段與圖表展示；側欄可切換「對話」「Eval 運行記錄」「Eval 批次結果」。

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
| `TAVILY_API_KEY` | 網路搜尋（Tavily） |
| `FIRECRAWL_API_KEY` | 網頁擷取／搜尋 |
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
# 使用 Groq（需 GROQ_API_KEY）：
uv run python eval/run_eval.py --groq
```

可選參數：`--eval-set eval/eval_set.json`、`--out-dir eval/runs`、`--top-k 5`。

**輸出：** `eval/runs/run_<timestamp>_results.jsonl`、`run_<timestamp>_metrics.json`（routing 準確率、Tool 成功率、Latency P50/P95）。

**網頁檢視：** Streamlit 側欄選「**Eval 批次結果**」→ 選擇一次 run → 查看各題問題、預期／實際 Tool、回答與指標。

---

## 五、進階與備註

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

## 結語

本專案整合 **RAG（Gemini + Pinecone + LangGraph）**、**多工具 Agent 路由**、**網路搜尋與網頁擷取（Tavily、Firecrawl）**、**ECharts 圖表**、**公司工具與專家子 Agent**，並以 **Streamlit** 提供多對話、上傳灌入與 **Eval 運行記錄／批次結果** 檢視；Eval 可選 Groq 避開 Gemini 免費額度限制，適合作為「知識庫 + 外部資訊 + 圖表 + 評估」的示範與延伸開發基礎。

---

## 授權（License）

本專案採用 **MIT License**，詳見 `LICENSE`。









