# Contract Compliance Agent — UX/UI 設計 Critique

> 目標對象：合約法務 / 內控審閱使用者
> 範圍：Vue 3 前端（`web/`），含 ChatView / SourcesView / UploadView / EvalView / AdminView
> 日期：2026-04-22
> 視角：資訊架構、互動流程、視覺層級、錯誤與載入狀態、可掃描性（scannability）

---

## Overall Impression

三欄式工作面板（文件預覽 / 對話流 / Risk Rail）是正確的合約審閱骨架，最近導入的結構化 Risk Card + 合約流程 stepper 已顯著提升可掃描性與進度可感知性。**但主要摩擦點仍在「Risk Card → 原文條款 → chunk 引用」三點之間的跳轉斷裂**：使用者看到風險卡後，還是需要自行在預覽面板滑動找條文；其次，多對話側欄、刪除 UX、空狀態與載入狀態都偏技術化，對法務使用者不夠友善。

---

## Top Findings

| # | 區塊 | 問題 | Severity | 建議 |
|---|------|------|----------|------|
| F1 | ChatView / Risk Rail | Risk Card 無法點擊跳到預覽面板對應條款；`chunkHint` 已有資料但沒連動 | 🔴 Critical | 卡片加 `@click` → emit scrollTo(article/chunkHint) → PreviewPane 高亮該段 |
| F2 | Document Preview | 分頁以 PDF 頁碼切，合約常見「一條跨兩頁」被切斷 | 🔴 Critical | 改以「第 X 條」為單位切分（backend 已能判別），或提供「按條款瀏覽」切換 |
| F3 | 所有 markdown 渲染點 | `marked` + `v-html` 無 sanitize，LLM 回應若含 `<script>` / `<img onerror>` 即 XSS | 🔴 Critical | 引入 DOMPurify，或改用 `markdown-it` + 白名單 |
| F4 | 對話側欄 | 刪除對話為「同一顆按鈕點兩次確認」，無差異化視覺回饋 | 🔴 Critical | 第二次點擊改紅色變體 + 3 秒 auto-cancel，或改用 Modal |
| F5 | Risk Rail | 無排序/篩選；20+ 張卡只能滾動 | 🟡 Moderate | 加 `[全部 / 高 / 中 / 低]` filter pills + 「按條款號 / 按嚴重度」排序切換 |
| F6 | 進度 stepper | 只有合約+法條路徑有 3-stage stepper；RAG / research 等長流程只剩 3 個點 | 🟡 Moderate | 抽象化成 `StageTimeline` 元件，各 tool 對應 1~N stages |
| F7 | 空狀態 | 首次開啟 ChatView / SourcesView 都是純文字提示，無 illustration / sample prompts | 🟡 Moderate | 加入 3 組 quick-start prompt chips：「審閱此合約」「找保密條款風險」「比對採購法」 |
| F8 | EvalView | 內容僅是 AdminView 換個路由，對外使用者無用 | 🟡 Moderate | 合併為 Admin 子頁籤，或以權限隱藏；避免導覽干擾 |
| F9 | 串流指示 | 單行 3 點動畫對 screen reader 不友善（無 `role="status"`） | 🟡 Moderate | 外層加 `role="status"` + `aria-live="polite"`；stepper 也需標記 |
| F10 | Risk 評分 | 風險分數計算公式黑箱，使用者不知「高」是 3 分還是 9 分 | 🟡 Moderate | hover 顯示 tooltip：「高風險 3 張 × 3 + 中 2 × 2 = 13」 |
| F11 | AdminView | 載入時直接空白，無 skeleton | 🟡 Moderate | 加 skeleton rows（3~5 列）降低感知延遲 |
| F12 | UploadView | 上傳只顯示「上傳中…」無百分比 | 🟡 Moderate | 利用 XHR `onprogress` 顯示百分比 + 預估剩餘時間 |
| F13 | 側欄分區 | 對話列表 / 檔案列表 / 系統設定視覺差異不足，長時間使用易迷路 | 🟢 Minor | 加 section header 底色差異或 1px divider |
| F14 | 語氣提示 | 「語氣偵測」用關鍵字 heuristic，誤判率高（合約常見「應」「得」被判為嚴厲） | 🟢 Minor | 移除或改為 opt-in dev 工具 |
| F15 | Quick Actions | 首頁 quick actions 是靜態 4 顆按鈕，與實際使用頻率不符 | 🟢 Minor | 依用戶歷史動態排序，或讓使用者自訂 |
| F16 | 配色 | 深藍 navy 背景 + 高飽和紅 risk 在長時間使用下易視覺疲勞 | 🟢 Minor | 提供 light mode；或將 navy 降飽和 10~15% |
| F17 | Stepper 字級 | stepper label 與 body text 同字級，層級不明 | 🟢 Minor | label 降為 13px / 次要色；當前階段維持 14px |

