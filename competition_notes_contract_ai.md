# 2026 智慧創新大賞構想筆記：合約／採購法遵審閱助理

> 依據專案 `agent-demo`（RAG + 多工具 Agent + Streamlit）與經濟部「2026 智慧創新大賞」競賽須知，整理的參賽題目與技術分析筆記。

---

## 一、競賽關鍵重點整理

- **類別**：AI 應用類（學生組或企業組依實際情況）
- **評分項目**  
  - 產業應用性：40%（價值創造、應用效益）  
  - 技術創新性：30%（與既有技術／產品的差異化程度）  
  - 作品完整性：30%（功能完整、技術驗證程度）
- **交件內容（初賽書審）**  
  - 作品說明書（Word，最多 5 頁）  
  - 3 分鐘作品介紹影片（YouTube 不公開連結）  
  - 加分佐證文件（GitHub／Stack Overflow／Hugging Face／Crunchbase 登錄、AI 輕量化說明）  
- **限制與規則重點**  
  - 嚴禁使用中國開發之軟硬體  
  - 學生組文件中不得出現學校名稱、Logo、指導教授姓名等辨識資訊

---

## 二、現有專案技術與結構摘要

- **整體定位**：  
  「知識庫問答 + 多工具 Agent 路由」示範系統，可上傳文件灌入向量庫，支援 RAG 問答、網路搜尋、網頁擷取、圖表生成與 Eval。

- **技術棧**（摘自 README）
  - LLM / Embedding：Google Gemini（對話 `gemini-2.5-flash`，向量 `gemini-embedding-001`），Eval 可選 Groq
  - 向量庫：Pinecone
  - RAG 流程：LangGraph StateGraph（retrieve → generate）
  - 前端：Streamlit（多對話、上傳灌入、檢索片段與圖表、Eval 運行記錄與批次結果）
  - 網路搜尋：Tavily
  - 網頁擷取：Firecrawl
  - 圖表：ECharts（`streamlit-echarts`，可搭配 ECharts MCP 產 PNG）

- **主要模組**
  - `rag_common.py`：chunk、格式化 context、初始化 Pinecone / Gemini、embedding
  - `rag_graph.py`：RAG 核心（檢索 → 過濾／dedup／MMR 或 LLM rerank → 生成）
  - `agent_router.py`：多工具總管 Agent（tool routing，含 Firecrawl gate、ask_web_vs_rag、圖表相關工具）
  - `streamlit_app.py`：Streamlit UI（多對話、上傳灌庫、問答與圖表展示、Eval 運行記錄／批次結果頁）
  - `company_tools.py`：財報指標計算、日期解析、季度計畫表
  - `expert_agents.py`：財報專家、ESG／法遵專家、資料分析專家
  - `echarts_tools.py` / `echarts_mcp_client.py`：圖表 option 與 MCP 圖輸出
  - `firecrawl_tools.py`：Firecrawl API；`eval/`：Eval 腳本與歷史結果

- **特點**
  - RAG 支援 dedup / MMR / LLM rerank 與多種環境變數調整（`RAG_INTERNAL_TOP_K`、`RAG_RERANK_TOP_N` 等）
  - Agent Router 支援多種工具並有 Firecrawl 規則＋LLM gate、多輪澄清流程（知識庫 vs 網路、先問再產圖）
  - 具備 Eval 能力：單次運行記錄（latency、tool_name 等）與批次 Eval（routing 準確率、tool 成功率、Latency P95）

---

## 三、選定參賽主題：合約／採購法遵審閱助理

- **一句話定位**  
  「幫企業自動閱讀合約與採購文件，標出風險條款、給出修改建議，並附上可追溯的條文出處。」

- **核心價值**
  - 降低合約／採購文件審閱時間（例如由 1 小時降到 10 分鐘等可量化假設）
  - 降低漏看高風險條款的機率（付款條件、違約金、責任限制、保固、競業禁止、解約條款等）
  - 讓非法律背景的採購／業務能先做「第一輪審閱」，提供法務參考

- **典型流程**
  1. 使用者在 Streamlit 前端選擇「合約審閱模式」
  2. 上傳合約 PDF／採購文件（支援 `.pdf`，系統自動解析與 chunk）
  3. 系統將文件灌入向量庫（帶 `chat_id`，利於隔離對話來源）
  4. 使用者送出審閱請求（固定 prompt 或自由問題）
  5. 後端以合約／法遵專家 Agent 檢索 RAG context，產出：
     - 條款列表（類型、風險等級、原文節錄、建議改寫）
     - 風險分布圖表（ECharts）
     - 引用來源 chunk（可展開）

---

## 四、技術適配與差異化分析

### 4.1 已具備、可直接運用的能力

- RAG：支援長文件檢索與 rerank，適用於多頁合約／採購文件
- 檔案上傳與 PDF ingest：`streamlit_app.py` 已支援 `.pdf` 解析與 chunk → `rag_ingest` 流程共用
- 專家 Agent 模式：`expert_agents.py` 中已有 ESG／法遵與財報專家，可平行新增合約專家
- 工具路由：`agent_router.py` 中 `_decide_tool`＋`SUPPORTED_TOOLS` 已形成可擴充的 tool orchestration 架構
- 視覺化：ECharts（含 MCP PNG 輸出），可做風險分布、條款類型統計等圖表
- Eval 能力：可對固定題集（示範合約）做 routing / answer 品質評估，提供「作品完整性」佐證

