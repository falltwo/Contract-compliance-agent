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
      message: `${prefix}: ${err.message}`,
      details: err.details,
    });
    return;
  }
  pushToast({
    variant: "error",
    message: `${prefix}: ${err instanceof Error ? err.message : String(err)}`,
  });
}

async function loadHealth() {
  healthError.value = null;
  try {
    health.value = await getAdminHealth({ showLoading: false });
  } catch (err) {
    health.value = null;
    healthError.value = err;
    notifyApiError("Failed to load health", err);
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
    notifyApiError("Failed to load service status", err);
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
    notifyApiError("Failed to load Ollama models", err);
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
    notifyApiError("Failed to load Docker containers", err);
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
      throw new Error(`Restart failed: ${res.failed_services.join(", ")}`);
    }
    pushToast({
      variant: "info",
      message: `Restarted: ${res.restarted_services.join(", ") || "(none)"}`,
    });
  } catch (err) {
    restartError.value = err;
    notifyApiError("Failed to restart service", err);
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
    notifyApiError("Failed to load sources", err);
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
    pushToast({ variant: "info", message: "Upload and ingest completed." });
  } catch (err) {
    uploadError.value = err;
    notifyApiError("Upload / ingest failed", err);
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
    notifyApiError("Failed to load EVAL config", err);
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
    notifyApiError("Failed to load online EVAL runs", err);
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
    notifyApiError("Failed to load batch run list", err);
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
    notifyApiError("Failed to load batch run detail", err);
  } finally {
    loadingDetail.value = false;
  }
}

async function refreshEvalAll() {
  await Promise.all([loadConfig(), loadOnlineRuns(), loadBatchRuns()]);
}

const metricsText = computed(() => {
  if (!batchDetail.value?.metrics) {
    return "No metrics found.";
  }
  return toPrettyJson(batchDetail.value.metrics);
});

const resultCount = computed(() => asList(batchDetail.value?.results).length);

