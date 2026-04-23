<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { marked } from "marked";
import DOMPurify from "dompurify";

import { postChatStream } from "@/api/chatStream";
import { downloadSource, getSourcePreview, getSources, type SourcePreviewResponse } from "@/api/sources";
import ChatAssistantMessage from "@/components/chat/ChatAssistantMessage.vue";
import ChatRetrievalSettingsModal from "@/components/chat/ChatRetrievalSettingsModal.vue";
import ApiErrorBlock from "@/components/ui/ApiErrorBlock.vue";
import { pushToast } from "@/state/toast";
import { useConversationStore } from "@/stores/conversation";
import { useSettingsStore } from "@/stores/settings";
import type { ChatRequest } from "@/types/api";
import { isAssistantMessage } from "@/types/conversation";
import { conversationToChatHistory } from "@/utils/chatHistory";
import { parseSourceRow } from "@/utils/sourceEntry";
import { syncRagScopeFromSourcesForChat } from "@/utils/syncRagScopeFromSources";

type SourceRow = {
  source: string;
  chunk_count: number;
  chat_id: string | null;
};

type RailTab = "risk" | "assistant";

type RiskCard = {
  id: string;
  title: string;
  summary: string;
  suggestion: string;
  severity: "high" | "medium" | "low";
  section: string;
};

const conversation = useConversationStore();
const settings = useSettingsStore();
const route = useRoute();

const input = ref("");
const sending = ref(false);
const chatError = ref<unknown>(null);
const streamingStatus = ref<string | null>(null);

/** 合約+法條流程後端推送的 stage 名稱（依序）；用於 stepper UI */
const CONTRACT_LAW_STAGES = [
  { key: "contract_retrieve", label: "檢索合約" },
  { key: "law_search", label: "搜尋法條" },
  { key: "contract_generate", label: "產出風險評估" },
] as const;
type StageState = "pending" | "active" | "done";
type StreamStage = { key: string; label: string; message: string; state: StageState };
const streamingStages = ref<StreamStage[]>([]);

function resetStreamingStages() {
  streamingStages.value = [];
}

function handleStreamStatus(stage: string, message: string) {
  // 只在合約+法條流程顯示 stepper（其他路徑沿用單行 streamingStatus）
  const hit = CONTRACT_LAW_STAGES.find((s) => s.key === stage);
  if (hit) {
    const existing = streamingStages.value;
    const knownKeys = existing.map((s) => s.key);
    // 初次進入合約流程：展開全部 stages（未命中的保持 pending）
    if (existing.length === 0) {
      streamingStages.value = CONTRACT_LAW_STAGES.map((s) => ({
        key: s.key,
        label: s.label,
        message: s.key === stage ? message : "",
        state: s.key === stage ? "active" : "pending",
      }));
    } else {
      // 把之前 active 的標 done、當前標 active
      streamingStages.value = existing.map((s) => {
        if (s.key === stage) return { ...s, message, state: "active" };
        if (s.state === "active" && CONTRACT_LAW_STAGES.findIndex((c) => c.key === s.key)
            < CONTRACT_LAW_STAGES.findIndex((c) => c.key === stage)) {
          return { ...s, state: "done" };
        }
        return s;
      });
      // 若是尚未加入的 stage（理論上不會），補上
      if (!knownKeys.includes(stage)) {
        streamingStages.value.push({ key: stage, label: hit.label, message, state: "active" });
      }
    }
  }
  streamingStatus.value = message;
}

function finalizeStreamingStages() {
  // 結束時把所有 active/pending 的合約 stage 標 done
  if (streamingStages.value.length > 0) {
    streamingStages.value = streamingStages.value.map((s) => ({ ...s, state: "done" }));
  }
}
const currentAbortController = ref<AbortController | null>(null);
const settingsOpen = ref(false);
const scopeSyncState = ref<"loading" | "has" | "none" | "error">("loading");
const railTab = ref<RailTab>("risk");
const railExpanded = ref(false);

const sourceRows = ref<SourceRow[]>([]);
const preview = ref<SourcePreviewResponse | null>(null);
const previewLoading = ref(false);
const assistantFeedEl = ref<HTMLElement | null>(null);
const stickToBottom = ref(true);

const riskFilter = ref<"all" | "high" | "action">("all");

const activeConversation = computed(() => conversation.activeConversation);

const latestAssistant = computed(() => {
  const messages = activeConversation.value?.messages ?? [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (isAssistantMessage(message)) {
      return message;
    }
  }
  return null;
});

const latestContractAssistant = computed(() => {
  const latest = latestAssistant.value;
  if (!latest) {
    return null;
  }
  if (
    latest.tool_name === "contract_risk_agent" ||
    latest.tool_name === "contract_risk_with_law_search"
  ) {
    return latest;
  }
  return null;
});

const currentDocumentTitle = computed(() => {
  if (preview.value?.title?.trim()) {
    return preview.value.title.trim();
  }
  return activeConversation.value?.title?.trim() || "Contract Preview";
});