---

## Quick Wins（低工 / 高收益，皆 < 0.5 day）

| 優先 | 項目 | 工作量 | 收益 |
|------|------|--------|------|
| 1 | **F1**：Risk Card 點擊 → 預覽面板滾動 + 高亮 | 2~3 小時 | 核心工作流跳轉補齊，使用者最有感 |
| 2 | **F5**：Risk Rail filter pills + 排序 | 2 小時 | 20+ 卡片變可用 |
| 3 | **F10**：Risk 分數 tooltip | 30 分鐘 | 除去黑箱感 |
| 4 | **F9**：SSE indicator a11y | 30 分鐘 | 合規 + screen reader 支援 |
| 5 | **F7**：Quick-start prompt chips | 1~2 小時 | 首次體驗大幅改善 |
| 6 | **F3**：DOMPurify | 1 小時 | 堵住 XSS 洞 |

---

## Trade-offs（需決策）

| 議題 | 選項 A | 選項 B | 建議 |
|------|--------|--------|------|
| Risk Card 點擊目標 | 跳到 preview 對應條文 | 開 popover 顯示原文 | A — 使用者需要「條款→全文脈絡」 |
| 預覽分段單位 | PDF 頁碼 | 合約條款 `第X條` | B（可加切換） |
| 多對話側欄 | 保留列表 | 改 tab 式切換 | 保留，但加「釘選」與搜尋 |
| EvalView 去留 | 合併進 Admin | 隱藏於權限後 | 合併 — 降低導覽噪音 |
| Quick Actions | 靜態 | 動態 | 靜態先，等 usage telemetry 再動態化 |
| Dark mode | 強制深色 | 提供 light toggle | 提供 toggle |

---

## What Works Well

- **三欄比例（文件 / 對話 / Risk Rail）**：法務實際翻閱合約時正是這個視線動線
- **合約+法條 stepper** 的 active→done 推進語意清楚，比純文字「處理中…」強非常多
- **judicial.gov.tw 自動連結**：法條 chips 外連成熟法律資料庫，信任感強
- **嚴重度配色一致**：Risk Card / Rail / badge 三處紅橘綠一致，不用重新學習
- **停止按鈕紅色狀態**：streaming 時視覺變化明確，不會誤以為可送下一則
- **Risk Card parser fallback**：`extra.risk_cards` 無值時 regex markdown parse 漸進降級，使用者不會看到空白

---

## Priority Recommendations

1. **F1 + F5**：補齊 Risk Card → 原文跳轉 + 篩選排序（這兩個合在一起做，同時提升「找得到 + 看得到」）
2. **F3 + F9**：Sanitize + a11y，2 小時內搞定合規底線
3. **F7**：Quick-start prompts，降低首次使用門檻
4. **F2**：預覽改按條款切分，為 F1 鋪路
5. **F4**：刪除 UX 改成 Modal / 紅色變體二次確認
6. **F6**：進度 stepper 抽象化至其他長流程（research、multi-query RAG）

---

## 驗收建議

- F1 完成後，加一條 Playwright：Risk Card 點擊後預覽容器 `scrollTop` 應變動且對應段有 `.highlight` class
- F3 完成後，加 unit test：帶 `<script>` 的 LLM 回應渲染結果不含 `<script>` tag
- F9 完成後，以 axe-core 掃 ChatView，`role="status"` 區塊應通過 aria-live 規則

---

*本 critique 聚焦 UX/UI，程式風格建議從略。實作順序以 Priority Recommendations 為準。*
