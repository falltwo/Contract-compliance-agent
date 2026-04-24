<script setup lang="ts">
import { computed, defineAsyncComponent, reactive, ref, watch } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";

import ChunkOriginalPopover from "./ChunkOriginalPopover.vue";
import HighRiskCompareModal from "./HighRiskCompareModal.vue";

const ChartOptionMount = defineAsyncComponent(() => import("./ChartOptionMount.vue"));
import type { AssistantConversationMessage } from "@/types/conversation";
import type { ChunkItem } from "@/types/api";
import type { HighRiskClause } from "@/utils/contractHighRisk";
import {
  riskReasonFromChunk,
  sourceTextFromChunk,
  splitMainByHighRiskClauses,
} from "@/utils/contractHighRisk";
import { splitAnswerAndRefs } from "@/utils/splitAnswerRefs";

marked.use({ breaks: true });

const props = defineProps<{
  message: AssistantConversationMessage;
}>();

/**
 * 比對中文法規名稱 + 條號（含可選項/款）。
 * 例：民法第188條、政府採購法第50條第1項、勞動基準法第84條之1
 */
const LAW_REF_RE =
  /[\u4e00-\u9fff]{2,12}(?:法|條例|規則|辦法|細則)第\d+條(?:之\d+)?(?:第\d+[項款])?/g;

/** 法條引用前常見的動詞/介詞前綴，不屬於法律名稱本身，需從搜尋關鍵字中去除 */
const LAW_PREFIX_STRIP_RE = /^(?:參考|依照|依據|根據|違反|另參|另見|詳見|請參|如|依|按)\s*/;

/**
 * 從 regex 匹配結果中提取真正的法條引用（去除「參考」等前置詞），用於 URL 查詢。
 * 顯示文字保持原樣，只有送給法規資料庫的 kw 參數被清理。
 */
function extractLawSearchTerm(match: string): string {
  return match.replace(LAW_PREFIX_STRIP_RE, "");
}

/**
 * 將 HTML 字串中的法條引用包成可點擊的全國法規資料庫連結。
 * 已在 <a> 標籤內的文字不會重複處理。
 */
function linkLawRefs(html: string): string {
  // 以現有 <a>...</a> 為界分割，奇數索引塊已是連結，直接保留
  const chunks = html.split(/(<a\b[^>]*>[\s\S]*?<\/a>)/gi);
  return chunks
    .map((chunk, idx) => {
      if (idx % 2 === 1) return chunk;
      return chunk.replace(LAW_REF_RE, (match) => {
        const searchTerm = extractLawSearchTerm(match);
        const url = `https://law.moj.gov.tw/LawClass/LawSearchAll.aspx?kw=${encodeURIComponent(searchTerm)}`;
        return `<a href="${url}" target="_blank" rel="noopener noreferrer" class="law-ref-link">${match}</a>`;
      });
    })
    .join("");
}

function mdToHtml(src: string): string {
  const html = marked(src, { async: false }) as string;
  const linkedHtml = linkLawRefs(html);
  const plainHtml = linkedHtml.replace(/<a\b[^>]*>([\s\S]*?)<\/a>/gi, "$1");
  return DOMPurify.sanitize(plainHtml);
}

const split = computed(() => splitAnswerAndRefs(props.message.content));

const mainHtml = computed(() => mdToHtml(split.value.main || ""));

const refsHtml = computed(() =>
  split.value.refs ? mdToHtml(split.value.refs) : "",
);

const extra = computed(() => props.message.extra);

/** 優先圖片，否則 echarts option */
const chartImageSrc = computed(() => {
  const b64 = extra.value?.chart_image_base64;
  if (typeof b64 !== "string" || !b64.trim()) {
    return null;
  }
  const t = b64.trim();
  return t.startsWith("data:") ? t : `data:image/png;base64,${t}`;
});

