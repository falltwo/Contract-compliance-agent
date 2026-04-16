# DGX Contract Agent 使用與維運手冊

**版本：v1.2.0　更新日期：2026-04-17**
適用環境：NVIDIA DGX Spark / NVIDIA AI Workbench / Ollama / Tailscale

本手冊整理這個專案目前在 DGX 上的啟動方式、團隊日常操作、更新部署流程，以及常見維護與排錯指令。目標是讓組員只要照著這份文件，就能正常使用系統，也能在需要時協助維運。

---

## 一、系統用途與架構

這套系統目前已經部署成 DGX 上的常駐內部網站服務，主要由兩個部分組成：

- **後端**：FastAPI，提供健康檢查、聊天、檔案 ingest、來源查詢、管理後台與 EVAL API。
- **前端**：Vue，分成兩個網站：
  - 前台網站（給一般使用者）：聊天、上傳、來源頁
  - 後台網站（給管理者）：管理後台、EVAL
- **模型**：使用 DGX 本機上的 Ollama。
- **向量資料**：目前仍使用 Pinecone。
- **存取方式**：區網 IP 與 Tailscale IP。

---

## 二、目前可使用的網址

| 服務 | 區網 | Tailscale |
|---|---|---|
| 前台網站 | `http://192.168.0.103:4173/chat` | `http://100.106.23.28:4173/chat` |
| 後台網站 | `http://192.168.0.103:4174/admin` | `http://100.106.23.28:4174/admin` |
| API 健康檢查 | `http://192.168.0.103:8000/health` | `http://100.106.23.28:8000/health` |

**權限分工：**
- 一般使用者只使用前台網站（4173），看不到 Admin 路由。
- 管理者使用後台網站（4174）查看服務狀態、模型、來源、上傳 ingest 與 EVAL。

---

## 三、日常使用方式

### 1. 一般使用者

- 開瀏覽器進入前台網站：`/chat`、`/upload`、`/sources`。
- 如果要問合約問題，使用聊天頁。
- 如果要加入新的文件，使用上傳頁或管理後台的上傳區塊。
- 如果要確認模型、服務或 EVAL，請使用後台網站。

### 2. 管理後台可以做什麼

- 查看 API 與前端服務狀態。
- 單獨重啟 API、前端或預設服務組。
- 查看目前 Ollama 已安裝的模型。
- 查看 Docker 容器狀態。
- 查看知識來源清單與 chunk 數量。
- 上傳文件並觸發 ingest。
- 查看 EVAL 線上紀錄與批次結果。

---

## 四、第一次安裝或重建 DGX 服務

第一次在 DGX 上建立常駐服務時，請在 DGX 終端機執行：

```bash
cd ~/Code_space/Contract-compliance-agent
bash scripts/install_dgx_services.sh
```

這個腳本會做以下事情：

- 依照目前專案路徑與使用者資訊，產生 systemd service。
- 安裝 `contract-agent-api.service`
- 安裝 `contract-agent-web-frontend.service`
- 安裝 `contract-agent-web-admin.service`
- 啟用開機自動啟動。
- 預設立即重啟三個服務。

---

## 五、平常啟動、停止、重啟

### 1. 查看服務狀態

```bash
sudo systemctl status contract-agent-api.service --no-pager -l
sudo systemctl status contract-agent-web-frontend.service --no-pager -l
sudo systemctl status contract-agent-web-admin.service --no-pager -l
```

### 2. 啟動服務

```bash
sudo systemctl start contract-agent-api.service contract-agent-web-frontend.service contract-agent-web-admin.service
```

### 3. 停止服務

```bash
sudo systemctl stop contract-agent-api.service contract-agent-web-frontend.service contract-agent-web-admin.service
```

### 4. 重啟服務

```bash
sudo systemctl restart contract-agent-api.service contract-agent-web-frontend.service contract-agent-web-admin.service
```

### 5. 確認是否開機自啟

```bash
systemctl is-enabled contract-agent-api.service
systemctl is-enabled contract-agent-web-frontend.service
systemctl is-enabled contract-agent-web-admin.service
```

如果回傳 `enabled`，表示 DGX 重開機後會自動拉起服務。

---

