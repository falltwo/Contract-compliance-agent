# 更新總結（2026-04-15）

本文件整理本輪已完成並推送到 `main` 的更新，重點涵蓋：

- 版本已正式提升為 `v1.0.0`
- `weck06-0410`、`ewiwi` 兩個分支已併回 `main`
- 專案已支援 Ollama 作為本地聊天模型與 embedding provider
- 專案已補上 DGX `FastAPI + Vue` 的內部服務化部署方案
- 已完成一輪實機部署驗證與維運腳本強化
- 新增 `/admin` 管理後台頁，並將 EVAL 整合到同一頁

## 1. 近期 commit 摘要

| Commit | 類型 | 重點 |
|--------|------|------|
| `7349738` | merge | 將 `weck06-0410` 合併進 `main` |
| `0d285b3` | merge | 將 `ewiwi` 合併進 `main` |
| `8336e7a` | feat | 新增 Ollama chat 與 embedding provider wiring |
| `ea62084` | feat | 新增 DGX 內部服務部署、runtime API routing、systemd 模板與部署腳本 |
| `02fa0d0` | chore | 強化部署腳本健康檢查重試，降低服務剛重啟時的誤判失敗 |

## 2. 本地模型與 Ollama 支援

### 已完成

- `llm_client.py`
  - 支援 `CHAT_PROVIDER=ollama`
  - 以 Ollama 的 OpenAI-compatible chat endpoint 作為聊天模型來源
- `rag_common.py`
  - 支援 `EMBEDDING_PROVIDER=ollama`
  - 可使用本地 embedding 模型建立向量
  - 加入 Pinecone 維度檢查，降低模型維度不一致造成的錯誤
- `.env.example`、`README.md`、`backend/README.md`
  - 補上 Ollama 所需設定說明

### 建議設定

```env
CHAT_PROVIDER=ollama
OLLAMA_CHAT_MODEL=gemma3:27b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBED_MODEL=snowflake-arctic-embed2:568m
```

## 3. DGX 內部服務化部署

### 目前部署模式

採用 `systemd` 常駐服務，不使用 Docker。

- API service：`contract-agent-api.service`
- Web service：`contract-agent-web.service`
- 後端：`FastAPI + uvicorn`
- 前端：`Vue build + vite preview`

### 新增檔案

- `deploy/systemd/contract-agent-api.service.template`
- `deploy/systemd/contract-agent-web.service.template`
- `scripts/install_dgx_services.sh`
- `scripts/deploy_contract_agent.sh`

### 重要行為調整

- `web/src/api/client.ts`
  - production 會優先使用 `VITE_API_BASE_URL`
  - 若未設定，會自動以「目前瀏覽器主機 + API port」推導 API base
- `backend/config.py`、`backend/main.py`
  - 新增 `API_CORS_ORIGIN_REGEX`
  - 讓區網 IP、Tailscale IP、localhost 等來源可在部署環境中正常跨埠存取

## 4. 實際維運流程

DGX 上更新流程已收斂為：

```bash
cd ~/Code_space/Contract-compliance-agent
bash scripts/deploy_contract_agent.sh
```

這個腳本會自動完成：

1. `git pull --ff-only origin main`
2. `uv sync`
3. `cd web && npm ci && npm run build`
4. `systemctl restart contract-agent-api.service contract-agent-web.service`
5. API health check

## 5. 驗證結果

### 程式與契約層

- `uv run python -m pytest tests/test_chat_api.py tests/test_ingest_api.py -v`
  - 測試通過（6 tests）

### DGX 部署層

- `contract-agent-api.service`、`contract-agent-web.service`
  - 已確認 `enabled`
  - 已確認 `active`
- 內部健康檢查
  - `/health` 可回傳 `200`
- 對外存取
  - 已從 Windows 端驗證 LAN 與 Tailscale 路徑可存取前端與 API
- Chat smoke test
  - 已透過部署中的 `/api/v1/chat` 成功取得回應

## 6. 部署腳本補強

`scripts/deploy_contract_agent.sh` 已補上健康檢查重試：

- 新增 `HEALTH_TIMEOUT_SEC`，預設 `30`
- API 重啟後會在 timeout 內持續輪詢 `/health`
- 避免服務剛啟動、health endpoint 尚未 ready 時就直接判定部署失敗

## 7. 目前版本的操作重點

- 若要在 DGX 使用本地模型，優先採用 Ollama 設定
- 若要讓團隊直接透過網址使用，優先採用 `systemd` 常駐部署
- 目前版本不依賴 Docker
- GitHub `main` 應視為唯一版本來源；DGX 只做 `git pull + restart`

## 8. 建議後續工作

- 將 `vite preview` 升級為 Nginx 或 Caddy 靜態檔服務
- 若需要正式站型入口，可將 `/` 與 `/api` 收斂到單一反向代理
- 若 Pinecone 後續希望降成本或離線化，可再評估改為本地向量庫
- 若需要更正式的維運流程，可在現有部署腳本上再接 CI/CD