const chartOption = computed((): Record<string, unknown> | null => {
  const o = extra.value?.chart_option;
  if (o && typeof o === "object" && !Array.isArray(o)) {
    return o as Record<string, unknown>;
  }
  return null;
});

const chartChunks = computed((): Record<string, unknown>[] => {
  const raw = extra.value?.chart_chunks;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter(
    (x): x is Record<string, unknown> =>
      x != null && typeof x === "object" && !Array.isArray(x),
  );
});

function chunkTag(c: Record<string, unknown>): string {
  const t = c.tag;
  return typeof t === "string" ? t : "";
}

function chunkText(c: Record<string, unknown>): string {
  const t = c.text;
  return typeof t === "string" ? t : "";
}

const isContractTool = computed(
  () =>
    props.message.tool_name === "contract_risk_agent" ||
    props.message.tool_name === "contract_risk_with_law_search",
);

const mainSegments = computed(() => {
  if (!isContractTool.value) {
    return [{ kind: "md" as const, text: split.value.main }];
  }
  return splitMainByHighRiskClauses(split.value.main);
});

const useSplitRiskLayout = computed(() =>
  mainSegments.value.some((s) => s.kind === "risk"),
);

const compareOpen = ref(false);
const compareClause = ref<HighRiskClause | null>(null);
const compareChunk = ref<ChunkItem | null>(null);

const compareAnnotationText = computed(() => compareClause.value?.displayMd ?? "");

const compareSourceText = computed(() => {
  const q = compareClause.value?.quotedExcerpt?.trim();
  if (q) {
    return q;
  }
  const ch = compareChunk.value;
  if (!ch) {
    return "";
  }
  return sourceTextFromChunk(ch as ChunkItem & Record<string, unknown>);
});

const compareRiskReason = computed(() => {
  const ch = compareChunk.value;
  if (!ch) {
    return "";
  }
  return riskReasonFromChunk(ch as Record<string, unknown>);
});

function resolveChunkForClause(clause: HighRiskClause): ChunkItem | null {
  const chunks = props.message.chunks;
  if (!chunks.length) {
    return null;
  }
  const idx = clause.chunkHintIndex;
  if (idx != null && chunks[idx]) {
    return chunks[idx];
  }
  return chunks[0] ?? null;
}

function openHighRiskCompare(clause: HighRiskClause) {
  compareClause.value = clause;
  compareChunk.value = resolveChunkForClause(clause);
  compareOpen.value = true;
}

const hasChunkList = computed(
  () =>
    props.message.chunks.length > 0 || chartChunks.value.length > 0,
);

const detailOpen = reactive({
  splitRefs: false,
  chartChunks: false,
  sources: false,
  chunks: false,
});

watch(
  () => props.message.content,
  () => {
    detailOpen.splitRefs = false;
    detailOpen.chartChunks = false;
    detailOpen.sources = false;
    detailOpen.chunks = false;
  },
);

function onDetailToggle(e: Event, key: keyof typeof detailOpen) {
  const t = e.target;
  if (t instanceof HTMLDetailsElement) {
    detailOpen[key] = t.open;
  }
}
</script>

