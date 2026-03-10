---
name: data-analyst
description: 當使用者要求「分析資料、報表摘要、數據趨勢、建儀表板、處理 CSV/Excel、做資料視覺化」時使用。引導以結構化方式做資料分析、摘要與圖表，並可與本專案 data_analyst_agent、echarts、RAG 整合。
---

# 資料分析師（Data Analyst）

## When to Use

- 使用者說：**分析這份資料**、**報表摘要**、**數據趨勢**、**幫我分析 CSV/Excel**、**建儀表板**、**資料視覺化**、**data analysis**、**dashboard**
- 使用者要求對專案內 `data/` 或指定檔案的數據做統計、摘要、圖表或建議

## Instructions

當使用者要求與「資料分析、報表、數據、儀表板」相關的任務時，請依下列方式進行：

### 1. 釐清範圍與資料來源

- 確認資料來源：專案內 `data/` 目錄、使用者上傳、或知識庫（RAG）檢索內容。
- 本專案**端使用者**的「分析資料」已由 **data_analyst_agent**（在 `expert_agents.py`）處理：先從知識庫檢索，再以資料分析師 prompt 做摘要與重點發現；無需在此重複實作相同流程。
- 若需求是**開發端**（例如在 repo 內寫腳本分析 CSV、產出圖表、或改進 data_analyst_agent），則依下方步驟進行。

### 2. 資料分析流程建議

- **資料探索**：若有 CSV/Excel/JSON，先檢視欄位、型別、缺失值、基本統計（describe、value_counts）。
- **摘要與重點**：用條列或短段整理「資料摘要」「關鍵數字」「趨勢或異常」；區分「資料明確呈現」與「推論」。
- **視覺化**：本專案已有 **ECharts**（`echarts_tools.py`、`streamlit-echarts`、可選 ECharts MCP）；可建議長條、折線、圓餅、散點等圖型，並與既有 `create_chart` / `analyze_and_chart` 對齊。
- **安全**：若需執行使用者上傳或外部資料的程式碼，應在沙箱或白名單路徑內（例如僅允許 `data/`），避免任意執行。

### 3. 與本專案整合

- **Agent 端**：使用者問「幫我分析這份報表」時，由 `agent_router` 路由到 **data_analyst_agent**，無需改前端。
- **開發端**：若要新增「讀取 CSV 再分析」的 tool，可考慮在 `company_tools` 或 `expert_agents` 中擴充，並在 `agent_router.SUPPORTED_TOOLS` 與 `_decide_tool` 中註冊；或參考業界清單（`.cursor/業界等級清單_實作順序.md`）的擴充建議。
- **MCP**：若未來要接「資料庫查詢、自然語言轉 SQL」等，可搜尋 MCP 目錄（如 mcpradar.com）的 database / SQL 類 MCP，再於專案 MCP 設定中掛載。

### 4. 輸出建議

- 先給一兩句結論，再分「資料摘要／重點發現／建議圖表或後續分析」。
- 若有產出圖表或程式碼，註明檔案位置與使用方式。
- 可提醒：端上「分析資料」問題已可透過 **data_analyst_agent** 在對話中調用。

## 觸發範例

- 「分析 data 目錄裡的資料」
- 「幫我做報表摘要與趨勢」
- 「建一個銷售數據的儀表板」
- 「data analysis」「analyze this dataset」

上述說法會觸發本 SKILL，引導結構化資料分析並與專案 data_analyst_agent、echarts 整合。
