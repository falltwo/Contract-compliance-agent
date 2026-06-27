# Contract Compliance Agent

> 企業級 AI 合約審閱系統：RAG + 多專家代理 + 法條查詢，支援 Streamlit Demo 與 FastAPI + Vue 內部部署。

[![CI](https://github.com/falltwo/Contract-compliance-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/falltwo/Contract-compliance-agent/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/falltwo/Contract-compliance-agent)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Vue](https://img.shields.io/badge/vue-3-42b883?logo=vue.js&logoColor=white)](https://vuejs.org/)

這是一個面向企業法務、採購、內控與內部 AI 專案團隊的合約審閱系統。它把「合約風險辨識、法條查詢、知識庫問答、評測驗證」整合成單一入口，讓使用者能在幾分鐘內完成第一輪合約審閱，並保留引用來源與可追溯性。

英文版文件請見 [README.en.md](README.en.md)。

---

## 為什麼這個專案值得看

- ⚡ 把合約初審從人工逐條閱讀，縮短成「上傳文件後直接問答或一鍵審閱」。
- ⚖️ 不只做摘要，還能結合法條查詢與風險條款說明，幫助使用者快速聚焦高風險段落。
- 🔎 回答以 RAG 檢索結果為基礎，支援引用來源、片段檢視與嚴格模式，降低幻覺風險。
- 🧭 同時提供 `Streamlit` 與 `FastAPI + Vue` 兩種介面路徑，從 Demo 到內部部署都能落地。
- 📊 內建 Eval 題集與批次驗證流程，不只生成答案，也能衡量路由準確率與工具成功率。

## 適用對象

| 對象 | 適合的使用方式 |
|------|----------------|
| 法務 / 合規團隊 | 快速找出高風險條款、比對法條，5 分鐘內完成合約初審 |
| 內部 AI 團隊 | 以現成 RAG + Agent 架構為基礎，擴充更多合約類型與工具鏈 |
| PoC / 競賽團隊 | 展示「可上傳文件、可檢索、可審閱、可驗證」的完整作品 |
| 平台 / IT 團隊 | 以 `FastAPI + Vue` 接入內網，部署到 DGX 或其他 Linux 主機 |

---

## 典型使用情境

### 情境 A：採購合約快速審閱

> 採購主管收到廠商寄來的 15 頁 IT 服務採購合約。

1. 上傳合約（PDF / DOCX），系統自動向量化
2. 輸入「請審閱這份合約的高風險條款」
3. 系統在 **20–40 秒** 內回傳：
   - 結構化風險卡片（違約責任、服務水準、保密義務…）
   - 每張卡附 **風險等級（高/中/低）**、**法條依據**（連結司法院）、**修改建議**
4. 對照右側文件預覽，直接點卡片跳到原文條款

**效益：** 人工逐頁閱讀通常需要 60–90 分鐘，AI 輔助初審縮短至 5 分鐘，法務人員聚焦於高風險條款深度判斷。

### 情境 B：法條查詢 + 合約交叉比對

> 合約中出現「連帶保證人責任」條款，想確認是否符合民法規定。

```text
這份合約第 12 條的連帶保證人條款是否符合民法規定？請引用相關法條。
```

系統自動：(1) 查詢 Tavily + 司法院 (2) 擷取民法相關條文 (3) 與合約原文交叉比對

### 情境 C：NDA 保密期限風險評估

> 確認保密義務條款的年限是否過長、是否逾越合理範圍。

```text
這份 NDA 的保密義務持續 5 年，在台灣法律實務上是否合理？有無判例可參考？
```

---

## 系統架構

### 請求處理流程

```mermaid
flowchart TD
    U([使用者輸入]) --> AR[Agent Router\nagent_router.py]
    %% 意圖偵測層：規則優先（intent_detector.py），無命中才交給 LLM 決策
    AR --> ID{意圖偵測\nintent_detector.py}
    ID -->|規則：合約+法條| CRL[合約+法條分析\ncontract_risk_with_law_search]
    ID -->|規則：合約審閱| CRA[合約風險代理\nContractRiskAgent]
    ID -->|規則：司法院查詢| TLW[tw_law_web_search\nTavily + judicial.gov.tw]
    ID -->|規則無命中| LLMD{LLM 決策\nrouter}

    LLMD -->|RAG 知識庫| RG[LangGraph RAG\nrag_graph.py]
    LLMD -->|研究模式| RES[research\nRAG + Web]
    LLMD -->|財報 / ESG / 資料| EA[專家代理\nexpert_agents.py]
    LLMD -->|純網路搜尋\n不需知識庫語境| WS[web_search\nTavily]
    LLMD -->|圖表分析\n不需知識庫語境| CH[analyze_and_chart\nECharts MCP]
    LLMD -->|閒聊| ST[small_talk\n直接 LLM]

    %% RAG 是所有知識庫查詢的共用基礎設施（非平行獨立路徑）
    CRL -->|retrieve_only| RG
    CRA -->|retrieve_only| RG
    EA  -->|領域前處理後\n財報格式化／ESG指標抽取| RG
    RES --> RG

    %% 法條查詢是合約+法條流程的子步驟，三階段推送 SSE 進度
    CRL -.子流程.-> LS[法條搜尋\nTavily → judicial.gov.tw\n並行查詢（最多 4 條）]
    CRL -.progress.-> PG[SSE status\ncontract_retrieve → law_search → contract_generate]

    %% RAG 核心流程
    RG --> Q[Query 重寫 + Multi-query\n（aux 並行 embedding+search）]
    Q --> RET[Hybrid 檢索\nPinecone + BM25（jieba）]
    RET --> RK[重排序\nMMR 預設 / LLM / none]
    RK --> PKG[Investigator 打包證據]
    PKG --> GEN[Judge 評估風險]

    %% 備援路徑
    RG -->|檢索無結果或信心值低| FB[備援\n提示補充資訊]
    FB --> OUT

    %% AI 自檢（合約+法條模式，CONTRACT_RISK_SELF_CHECK_ENABLED=1 時）
    GEN --> CHK[AI 自檢\n驗證答案與來源一致性]
    CHK --> OUT

    %% 純工具直接輸出
    LS --> OUT
    WS --> OUT
    TLW --> OUT
    CH --> OUT
    ST --> OUT

    OUT([回答 + 引用來源 + 風險標注])
```

### LangGraph RAG 狀態機

```mermaid
stateDiagram-v2
    [*] --> retrieve : 使用者問題
    retrieve --> rewrite : RAG_MULTI_QUERY=1
    retrieve --> rerank : 直接重排
    rewrite --> aux_parallel : 產生 aux queries
    aux_parallel --> rerank : ThreadPool\n（RAG_AUX_QUERY_CONCURRENCY=4）
    rerank --> package : Investigator 整理證據\n（RAG_RERANK_METHOD=mmr|llm|none）
    package --> generate : Judge 評估風險
    generate --> [*] : 回答 + 來源 + 風險標注
```

### 部署架構

```mermaid
flowchart LR
    subgraph 使用者端
        B1[瀏覽器\n前台 :4173]
        B2[瀏覽器\n後台 :4174]
    end

    subgraph DGX["DGX Spark (spark-98e3)"]
        FE[Vue Frontend\n:4173]
        AD[Vue Admin\n:4174]
        API[FastAPI\n:8000]
        OL[Ollama\ngemma3:27b]
    end

    subgraph Cloud[雲端服務]
        PC[(Pinecone\n向量資料庫)]
        TV[Tavily\n法條查詢]
    end

    B1 <--> FE
    B2 <--> AD
    FE <--> API
    AD <--> API
    API <--> OL
    API <--> PC
    API <--> TV
```

### CI / 自動部署流程

```mermaid
flowchart LR
    P[git push\nto main] --> CI[GitHub Actions]
    CI --> T1[pytest]
    CI --> T2[web build\n+ TS check]
    CI --> T3[Playwright E2E]
    T1 & T2 & T3 -->|全部通過| D[Deploy Job]
    D -->|Tailscale SSH| DGX2[DGX\ndeploy script]
    DGX2 --> DONE([服務自動更新 ✓])
```

---

## 功能特色

- 📄 支援合約與文件上傳，接受 `.txt`、`.md`、`.pdf`、`.docx`
- 🧠 使用 LangGraph 驅動的 RAG 流程，支援多輪對話與知識庫問答
- 🔀 透過 Agent Router 自動選擇 RAG、合約審閱、法條查詢、專家代理等工具
- ⚖️ 提供「合約風險評估 + 法條查詢」流程，整合法條搜尋與 AI 自檢
- 🔍 支援 Hybrid Retrieval，結合向量檢索與 BM25 精準匹配
- 🧪 內建 Eval 題集、批次執行與結果輸出，方便追蹤品質與回歸測試
- 🚀 提供 Streamlit Demo 介面與 Vue Web MVP，可按場景切換
- 🖥️ 支援 Ollama 本地模型與 DGX 常駐部署
- 🤖 CI 通過後自動部署到 DGX（GitHub Actions + Tailscale）

---

## 快速開始

### 先決條件

| 項目 | 說明 |
|------|------|
| Python | `3.13+` |
| `uv` | Python 套件與執行環境管理工具 |
| Pinecone | 需先建立 index，供向量檢索使用 |
| LLM Provider | 二選一：Google Gemini 或 Ollama |
| Tavily | 若要啟用法條 / 網路查詢功能則需要 |
| Node.js | 若要啟動 Vue 前端，建議使用 LTS 版本 |

### 1. 建立環境變數

```bash
cp .env.example .env
# 編輯 .env，填入必要欄位
```

### 2. 必填與常用設定

| 變數 | 是否必填 | 用途 |
|------|----------|------|
| `PINECONE_API_KEY` | 必填 | Pinecone API 金鑰 |
| `PINECONE_INDEX` | 必填 | Pinecone index 名稱 |
| `CHAT_PROVIDER` | 必填 | `gemini` 或 `ollama` |
| `GOOGLE_API_KEY` | Gemini 時必填 | 雲端聊天模型 |
| `EMBEDDING_PROVIDER` | 建議填寫 | `gemini` 或 `ollama` |
| `OLLAMA_CHAT_MODEL` | Ollama 時必填 | 本地聊天模型名稱 |
| `OLLAMA_EMBED_MODEL` | Ollama 時建議填寫 | 本地 embedding 模型名稱 |
| `TAVILY_API_KEY` | 選填 | 啟用法條 / 網路搜尋 |

推薦本地模型設定：

```env
PINECONE_INDEX=weck06
CHAT_PROVIDER=ollama
OLLAMA_CHAT_MODEL=gemma3:27b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBED_MODEL=snowflake-arctic-embed2:568m
```

### 團隊共用 `.env` 規範

1. `PINECONE_INDEX` 在共用環境固定為 `weck06`，未經維運人員同意不得修改。
2. 需要做個人實驗時，只能改自己本機未追蹤的 `.env`，不能改 `.env.example`。
3. 不得把個人 / 臨時 index 名稱提交到 `main`。

### 多模型分流（選填）

```env
# 低成本階段（路由 / rewrite / rerank）
OLLAMA_ROUTER_MODEL=gemma3:4b-it-qat
OLLAMA_RAG_REWRITE_MODEL=gemma3:4b-it-qat
OLLAMA_RAG_RERANK_MODEL=gemma3:4b-it-qat

# 主回答階段
OLLAMA_RAG_GENERATE_MODEL=gemma3:27b

# 合約高品質覆核（可選）
OLLAMA_CONTRACT_RISK_VERIFY_MODEL=gpt-oss:120b
```

### 輕量化部署模式

複製 `.env.lightweight` 取代預設 `.env`，可在資源受限環境（筆電、低顯存 GPU）全程本地推論，無需任何雲端 API：

```bash
cp .env.lightweight .env
# 預拉所需模型
ollama pull gemma3:4b-it-qat   # 路由 / rewrite / rerank（~2.5 GB VRAM）
ollama pull gemma3:12b          # 主生成（~7 GB VRAM）
ollama pull snowflake-arctic-embed2:568m  # Embedding（568M 參數）
```

**標準部署 vs 輕量化部署對比：**

| 項目 | 標準部署（Gemini/27B） | 輕量化部署（4B + 12B） |
|------|----------------------|----------------------|
| 路由 / rewrite / rerank 模型 | Gemini flash-lite / gemma3:27b | gemma3:4b-it-qat |
| 主生成模型 | gemma3:27b | gemma3:12b |
| Embedding 模型 | gemini-embedding-001（雲端） | snowflake-arctic-embed2:568m（本地） |
| 每次查詢 LLM 呼叫數 | 最多 12 次（預算上限） | 最多 6 次（輕量上限） |
| Multi-query | 開啟（+3 次 LLM 呼叫） | 關閉 |
| Rerank 策略 | MMR（預設）/ LLM rerank（選配） | MMR 固定 |
| 路由決策 LLM 用量 | 0（規則優先，無命中才呼叫 LLM） | 0（同左，規則覆蓋 80%+ 情境） |
| 雲端 API 依賴 | Google / Gemini | **零依賴**（全本地） |
| 建議 VRAM | 24 GB（A5000+） | **10 GB+**（消費級 GPU 可用） |

> **設計理念：** 輕量化模式的關鍵在「輕任務用 4B 量化模型（路由/改寫/重排），重任務才用 12B（生成）」，
> 而非把所有階段都降到最小模型。路由與改寫的品質影響較小，生成品質才是使用者直接感知的。

### Timeout 設定

```env
OLLAMA_TIMEOUT_SEC=120
OLLAMA_ROUTER_TIMEOUT_SEC=20
OLLAMA_RAG_REWRITE_TIMEOUT_SEC=20
OLLAMA_RAG_RERANK_TIMEOUT_SEC=25
OLLAMA_RAG_GENERATE_TIMEOUT_SEC=120
```

### 3. 安裝依賴

```bash
uv sync
```

### 4. 灌入範例資料

```bash
uv run rag_ingest.py
```

### 5. 啟動 Streamlit Demo

```bash
uv run streamlit run streamlit_app.py
# http://localhost:8501
```

---

## Web 模式：FastAPI + Vue

### 後端 API

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# API 文件：http://127.0.0.1:8000/docs
```

### 前端

```bash
cd web && npm ci && npm run dev
# 前台：http://localhost:5173/chat
# 後台：http://localhost:5173/admin
```

---

## 部署方式

### DGX / Linux 內部部署

```bash
bash scripts/install_dgx_services.sh   # 初次安裝
bash scripts/deploy_contract_agent.sh  # 手動更新
```

| 服務 | Port | 路由 |
|------|------|------|
| `contract-agent-api` | `8000` | FastAPI 後端 |
| `contract-agent-web-frontend` | `4173` | 前台（chat / upload / sources） |
| `contract-agent-web-admin` | `4174` | 後台（admin / eval） |

### 自動部署（已設定）

push 到 `main` 且 CI 全部通過後，GitHub Actions 透過 Tailscale SSH 自動執行部署腳本，組員無需手動操作 DGX。

確認部署狀態：GitHub repo → Actions → 最新 run → **Deploy to DGX** job。

詳細維運說明請參考 [`docs/DGX_網站使用與維運手冊_v1.2.md`](docs/DGX_網站使用與維運手冊_v1.2.md)。

---

## 使用說明

### 合約審閱

```text
請審閱這份合約的風險條款
```

```text
請整理高風險條款，並列出相對應的法律依據
```

### 知識庫問答

```text
這份 NDA 的保密義務持續多久？
```

```text
列出目前知識庫有哪些文件
```

### 嚴格模式

在介面中開啟嚴格模式，回答只根據知識庫內容，不混入模型推測。適用法遵或內部審核場景。

---

## 技術棧

| 類別 | 技術 |
|------|------|
| 語言 | Python 3.13+、TypeScript |
| 後端 | FastAPI、Pydantic Settings、Uvicorn |
| 前端 | Vue 3、Vite、Pinia、Vue Router |
| Demo UI | Streamlit |
| AI / RAG | LangGraph、Pinecone、BM25（jieba）、Ollama、Google Gemini |
| 外部工具 | Tavily（法條 / 網路搜尋）、Groq、Firecrawl（選配，網頁擷取） |
| 測試 | pytest、Playwright |
| CI/CD | GitHub Actions + Tailscale SSH |

---

## 技術架構創新點

### 1. 規則優先意圖路由（零 LLM 路由成本）

`intent_detector.py` 以純 regex/字串比對處理 80%+ 的路由決策（合約審閱、台灣法條查詢、Firecrawl 爬取），**不消耗任何 LLM 呼叫**。只有無法被規則覆蓋的模糊意圖才走 LLM router，最大化整體效率。

| 方案 | 路由方式 | 路由成本 |
|------|---------|---------|
| 純 LLM 路由（如 ReAct / function-calling） | 每次都調 LLM | 1 次 LLM / query |
| 本系統 | 規則優先 → LLM 後備 | **0 次 LLM（規則命中）** |

### 2. 階段化模型分配（Tiered Model Routing）

不同推論難度使用不同大小的模型，而非一律套用最大模型：

```
路由 / 改寫 / rerank：gemma3:4b-it-qat（量化，<3 GB VRAM，快）
主生成 / 合約審閱：gemma3:27b（標準）/ gemma3:12b（輕量）
合約覆核（選配）：gpt-oss:120b（開源大模型）
Embedding：snowflake-arctic-embed2:568m（568M，本地，零 API 費用）
```

### 3. 混合檢索 + 中文法律術語 BM25（Hybrid RAG）

Pinecone 語意向量 + BM25 關鍵字檢索 + RRF Fusion，jieba 詞級分詞並內建約 50 個台灣合約術語辭典（「違約金」「政府採購法」「智慧財產權」等），確保法律術語精準命中。

### 4. Investigator / Judge 雙 Prompt 架構

合約審閱分兩階段：(1) **Investigator** 負責蒐集相關條款證據並交叉比對，(2) **Judge** 根據證據給出最終風險評估與建議。分離「資訊蒐集」與「判斷推理」提升準確率與可解釋性。

### 5. ContextVar-based SSE 進度串流

合約＋法條查詢流程（20–40s）透過 `progress.py` 的 ContextVar 介面，把進度事件從 `ThreadPoolExecutor` 內部安全傳遞到 SSE 串流端，前端即時顯示「檢索合約 → 搜尋法條 → 產出評估」三階段 stepper。

---

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/health` | 服務健康檢查 |
| POST | `/api/v1/chat` | 主要問答（RAG / 代理路由）|
| POST | `/api/v1/ingest/upload` | 多檔上傳與向量化 |
| GET | `/api/v1/sources` | 列出已上傳來源 |
| GET | `/api/v1/sources/preview` | 預覽來源 chunk |
| GET | `/api/v1/eval/runs` | 線上評測紀錄 |
| GET | `/api/v1/eval/batch/{run_id}` | 批次評測詳情 |
| GET | `/api/v1/admin/services` | systemd 服務狀態 |
| POST | `/api/v1/admin/services/restart` | 重啟指定服務 |
| GET | `/api/v1/admin/ollama/models` | Ollama 已載入模型 |

完整 Schema：`http://127.0.0.1:8000/docs`

---

## Eval 與品質驗證

```bash
# 執行 API 測試
uv sync --extra dev
uv run pytest tests/test_chat_api.py tests/test_ingest_api.py -v

# 執行通用評測集
uv run python eval/run_eval.py

# 執行合約評測集
uv run python eval/run_eval.py --eval-set eval/eval_set_contract.json

# 使用 Groq 加速
uv run python eval/run_eval.py --groq
```

Eval 輸出至 `eval/runs/run_<timestamp>_metrics.json`，追蹤：路由準確率、工具成功率、延遲 P50/P95。

---

## 專案結構

```text
Contract-compliance-agent/
├── agent_router.py          # 意圖路由核心
├── rag_graph.py             # LangGraph RAG 狀態機
├── rag_common.py            # Pinecone、Embedding、BM25 共用
├── chat_service.py          # 對話入口與 Eval 日誌
├── expert_agents.py         # 財務、ESG、風險專家代理
├── ingest_service.py        # 文件灌入與來源管理
├── llm_client.py            # LLM 客戶端與多模型路由
├── streamlit_app.py         # Demo UI
│
├── backend/
│   ├── main.py              # FastAPI 應用入口
│   ├── config.py            # Pydantic Settings
│   └── api/routes/          # chat / ingest / eval / admin / health
│
├── web/                     # Vue 3 前端
│   └── src/
│       ├── views/           # 頁面
│       ├── components/      # UI 元件
│       └── stores/          # Pinia 狀態
│
├── eval/                    # 評測集與批次執行器
├── data/                    # 範例知識庫文件
├── deploy/systemd/          # systemd 服務模板
├── scripts/                 # 安裝與部署腳本
├── docs/                    # 維運手冊與更新紀錄
└── tests/                   # pytest 測試
```

---

## 常見問題

| 問題 | 排查方向 |
|------|---------|
| 無檢索結果 | 確認 Pinecone API Key、Index 名稱正確，且資料已灌入 |
| 模型找不到 | 確認 `ollama list` 有對應模型，或 `GOOGLE_API_KEY` 有效 |
| 回應 Timeout | 調高 `OLLAMA_*_TIMEOUT_SEC`，或降低 `TOP_K` |
| 檔案上傳失敗 | 確認檔案 < 32MB，格式為 `.txt` `.md` `.pdf` `.docx` |
| CORS 錯誤 | 確認 `API_CORS_ORIGINS` 或 `API_CORS_ORIGIN_REGEX` 包含前端 URL |
| 自動部署未觸發 | 確認 push 到 `main`，且 CI 三個 job 全部通過 |

---

## 限制與免責聲明

本專案是「合約第一輪審閱輔助工具」，不是法律意見系統。

- 本系統輸出內容不構成法律意見或正式法律建議
- AI 可能誤判、漏判或誤引，所有結果應由合格法律專業人士複核
- 法條查詢依賴外部搜尋結果，仍需人工確認最新版本與適用性
---

## 貢獻指南

歡迎 issue 與 pull request，特別是以下方向：

- 合約風險規則與提示詞優化
- 法條查詢流程與引用品質改善
- Eval 題集擴充與回歸測試補強
- DGX / Linux 部署與維運流程改善
- Vue 管理後台與使用者體驗優化

送出變更前，建議至少完成：

1. 若修改 API 或資料結構，執行前後端契約檢查
2. 若修改 chat / ingest 流程，執行對應 pytest
3. 若修改前端互動，至少手動驗證 `/chat`、`/upload`、`/admin`
4. 若修改啟動或部署流程，請同步更新 `README` 或 `docs/`

---

## License

本專案採用 [MIT License](LICENSE)。