<template>
  <div class="assistant-msg">
    <div
      v-if="!useSplitRiskLayout"
      class="markdown-body bubble"
      v-html="mainHtml"
    />
    <div
      v-else
      class="bubble bubble--segmented"
    >
      <template v-for="(seg, si) in mainSegments" :key="si">
        <div
          v-if="seg.kind === 'md'"
          class="markdown-body bubble-part"
          v-html="mdToHtml(seg.text || '')"
        />
        <div
          v-else
          class="risk-clause"
        >
          <div class="risk-clause-body">
            <div
              class="markdown-body"
              v-html="mdToHtml(seg.clause.displayMd)"
            />
          </div>
          <div class="risk-clause-actions">
            <button
              type="button"
              class="risk-compare-btn"
              aria-label="對照此高風險標註與原文"
              @click="openHighRiskCompare(seg.clause)"
            >
              <svg
                class="risk-compare-ico"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                aria-hidden="true"
                focusable="false"
              >
                <path
                  fill="currentColor"
                  d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h10v2H4v-2zM20 16l-4 3v-6l4 3z"
                />
              </svg>
              <span>對照原文</span>
            </button>
          </div>
        </div>
      </template>
    </div>

    <HighRiskCompareModal
      v-model:open="compareOpen"
      title="高風險標註與原文對照"
      :annotation-text="compareAnnotationText"
      :risk-reason="compareRiskReason"
      :source-text="compareSourceText"
    />

    <details
      v-if="hasChunkList"
      class="chunk-guide-details"
    >
      <summary class="chunk-guide-summary">
        需要核對段落時怎麼看？
      </summary>
      <p class="chunk-guide-inner" role="note">
        每段旁可點「原文」對照；要一次看全部，請再展開下方「查看完整段落列表」（或圖表相關區塊）。
      </p>
    </details>

    <details
      v-if="split.refs"
      class="refs-block"
      @toggle="onDetailToggle($event, 'splitRefs')"
    >
      <summary :aria-expanded="detailOpen.splitRefs">查看引用出處（內文）</summary>
      <div
        class="markdown-body refs-inner"
        role="region"
        aria-label="引用出處內文"
        v-html="refsHtml"
      />
    </details>

    <figure v-if="chartImageSrc" class="chart-figure">
      <img
        :src="chartImageSrc"
        alt="圖表"
        class="chart-img"
        loading="lazy"
        decoding="async"
      />
    </figure>
    <ChartOptionMount
      v-else-if="chartOption"
      :option="chartOption"
    />

    <details
      v-if="chartChunks.length > 0"
      class="refs-block chart-chunks-block"
      @toggle="onDetailToggle($event, 'chartChunks')"
    >
      <summary :aria-expanded="detailOpen.chartChunks">查看圖表依據的段落</summary>
      <div class="chunk-items">
        <div
          v-for="(c, i) in chartChunks"
          :key="i"
          class="chunk-item"
        >
          <div class="chunk-head">
            <p class="chunk-tag">
              {{ chunkTag(c) }}
            </p>
            <ChunkOriginalPopover
              :tag-label="chunkTag(c)"
              :body="chunkText(c)"
            />
          </div>
          <div class="chunk-text">
            {{ chunkText(c) }}
          </div>
        </div>
      </div>
    </details>

    <details
      v-if="message.sources.length > 0"
      class="refs-block"
      @toggle="onDetailToggle($event, 'sources')"
    >
      <summary :aria-expanded="detailOpen.sources">查看引用出處（檔名／連結）</summary>
      <ul class="sources-list">
        <li v-for="(s, i) in message.sources" :key="i">
          <a
            v-if="s.startsWith('http')"
            :href="s"
            target="_blank"
            rel="noopener noreferrer"
            class="source-link"
          >{{ s }}</a>
          <span v-else>{{ s }}</span>
        </li>
      </ul>
    </details>

    <p
      v-if="isContractTool && message.chunks.length > 0"
      class="risk-note"
    >
      以下為合約或風險相關分析，請自行核對；<strong>以原始文件為準</strong>。
    </p>

    <details
      v-if="message.chunks.length > 0"
      class="chunks-block"
      @toggle="onDetailToggle($event, 'chunks')"
    >
      <summary :aria-expanded="detailOpen.chunks">查看完整段落列表</summary>
      <div class="chunk-items">
        <div v-for="(c, i) in message.chunks" :key="i" class="chunk-item">
          <div class="chunk-head">
            <p class="chunk-tag">{{ c.tag }}</p>
            <ChunkOriginalPopover
              :tag-label="c.tag"
              :body="c.text"
            />
          </div>
          <div class="chunk-text">{{ c.text }}</div>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.assistant-msg {
  max-width: 100%;
}