const previewParagraphs = computed(() => {
  const text = preview.value?.content?.trim() ?? "";
  if (!text) {
    return [];
  }
  return text
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
});

marked.use({ breaks: true });

function mdToHtml(src: string): string {
  const rawHtml = marked(src || "", { async: false }) as string;
  return DOMPurify.sanitize(rawHtml);
}

function scrollToClause(sectionName: string) {
  if (!sectionName) return;
  const normalizedTarget = sectionName.replace(/\s+/g, "");

  const container = document.querySelector(".document-scroll");
  if (!container) return;

  const paragraphs = document.querySelectorAll(".paper-body");
  for (const p of paragraphs) {
    const text = p.textContent?.replace(/\s+/g, "") || "";
    if (text.includes(normalizedTarget)) {
      p.scrollIntoView({ behavior: "smooth", block: "center" });

      // Highlight animation
      p.classList.remove("highlight-pulse");
      void (p as HTMLElement).offsetWidth; // Force reflow
      p.classList.add("highlight-pulse");
      return;
    }
  }
  pushToast({ variant: "info", message: `未能在目前文件中定位「${sectionName}」` });
}

function extractField(block: string, label: string): string {
  const re = new RegExp(`【${label}】([\\s\\S]*?)(?=【|$)`);
  return (block.match(re)?.[1] ?? "").trim();
}

function severityFromLabel(label: string): RiskCard["severity"] {
  if (/高風險/.test(label)) return "high";
  if (/中風險/.test(label)) return "medium";
  return "low";
}

// 條號：阿拉伯數字 或 中文數字（一~百千萬）
const ARTICLE_NUM = "[\\d一二三四五六七八九十百千萬]+";
// 條首切割：第X條 後可接 ：: 或空白或換行
const ARTICLE_SPLIT_RE = new RegExp(`(?=第\\s*${ARTICLE_NUM}\\s*條[\\s：:])`, "u");
const ARTICLE_HEADER_RE = new RegExp(`第\\s*${ARTICLE_NUM}\\s*條[\\s：:]*([^\\n【]*)`, "u");

function parseAnswerToCards(answer: string): RiskCard[] {
  const blocks = answer
    .split(ARTICLE_SPLIT_RE)
    .filter((b) => new RegExp(`第\\s*${ARTICLE_NUM}\\s*條`, "u").test(b)
      && /【風險等級】|【法務實務推演】|【修改建議】/.test(b));
  if (blocks.length === 0) return [];

  return blocks.slice(0, 15).map((block, index) => {
    const titleMatch = block.match(ARTICLE_HEADER_RE);
    const title = (titleMatch?.[1]?.trim() || `條款 ${index + 1}`).replace(/\*+/g, "").trim();
    const typeLabel = extractField(block, "條款類型");
    const riskLabel = extractField(block, "風險等級");
    const analysis = extractField(block, "法務實務推演");
    const suggestion = extractField(block, "修改建議");
    const description = extractField(block, "具體內容描述");

    return {
      id: `parsed-${index}-${title.slice(0, 12)}`,
      title,
      summary: analysis || description || "請參閱右側法律助理的完整分析。",
      suggestion: suggestion || "",
      severity: severityFromLabel(riskLabel),
      section: typeLabel || riskLabel || `條款 ${index + 1}`,
    };
  });
}

/**
 * 後端 extra.risk_cards（結構化）→ 前端 RiskCard。
 * 與 backend/schemas/chat.RiskCard 對齊：riskLevel, title, clauseType, reasoning, suggestion…
 */
type BackendRiskCard = {
  id?: string;
  title?: string;
  clauseType?: string | null;
  riskLevel?: "high" | "medium" | "low";
  riskLabel?: string | null;
  reasoning?: string | null;
  suggestion?: string | null;
};

function riskCardsFromExtra(extra: Record<string, unknown> | null | undefined): RiskCard[] {
  if (!extra) return [];
  const raw = (extra as { risk_cards?: unknown }).risk_cards;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  return raw.map((item, idx) => {
    const c = item as BackendRiskCard;
    const title = (c.title || `條款 ${idx + 1}`).replace(/\*+/g, "").trim();
    return {
      id: c.id || `extra-${idx}`,
      title,
      summary: (c.reasoning || "").trim() || "請參閱右側法律助理的完整分析。",
      suggestion: (c.suggestion || "").trim(),
      severity: c.riskLevel || "low",
      section: c.clauseType || c.riskLabel || `條款 ${idx + 1}`,
    } satisfies RiskCard;
  });
}

