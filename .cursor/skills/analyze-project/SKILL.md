---
name: analyze-project
description: 當使用者說「分析這個專案」「分析專案」「專案分析」「analyze this project」「幫我分析專案」時使用。依本專案架構進行分析，並主動調用可用的 MCP 與專案 SKILL。
---

# 專案分析（分析這個專案時觸發）

## When to Use

- 使用者說：**分析這個專案**、**分析專案**、**專案分析**、**幫我分析專案**、**analyze this project**
- 使用者要求對當前 repo 做架構說明、技術棧整理、模組職責或改進建議

## Instructions

當使用者要求「分析這個專案」時，請依下列方式進行，並**主動調用**可用的工具與上下文：

### 1. 調用 MCP（可依需要選用）

- **sequential-thinking**：若分析較複雜，用「結構化推理」拆解：先架構與邊界、再模組、再資料流與風險。
- **fetch**：若專案內有文件連結、README 連結、或需參考外部文件時，用 fetch 抓網址內容再納入分析。
- **memory**：若之前有分析過此專案，可先查 memory 是否有既有結論或偏好；分析完成後可把重點寫入 memory 供之後參考。
- **echarts**：若分析到圖表、儀表板或資料視覺化部分，可參考 ECharts 能力。

### 2. 依專案架構分析（與 rag-streamlit-agent 一致）

本專案是 **RAG + 多工具 Agent + Streamlit** 示範，分析時請涵蓋：

- **技術棧**：Gemini（LLM + embedding）、Pinecone（向量庫）、LangGraph（RAG StateGraph）、Streamlit（前端）、可選 Tavily / Firecrawl / ECharts。
- **核心模組**：`rag_graph.py`（檢索→生成）、`agent_router.py`（工具選擇與執行）、`streamlit_app.py`（介面）、`rag_ingest.py`（灌庫）、`expert_agents.py`（子 Agent）、`firecrawl_tools` / `echarts_tools`。
- **可選面向**：程式碼結構、環境變數與設定、可擴充點（新 tool、新專家 agent）、與 **firecrawl**、**frontend-design** 等 SKILL 的對應關係。

### 3. 輸出建議

- 先簡短總覽（一兩句），再分「架構／模組／資料流／改進或風險」等小節。
- 若有用到 MCP（例如 sequential-thinking 或 fetch），可在分析中註明「已透過 xxx 輔助」。
- 結尾可提醒：若要改 RAG、加新 tool 或新 Agent，可參考專案內 **rag-streamlit-agent** SKILL 的慣例。

## 觸發範例

- 「分析這個專案」
- 「幫我分析專案架構」
- 「分析專案並給改進建議」
- 「analyze this project」

上述任一說法都會觸發本 SKILL，並依上述指示調用 MCP 與專案架構進行分析。