.bubble {
  max-width: min(100%, var(--prose-measure));
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  box-shadow: var(--shadow-sm);
}

.bubble--segmented {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.bubble-part:not(:first-child) {
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border-subtle);
}

.risk-clause {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border-subtle);
}

.risk-clause:first-child {
  padding-top: 0;
  border-top: none;
}

.risk-clause-body {
  min-width: 0;
}

.risk-clause-actions {
  display: flex;
  justify-content: flex-end;
}

.risk-compare-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  font-family: var(--font-body);
  font-size: var(--text-caption-size);
  font-weight: 600;
  line-height: 1.2;
  color: var(--color-accent);
  background: var(--color-bg-muted);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.risk-compare-btn:hover {
  background: var(--color-accent-muted);
  color: var(--color-text-primary);
}

.risk-compare-btn:focus-visible {
  outline: var(--focus-ring);
  outline-offset: var(--focus-ring-offset);
}

.risk-compare-ico {
  flex-shrink: 0;
  opacity: 0.9;
}

.chunk-guide-details {
  margin: var(--space-2) 0 0;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-elevated);
  padding: var(--space-1) var(--space-2);
}

.chunk-guide-summary {
  cursor: pointer;
  font-size: var(--text-body-sm-size);
  font-weight: 600;
  color: var(--color-text-muted);
  padding: var(--space-1) 0;
  list-style: none;
}

.chunk-guide-summary::-webkit-details-marker {
  display: none;
}

.chunk-guide-inner {
  margin: 0 0 var(--space-1);
  padding: 0 0 var(--space-1);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
  color: var(--color-text-secondary);
}

.chart-figure {
  margin: var(--space-2) 0 0;
  padding: 0;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-elevated);
}

.chart-img {
  display: block;
  width: 100%;
  height: auto;
  vertical-align: middle;
}

.refs-block,
.chunks-block {
  margin-top: var(--space-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-elevated);
  padding: var(--space-1) var(--space-2);
}

.chart-chunks-block {
  margin-top: var(--space-2);
}

.refs-block summary,
.chunks-block summary {
  cursor: pointer;
  font-size: var(--text-body-sm-size);
  font-weight: 600;
  color: var(--color-accent);
  padding: var(--space-1) 0;
}

.refs-inner {
  padding: var(--space-2) 0 var(--space-1);
}

.sources-list {
  margin: var(--space-2) 0 var(--space-1);
  padding-left: var(--space-4);
  font-size: var(--text-body-sm-size);
  color: var(--color-text-secondary);
}

.source-link {
  color: var(--color-accent);
  word-break: break-all;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.source-link:hover {
  color: var(--color-text-primary);
}

/* 法條自動連結 */
.markdown-body :deep(.law-ref-link) {
  color: var(--color-accent);
  text-decoration: underline dotted;
  text-underline-offset: 2px;
  font-weight: inherit;
  cursor: pointer;
  white-space: nowrap;
}

.markdown-body :deep(.law-ref-link:hover) {
  text-decoration: underline solid;
  color: var(--color-text-primary);
}

.risk-note {
  margin: var(--space-2) 0 0;
  font-size: var(--text-caption-size);
  color: var(--color-text-muted);
}

.chunk-items {
  padding: var(--space-2) 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.chunk-item {
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border-subtle);
}

.chunk-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.chunk-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}

.chunk-tag {
  margin: 0;
  font-weight: 600;
  font-size: var(--text-body-sm-size);
  color: var(--color-text-primary);
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.chunk-text {
  font-size: var(--text-body-sm-size);
  line-height: var(--text-body-sm-leading);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
}

/* marked 產出 */
.markdown-body :deep(p) {
  margin: 0 0 0.5em;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.25em;
}

.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: var(--text-code-size);
  background: var(--color-bg-muted);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-subtle);
}

.markdown-body :deep(pre) {
  overflow: auto;
  padding: var(--space-2);
  background: var(--color-bg-muted);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-subtle);
}
</style>
