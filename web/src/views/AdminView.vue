<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { ApiError } from "@/api/client";
import {
  getAdminDockerContainers,
  getAdminHealth,
  getAdminOllamaModels,
  getAdminServices,
  postAdminRestartServices,
  type AdminDockerContainer,
  type AdminOllamaModel,
  type AdminServiceStatus,
} from "@/api/admin";
import {
  getEvalBatchDetail,
  getEvalConfig,
  getEvalRuns,
  listEvalBatchRuns,
} from "@/api/eval";
import { postIngestUpload } from "@/api/ingest";
import { getSources } from "@/api/sources";
import ApiErrorBlock from "@/components/ui/ApiErrorBlock.vue";
import { pushToast } from "@/state/toast";
import type {
  EvalBatchDetailResponse,
  EvalRunEntry,
  HealthResponse,
  IngestUploadResponse,
} from "@/types/api";
import { parseSourceRow } from "@/utils/sourceEntry";

type SourceRow = { source: string; chunk_count: number; chat_id: string | null };

const fileInput = ref<HTMLInputElement | null>(null);
const uploadChatId = ref("");
const sourceFilterChatId = ref("");
const uploading = ref(false);

const health = ref<HealthResponse | null>(null);
const serviceRows = ref<AdminServiceStatus[]>([]);
const modelRows = ref<AdminOllamaModel[]>([]);
const dockerRows = ref<AdminDockerContainer[]>([]);
const sourceRows = ref<SourceRow[]>([]);
const uploadResult = ref<IngestUploadResponse | null>(null);

const loadingInfra = ref(false);
const loadingServices = ref(false);
const loadingModels = ref(false);
const loadingDocker = ref(false);
const restarting = ref(false);
const restartTarget = ref("");
const dockerEngineAvailable = ref<boolean | null>(null);

const loadingSources = ref(false);
const sourcesError = ref<unknown>(null);
const uploadError = ref<unknown>(null);

const onlineLimit = ref(120);
const evalLogEnabled = ref<boolean | null>(null);
const onlineRuns = ref<EvalRunEntry[]>([]);
const batchRunIds = ref<string[]>([]);
const selectedRunId = ref("");
const batchDetail = ref<EvalBatchDetailResponse | null>(null);

const loadingConfig = ref(false);
const loadingRuns = ref(false);
const loadingBatch = ref(false);
const loadingDetail = ref(false);

const healthError = ref<unknown>(null);
const servicesError = ref<unknown>(null);
const modelsError = ref<unknown>(null);
const dockerError = ref<unknown>(null);
const restartError = ref<unknown>(null);
const configError = ref<unknown>(null);
const onlineError = ref<unknown>(null);
const batchError = ref<unknown>(null);
const detailError = ref<unknown>(null);

function asList<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function trimOrNull(value: string): string | null {
  const v = value.trim();
  return v ? v : null;
}

function shortText(value: string | undefined, max = 90): string {
  const text = (value || "").trim();
  if (!text) {
    return "-";
  }
  return text.length <= max ? text : `${text.slice(0, max)}...`;
}

function toPrettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function notifyApiError(prefix: string, err: unknown): void {
  if (err instanceof ApiError) {
    pushToast({
      variant: "error",
      code: err.code,
      message: `${prefix}：${err.message}`,
      details: err.details,
    });
    return;
  }
  pushToast({
    variant: "error",
    message: `${prefix}：${err instanceof Error ? err.message : String(err)}`,
  });
}

async function loadHealth() {
  healthError.value = null;
  try {
    health.value = await getAdminHealth({ showLoading: false });
  } catch (err) {
    health.value = null;
    healthError.value = err;
    notifyApiError("讀取健康檢查失敗", err);
  }
}

async function loadServices() {
  loadingServices.value = true;
  servicesError.value = null;
  try {
    const res = await getAdminServices({ showLoading: true });
    serviceRows.value = asList(res.services);
  } catch (err) {
    serviceRows.value = [];
    servicesError.value = err;
    notifyApiError("讀取服務狀態失敗", err);
  } finally {
    loadingServices.value = false;
  }
}