const previewResultsText = computed(() => {
  const rows = asList(batchDetail.value?.results).slice(0, 20);
  if (!rows.length) {
    return "No result rows.";
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
      <h1 class="ds-page-title">Admin Console (v1.0.0)</h1>
      <p class="ds-page-desc">
        Unified operations page for health, services, Ollama, sources, ingest, and EVAL.
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
            {{ loadingInfra ? "Refreshing..." : "Refresh Infra" }}
          </button>
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="restarting"
            @click="void restartServices([])"
          >
            {{ restarting && !restartTarget ? "Restarting..." : "Restart API + Web" }}
          </button>
          <RouterLink to="/chat" class="ds-btn ds-btn--secondary">
            Open Chat
          </RouterLink>
        </div>
      </div>
      <p v-if="restarting" class="hint">Restart target: {{ restartTarget || "default set" }}</p>
      <ApiErrorBlock v-if="restartError" :error="restartError" title="Restart failed" />
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">Health</h2>
      </div>
      <ApiErrorBlock v-if="healthError" :error="healthError" title="Health check failed" />
      <div v-else-if="health" class="stat-grid">
        <div class="stat-item">
          <span class="k">status</span>
          <strong class="v">{{ health.status }}</strong>
        </div>
        <div class="stat-item">
          <span class="k">service</span>
          <strong class="v">{{ health.service }}</strong>
        </div>
        <div class="stat-item">
          <span class="k">version</span>
          <strong class="v">{{ health.version }}</strong>
        </div>
      </div>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">Service Status + Restart</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingServices"
          @click="void loadServices()"
        >
          {{ loadingServices ? "Loading..." : "Refresh Services" }}
        </button>
      </div>
      <ApiErrorBlock v-if="servicesError" :error="servicesError" title="Service query failed" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>service</th>
            <th>active</th>
            <th>sub</th>
            <th>enabled</th>
            <th>description / error</th>
            <th>action</th>
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
                Restart
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="ds-card card">
      <div class="section-head">
        <h2 class="section-title">Ollama Models</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingModels"
          @click="void loadOllamaModels()"
        >
          {{ loadingModels ? "Loading..." : "Refresh Models" }}
        </button>
      </div>
      <ApiErrorBlock v-if="modelsError" :error="modelsError" title="Ollama query failed" />
      <div v-else-if="modelRows.length === 0" class="empty">
        No models found.
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>name</th>
            <th>id</th>
            <th>size</th>
            <th>modified</th>
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
        <h2 class="section-title">Docker Containers</h2>
        <button
          type="button"
          class="ds-btn ds-btn--secondary"
          :disabled="loadingDocker"
          @click="void loadDockerContainers()"
        >
          {{ loadingDocker ? "Loading..." : "Refresh Containers" }}
        </button>
      </div>
      <p class="hint">
        Docker engine:
        <strong>{{ dockerEngineAvailable == null ? "unknown" : dockerEngineAvailable ? "available" : "unavailable" }}</strong>
      </p>
      <ApiErrorBlock v-if="dockerError" :error="dockerError" title="Docker query failed" />
      <div v-else-if="dockerRows.length === 0" class="empty">
        No running container found.
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>id</th>
            <th>name</th>
            <th>image</th>
            <th>status</th>
            <th>state</th>
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
        <h2 class="section-title">Knowledge Sources</h2>
        <div class="section-actions">
          <input
            v-model="sourceFilterChatId"
            type="text"
            class="ds-select text-input"
            placeholder="Optional chat_id"
          >
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingSources"
            @click="void loadSources()"
          >
            {{ loadingSources ? "Loading..." : "Refresh Sources" }}
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="sourcesError" :error="sourcesError" title="Sources query failed" />
      <div v-else-if="sourceRows.length === 0" class="empty">
        No source rows found.
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>source</th>
            <th>chunk_count</th>
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
        <h2 class="section-title">Upload / Ingest</h2>
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
          placeholder="Optional chat_id"
          :disabled="uploading"
        >
        <button type="submit" class="ds-btn ds-btn--primary" :disabled="uploading">
          {{ uploading ? "Uploading..." : "Upload and Ingest" }}
        </button>
      </form>
      <ApiErrorBlock v-if="uploadError" :error="uploadError" title="Upload / ingest failed" />
      <div v-if="uploadResult" class="result-box">
        <p>chunks_ingested: <strong>{{ uploadResult.chunks_ingested }}</strong></p>
        <p>sources_updated: <strong>{{ uploadResult.sources_updated?.length ?? 0 }}</strong></p>
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
          Refresh EVAL
        </button>
      </div>

      <p class="hint">
        EVAL_LOG:
        <strong>{{ evalLogEnabled == null ? "unknown" : evalLogEnabled ? "enabled" : "disabled" }}</strong>
      </p>
      <ApiErrorBlock v-if="configError" :error="configError" title="EVAL config failed" />

      <div class="section-head">
        <h3 class="sub-title">Online Runs</h3>
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
            Refresh Online Runs
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="onlineError" :error="onlineError" title="Online runs query failed" />
      <table v-else-if="onlineRuns.length" class="table">
        <thead>
          <tr>
            <th>timestamp</th>
            <th>tool</th>
            <th>latency</th>
            <th>sources</th>
            <th>question</th>
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
      <p v-else class="empty">No online EVAL records found.</p>

      <div class="section-head">
        <h3 class="sub-title">Batch Runs</h3>
        <div class="section-actions">
          <button
            type="button"
            class="ds-btn ds-btn--secondary"
            :disabled="loadingBatch"
            @click="void loadBatchRuns()"
          >
            Refresh Batch List
          </button>
        </div>
      </div>
      <ApiErrorBlock v-if="batchError" :error="batchError" title="Batch list query failed" />
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
          Reload Detail
        </button>
      </div>
      <p v-else class="empty">No batch run found.</p>

      <ApiErrorBlock v-if="detailError" :error="detailError" title="Batch detail query failed" />
      <div v-else-if="batchDetail" class="result-box">
        <p>run_id: <span class="mono">{{ batchDetail.run_id }}</span> | results: {{ resultCount }}</p>
        <h4 class="mini-title">metrics.json</h4>
        <pre class="json-block">{{ metricsText }}</pre>
        <h4 class="mini-title">results (first 20 rows)</h4>
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
