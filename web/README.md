# Vue 前端（Vite）

合約／法遵審閱助理之 **Web MVP**（方案 A）。側欄已包含 **管理後台**（`/admin`），同頁整合服務狀態、Ollama 模型、來源清單、上傳 ingest、健康檢查與 EVAL。

## 同時啟動後端與前端

請開 **兩個終端機**（或 VS Code 兩個終端分頁），工作目錄皆為專案根目錄 `agent-demo-teamA`。

### 終端 1：FastAPI（`uvicorn`）

```powershell
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

- 健康檢查：<http://127.0.0.1:8000/health>
- OpenAPI：<http://127.0.0.1:8000/docs>

### 終端 2：Vite 開發伺服器

```powershell
cd web
npm install
npm run dev
```

瀏覽器開啟終端機顯示的網址（預設 **<http://localhost:5173>**）。

`vite.config.ts` 已將 `/api`、`/health` **proxy** 到 `http://127.0.0.1:8000`，前端請一律使用相對路徑（例如 `/api/v1/chat`），**不要**在前端寫死 `:8000`，以免與 CORS／部署環境不一致。

## 環境變數（`VITE_*`）

目前前端支援：

- `VITE_API_BASE_URL`：若設定，production 直接使用這個 API base
- `VITE_API_PORT`：若未設定 `VITE_API_BASE_URL`，production 會改用「目前瀏覽器主機 + 此 port」（預設 `8000`）
- `import.meta.env.BASE_URL`：給 Vue Router 的 `createWebHistory` 使用

DGX 常駐部署時，通常不必額外設定 `VITE_API_BASE_URL`；前端會自動用目前網址的 host，改連同主機的 `:8000`。

## 設計與 API

- 設計 tokens：`src/assets/direction-a-tokens.css`
- 錯誤格式與後端一致：`{ error: { code, message, details } }`（見 `src/api/client.ts`、`src/components/ui/ApiErrorBlock.vue`）

## OpenAPI 契約與前端型別

- 固定契約檔：`contracts/openapi.json`（由後端匯出，應納入版本控制）。
- 產生 TypeScript：`web/src/types/openapi.generated.ts`（**勿手改**）。
- 業務匯出：`web/src/types/api.ts` 自 `openapi.generated` 重新匯出 schema，並保留未列入 OpenAPI 的錯誤本文型別。

在 **`web/`** 目錄執行：

| 指令 | 說明 |
|------|------|
| `npm run openapi:export` | 自後端寫入 `../contracts/openapi.json`（需已安裝 `uv`，於 repo 根執行 `cd ..`） |
| `npm run openapi:gen` | 依契約檔產生 `openapi.generated.ts` |
| `npm run openapi:sync` | 上兩者依序執行（後端路由有變時請跑並提交） |
| `npm run contract:check` | 產生型別並 `vue-tsc`（與 CI 一致） |

## 本地測試（選修）

```powershell
# 專案根：pytest
uv run pytest

# web：Playwright（會自動啟動 uvicorn + vite，見 playwright.config.ts）
cd web
npm run test:e2e
```

## CI（GitHub Actions）

`.github/workflows/ci.yml` 包含：

1. **`pytest`**：`uv sync --extra dev` 後 `uv run pytest`
2. **`web`**：`npm run contract:check` + `npm run build`
3. **`playwright`**：Chromium E2E（`/health` + 對話頁 mock `POST /api/v1/chat`）

## 建置

```powershell
cd web
npm run build
```

產出於 `web/dist/`。

## DGX 常駐部署

DGX 上建議不要使用 `npm run dev`，而是：

```bash
cd web
npm run build
npm run preview -- --host 0.0.0.0 --port 4173
```

- 使用者入口：`http://<dgx-ip>:4173`
- 前端會自動呼叫 `http://<dgx-ip>:8000`
- 後端需同步允許該來源，建議在專案根 `.env` 設 `API_CORS_ORIGIN_REGEX`

## MVP 驗收自測（建議）

於後端與 `.env` 已正確設定（LLM、Pinecone 等）的前提下，手動依序驗證：

| 項目 | 作法 |
|------|------|
| 多對話 | 左側欄「對話」區：＋新對話、點選切換、✎重新命名、×刪除（確認）；重新整理後列表與訊息仍保留（localStorage） |
| Ingest | 「上傳灌入」選檔 → 灌入 → 檢視 `chunks_ingested` / `sources_updated` |
| 問答 | 「對話」輸入問題，助理回覆與參考連結／檢索片段是否正常 |
| `strict` | 側欄勾選「嚴格只根據知識庫回答」後送問，行為是否符合預期 |
| `rag_scope` | 有上傳之對話進入後是否預設「只搜尋本對話上傳」；可手動取消 |
| 澄清兩輪 | 觸發 `ask_web_vs_rag` 時依後續輪次帶 `original_question` / `clarification_reply`（見 Pinia pending） |
| 圖表其一 | 觸發含 `chart_option` 或 `chart_image_base64` 的回覆，圖表區是否顯示 |

**Phase 2（選修）**：後端同步對話標題／刪除遠端向量等（目前僅前端狀態 + localStorage）。