async function loadOllamaModels() {
  loadingModels.value = true;
  modelsError.value = null;
  try {
    const res = await getAdminOllamaModels({ showLoading: true });
    modelRows.value = asList(res.models);
    if (res.error) {
      throw new Error(res.error);
    }
  } catch (err) {
    modelRows.value = [];
    modelsError.value = err;
    notifyApiError("讀取 Ollama 模型失敗", err);
  } finally {
    loadingModels.value = false;
  }
}

async function loadDockerContainers() {
  loadingDocker.value = true;
  dockerError.value = null;
  try {
    const res = await getAdminDockerContainers({ showLoading: true });
    dockerEngineAvailable.value = res.engine_available;
    dockerRows.value = asList(res.containers);
    if (res.error) {
      throw new Error(res.error);
    }
  } catch (err) {
    dockerRows.value = [];
    dockerEngineAvailable.value = false;
    dockerError.value = err;
    notifyApiError("讀取 Docker 容器失敗", err);
  } finally {
    loadingDocker.value = false;
  }
}

async function refreshInfrastructure() {
  loadingInfra.value = true;
  await Promise.all([loadHealth(), loadServices(), loadOllamaModels(), loadDockerContainers()]);
  loadingInfra.value = false;
}

async function restartServices(target: string[]) {
  restarting.value = true;
  restartError.value = null;
  restartTarget.value = target.join(", ");
  try {
    const res = await postAdminRestartServices(target, { showLoading: true });
    serviceRows.value = asList(res.services);
    if (res.failed_services.length) {
      throw new Error(`重啟失敗：${res.failed_services.join(", ")}`);
    }
    pushToast({
      variant: "info",
      message: `已重啟：${res.restarted_services.join(", ") || "無"}`,
    });
  } catch (err) {
    restartError.value = err;
    notifyApiError("重啟服務失敗", err);
  } finally {
    restarting.value = false;
    restartTarget.value = "";
    await loadServices();
  }
}

async function loadSources() {
  loadingSources.value = true;
  sourcesError.value = null;
  try {
    const cid = trimOrNull(sourceFilterChatId.value);
    const res = await getSources(cid, { showLoading: true });
    sourceRows.value = asList(res.entries).map((row) =>
      parseSourceRow(row as Record<string, unknown>),
    );
  } catch (err) {
    sourceRows.value = [];
    sourcesError.value = err;
    notifyApiError("讀取知識來源失敗", err);
  } finally {
    loadingSources.value = false;
  }
}

async function submitUpload() {
  const input = fileInput.value;
  const files = input?.files;
  if (!files?.length || uploading.value) {
    return;
  }

  uploading.value = true;
  uploadError.value = null;
  uploadResult.value = null;
  try {
    const list = Array.from(files);
    uploadResult.value = await postIngestUpload(list, trimOrNull(uploadChatId.value), {
      showLoading: true,
    });
    if (input) {
      input.value = "";
    }
    await loadSources();
    pushToast({ variant: "info", message: "檔案上傳與 ingest 已完成。" });
  } catch (err) {
    uploadError.value = err;
    notifyApiError("上傳 / ingest 失敗", err);
  } finally {
    uploading.value = false;
  }
}

async function loadConfig() {
  loadingConfig.value = true;
  configError.value = null;
  try {
    const res = await getEvalConfig({ showLoading: false });
    evalLogEnabled.value = res.eval_log_enabled;
  } catch (err) {
    evalLogEnabled.value = null;
    configError.value = err;
    notifyApiError("讀取 EVAL 設定失敗", err);
  } finally {
    loadingConfig.value = false;
  }
}

async function loadOnlineRuns() {
  loadingRuns.value = true;
  onlineError.value = null;
  try {
    const res = await getEvalRuns(onlineLimit.value, { showLoading: true });
    evalLogEnabled.value = res.eval_log_enabled;
    onlineRuns.value = asList(res.entries);
  } catch (err) {
    onlineRuns.value = [];
    onlineError.value = err;
    notifyApiError("讀取線上 EVAL 紀錄失敗", err);
  } finally {
    loadingRuns.value = false;
  }
}