### 4.2 需補強的關鍵點（針對法遵／合約領域）

- **領域專家 Agent（contract_risk_agent）**
  - 輸入：使用者問題（例如「請審閱這份合約，列出風險條款與修改建議」）＋ RAG 檢索結果
  - 輸出：結構化結果（JSON 或 markdown 表格），包括：
    - 條款類型（付款條件、違約金、責任限制、保固、競業禁止、解約條款等）
    - 風險等級（高／中／低）與風險說明
    - 條文原文節錄與出處（source#chunk）
    - 建議修改方向／替代文字示意

- **結構化輸出與 UI 呈現**
  - 文字回答外，前端應顯示：
    - 條款清單（可點開看原文節錄）  
    - 風險等級與類型統計（ECharts 長條／圓餅圖）  
    - 檢索片段展開區（已在現有 Streamlit 中提供）

- **治理與合規敘事（在作品說明書中說清楚）**
  - 模型角色定位：**輔助審閱工具**，不是提供最終法律意見  
  - 嚴格模式：可只根據已上傳文件回答、不補外部世界知識  
  - 部署選項：未來可支援 on-prem／VPC 版向量庫與推論服務，避免敏感合約出境

---

## 五、與評分項目的對應策略

### 5.1 產業應用性（40%）

- 目標客群：  
  - 中大型製造業、科技業、系統整合商之採購與法務部門  
  - 有大量供應商合約與專案合約審閱需求的企業
- 價值主張：
  - 審閱時間下降（假設數據，可在說明書與影片中舉例）
  - 減少遺漏風險條款，提高談判籌碼
  - 提供合約風險可視化報告，利於管理階層決策

### 5.2 技術創新性（30%）

- 多工具 Agent 路由：  
  - 以 `_decide_tool` 與 `SUPPORTED_TOOLS` 管理，例如：
    - 一般知識庫問答 → `rag_search`  
    - 合約審閱 → `contract_risk_agent`  
    - 需要圖表 → `analyze_and_chart` + `create_chart`
- RAG 流程優化：
  - `RAG_INTERNAL_TOP_K`、`MMR`、`LLM rerank` 等設計，可作為「檢索效果優化」研究與實務調參依據
- Eval 與可觀測性：
  - Eval log 與 batch eval，支援 routing 準確率、tool 成功率、Latency 等指標，有利於後續產學合作或產品化

### 5.3 作品完整性（30%）

- 功能面：  
  - 上傳合約／內規文件  
  - 合約審閱（風險條款列表＋建議＋引用出處）  
  - 風險分布與條款類型圖表  
  - 評估報告（透過 Eval）  
- 技術驗證：  
  - 以數份示範合約與人工作為「標準答案」，比較系統標記與人工標記的一致程度（可在說明書中說明方法）

---

## 六、實作路線草案（針對後續改程式）

1. **合約風險領域建模**
   - 定義常見條款類別與風險項目  
   - 準備少量標註示範合約（用於 Demo 與 Eval）

2. **新增 `contract_risk_agent`**
   - 參考 `expert_agents.py` 既有結構  
   - 透過 `retrieve_only` 取得合約／內規相關 chunks，再由 LLM 產生結構化風險分析結果

3. **整合到 `agent_router.py`**
   - 在 `SUPPORTED_TOOLS` 加入 `contract_risk_agent`  
   - 在 `_decide_tool` 的 system prompt 中描述合約／採購相關語意對應此 tool

4. **前端（Streamlit）支援「合約審閱模式」**
   - 側欄或主畫面新增一個模式／按鈕  
   - 針對目前對話上傳的合約，固定呼叫 `contract_risk_agent` 並以表格＋圖表呈現

5. **Eval 題集與指標設計**
   - 某些條款預期應被標為「高風險」／「中風險」  
   - 以 `eval/run_eval.py` 跑批次，輸出 routing 准確率與 answer 長度等指標

---

## 七、作品說明書撰寫與簡報方向提示

在 5 頁作品說明書中建議結構：

1. **創意發想背景及概述**  
   - 企業合約／採購文件審閱耗時、風險高  
   - AI 進展讓 RAG + Agent 成為可行解決方案  
   - 系統目標：合約／採購法遵審閱助理
2. **作品功能簡介及特色**  
   - 合約文件上傳與知識庫化  
   - 條款風險偵測與建議  
   - 視覺化報告與可追溯引用  
   - Eval 與指標
3. **開發工具與技術**  
   - Gemini、Pinecone、LangGraph、Streamlit、Tavily、Firecrawl、ECharts 等  
   - 多工具 Agent 路由、RAG 流程設計、Eval 架構
4. **使用對象及環境**  
   - 企業採購／法務部門、顧問公司  
   - 部署在公司內網伺服器（可搭配 VPC 與私有向量庫）
5. **產業應用性**  
   - 節省工時、降低錯誤、提升談判籌碼  
   - 未來商業模式（SaaS／on-prem）
6. **結語**  
   - 未來計畫：更多法規領域、進階評估方法、與企業實際資料合作 PoC

本檔案可作為之後撰寫作品說明書、簡報與影片腳本的基礎備忘。