const riskCards = computed<RiskCard[]>(() => {
  const msg = latestContractAssistant.value;
  if (!msg) {
    return [
      {
        id: "placeholder",
        title: "等待分析結果",
        summary: "送出審閱問題後，系統會在這裡整理重點風險與關鍵條文。",
        suggestion: "",
        severity: "low",
        section: "Waiting for analysis",
      },
    ];
  }

  // 1. 優先使用後端結構化資料（contract_risk_parser.parse_risk_cards 的輸出）
  const fromExtra = riskCardsFromExtra(msg.extra);
  if (fromExtra.length > 0) return fromExtra;

  // 2. Fallback：LLM 格式未解析成功時，用舊版 regex parse markdown
  const parsed = parseAnswerToCards(msg.content);
  if (parsed.length > 0) return parsed;

  // Fallback: show a single guidance card (never expose raw chunk text)
  return [
    {
      id: "fallback-guidance",
      title: "合約分析完成",
      summary: "AI 已完成分析，詳細說明請參閱右側「法律助理」欄位的完整回應。",
      suggestion: "",
      severity: "low" as const,
      section: "完整分析",
    },
  ];
});

const overallRiskScore = computed(() => {
  const cards = riskCards.value.filter((item) => item.id !== "placeholder" && item.id !== "fallback-guidance");
  if (cards.length === 0) return 0;
  const high = cards.filter((c) => c.severity === "high").length;
  const medium = cards.filter((c) => c.severity === "medium").length;
  const low = cards.filter((c) => c.severity === "low").length;
  // Weighted risk index: high=3pts, medium=1.5pts, low=0.5pts
  // Normalised to 0–100 relative to all cards being high risk
  const raw = high * 3 + medium * 1.5 + low * 0.5;
  const max = cards.length * 3;
  return Math.round((raw / max) * 100);
});

const riskBreakdown = computed(() => {
  let h = 0, m = 0, l = 0;
  for (const c of riskCards.value) {
    if (c.severity === "high") h++;
    else if (c.severity === "medium") m++;
    else if (c.severity === "low") l++;
  }
  return `高風險 ${h} 項、中風險 ${m} 項、低風險 ${l} 項`;
});

const filteredRiskCards = computed(() => {
  let filtered = riskCards.value;
  if (riskFilter.value === "high") {
    filtered = filtered.filter((c) => c.severity === "high");
  } else if (riskFilter.value === "action") {
    filtered = filtered.filter((c) => !!c.suggestion);
  }

  const weight = { high: 3, medium: 2, low: 1 };
  return [...filtered].sort((a, b) => {
    const wA = weight[a.severity as keyof typeof weight] || 0;
    const wB = weight[b.severity as keyof typeof weight] || 0;
    return wB - wA; // desc
  });
});

const complianceLabel = computed(() => {
  if (overallRiskScore.value >= 70) return "危險";
  if (overallRiskScore.value >= 40) return "注意";
  return "安全";
});

const keyDates = computed(() => [
  { label: "檔案名稱", value: currentDocumentTitle.value },
  { label: "來源數量", value: `${sourceRows.value.length} 份` },
]);

const quickActions = [
  { label: "法遵檢查", prompt: "請檢查這份合約的法遵風險。" },
  { label: "風險摘要", prompt: "請整理這份合約的主要風險。" },
];

function severityLabel(severity: RiskCard["severity"]): string {
  if (severity === "high") {
    return "High";
  }
  if (severity === "medium") {
    return "Medium";
  }
  return "Low";
}

function onAssistantFeedScroll() {
  const el = assistantFeedEl.value;
  if (!el) {
    return;
  }
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scrollAssistantFeedToBottom() {
  const el = assistantFeedEl.value;
  if (!el) {
    return;
  }
  el.scrollTop = el.scrollHeight;
}

async function loadPreview(chatId: string | null | undefined, specificSource?: string | null) {
  preview.value = null;
  previewLoading.value = true;
  try {
    const entries = await getSources(chatId ?? null, { showLoading: false });
    sourceRows.value = Array.isArray(entries.entries)
      ? entries.entries.map((row) => parseSourceRow(row as Record<string, unknown>))
      : [];

    // Use the specifically requested source, or fall back to the first source of the conversation.
    const targetSource = specificSource
      ? (sourceRows.value.find((r) => r.source === specificSource) ?? null)
      : (sourceRows.value[0] ?? null);

    if (specificSource && !targetSource) {
      // Source is globally indexed (no chatId) — preview it directly.
      preview.value = await getSourcePreview(specificSource, null, { showLoading: false });
      return;
    }

    if (!targetSource?.source) {
      preview.value = null;
      return;
    }

    preview.value = await getSourcePreview(targetSource.source, targetSource.chat_id, {
      showLoading: false,
    });
  } catch {
    sourceRows.value = [];
    preview.value = null;
  } finally {
    previewLoading.value = false;
  }
}

watch(
  () => activeConversation.value?.messages.length,
  async () => {
    await nextTick();
    if (stickToBottom.value) {
      scrollAssistantFeedToBottom();
    }
  },
);

watch(
  () => [conversation.activeConversationId, route.query.source] as const,
  async ([id, sourceParam]) => {
    scopeSyncState.value = "loading";
    const syncResult = await syncRagScopeFromSourcesForChat(id, { showLoading: false }).catch(() => null);
    if (!syncResult || !syncResult.ok) {
      scopeSyncState.value = "error";
    } else {
      scopeSyncState.value = syncResult.hasUploads ? "has" : "none";
    }

    const specificSource = typeof sourceParam === "string" ? sourceParam : null;
    await loadPreview(id, specificSource);
    stickToBottom.value = true;
    await nextTick();
    scrollAssistantFeedToBottom();
  },
  { immediate: true },
);

function setPrompt(prompt: string) {
  railTab.value = "assistant";
  input.value = prompt;
}

function onTextareaKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void sendMessage();
  }
}

