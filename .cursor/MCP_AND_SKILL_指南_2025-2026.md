# 2025–2026 熱門 MCP 與 SKILL 安裝指南

根據 2025 至 2026 年常見推薦整理，適合在 Cursor 使用的 **MCP 伺服器**與 **SKILL** 清單與安裝方式。

---

## 一、熱門 MCP 伺服器（2025–2026）

### 已為你加入的推薦 MCP（見 `.cursor/mcp.json`）

| 名稱 | 用途 | 備註 |
|------|------|------|
| **echarts** | 圖表（ECharts） | 你原本已有 |
| **sequential-thinking** | 結構化推理、拆解複雜問題 | Anthropic 官方，無需 API |
| **fetch** | 抓取網址內容並轉成 Markdown | 需聯網，適合讀網頁給 AI 用 |

### 其他熱門 MCP（可依需求手動加入）

#### 開發與文件

- **Context7**（約 23k+ stars）  
  - 用途：最新程式文件，給 LLM / AI 編輯器用。  
  - 安裝：`npx @upstash/context7-mcp@latest init --cursor` 或手動在 `mcp.json` 加：
  ```json
  "context7": {
    "command": "npx",
    "args": ["-y", "@upstash/context7-mcp"]
  }
  ```
  - 可選：到 [context7.com](https://context7.com) 申請 API key 後在 args 加 `"--api-key", "YOUR_KEY"`。

- **GitHub**（官方）  
  - 用途：倉庫、Issue、PR 等。  
  - 需在 Cursor 的 MCP 設定裡用 GitHub 登入或設定 token。

- **Markitdown**（Microsoft）  
  - 用途：多種檔案/網址轉成 Markdown 給 LLM 用。  
  - 安裝（NPX 版）：`"markitdown": { "command": "npx", "args": ["-y", "markitdown-mcp-npx"] }`  
  - 需 Python 3.10+ 與 Node.js。

#### 搜尋與爬取（你已有 Firecrawl SKILL，可互補）

- **Firecrawl**：搜尋、爬站、單頁抓取（需 API key，你已有 SKILL）。
- **Playwright**：瀏覽器自動化、網頁操作。
- **Exa / Perplexity**：即時網路搜尋（需各自 API key）。

#### 生產力與筆記

- **Notion**（官方）：筆記、專案管理整合。

#### 官方參考用（Anthropic）

- **Everything**：測試用，內含 prompts、resources、tools。
- **Filesystem**：檔案讀寫（注意權限）。
- **Git**：讀取/搜尋/操作 Git 倉庫。
- **Memory**：持久化記憶／知識圖。
- **Time**：時間與時區轉換。

---

## 二、MCP 設定檔位置（Windows）

- **使用者全域**：`%USERPROFILE%\.cursor\mcp.json`  
  例如：`C:\Users\USER\.cursor\mcp.json`
- **專案用**：專案內 `.cursor/mcp.json`（你目前是專案級設定）

編輯後需 **重新啟動 Cursor** 才會載入。

---

## 三、SKILL 是什麼、放哪裡

- **SKILL** = 可重複使用的指令檔（`SKILL.md`），讓 AI 在符合描述時自動套用。
- **位置**：
  - 全域：`~/.cursor/skills-cursor/`（所有專案）
  - 專案：`你的專案/.cursor/skills/`（僅此專案）

你目前專案已有：

- `.cursor/skills/firecrawl/SKILL.md` — 網頁爬取、搜尋、轉 Markdown。
- `.cursor/skills/frontend-design/SKILL.md` — 高品質前端介面設計與實作。
- `.cursor/skills/rag-streamlit-agent/SKILL.md` — RAG、Agent 路由、Streamlit、LangGraph／Pinecone／Gemini 專案慣例與模組職責。
- `.cursor/skills/analyze-project/SKILL.md` — **「分析這個專案」** 時觸發：依專案架構分析，並主動調用 MCP（sequential-thinking、fetch、memory、echarts）與其他 SKILL。

### 熱門 SKILL 類型（可自建或從社群找）

- **程式文件 / 框架**：例如 Next.js、React、Prisma 的慣例與範例。
- **程式碼風格**：命名、註解、測試撰寫方式。
- **除錯與重構**：錯誤排查步驟、重構檢查清單。
- **API / 後端**：REST/GraphQL 設計、認證、錯誤處理。

自建 SKILL 建議結構（`SKILL.md`）：

```markdown
---
name: 技能名稱
description: 何時使用（一句話，AI 會依此觸發）
---

# 標題
## When to Use
- 情境 1
- 情境 2
## Instructions
1. 步驟一
2. 步驟二
## Examples（可選）
...
```

---

## 四、針對 RAG / 生成式 AI Agent / Streamlit 的建議

你平常寫程式、做 **RAG**、**生成式 AI Agent** 與 **Streamlit**，以下組合特別適合。

### 已為你加入的 MCP（含本節新增）

| MCP | 用途 | 與你工作的關係 |
|-----|------|----------------|
| **echarts** | 圖表 | 你專案已有 streamlit-echarts + ECharts MCP，可產 PNG |
| **sequential-thinking** | 結構化推理 | Agent 拆解複雜問題、多步驟決策 |
| **fetch** | 抓網址轉 Markdown | RAG 資料來源、文件/網頁給 LLM 吃 |
| **memory** | 知識圖持久記憶 | Agent 跨對話記住實體、關係、偏好，適合多輪 agent |

### 強烈建議再加（RAG / Agent 常用）

- **Context7**  
  - 查 **LangChain / LangGraph、Streamlit、Pinecone、Gemini** 等最新文件與範例，寫 RAG 與 Agent 時少踩雷。  
  - 安裝：`npx @upstash/context7-mcp@latest init --cursor` 或在 `mcp.json` 加 `"context7": { "command": "npx", "args": ["-y", "@upstash/context7-mcp"] }`。

- **Markitdown**（Microsoft）  
  - 把 PDF、Office、網址轉成 Markdown，方便做 **RAG 灌庫**（ingest）或給 LLM 當 context。  
  - 需 Python 3.10+；NPX 版：`"markitdown": { "command": "npx", "args": ["-y", "markitdown-mcp-npx"] }`。

### 若用向量庫 / 資料庫做 RAG

- **Qdrant MCP**：你目前用 Pinecone；若之後加 Qdrant 或想讓 AI 查 Qdrant，可接 [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant)（多為 Python/uvx）。
- **MindsDB Vector Store MCP**：統一介面接多種向量庫（Pinecone、Qdrant、Chroma、Weaviate、PGVector 等），適合多後端 RAG 實驗。
- **PostgreSQL / 關聯式 DB MCP**：若 RAG 元資料或 Agent 狀態存 DB，可讓 Cursor 查表結構或寫查詢。

### Streamlit 開發

- 沒有專屬「Streamlit MCP」，但 **Context7** 可查 Streamlit 官方文件與範例。
- 你已有 **frontend-design** SKILL，做 Streamlit UI 時會自動套用高品質介面指引。
- 除錯時可用 **Fetch** 抓 Streamlit 文件頁面給 AI 看。

### 專案專用 SKILL（已為你新增）

- **rag-streamlit-agent**：當你提到 RAG、Agent、Streamlit、LangGraph、Pinecone、工具路由等時，AI 會依你專案慣例（StateGraph、retrieve → generate、agent_router、streamlit_app）給建議與程式碼。  
- 位置：`.cursor/skills/rag-streamlit-agent/SKILL.md`。

---

## 五、其他「適合你」的組合（通用）

1. **已加入**：`sequential-thinking`、`fetch`、`memory` — 推理 + 抓網頁 + Agent 記憶。
2. **若常查文件**：加 **Context7**（RAG/Agent/Streamlit 文件都很實用）。
3. **若用 GitHub 很多**：加 **GitHub** MCP。
4. **若常把 PDF/Office/網頁轉成 Markdown 做 RAG**：加 **Markitdown**。

SKILL 部分你已有 **firecrawl**、**frontend-design**，以及新的 **rag-streamlit-agent**。

---

## 六、參考連結

- [cursormcp.dev](https://cursormcp.dev/) — Cursor MCP 精選清單  
- [MCPServersList](https://mcpserverslist.com/) — 500+ MCP 一覽  
- [Context7 for Cursor](https://context7.com/docs/clients/cursor)  
- [Claude Skills 說明](https://design.dev/guides/claude-skills/)（SKILL 結構與觸發方式）

完成編輯 `mcp.json` 後記得 **重啟 Cursor**。
