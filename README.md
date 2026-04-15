# Autonomous Contract Risk Assessment Agent System

> AI-powered first-pass contract review with RAG, legal grounding, and deployable Streamlit / FastAPI + Vue interfaces.

[![Version](https://img.shields.io/badge/version-v1.0.0-2ea44f)](https://github.com/falltwo/Contract-compliance-agent)
[![License](https://img.shields.io/github/license/falltwo/Contract-compliance-agent)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![README](https://img.shields.io/badge/README-繁中-0F766E)](README.md)
[![Stars](https://img.shields.io/github/stars/falltwo/Contract-compliance-agent?style=social)](https://github.com/falltwo/Contract-compliance-agent/stargazers)

這是一個面向企業法務、採購、內控與內部 AI 專案團隊的合約審閱系統。它把「合約風險辨識、法條查詢、知識庫問答、評測驗證」整合成單一入口，讓使用者能在幾分鐘內完成第一輪合約審閱，並保留引用來源與可追溯性。

英文版文件請見 [README.en.md](README.en.md)。

## 為什麼這個專案值得看

- ⚡ 把合約初審從人工逐條閱讀，縮短成「上傳文件後直接問答或一鍵審閱」。
- ⚖️ 不只做摘要，還能結合法條查詢與風險條款說明，幫助使用者快速聚焦高風險段落。
- 🔎 回答以 RAG 檢索結果為基礎，支援引用來源、片段檢視與嚴格模式，降低幻覺風險。
- 🧭 同時提供 `Streamlit` 與 `FastAPI + Vue` 兩種介面路徑，從 Demo 到內部部署都能落地。
- 📊 內建 Eval 題集與批次驗證流程，不只生成答案，也能衡量路由準確率與工具成功率。

## 適用對象

| 對象 | 適合的使用方式 |
|------|----------------|
| 法務 / 合規團隊 | 快速找出高風險條款、比對法條、形成第一輪審閱意見 |
| 內部 AI 團隊 | 以現成 RAG + Agent 架構為基礎，擴充更多合約類型與工具鏈 |
| PoC / 競賽團隊 | 用最短時間展示「可上傳文件、可檢索、可審閱、可驗證」的完整作品 |
| 平台 / IT 團隊 | 以 `FastAPI + Vue` 方式接入內網環境，部署到 DGX 或其他 Linux 主機 |

## 功能特色

- 📄 支援合約與文件上傳，接受 `.txt`、`.md`、`.pdf`、`.docx`
- 🧠 使用 LangGraph 驅動的 RAG 流程，支援多輪對話與知識庫問答
- 🔀 透過 Agent Router 自動選擇 RAG、合約審閱、法條查詢、專家代理等工具
- ⚖️ 提供「合約風險評估 + 法條查詢」流程，整合法條搜尋與 AI 自檢
- 🔍 支援 Hybrid Retrieval，結合向量檢索與 BM25 精準匹配
- 🧪 內建 Eval 題集、批次執行與結果輸出，方便追蹤品質與回歸測試
- 🚀 提供 Streamlit Demo 介面與 Vue Web MVP，可按場景切換
- 🖥️ 支援 Ollama 本地模型與 DGX 常駐部署

## 系統概覽

### 請求流程

使用者提問後，系統會先做意圖判斷，再路由到對應工具或專家流程，最後回傳答案、引用與風險說明。

![系統流程](assets/flowchart.png)

### 架構圖

核心模組包含前端介面、Agent Router、RAG 檢索、法條查詢、文件灌入與 Eval 驗證。

![系統架構](assets/architecture-diagram.png)

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

`README` 首次上手最常卡在這一步。建議先複製 `.env.example`，再填入最少必要欄位。

**macOS / Linux**

```bash
cp .env.example .env
```

**PowerShell**

```powershell
Copy-Item .env.example .env
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

如果你要直接使用本專案預設推薦的本地模型，可在 `.env` 中採用這組設定：

```env
CHAT_PROVIDER=ollama
OLLAMA_CHAT_MODEL=gemma3:27b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBED_MODEL=snowflake-arctic-embed2:568m
```

### 3. 安裝依賴

```bash
uv sync
```

### 4. 灌入範例資料

專案已內建 `data/sample.txt` 與 `data/sample_contract_NDA.txt`，第一次試跑不需要另外準備文件。

```bash
uv run rag_ingest.py
```

### 5. 啟動最快的體驗路徑：Streamlit

```bash
uv run streamlit run streamlit_app.py
```

啟動後打開 `http://localhost:8501`，就可以：

- 上傳合約並灌入目前對話
- 直接詢問知識庫內容
- 使用側欄按鈕執行一鍵合約審閱
- 在有 `TAVILY_API_KEY` 的情況下啟用法條查詢版審閱

## 5 分鐘試跑建議

如果你希望第一次就成功，建議照這條最短路徑走：

1. 複製 `.env.example` 成 `.env`
2. 填好 Pinecone 與一組模型設定
3. 執行 `uv sync`
4. 執行 `uv run rag_ingest.py`
5. 執行 `uv run streamlit run streamlit_app.py`
6. 在介面輸入：`請審閱這份合約的風險條款`

如果你已設定 `TAVILY_API_KEY`，也可以直接輸入：

```text
合約風險評估並查相關法條
```

## Web 模式：FastAPI + Vue

如果你要把專案當成 API 服務或內部 Web 工具來用，建議走這條路徑。

### 啟動後端 API

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

啟動後可用：

- API 文件：`http://127.0.0.1:8000/docs`
- 健康檢查：`http://127.0.0.1:8000/health`

### 啟動 Vue 前端

```bash
cd web
npm ci
npm run dev
```

啟動後可用：

- 對話頁：`http://localhost:5173/chat`
- 上傳頁：`http://localhost:5173/upload`
- 來源清單：`http://localhost:5173/sources`
- 管理後台：`http://localhost:5173/admin`
- Eval 頁：`http://localhost:5173/eval`

## 部署方式

### DGX / Linux 內部部署

本專案已提供 `systemd` 模板與部署腳本，適合常駐在 DGX 或其他內網 Linux 主機。

```bash
bash scripts/install_dgx_services.sh
bash scripts/deploy_contract_agent.sh
```

部署完成後，預設服務如下：

| 服務 | 預設埠 | 說明 |
|------|--------|------|
| `contract-agent-api.service` | `8000` | FastAPI 後端 |
| `contract-agent-web.service` | `4173` | Vue build 後的預覽服務 |

部署時請留意：

- Vue production 會優先讀取 `VITE_API_BASE_URL`
- 若未設定，前端會自動以「目前瀏覽器主機 + `:8000`」推導 API 位址
- 若要支援區網 IP、Tailscale IP 與 localhost 跨埠存取，請設定 `API_CORS_ORIGIN_REGEX`

## 使用說明

### 合約審閱

最直接的使用方式有兩種：

1. 在 Streamlit 側欄點選「一鍵審閱（僅知識庫）」或「一鍵審閱（含法條查詢）」
2. 直接輸入自然語言，例如：

```text
請審閱這份合約的風險條款
```

```text
請整理高風險條款，並列出相對應的法律依據
```

### 知識庫問答

當文件已完成灌入後，可直接詢問：

```text
這份 NDA 的保密義務持續多久？
```

```text
列出目前知識庫有哪些文件
```

### 嚴格模式

若你希望回答只根據知識庫內容，不混入模型推測，可在介面中開啟嚴格模式。這對法遵或內部審核場景特別重要。

## 技術棧

| 類別 | 技術 |
|------|------|
| 語言 | Python、TypeScript |
| 後端 | FastAPI、Pydantic Settings、Uvicorn |
| 前端 | Streamlit、Vue 3、Vite、Pinia、Vue Router |
| AI / RAG | LangGraph、Pinecone、BM25、Ollama、Google Gemini |
| 外部工具 | Tavily、Firecrawl、ECharts、Groq |
| 測試 | Pytest、Playwright |

## 專案結構

```text
Contract-compliance-agent/
├─ backend/                 # FastAPI API、schema、service adapter
├─ web/                     # Vue 3 + Vite 前端
├─ data/                    # 預設文件與範例合約
├─ eval/                    # Eval 題集與批次執行腳本
├─ docs/                    # 更新摘要與文件索引
├─ deploy/systemd/          # DGX / Linux 服務模板
├─ scripts/                 # 安裝與部署腳本
├─ streamlit_app.py         # Streamlit 主入口
├─ agent_router.py          # Agent 路由核心
├─ rag_graph.py             # RAG 工作流
├─ rag_common.py            # 檢索與 embedding 共用邏輯
└─ ingest_service.py        # 文件灌入與來源管理
```

## 核心模組說明

| 模組 | 作用 |
|------|------|
| `streamlit_app.py` | Demo 與操作最快的主入口，提供對話、灌入與 Eval 檢視 |
| `backend/main.py` | FastAPI 應用入口，整合 chat、ingest、admin、eval、health 路由 |
| `agent_router.py` | 根據使用者意圖路由到 RAG、合約審閱、法條查詢或其他工具 |
| `rag_graph.py` | LangGraph 驅動的檢索與生成流程 |
| `rag_common.py` | Pinecone、embedding provider、BM25、檢索排序等共用邏輯 |
| `expert_agents.py` | 專家代理邏輯，處理合約法遵、資料分析等延伸任務 |
| `ingest_service.py` | 處理文件灌入、來源註冊與 chunk 寫入 |

## Eval 與品質驗證

本專案不是只有 Demo 介面，也內建了可重跑的驗證流程。

### 執行 API 測試

```bash
uv sync --extra dev
uv run python -m pytest tests/test_chat_api.py tests/test_ingest_api.py -v
```

### 執行 Eval 題集

```bash
uv run python eval/run_eval.py
```

```bash
uv run python eval/run_eval.py --eval-set eval/eval_set_contract.json
```

```bash
uv run python eval/run_eval.py --groq
```

Eval 會輸出：

- `eval/runs/run_<timestamp>_results.jsonl`
- `eval/runs/run_<timestamp>_metrics.json`

你可以在結果中追蹤：

- routing accuracy
- tool success rate
- latency P50 / P95

## 限制與免責聲明

這個專案是「合約第一輪審閱輔助工具」，不是法律意見系統。

- 本系統輸出內容不構成法律意見或正式法律建議
- AI 可能誤判、漏判或誤引，因此所有結果都應由合格法律專業人士複核
- 法條查詢依賴外部搜尋結果與模型整合，仍需人工確認最新版本與適用性
- 若知識庫未完整灌入、模型設定不正確或 Pinecone 維度設定不一致，結果品質會顯著下降

## 延伸閱讀

- [docs/README.md](docs/README.md)：近期更新與文件索引
- [docs/update-summary-2026-04-15.md](docs/update-summary-2026-04-15.md)：本輪整合與部署摘要
- [backend/README.md](backend/README.md)：FastAPI API、測試與部署補充
- [web/README.md](web/README.md)：Vue 前端開發與建置說明
- [STREAMLIT至Vue前端改版說明.md](STREAMLIT至Vue前端改版說明.md)：前端改版背景

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

## License

本專案採用 [MIT License](LICENSE)。
