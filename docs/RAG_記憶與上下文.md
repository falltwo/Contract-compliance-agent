# RAG 記憶與上下文

本文件說明「使用者問題 → RAG → 記憶／回憶」的資料流與實作細節，使回答能**記得上下文**（指代、情境、多輪對話）。

---

## 一、整體流程

```
使用者輸入
    ↓
Streamlit 從當前對話 session 收集 messages
    ↓
整理成 history_for_model（role + content，僅 user/assistant）
    ↓
agent_router.route_and_answer(question=目前問題, history=history_for_model)
    ↓
├─ 路由：_decide_tool(question, history) 用最近 6 輪決定 tool
├─ RAG：run_rag(question, top_k, history, strict)
│       → rag_graph retrieve（可選：用 history 擴展檢索 query）
│       → rag_graph generate：prompt = 「對話歷史」+「目前問題」+「檢索內容」
└─ 專家：contract_risk_agent（合約法遵）/ financial_report_agent / esg_agent / data_analyst_agent(question, history=...)
         → 同樣將 history 納入 prompt
```

- **目前問題**：本輪使用者送出的那一句。
- **history**：本輪之前的所有 user/assistant 訊息（不含本輪），讓模型能解讀「它」「那個」「去年」「這家公司」等指代。

---

## 二、各層如何用 history

| 位置 | 用途 |
|------|------|
| **streamlit_app.py** | `current_conv["messages"]` → 篩成 `history_for_model`，傳給 `_answer_with_rag_and_log(history=...)`。 |
| **agent_router** | `_decide_tool` 用 `history[-6:]` 做工具路由；`run_rag` / 專家都帶入完整 `history`。 |
| **rag_graph.run_rag** | state 帶 `history`；`generate()` 只取最近 `RAG_MAX_HISTORY_TURNS` 輪（預設 12）拼成「對話歷史」放進 prompt。 |
| **rag_graph.retrieve** | 當 `RAG_USE_HISTORY_FOR_QUERY=1`（建議預設開啟）時，使用 **LLM 根據對話歷史智能重寫檢索問句 (Query Rewriting)**，將簡短或具代名詞的問題改寫成完整、獨立且適合向量檢索的查詢，避免如「謝謝，那下一條呢？」產生無效檢索。 |
| **expert_agents** | `_build_history_text(history)` 只取最近 `RAG_MAX_HISTORY_TURNS` 輪，拼成「對話歷史」+「目前問題」+「檢索內容」給各專家（含 **contract_risk_agent** 合約法遵專家）。 |

---

## 三、生成階段的「記得上下文」指示

- **rag_graph.generate()**（嚴格／非嚴格）  
  system 中已加入：  
  「請結合對話歷史理解『目前問題』的指代與情境（例如『它』『那個』『去年』『這家公司』），再根據檢索內容回答。」

- **專家 (contract_risk_agent / financial_report / esg / data_analyst)**  
  合約法遵專家 (contract_risk_agent) 採合約法遵審閱助理／合約審閱顧問的 system，強調指代消解、嚴格守法、細節回憶與來源標記；其餘專家 system 也有一條：  
  「請結合『對話歷史』理解目前問題的指代與情境，再根據檢索內容回答。」

這樣模型在生成時會明確被要求使用對話歷史來解析指代與情境。

---

## 三之一、主 RAG 流程：「調查員（RAG）+ 判官（主 Agent）」雙 Prompt 架構

主 RAG 為**兩次 LLM 呼叫**、**兩段獨立 system prompt**：

1. **retrieve** → 向量檢索 + rerank，產出 `context`。
2. **package（調查員）**：以 `_INVESTIGATOR_SYSTEM` 為 system，輸入「對話歷史 + 目前問題 + 檢索內容」，LLM 產出打包後的「原始條文脈絡」與「知識庫參考依據」，寫入 state `packaged_context`。若無檢索內容則直接 pass through。
3. **generate（判官）**：以 `_JUDGE_SYSTEM_STRICT` / `_JUDGE_SYSTEM_ADVISOR` 為 system，輸入「對話歷史 + 目前問題 + 檢索專家提供的脈絡（packaged_context）」，LLM 產出最終風險判定與回答。

圖：`retrieve → package → generate → END`。

---

## 四、環境變數（可選）

| 變數 | 說明 | 預設 |
|------|------|------|
| `RAG_MAX_HISTORY_TURNS` | 生成時納入的最近對話輪數（RAG + 專家共用） | 12 |
| `RAG_USE_HISTORY_FOR_QUERY` | 設為 `1`/`true`/`yes` 時，retrieve 使用 **LLM Query Rewriting**：根據對話歷史將簡短或代名詞問題改寫成完整檢索問句 | 建議 `1` 或 `true`（預設開啟） |
| `RAG_MULTI_QUERY` | 設為 `1`/`true`/`yes` 時，retrieve 會依主問句產出 **輔助檢索問句**（如「管轄法院」→ 輔助「雙方地址」「付款條件」），多查詢後合併去重，以補足交叉比對脈絡 | 未設定（關閉） |
| `RAG_AUX_QUERY_TOP_K` | 每個輔助問句檢索的 top_k | 8 |
| `RAG_AUX_QUERY_MAX` | 輔助問句最多幾筆 | 3 |
| `RAG_MERGE_CAP` | 多查詢合併後保留的候選數上限（再送 rerank） | 2×internal_top_k |

---

## 五、小結

- **記憶從哪來**：Streamlit 的 `conversations[active_conv_id]["messages"]`，每輪問答後都會 append，下一輪整段當成 history 傳下去。
- **回憶用在哪**：路由（最近 6 輪）、RAG 生成與專家生成（最近 N 輪）+ 明確的「結合對話歷史理解指代與情境」指示；建議開啟 `RAG_USE_HISTORY_FOR_QUERY` 以在 retrieve 階段用 LLM Query Rewriting 改寫短問／代名詞問句。

若你要加強「記得上下文」，可優先：  
1）確認 UI 有把同一對話的訊息都放在同一個 `current_conv["messages"]`；  
2）必要時調大 `RAG_MAX_HISTORY_TURNS`，並將 `RAG_USE_HISTORY_FOR_QUERY=1` 設為預設開啟。
