# FastAPI 後端（階段 1 骨架）

與專案根目錄的 `streamlit_app.py`、`agent_router.py`、`rag_graph.py` 等**並存**；設定沿用根目錄 `.env`（`CHAT_PROVIDER`、`EMBEDDING_PROVIDER`、`PINECONE_*`、`GOOGLE_API_KEY` 或 `OLLAMA_*` 等），另可選 `API_*` 變數控制 API 本身。

## 目錄邊界

| 路徑 | 用途 |
|------|------|
| `backend/main.py` | 建立 `app`、CORS、例外處理、掛載路由 |
| `backend/config.py` | `Settings`（pydantic-settings）+ `get_settings()` |
| `backend/api/deps.py` | `Depends(get_settings)` 型別別名 |
| `backend/api/routes/` | 各功能路由（`health`、`chat`、`ingest`、`stub`） |
| `ingest_service.py`（專案根） | 與 Streamlit 共用：`ingest_file_items`／`ingest_uploaded_files` → Pinecone + `sources_registry` + BM25 |
| `backend/rag_clients.py` | `get_cached_rag_stack()` 快取連線（類 Streamlit cache） |
| `backend/services/ingest_adapter.py` | 大小／數量驗證後呼叫 `ingest_service` |
| `chat_service.py`（專案根） | Streamlit 與 API 共用：`answer_with_rag_and_log` → `route_and_answer` |
| `backend/services/chat_adapter.py` | HTTP DTO ↔ `chat_service` 薄轉換 |
| `backend/schemas/` | Pydantic 請求／回應與 `ErrorResponse` |
| `backend/exception_handlers.py` | 統一 `{ "error": { code, message, details } }` |

後續新增 `chat`、`ingest` 等路由時，建議：**業務邏輯仍呼叫既有模組**（例如 `agent_router.route_and_answer`），API 層只做驗證、對映 DTO、錯誤轉換。

## 啟動 FastAPI

在**專案根目錄**（`agent-demo-teamA/`）：

```bash
uv sync
uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

- 文件：`http://127.0.0.1:8000/docs`
- 健康檢查：`GET http://127.0.0.1:8000/health`
- 範例：`GET http://127.0.0.1:8000/api/v1/info`
- **對話（與 Streamlit 主流程一致）**：`POST http://127.0.0.1:8000/api/v1/chat`（見 OpenAPI `ChatRequest`／`ChatResponse`）
- **上傳灌入**：`POST /api/v1/ingest/upload`（multipart）；**來源列表**：`GET /api/v1/sources?chat_id=`

`API_HOST`／`API_PORT` 僅供設定讀取；實際 bind 以 uvicorn 為準。

## 串流（現況與建議）

目前 **`route_and_answer`／LangGraph 皆為「整段完成後回傳」**，與 Streamlit 相同；尚未實作 token 串流。

| 方案 | 說明 |
|------|------|
| **SSE（建議）** | 單向、基於 HTTP、瀏覽器 `EventSource` 與 Vue 生態成熟；FastAPI 可用 `StreamingResponse` 送 `text/event-stream`。適合**僅伺服器推播**（token、最後一段 metadata）。與現有「整段 JSON」可並存：先上 `POST /chat`，日後加 `POST /chat/stream`。 |
| **WebSocket** | 雙向長連線；若只需串流文字與結尾事件，複雜度與佈署較高（proxy 逾時、負載均衡 sticky）。較適合**需雙向互動**（中斷生成、即時傳參）時再考慮。 |

若未來實作 SSE，**事件格式範例**（每行一個 `data:` JSON，最後 `event: done`）：

```
event: meta
data: {"tool_name":"rag_search","chat_id":"..."}

event: token
data: {"text":"片段"}

event: result
data: {"answer":"...","sources":[],"chunks":[],"tool_name":"rag_search","extra":null}

event: done
data: {}
```

（實作時需將 `llm_client`／`generate_content` 改為可串流 iterator，或僅對最後一層串流；**圖表**仍建議以 `result` 事件一次給 `chart_option`／`chart_image_base64`，與現有 `extra` 相容。）

## 測試

```bash
uv sync --extra dev
uv run python -m pytest tests/test_chat_api.py tests/test_ingest_api.py -v
```

以 **`unittest.mock` patch `chat_service.route_and_answer`** 或 **`backend.services.ingest_adapter.ingest_file_items`**，驗證契約，不依賴真實金鑰／向量庫。

## 上傳灌入（階段 3）

### 與 `rag_ingest.py`／Streamlit 的對齊