## 六、正式更新與部署方式

### （一）自動部署（推薦，已設定）

只要有組員將變更合併進 GitHub `main`，GitHub Actions CI 會自動執行：

1. pytest（後端單元測試）
2. web build + TypeScript 檢查
3. Playwright E2E 測試

三個檢查全部通過後，CI 會透過 Tailscale 自動 SSH 進 DGX，執行部署腳本。**組員不需要手動操作 DGX。**

**確認部署是否成功：**
- 到 GitHub repo → Actions → 最新一筆 CI run，確認「Deploy to DGX」job 為綠色。
- 或直接測試 `http://127.0.0.1:8000/health` 是否回應 ok。

### （二）手動部署（緊急或 CI 有問題時使用）

若需要繞過 CI 直接在 DGX 部署，執行：

```bash
cd ~/Code_space/Contract-compliance-agent
bash scripts/deploy_contract_agent.sh
```

這個部署腳本會依序完成：

- `git pull --ff-only origin main`
- `uv sync`
- `cd web && npm ci && npm run build`
- `sudo systemctl daemon-reload && restart` 三個 service
- 每秒輪詢 `http://127.0.0.1:8000/health`，最多等 30 秒，服務正常才結束
- 部署成功後列出 LAN 與 Tailscale 的各服務連結

> **注意**：執行前請確認帳號已設定 `sudo NOPASSWD` 給 systemctl（安裝腳本 `install_dgx_services.sh` 會處理這項設定）。

---

## 七、SSH 連線方式

從管理端電腦連進 DGX（區網）：

```bash
ssh falltwo@192.168.0.103
```

走 Tailscale（不在同一區網時）：

```bash
ssh falltwo@100.106.23.28
```

---

## 七之一、自動部署前置設定（一次性，已完成）

> 日常不需要重做。若 DGX 重建或 SSH key 需要輪換時，請依下列步驟重新設定。

### 1. DGX 上產生 deploy 專用 SSH key

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" \
  -f ~/.ssh/github_actions_deploy -N ""
cat ~/.ssh/github_actions_deploy.pub >> ~/.ssh/authorized_keys
```

### 2. GitHub repo Secrets

前往 `https://github.com/falltwo/Contract-compliance-agent/settings/secrets/actions`

| Secret 名稱 | 內容 |
|---|---|
| `DEPLOY_SSH_PRIVATE_KEY` | `~/.ssh/github_actions_deploy` 私鑰全文 |
| `TAILSCALE_OAUTH_CLIENT_ID` | Tailscale OAuth client ID |
| `TAILSCALE_OAUTH_CLIENT_SECRET` | Tailscale OAuth client secret |
| `DGX_TAILSCALE_IP` | `100.106.23.28` |
| `DGX_SSH_USER` | `falltwo` |
| `DGX_HOST_KEY` | `ssh-keyscan -t ed25519 100.106.23.28` 的 ed25519 輸出 |

### 3. Tailscale admin

前往 `https://login.tailscale.com/admin`

- **OAuth client**：Scopes: Devices Write
- **ACL tagOwners**：加入 `"tag:ci": ["autogroup:admin"]`
- **ACL grants**：加入 `{"src": ["tag:ci"], "dst": ["100.106.23.28"], "ip": ["22"]}`

---

## 八、Tailscale 連線方式

如果組員不在同一個區網，而是要從外部網路使用 DGX 上的網站或 SSH，請改走 Tailscale。

### 1. 組員端要先完成的事

- 在自己的電腦安裝 Tailscale
- 登入和 DGX 同一個 tailnet / team
- 確認自己的 Tailscale 狀態是已連線

**團隊邀請連結：**
`https://login.tailscale.com/uinv/izZFMx1cfC21XdbT2bYvq11`

建議流程：
1. 先開啟上方邀請連結加入團隊
2. 再安裝並登入 Tailscale
3. 確認可以看到 DGX 所在的 tailnet 裝置

```bash
tailscale status
tailscale ip -4
```

### 2. 使用 Tailscale 存取網站

DGX 的 Tailscale IPv4：`100.106.23.28`

- 前台網站：`http://100.106.23.28:4173/chat`
- 後台網站：`http://100.106.23.28:4174/admin`
- API 健康檢查：`http://100.106.23.28:8000/health`