function stopStreaming() {
  currentAbortController.value?.abort();
}

async function sendMessage() {
  const raw = input.value.trim();
  if (!raw || sending.value) {
    return;
  }

  const convId = conversation.activeConversationId;
  if (!convId || !conversation.conversations[convId]) {
    return;
  }

  const priorHistory = conversationToChatHistory(conversation.conversations[convId].messages);
  const pendingPart = conversation.consumePendingForNextUserMessage(raw);

  conversation.appendUserMessage(convId, raw);
  input.value = "";
  sending.value = true;
  chatError.value = null;
  streamingStatus.value = "正在分析問題類型...";
  resetStreamingStages();
  railTab.value = "assistant";

  const body: ChatRequest = {
    message: raw,
    history: priorHistory,
    top_k: settings.topK,
    strict: settings.strict,
    chat_id: convId,
    rag_scope_chat_id: settings.resolveRagScopeChatId(convId),
    active_source: preview.value?.source ?? undefined,
    ...pendingPart,
  };

  // 建立 AbortController，供使用者中斷使用
  const abortCtrl = new AbortController();
  currentAbortController.value = abortCtrl;

  // 插入空的 assistant placeholder，streaming 時逐步填入
  conversation.appendStreamingPlaceholder(convId);
  let hasReceivedTokens = false;

  try {
    await postChatStream(body, {
      onStatus({ stage, message }) {
        handleStreamStatus(stage, message);
      },
      onToken(fragment) {
        if (!hasReceivedTokens) {
          hasReceivedTokens = true;
          streamingStatus.value = null;
          finalizeStreamingStages();
        }
        conversation.appendStreamingToken(convId, fragment);
      },
      onMeta(meta) {
        conversation.finalizeStreamingMessage(convId, meta);
        conversation.applyChatResponseNextFields(meta as any);
      },
      onDone() {
        streamingStatus.value = null;
        resetStreamingStages();
      },
      onAbort() {
        streamingStatus.value = null;
        resetStreamingStages();
        // 已收到部分 token：保留已生成的內容，標記為中斷
        if (hasReceivedTokens) {
          conversation.finalizeStreamingMessage(convId, {});
        } else {
          conversation.removeLastMessage(convId);
        }
      },
      onError(message) {
        streamingStatus.value = null;
        resetStreamingStages();
        if (!hasReceivedTokens) {
          conversation.removeLastMessage(convId);
        }
        chatError.value = new Error(message);
        pushToast({ variant: "error", message });
      },
    }, abortCtrl.signal);
  } catch (error) {
    streamingStatus.value = null;
    resetStreamingStages();
    if (!hasReceivedTokens) {
      conversation.removeLastMessage(convId);
    }
    chatError.value = error;
    pushToast({
      variant: "error",
      message: error instanceof Error ? error.message : String(error),
    });
  } finally {
    sending.value = false;
    streamingStatus.value = null;
    resetStreamingStages();
    currentAbortController.value = null;
  }
}
</script>