async function loadBatchRuns() {
  loadingBatch.value = true;
  batchError.value = null;
  try {
    const res = await listEvalBatchRuns({ showLoading: true });
    batchRunIds.value = asList(res.run_ids);
    if (!batchRunIds.value.length) {
      selectedRunId.value = "";
      batchDetail.value = null;
      return;
    }
    if (!batchRunIds.value.includes(selectedRunId.value)) {
      selectedRunId.value = batchRunIds.value[0] || "";
    }
  } catch (err) {
    batchRunIds.value = [];
    selectedRunId.value = "";
    batchDetail.value = null;
    batchError.value = err;
    notifyApiError("讀取批次 run 清單失敗", err);
  } finally {
    loadingBatch.value = false;
  }
}

async function loadBatchDetail(runId: string) {
  if (!runId) {
    batchDetail.value = null;
    return;
  }
  loadingDetail.value = true;
  detailError.value = null;
  try {
    batchDetail.value = await getEvalBatchDetail(runId, { showLoading: true });
  } catch (err) {
    batchDetail.value = null;
    detailError.value = err;
    notifyApiError("讀取批次 run 詳細資料失敗", err);
  } finally {
    loadingDetail.value = false;
  }
}

async function refreshEvalAll() {
  await Promise.all([loadConfig(), loadOnlineRuns(), loadBatchRuns()]);
}

const metricsText = computed(() => {
  if (!batchDetail.value?.metrics) {
    return "目前沒有 metrics 資料。";
  }
  return toPrettyJson(batchDetail.value.metrics);
});

const resultCount = computed(() => asList(batchDetail.value?.results).length);

const previewResultsText = computed(() => {
  const rows = asList(batchDetail.value?.results).slice(0, 20);
  if (!rows.length) {
    return "目前沒有結果資料。";
  }
  return rows.map((row) => toPrettyJson(row)).join("\n\n");
});

watch(selectedRunId, (runId) => {
  void loadBatchDetail(runId);
});

void Promise.all([refreshInfrastructure(), loadSources(), refreshEvalAll()]);
</script>