### 3. 使用 Tailscale 連 SSH

```bash
ssh falltwo@100.106.23.28
```

### 4. 在 DGX 上確認 Tailscale 是否正常

```bash
tailscale status
tailscale ip -4
sudo systemctl status tailscaled --no-pager -l
```

### 5. Tailscale 連不上時先檢查什麼

- 確認組員端已登入同一個 Tailscale 網路
- 確認 DGX 上 `tailscaled` 正常運作
- 確認 DGX 的 SSH 服務有啟動
- 確認使用的是 Tailscale IP，不是區網 IP
- 若網站打不開，先測 `http://100.106.23.28:8000/health`

```bash
tailscale status
tailscale ip -4
sudo systemctl status tailscaled --no-pager -l
sudo systemctl status ssh --no-pager -l
curl -sS http://100.106.23.28:8000/health
```

---

## 九、常用維護指令

```bash
# API 健康檢查
curl -sS http://127.0.0.1:8000/health

# SSH 狀態
sudo systemctl status ssh --no-pager -l

# Ollama 模型列表
ollama list

# Docker 狀態
sudo docker ps

# API journal
sudo journalctl -u contract-agent-api.service -n 200 --no-pager

# Web journal
sudo journalctl -u contract-agent-web-frontend.service -n 200 --no-pager
sudo journalctl -u contract-agent-web-admin.service -n 200 --no-pager
```

---

## 十、環境設定重點

目前部署使用的核心設定在 `.env`，至少要確認以下欄位正確：

```
CHAT_PROVIDER=ollama
OLLAMA_CHAT_MODEL=gemma3:27b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBED_MODEL=snowflake-arctic-embed2:568m
PINECONE_API_KEY=（Pinecone Key）
PINECONE_INDEX=weck06
API_CORS_ORIGIN_REGEX=^https?://(localhost|127\.0\.0\.1|192\.168\.[0-9]+\.[0-9]+|100\.[0-9]+\.[0-9]+\.[0-9]+)(:[0-9]+)?$
```

如果 `.env` 缺值或 Pinecone 金鑰錯誤，服務可能能啟動，但聊天或 ingest 會失敗。

### 共用環境保護規範（避免誤改）

1. DGX 共用環境的 `PINECONE_INDEX` 固定 `weck06`，只能由維運人員修改。
2. 組員若要做個人測試，只能在自己的本機 `.env` 做，不可改 DGX 共用 `.env` 基準。
3. 禁止把個人/臨時 Pinecone index 名稱提交到 `main`。

### 多模型自動分流（已支援）

使用者不需要在前端選模型，後端會依階段自動分配。建議在 `.env` 加上：

```
# 輕量快速（路由、改寫、重排）
OLLAMA_ROUTER_MODEL=gemma3:4b-it-qat
OLLAMA_RAG_REWRITE_MODEL=gemma3:4b-it-qat
OLLAMA_RAG_RERANK_MODEL=gemma3:4b-it-qat

# 高品質生成
OLLAMA_RAG_GENERATE_MODEL=gemma3:27b
OLLAMA_RESEARCH_GENERATE_MODEL=gemma3:27b
OLLAMA_ANALYSIS_MODEL=gemma3:27b

# 合約風險驗證（最強模型）
OLLAMA_CONTRACT_RISK_VERIFY_MODEL=gpt-oss:120b
```

### Timeout 建議

```
OLLAMA_TIMEOUT_SEC=120
OLLAMA_ROUTER_TIMEOUT_SEC=20
OLLAMA_RAG_REWRITE_TIMEOUT_SEC=20
OLLAMA_RAG_RERANK_TIMEOUT_SEC=25
OLLAMA_RAG_GENERATE_TIMEOUT_SEC=120
```

若優先速度，可加：

```
RAG_MMR_LAMBDA=0.6
```

---

## 十一、常見情境與處理方式

### 1. 網站打不開

- 先確認前台與後台服務是否為 `active (running)`。
- 前台看 `4173`，後台看 `4174`。
- 若前端有更新過但頁面沒變，先重新整理瀏覽器快取，再重跑部署腳本。