<template>
  <div class="workspace-page">
    <ChatRetrievalSettingsModal
      v-model:open="settingsOpen"
      :scope-sync-state="scopeSyncState"
    />

    <section class="review-workspace" :class="{ 'review-workspace--rail-expanded': railExpanded }">
      <div class="document-frame ds-card">
        <header class="viewer-toolbar">
          <div class="viewer-toolbar__left">
            <button type="button" class="viewer-icon" aria-label="縮小">-</button>
            <span class="viewer-zoom">100%</span>
            <button type="button" class="viewer-icon" aria-label="放大">+</button>
          </div>

          <div class="viewer-toolbar__center">
            <!-- Pagination removed to render all articles continuously -->
          </div>

          <div class="viewer-toolbar__right">
            <button
              type="button"
              class="viewer-icon viewer-icon--wide"
              aria-label="下載"
              :disabled="!preview?.source"
              @click="preview?.source && downloadSource(preview.source).catch(e => pushToast({ variant: 'error', message: e.message }))"
            >Save</button>
          </div>
        </header>

        <div class="document-scroll">
          <div v-if="previewLoading" class="document-empty">
            <p class="document-empty__eyebrow">Document Preview</p>
            <h2>正在載入文件內容</h2>
            <p>系統正在整理最新索引結果，完成後會在這裡顯示可讀版面。</p>
          </div>

          <div v-else-if="previewParagraphs.length === 0" class="document-empty">
            <p class="document-empty__eyebrow">Workspace Ready</p>
            <h2>上傳文件後即可開始審閱</h2>
            <p>左側選擇已索引文件，中央會以閱讀版面顯示內容，右側則可查看風險評估與法律助理回覆。</p>
          </div>

          <article v-else class="document-paper">
            <header class="paper-header">
              <p class="paper-file">{{ currentDocumentTitle }}</p>
              <p class="paper-meta">Document Preview</p>
            </header>

            <section
              v-for="(paragraph, index) in previewParagraphs"
              :key="`${index}-${paragraph.slice(0, 24)}`"
              class="paper-section"
            >
              <p class="paper-body">{{ paragraph }}</p>
            </section>
          </article>
        </div>
      </div>

      <aside class="analysis-rail ds-card" :class="{ 'analysis-rail--expanded': railExpanded }">
        <div class="rail-tabs">
          <button
            type="button"
            class="rail-tab"
            :class="{ 'rail-tab--active': railTab === 'risk' }"
            @click="railTab = 'risk'"
          >
            Risk Report
          </button>
          <button
            type="button"
            class="rail-tab"
            :class="{ 'rail-tab--active': railTab === 'assistant' }"
            @click="railTab = 'assistant'"
          >
            Legal Assistant
          </button>
          <button
            type="button"
            class="rail-expand-btn"
            :aria-label="railExpanded ? '收回面板' : '展開面板'"
            @click="railExpanded = !railExpanded"
          >
            <svg
              v-if="!railExpanded"
              xmlns="http://www.w3.org/2000/svg"
              width="16" height="16" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"
            >
              <!-- expand: arrows pointing outward -->
              <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
              <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
            </svg>
            <svg
              v-else
              xmlns="http://www.w3.org/2000/svg"
              width="16" height="16" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"
            >
              <!-- collapse: arrows pointing inward -->
              <polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/>
              <line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/>
            </svg>
          </button>
        </div>

        <div v-if="railTab === 'risk'" class="rail-panel">
          <section class="score-card" :title="riskBreakdown">
            <p class="score-card__label">風險指數</p>
            <div class="score-card__row">
              <p class="score-card__value">{{ overallRiskScore }}<span>/100</span></p>
              <span class="score-chip" :class="`score-chip--${complianceLabel}`">
                {{ complianceLabel }}
              </span>
            </div>
            <p class="score-card__breakdown">{{ riskBreakdown }}</p>
          </section>

          <section class="report-section">
            <div class="report-section__header">
              <p class="report-section__title">KEY FINDINGS</p>
              <div class="risk-filters">
                <button
                  class="filter-pill"
                  :class="{ 'filter-pill--active': riskFilter === 'all' }"
                  @click="riskFilter = 'all'"
                >全部</button>
                <button
                  class="filter-pill"
                  :class="{ 'filter-pill--active': riskFilter === 'high' }"
                  @click="riskFilter = 'high'"
                >僅高風險</button>
                <button
                  class="filter-pill"
                  :class="{ 'filter-pill--active': riskFilter === 'action' }"
                  @click="riskFilter = 'action'"
                >需修改</button>
              </div>
            </div>
            <article
              v-for="card in filteredRiskCards"
              :key="card.id"
              class="finding-card finding-card--clickable"
              @click="scrollToClause(card.section)"
            >
              <div class="finding-card__head">
                <h3>{{ card.title }}</h3>
                <span class="finding-chip" :class="`finding-chip--${card.severity}`">
                  {{ severityLabel(card.severity) }}
                </span>
              </div>
              <p v-if="card.section" class="finding-card__section">
                {{ card.section }}
                <span class="finding-card__jump-hint">➜ 點擊查看原文</span>
              </p>
              <div class="finding-card__body markdown-body" v-html="mdToHtml(card.summary)" />
              <div v-if="card.suggestion" class="finding-card__suggestion">
                <p class="finding-card__suggestion-label">AI 建議</p>
                <div class="finding-card__suggestion-body markdown-body" v-html="mdToHtml(card.suggestion)" />
              </div>
            </article>
          </section>

          <section class="report-section">
            <p class="report-section__title">KEY DATES</p>
            <div class="date-list">
              <div
                v-for="item in keyDates"
                :key="item.label"
                class="date-row"
              >
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
          </section>
        </div>

        <div v-else class="rail-panel rail-panel--assistant">
          <div ref="assistantFeedEl" class="assistant-feed" @scroll="onAssistantFeedScroll">
            <p class="assistant-feed__label">Legal Assistant</p>

            <div v-if="activeConversation?.messages.length" class="assistant-feed__messages">
              <template v-for="(msg, index) in activeConversation?.messages" :key="index">
                <div v-if="msg.role === 'user'" class="assistant-user">{{ msg.content }}</div>
                <div v-else-if="isAssistantMessage(msg) && msg.content" class="assistant-reply">
                  <ChatAssistantMessage :message="msg" />
                </div>
              </template>

              <!-- Streaming 狀態指示器 -->
              <!-- 合約+法條流程：顯示 stepper；其他路徑：單行動畫點 -->
              <div v-if="streamingStages.length > 0" class="streaming-stepper" role="status" aria-live="polite">
                <ol class="stepper-list">
                  <li
                    v-for="stage in streamingStages"
                    :key="stage.key"
                    class="stepper-item"
                    :class="`stepper-item--${stage.state}`"
                  >
                    <span class="stepper-icon" aria-hidden="true">
                      <span v-if="stage.state === 'done'" class="stepper-check">✓</span>
                      <span v-else-if="stage.state === 'active'" class="stepper-spinner" />
                      <span v-else class="stepper-dot-empty" />
                    </span>
                    <span class="stepper-body">
                      <span class="stepper-label">{{ stage.label }}</span>
                      <span v-if="stage.state === 'active' && stage.message" class="stepper-message">
                        {{ stage.message }}
                      </span>
                    </span>
                  </li>
                </ol>
              </div>
              <div v-else-if="streamingStatus" class="streaming-indicator" role="status" aria-live="polite" :aria-label="streamingStatus">
                <span class="streaming-dot" />
                <span class="streaming-dot" />
                <span class="streaming-dot" />
                <span class="streaming-label">{{ streamingStatus }}</span>
              </div>
            </div>
            <div v-else class="assistant-empty">
              送出審閱問題後，法律助理會在這裡整理條文重點、修約方向與引用來源。
            </div>

            <ApiErrorBlock
              v-if="chatError"
              :error="chatError"
              title="訊息送出失敗"
            />
          </div>

          <div class="assistant-actions">
            <button
              v-for="item in quickActions"
              :key="item.label"
              type="button"
              class="assistant-action"
              @click="setPrompt(item.prompt)"
            >
              {{ item.label }}
            </button>
          </div>

          <div class="assistant-composer">
            <textarea
              id="chat-input"
              v-model="input"
              data-testid="chat-input"
              class="ds-textarea assistant-composer__input"
              rows="3"
              name="message"
              aria-label="Ask about this document"
              placeholder="Ask about this document..."
              :disabled="sending"
              autocomplete="off"
              @keydown="onTextareaKeydown"
            />
            <button
              v-if="sending"
              type="button"
              class="assistant-composer__send assistant-composer__send--stop"
              title="中斷回覆"
              @click="stopStreaming()"
            >
              ■
            </button>
            <button
              v-else
              type="button"
              data-testid="chat-send"
              class="assistant-composer__send"
              :disabled="!input.trim()"
              @click="sendMessage()"
            >
              Go
            </button>
          </div>
        </div>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.workspace-page {
  height: 100%;
  min-height: 200px;
}