<template>
  <div class="page">
    <header class="ds-page-head">
      <h1 class="ds-page-title">管理後台（v1.0.0）</h1>
      <p class="ds-page-desc">
        集中查看健康檢查、服務狀態、Ollama 模型、知識來源、上傳 ingest 與 EVAL。
      </p>
    </header>

    <section class="ds-card card">
      <div class="toolbar">
        <div class="toolbar-actions">
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingInfra"
            @click="void refreshInfrastructure()"
          >
            {{ loadingInfra ? "更新中..." : "重新整理基礎狀態" }}
          </button>
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="restarting"
            @click="void restartServices([])"
          >
            {{ restarting && !restartTarget ? "重啟中..." : "重啟 API + 前端" }}
          </button>
          <RouterLink to="/chat" class="ds-btn ds-btn--secondary">
            前往對話
          </RouterLink>
        </div>
      </div>
      <p v-if="restarting" class="hint">重啟目標：{{ restartTarget || "預設服務組" }}</p>
      <ApiErrorBlock v-if="restartError" :error="restartError" title="重啟失敗" />
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">健康檢查</h2>
      </div>
      <ApiErrorBlock v-if="healthError" :error="healthError" title="健康檢查讀取失敗" />
      <div v-else-if="health" class="stat-grid">
        <div class="stat-item">
          <span class="k">狀態</span>
          <strong class="v">{{ health.status }}</strong>
        </div>
        <div class="stat-item">
          <span class="k">服務</span>
          <strong class="v">{{ health.service }}</strong>
        </div>
        <div class="stat-item">
          <span class="k">版本</span>
          <strong class="v">{{ health.version }}</strong>
        </div>
      </div>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">服務狀態與重啟</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingServices"
          @click="void loadServices()"
        >
          {{ loadingServices ? "讀取中..." : "重新整理服務狀態" }}
        </button>
      </div>
      <ApiErrorBlock v-if="servicesError" :error="servicesError" title="服務狀態查詢失敗" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>服務名稱</th>
            <th>主狀態</th>
            <th>子狀態</th>
            <th>啟用狀態</th>
            <th>描述 / 錯誤</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in serviceRows" :key="row.name">
            <td class="mono">{{ row.name }}</td>
            <td>{{ row.active_state }}</td>
            <td>{{ row.sub_state }}</td>
            <td>{{ row.unit_file_state }}</td>
            <td>{{ shortText(row.description || row.error || "", 80) }}</td>
            <td>
              <button
                type="button"
                class="ds-btn ds-btn--secondary"
                :disabled="restarting"
                @click="void restartServices([row.name])"
              >
                重啟
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">Ollama 模型</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingModels"
          @click="void loadOllamaModels()"
        >
          {{ loadingModels ? "讀取中..." : "重新整理模型列表" }}
        </button>
      </div>
      <ApiErrorBlock v-if="modelsError" :error="modelsError" title="Ollama 查詢失敗" />
      <div v-else-if="modelRows.length === 0" class="empty">
        目前沒有可顯示的模型。
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>名稱</th>
            <th>ID</th>
            <th>大小</th>
            <th>更新時間</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in modelRows" :key="row.name">
            <td>{{ row.name }}</td>
            <td class="mono">{{ row.model_id }}</td>
            <td>{{ row.size }}</td>
            <td>{{ row.modified }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">Docker 容器</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingDocker"
          @click="void loadDockerContainers()"
        >
          {{ loadingDocker ? "讀取中..." : "重新整理容器狀態" }}
        </button>
      </div>
      <p class="hint">
        Docker 引擎：
        <strong>{{ dockerEngineAvailable == null ? "未知" : dockerEngineAvailable ? "可用" : "不可用" }}</strong>
      </p>
      <ApiErrorBlock v-if="dockerError" :error="dockerError" title="Docker 查詢失敗" />
      <div v-else-if="dockerRows.length === 0" class="empty">
        目前沒有執行中的容器。
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>名稱</th>
            <th>映像檔</th>
            <th>狀態</th>
            <th>執行階段</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in dockerRows" :key="row.container_id">
            <td class="mono">{{ row.container_id }}</td>
            <td>{{ row.name }}</td>
            <td>{{ row.image }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.state }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">知識來源</h2>
        <div class="section-actions">
          <input
            v-model="sourceFilterChatId"
            type="text"
            class="ds-select text-input"
            placeholder="可選填 chat_id"
          >
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingSources"
            @click="void loadSources()"
          >
            {{ loadingSources ? "讀取中..." : "重新整理來源列表" }}
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="sourcesError" :error="sourcesError" title="知識來源查詢失敗" />
      <div v-else-if="sourceRows.length === 0" class="empty">
        目前沒有來源資料。
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>來源檔案</th>
            <th>切片數量</th>
            <th>chat_id</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in sourceRows" :key="`${row.source}-${idx}`">
            <td class="break">{{ row.source }}</td>
            <td>{{ row.chunk_count }}</td>
            <td class="mono">{{ row.chat_id || "-" }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">上傳 / Ingest</h2>
      </div>
      <form class="upload-form" @submit.prevent="void submitUpload()">
        <input
          ref="fileInput"
          type="file"
          class="file-input"
          multiple
          accept=".txt,.md,.pdf,.docx"
          :disabled="uploading"
        >
        <input
          v-model="uploadChatId"
          type="text"
          class="ds-select text-input"
          placeholder="可選填 chat_id"
          :disabled="uploading"
        >
        <button type="submit" class="ds-btn ds-btn--primary" :disabled="uploading">
          {{ uploading ? "上傳中..." : "上傳並寫入知識庫" }}
        </button>
      </form>
      <ApiErrorBlock v-if="uploadError" :error="uploadError" title="上傳 / ingest 失敗" />
      <div v-if="uploadResult" class="result-box">
        <p>已寫入切片數：<strong>{{ uploadResult.chunks_ingested }}</strong></p>
        <p>已更新來源數：<strong>{{ uploadResult.sources_updated?.length ?? 0 }}</strong></p>
      </div>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">EVAL</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingConfig || loadingRuns || loadingBatch"
          @click="void refreshEvalAll()"
        >
          重新整理 EVAL
        </button>
      </div>

      <p class="hint">
        EVAL_LOG：
        <strong>{{ evalLogEnabled == null ? "未知" : evalLogEnabled ? "啟用" : "停用" }}</strong>
      </p>
      <ApiErrorBlock v-if="configError" :error="configError" title="EVAL 設定讀取失敗" />

      <div class="section-head">
        <h3 class="sub-title">線上紀錄</h3>
        <div class="section-actions">
          <select v-model.number="onlineLimit" class="ds-select">
            <option :value="50">50</option>
            <option :value="120">120</option>
            <option :value="200">200</option>
            <option :value="500">500</option>
          </select>
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingRuns"
            @click="void loadOnlineRuns()"
          >
            重新整理線上紀錄
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="onlineError" :error="onlineError" title="線上紀錄查詢失敗" />
      <table v-else-if="onlineRuns.length" class="table">
        <thead>
          <tr>
            <th>時間</th>
            <th>工具</th>
            <th>延遲</th>
            <th>來源數</th>
            <th>問題</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in onlineRuns" :key="`${row.timestamp || 'na'}-${idx}`">
            <td class="mono">{{ row.timestamp || "-" }}</td>
            <td>{{ row.tool_name }}</td>
            <td>{{ row.latency_sec.toFixed(3) }}</td>
            <td>{{ row.source_count }}</td>
            <td>{{ shortText(row.question, 120) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty">目前沒有線上 EVAL 紀錄。</p>

      <div class="section-head">
        <h3 class="sub-title">批次執行</h3>
        <div class="section-actions">
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingBatch"
            @click="void loadBatchRuns()"
          >
            重新整理批次清單
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="batchError" :error="batchError" title="批次清單查詢失敗" />
      <div v-else-if="batchRunIds.length" class="section-actions">
        <select v-model="selectedRunId" class="ds-select run-select">
          <option v-for="id in batchRunIds" :key="id" :value="id">
            {{ id }}
          </option>
        </select>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingDetail || !selectedRunId"
          @click="void loadBatchDetail(selectedRunId)"
        >
          重新載入明細
        </button>
      </div>
      <p v-else class="empty">目前沒有批次執行紀錄。</p>

      <ApiErrorBlock v-if="detailError" :error="detailError" title="批次明細查詢失敗" />
      <div v-else-if="batchDetail" class="result-box">
        <p>run_id：<span class="mono">{{ batchDetail.run_id }}</span> ｜ 結果筆數：{{ resultCount }}</p>
        <h4 class="mini-title">metrics.json</h4>
        <pre class="json-block">{{ metricsText }}</pre>
        <h4 class="mini-title">results（前 20 筆）</h4>
        <pre class="json-block">{{ previewResultsText }}</pre>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.toolbar,
.section-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.section-actions,
.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

.section-title,
.sub-title,
.mini-title {
  margin: 0;
  color: var(--color-text-primary);
}

.sub-title {
  font-size: var(--text-body-size);
}

.mini-title {
  font-size: var(--text-caption-size);
}

.hint {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-body-sm-size);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(160px, 1fr));
  gap: var(--space-2);
}

.stat-item {
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-2);
  background: var(--color-bg-muted);
}

.stat-item .k {
  display: block;
  color: var(--color-text-muted);
  font-size: var(--text-caption-size);
}

.stat-item .v {
  color: var(--color-text-primary);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-body-sm-size);
}

.table th,
.table td {
  border: 1px solid var(--color-border-subtle);
  padding: var(--space-2);
  text-align: left;
  vertical-align: top;
}

.table th {
  background: var(--color-bg-muted);
  font-weight: 600;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-caption-size);
  word-break: break-all;
}

.break {
  word-break: break-word;
}

.empty {
  color: var(--color-text-muted);
  font-size: var(--text-body-sm-size);
}

.text-input {
  min-height: 36px;
  padding: var(--space-1) var(--space-2);
}

.run-select {
  min-width: 320px;
}

.upload-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.file-input {
  min-height: 36px;
  color: var(--color-text-secondary);
}

.result-box {
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--color-bg-muted);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.result-box p {
  margin: 0;
}

.json-block {
  margin: 0;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
  font-size: var(--text-caption-size);
}

@media (max-width: 880px) {
  .stat-grid {
    grid-template-columns: 1fr;
  }

  .run-select {
    min-width: 220px;
  }

  .table {
    display: block;
    overflow-x: auto;
  }
}
</style>