| 項目 | `rag_ingest.py`（CLI） | Streamlit／本 API |
|------|------------------------|-------------------|
| 輸入 | 掃描 `RAG_DATA_DIR` 目錄內檔案 | 上傳位元組（可多檔） |
| `chat_id` | 無（全庫） | 可選；metadata 與 source 路徑 `uploaded/{chat_id}/{name}` |
| BM25 | `save_bm25_corpus` 全量覆寫 | `append_bm25_corpus` 追加（與原 Streamlit 相同） |
| registry | `update_registry_on_ingest` | 相同 |

**同步／非同步**：目前與 Streamlit 相同採 **同步**：請求在 embed + upsert 完成後才回傳（大檔可能逾時）。環境變數可調單檔／總量／檔數上限。若未來需 **job id**，可改為：接受上傳 → 寫入 `INGEST_TEMP_DIR` → 回 `202` + `job_id` → 背景執行 `ingest_file_items`（需 Redis／DB 記 job 狀態）。

### 限制與暫存目錄

- **允許副檔名**：`.txt`、`.md`、`.pdf`、`.docx`（與 Streamlit 一致）。
- **單檔／總量／檔數**：`INGEST_MAX_FILE_MB`、`INGEST_MAX_TOTAL_MB`、`INGEST_MAX_FILES`（見 `.env.example`）。
- **暫存**：預設 **不寫磁碟**，整段位元組在記憶體處理（與 `st.file_uploader` + `getvalue()` 一致）。`INGEST_TEMP_DIR` 保留給未來 spill 或背景 job；若啟用，建議每 job 使用子目錄 `uuid/`，完成後 **刪除整個子目錄**（成功或失敗皆清）。

### 安全性（設計／可逐步實作）

| 議題 | 建議 |
|------|------|
| 路徑穿越 | 僅使用 `Path(name).name` 並拒絕含 `..` 之檔名（`ingest_service.sanitize_upload_filename`）。 |
| MIME 與內容 | 可選加 `python-magic`／file 前綴檢查（魔數）與宣告副檔名交叉驗證；**注意**目前仍會完整讀入檔身以限制大小，極端情境可改為先讀少量位元組再決定是否繼續讀。 |
| 速率限制 | 建議 `slowapi` 或反向代理（nginx）限流：`/api/v1/ingest/upload` 按 IP／API key；另可限制每 `chat_id` 每日上傳量。 |
| 認證 | 生產環境應要求登入或 API key，避免公開寫入向量庫。 |

### OpenAPI 長相（摘要）

- **`POST /api/v1/ingest/upload`**  
  - **Request body**：`multipart/form-data`  
    - `files`：`array` of `File`（多個檔案欄位同名 `files`）  
    - `chat_id`：`string`（選填）  
  - **Response**：`application/json` → `IngestUploadResponse`  
    - `mode`：`"sync"`  
    - `chunks_ingested`：`integer`  
    - `sources_updated`：`array` of `{ source, chunk_count, chat_id }`  
    - `skipped_files`：`array` of `string`（副檔名或檔名不合法而略過的原始檔名）

- **`GET /api/v1/sources`**  
  - **Query**：`chat_id`（選填）  
  - **Response**：`{ "entries": [ ... ] }`（與 `list_sources` 一致）

### 前端上傳範例

**fetch**

```javascript
const form = new FormData();
form.append("chat_id", "chat-1");
for (const f of fileInput.files) {
  form.append("files", f); // 多檔同名欄位
}
const res = await fetch("http://127.0.0.1:8000/api/v1/ingest/upload", {
  method: "POST",
  body: form,
});
const data = await res.json();
// data.chunks_ingested, data.sources_updated, data.skipped_files
```

**axios**

```javascript
const form = new FormData();
form.append("chat_id", "chat-1");
files.forEach((f) => form.append("files", f));

const { data } = await axios.post(
  "http://127.0.0.1:8000/api/v1/ingest/upload",
  form,
  // 勿手動覆寫 Content-Type，需由瀏覽器帶入 multipart boundary
);
```

## 與 Streamlit 雙軌（過渡期）

1. **終端機 A**：`uv run streamlit run streamlit_app.py`（預設 `:8501`）
2. **終端機 B**：`uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`

兩者共用同一 `.env`、同一 Pinecone 與模型 provider 設定（Gemini 或 Ollama）；僅 UI 入口不同。Vue 開發伺服器預設 `http://localhost:5173`，請在 `.env` 設定 `API_CORS_ORIGINS`（已預設含 5173）。

## 錯誤格式

非 2xx 時 body 形如：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "請求參數驗證失敗",
    "details": []
  }
}
```

## CORS

- 預設允許：`http://localhost:5173`、`http://127.0.0.1:5173`
- 覆寫：環境變數 `API_CORS_ORIGINS`（逗號分隔，無空白或 trim 後使用）