@keyframes highlightPulse {
  0% { background-color: rgba(255, 255, 0, 0); }
  20% { background-color: rgba(255, 235, 59, 0.4); }
  80% { background-color: rgba(255, 235, 59, 0.4); }
  100% { background-color: rgba(255, 255, 0, 0); }
}
.highlight-pulse {
  animation: highlightPulse 2s ease-out;
  border-radius: 4px;
}

.review-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  height: 100%;
  min-height: 0;
}

.document-frame {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
  border: 1px solid #dbe6f2;
  border-radius: 18px;
  background:
    linear-gradient(180deg, #edf3fb 0%, #eaf1f8 100%);
}

.viewer-toolbar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid #dbe6f2;
}

.viewer-toolbar__left,
.viewer-toolbar__center,
.viewer-toolbar__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.viewer-toolbar__center {
  justify-content: center;
}

.viewer-toolbar__right {
  justify-content: flex-end;
}

.viewer-icon {
  min-width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: #64748b;
  font-size: 0.88rem;
  font-weight: 700;
  cursor: pointer;
}

.viewer-icon--wide {
  min-width: 58px;
  border-radius: 10px;
}

.viewer-icon:hover:not(:disabled) {
  border-color: #d7e2ee;
  background: #f8fbff;
}

.viewer-icon:disabled {
  cursor: not-allowed;
  opacity: 0.35;
}

.viewer-zoom,
.viewer-page {
  font-size: 0.96rem;
  font-weight: 600;
  color: #475569;
}

.document-scroll {
  min-height: 0;
  overflow: auto;
  padding: 22px 22px 26px;
}

.document-paper {
  width: min(100%, 780px);
  min-height: 1080px;
  margin: 0 auto;
  padding: 44px 72px 68px;
  background: #ffffff;
  border: 1px solid #e6edf5;
  box-shadow:
    0 24px 48px rgba(15, 23, 42, 0.08),
    0 3px 10px rgba(15, 23, 42, 0.04);
}

.paper-header {
  margin-bottom: 26px;
}

.paper-file {
  margin: 0;
  font-size: 1.02rem;
  font-weight: 800;
  color: #cad5e3;
}

.paper-meta {
  margin: 18px 0 0;
  font-size: 0.82rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #b6c3d4;
}

.paper-section + .paper-section {
  margin-top: 18px;
}