### 2. `/health` 失敗或 API 500

- 先看 `contract-agent-api.service` 狀態。
- 再看 `journalctl -u contract-agent-api.service`。
- 同時檢查 `.env` 是否存在，Ollama 是否正常，Pinecone Key 是否有效。

### 3. 管理後台看不到模型

- 確認 `ollama list` 是否有輸出。
- 確認 `ollama.service` 有啟動。
- 若模型尚未安裝，先在 DGX 手動 `ollama pull`。

### 4. 上傳文件成功，但查不到來源

- 檢查 ingest 是否成功完成。
- 檢查 Pinecone 是否可連線。
- 檢查資料是否被寫入指定的 index。

### 5. DGX 重開機後網站沒起來

- 檢查 `systemctl is-enabled` 是否仍為 `enabled`。
- 重新執行 `bash scripts/install_dgx_services.sh` 重新安裝 service。

### 6. 自動部署沒有觸發

- 確認 push 的是 `main` 分支，不是其他分支。
- 到 GitHub Actions 確認 CI 是否全部通過（pytest、web、playwright 都要綠）。
- 若 CI 有 job 失敗，修復後重新 push 即可觸發。

### 7. Tailscale 網址打不開，但區網可正常使用

- 先在 DGX 執行 `tailscale ip -4`，確認 Tailscale IP 沒有變。
- 若 IP 改了，要同步更新手冊與公告。
- 再確認 `tailscale status` 是否正常。
- 最後確認組員端是否已加入同一個 tailnet。

---

## 十二、建議的團隊分工方式

**一般組員：**
- 只使用網站，不直接改 DGX 系統設定。
- 如需更新功能，先 push 到 GitHub，CI 通過後自動部署。

**維運人員：**
- 確認 GitHub Actions 的「Deploy to DGX」job 有正常完成。
- CI 或自動部署失敗時，手動在 DGX 執行部署腳本排查。
- 檢查 service 狀態與 journal。
- 維護 `.env`、Ollama 模型、Tailscale 與 SSH。

**開發者：**
- 所有程式修改以 GitHub repo 為主，不直接把 DGX 當唯一版本來源。
- 若在 DGX 臨時 hotfix，之後也要補回 commit。

---

## 十三、快速操作清單

### 確認自動部署狀態

GitHub repo → Actions → 最新 CI run → Deploy to DGX

### 手動部署（緊急備用）

```bash
ssh falltwo@192.168.0.103
cd ~/Code_space/Contract-compliance-agent
bash scripts/deploy_contract_agent.sh
```

### Tailscale 外部連線

```bash
ssh falltwo@100.106.23.28
```

### 查服務

```bash
sudo systemctl status contract-agent-api.service --no-pager -l
sudo systemctl status contract-agent-web-frontend.service --no-pager -l
sudo systemctl status contract-agent-web-admin.service --no-pager -l
```

### 查模型

```bash
ollama list
```

### 查 API

```bash
curl -sS http://127.0.0.1:8000/health
```

### 網站入口

| | LAN | Tailscale |
|---|---|---|
| 前台 | `http://192.168.0.103:4173/chat` | `http://100.106.23.28:4173/chat` |
| 後台 | `http://192.168.0.103:4174/admin` | `http://100.106.23.28:4174/admin` |
| Health | `http://192.168.0.103:8000/health` | `http://100.106.23.28:8000/health` |

---

## 十四、文件維護方式

這份手冊的原稿與版本：

- **手冊原稿**：`Contract-compliance-agent/docs/DGX_網站使用與維運手冊_v1.2.md`
- **產 PDF 腳本**：`C:\Users\USER\Desktop\Code_space\build_dgx_manual_pdf.py`

未來如果網址、服務名稱、部署流程或版本有變動，請優先更新 Markdown 原稿，再重新產生 PDF。

### 版本歷史

| 版本 | 日期 | 主要變更 |
|---|---|---|
| v1.0.0 | 2026-04-15 | 初版 |
| v1.1.0 | 2026-04-16 | 多模型分流、Timeout 建議 |
| v1.2.0 | 2026-04-17 | 自動部署（GitHub Actions + Tailscale）、.gitignore 修正 |