.paper-body {
  margin: 0;
  white-space: pre-wrap;
  font-size: 1.05rem;
  line-height: 1.8;
  color: #29405c;
}

.document-empty {
  margin: 20px auto 0;
  max-width: 760px;
  border: 1px solid #e3ebf4;
  border-radius: 16px;
  background: #ffffff;
  padding: 30px 32px;
  text-align: center;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
}

.document-empty__eyebrow {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  color: #64748b;
}

.document-empty h2 {
  margin: 8px 0 12px;
  font-size: 2rem;
  color: #102a43;
}

.document-empty p {
  margin: 0;
  font-size: 1rem;
  line-height: 1.8;
  color: #486581;
}

.analysis-rail {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
  padding: 0;
  border: 1px solid #dbe6f2;
  border-radius: 16px;
  background: #ffffff;
  transition: all 0.2s ease;
}

.rail-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  border-bottom: 1px solid #dbe6f2;
}

.rail-tab {
  border: none;
  background: #ffffff;
  padding: 14px 10px;
  font-size: 0.96rem;
  font-weight: 700;
  color: #64748b;
  cursor: pointer;
}

.rail-tab--active {
  color: #102a43;
  box-shadow: inset 0 -3px 0 #102a43;
}

.rail-expand-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  border: none;
  background: #ffffff;
  color: #64748b;
  cursor: pointer;
  border-left: 1px solid #dbe6f2;
  transition: background 0.15s, color 0.15s;
}
.rail-expand-btn:hover {
  background: #f0f4f8;
  color: #102a43;
}

/* Expanded rail: wider panel, document frame shrinks */
.review-workspace--rail-expanded {
  grid-template-columns: minmax(0, 1fr) 580px;
}

.rail-panel {
  min-height: 0;
  overflow: auto;
  padding: 16px 14px 18px;
}

.score-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #fbfdff;
  padding: 16px;
}

.score-card__label,
.report-section__title,
.assistant-feed__label,
.assistant-focus__label {
  margin: 0 0 12px;
  font-size: 0.8rem;
  font-weight: 800;
  letter-spacing: 0.03em;
  color: #7c8da3;
}

.score-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.score-card__value {
  margin: 0;
  font-size: 2.7rem;
  font-weight: 900;
  line-height: 1;
  color: #102a43;
}

.score-card__value span {
  font-size: 1.5rem;
  color: #94a3b8;
}

.score-card__breakdown {
  font-size: 0.8rem;
  color: #7c8da3;
  margin-top: 8px;
  text-align: right;
}

.score-chip,
.finding-chip {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 0.78rem;
  font-weight: 800;
}

.score-chip--high,
.score-chip--危險,
.finding-chip--high {
  background: #fde8e8;
  color: #d64545;
}

.score-chip--medium,
.score-chip--注意,
.finding-chip--medium {
  background: #fff3d6;
  color: #b7791f;
}

.score-chip--low,
.score-chip--安全,
.finding-chip--low {
  background: #e8f8ee;
  color: #2f9e5f;
}

.report-section {
  margin-top: 20px;
  padding: 16px 20px;
}

.report-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.report-section__title {
  font-size: 0.75rem;
  font-weight: 700;
  color: #7c8da3;
  letter-spacing: 0.05em;
  margin: 0;
}

.risk-filters {
  display: flex;
  gap: 6px;
}

.filter-pill {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 2px 8px;
  font-size: 0.7rem;
  color: #7c8da3;
  cursor: pointer;
  transition: all 0.2s;
}

.filter-pill:hover {
  background: #f8fbff;
}

.filter-pill--active {
  background: #3b82f6;
  color: white;
  border-color: #3b82f6;
}

.finding-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
  padding: 14px;
  margin-bottom: 12px;
}

.finding-card--clickable {
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.finding-card--clickable:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  border-color: #3b82f6;
}

.finding-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.finding-card__head h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 800;
  color: #102a43;
}

.finding-card__body {
  margin: 10px 0 0;
  font-size: 0.95rem;
  line-height: 1.65;
  color: #52657d;
}

.finding-card__body :deep(p),
.finding-card__suggestion-body :deep(p) {
  margin: 0 0 0.4em;
}

.finding-card__body :deep(p:last-child),
.finding-card__suggestion-body :deep(p:last-child) {
  margin-bottom: 0;
}

.finding-card__body :deep(ul),
.finding-card__body :deep(ol),
.finding-card__suggestion-body :deep(ul),
.finding-card__suggestion-body :deep(ol) {
  margin: 0.3em 0;
  padding-left: 1.2em;
}

.finding-card__body :deep(strong),
.finding-card__suggestion-body :deep(strong) {
  font-weight: 700;
  color: inherit;
}

.finding-card__section {
  font-size: 0.8rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
}

.finding-card__jump-hint {
  font-size: 0.7rem;
  color: #3b82f6;
  opacity: 0;
  transition: opacity 0.2s;
}

.finding-card--clickable:hover .finding-card__jump-hint {
  opacity: 1;
}

.finding-card__suggestion {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f0f7ff;
  border-left: 3px solid #3b82f6;
}

.finding-card__suggestion-label {
  margin: 0 0 4px;
  font-size: 0.75rem;
  font-weight: 800;
  color: #2563eb;
  letter-spacing: 0.04em;
}

.finding-card__suggestion-body {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.6;
  color: #1e3a5f;
}

.date-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.date-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 0.92rem;
  color: #64748b;
}

.date-row strong {
  max-width: 56%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #102a43;
}

.rail-panel--assistant {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto auto auto;
  gap: 14px;
}

.assistant-feed {
  min-height: 0;
  overflow: auto;
  padding-right: 4px;
}

.assistant-feed__messages {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.assistant-user {
  align-self: flex-end;
  max-width: 100%;
  border-radius: 12px;
  background: #eff6ff;
  padding: 10px 12px;
  font-size: 0.94rem;
  line-height: 1.6;
  color: #1e3a5f;
}

.assistant-reply :deep(.bubble),
.assistant-reply :deep(.bubble--segmented) {
  max-width: 100%;
  border-radius: 12px;
}

.assistant-empty {
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  padding: 14px;
  font-size: 0.95rem;
  line-height: 1.6;
  color: #64748b;
}

.assistant-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.assistant-action {
  border: 1px solid #d7e2ee;
  border-radius: 999px;
  background: #ffffff;
  padding: 10px 12px;
  font-size: 0.88rem;
  font-weight: 700;
  color: #52657d;
  cursor: pointer;
}

.assistant-focus {
  border: 1px solid #dbe6f2;
  border-radius: 12px;
  background: #f8fbff;
  padding: 12px 14px;
}

.assistant-focus__label {
  margin-bottom: 6px;
}

.assistant-focus__value {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 800;
  color: #102a43;
}

.assistant-focus__hint {
  margin: 6px 0 0;
  font-size: 0.88rem;
  color: #7c8da3;
}

.assistant-composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 52px;
  gap: 10px;
  align-items: end;
}

.assistant-composer__input {
  min-height: 82px;
}

.assistant-composer__send {
  height: 48px;
  border: none;
  border-radius: 14px;
  background: #94a3b8;
  color: #ffffff;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
}

.assistant-composer__send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.assistant-composer__send--stop {
  background: #ef4444;
  font-size: 1rem;
  letter-spacing: 0;
  transition: background-color 120ms ease;
}

.assistant-composer__send--stop:hover {
  background: #dc2626;
}

@media (max-width: 1200px) {
  .review-workspace {
    grid-template-columns: 1fr;
  }

  .analysis-rail {
    min-height: 720px;
  }
}

@media (max-width: 720px) {
  .viewer-toolbar {
    grid-template-columns: 1fr;
  }

  .viewer-toolbar__left,
  .viewer-toolbar__center,
  .viewer-toolbar__right {
    justify-content: center;
  }

  .document-paper {
    min-height: auto;
    padding: 30px 22px 44px;
  }

  .assistant-actions {
    grid-template-columns: 1fr;
  }
}

/* --- Streaming 狀態指示器 --- */
.streaming-indicator {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 14px;
  margin-top: 8px;
  border-radius: 10px;
  background: linear-gradient(135deg, #f0f4ff 0%, #e8eeff 100%);
  animation: streamFadeIn 0.3s ease-out;
}

@keyframes streamFadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.streaming-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6366f1;
  animation: streamPulse 1.2s ease-in-out infinite;
}

.streaming-dot:nth-child(2) {
  animation-delay: 0.15s;
}

.streaming-dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes streamPulse {
  0%, 80%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1.1);
  }
}

.streaming-label {
  font-size: 0.82rem;
  color: #6366f1;
  font-weight: 600;
  margin-left: 4px;
}

/* ---------- SSE stage stepper（合約+法條流程專用） ---------- */
.streaming-stepper {
  margin: 18px 0 0;
  padding: 14px 18px;
  background: rgba(99, 102, 241, 0.06);
  border: 1px solid rgba(99, 102, 241, 0.18);
  border-radius: 12px;
}

.stepper-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.stepper-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 0.88rem;
  color: #9ca3af;
  transition: color 0.2s ease;
}

.stepper-item--active {
  color: #4f46e5;
  font-weight: 600;
}

.stepper-item--done {
  color: #10b981;
}

.stepper-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.stepper-check {
  color: #10b981;
  font-weight: 700;
  font-size: 0.95rem;
  line-height: 1;
}

.stepper-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid rgba(79, 70, 229, 0.25);
  border-top-color: #4f46e5;
  animation: stepper-spin 0.9s linear infinite;
}

.stepper-dot-empty {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid #d1d5db;
}

.stepper-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.stepper-label {
  line-height: 1.4;
}

.stepper-message {
  font-size: 0.78rem;
  font-weight: 400;
  color: #6b7280;
  line-height: 1.4;
}

@keyframes stepper-spin {
  to { transform: rotate(360deg); }
}
</style>
