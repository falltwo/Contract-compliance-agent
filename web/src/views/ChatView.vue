<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { ApiError } from "@/api/client";
import { postChat } from "@/api/chat";
import { postChatStream } from "@/api/chatStream";
import { getSourcePreview, getSources, type SourcePreviewResponse } from "@/api/sources";
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
const settingsOpen = ref(false);
const scopeSyncState = ref<"loading" | "has" | "none" | "error">("loading");
const railTab = ref<RailTab>("risk");

const sourceRows = ref<SourceRow[]>([]);
const preview = ref<SourcePreviewResponse | null>(null);
const previewLoading = ref(false);
const currentPreviewPage = ref(0);
const assistantFeedEl = ref<HTMLElement | null>(null);
const stickToBottom = ref(true);

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
    .filter(Boolean)
    .slice(0, 36);
});

const previewPages = computed(() => {
  const pageSize = 5;
  const pages: string[][] = [];
  for (let index = 0; index < previewParagraphs.value.length; index += pageSize) {
    pages.push(previewParagraphs.value.slice(index, index + pageSize));
  }
  return pages;
});

const activePreviewPageParagraphs = computed(() => {
  if (previewPages.value.length === 0) {
    return [];
  }
  const safeIndex = Math.min(currentPreviewPage.value, previewPages.value.length - 1);
  return previewPages.value[safeIndex] ?? [];
});

const pageLabel = computed(() => {
  const total = Math.max(1, previewPages.value.length);
  return `Page ${Math.min(currentPreviewPage.value + 1, total)} of ${total}`;
});

const canGoToPreviousPreviewPage = computed(() => currentPreviewPage.value > 0);
const canGoToNextPreviewPage = computed(
  () => currentPreviewPage.value < previewPages.value.length - 1,
);

function extractField(block: string, label: string): string {
  const re = new RegExp(`【${label}】([\\s\\S]*?)(?=【|$)`);
  return (block.match(re)?.[1] ?? "").replace(/\n+/g, " ").trim();
}

function severityFromLabel(label: string): RiskCard["severity"] {
  if (/高風險/.test(label)) return "high";
  if (/中風險/.test(label)) return "medium";
  return "low";
}

function parseAnswerToCards(answer: string): RiskCard[] {
  // Match each 第 X 條 block
  const blocks = answer.split(/(?=第\s*\d+\s*條[：:])/).filter((b) => /第\s*\d+\s*條/.test(b));
  if (blocks.length === 0) return [];

  return blocks.slice(0, 5).map((block, index) => {
    const titleMatch = block.match(/第\s*\d+\s*條[：:]\s*([^\n]+)/);
    const title = titleMatch?.[1]?.trim() || `條款 ${index + 1}`;
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

  // Try to parse structured answer first
  const parsed = parseAnswerToCards(msg.content);
  if (parsed.length > 0) return parsed;

  // Fallback: use raw chunks
  const chunks = msg.chunks ?? [];
  return chunks.slice(0, 5).map((chunk, index) => ({
    id: `chunk-${index}-${chunk.tag || "issue"}`,
    title: chunk.tag || `風險項目 ${index + 1}`,
    summary: chunk.text || "系統尚未提供摘要內容。",
    suggestion: "",
    severity: index === 0 ? "high" : index === 1 ? "medium" : "low",
    section: `Section ${index + 1}`,
  }));
});

const overallRiskScore = computed(() => {
  const cards = riskCards.value.filter((item) => item.id !== "placeholder");
  if (cards.length === 0) {
    return 0;
  }
  const total = cards.reduce((sum, card) => {
    if (card.severity === "high") {
      return sum + 84;
    }
    if (card.severity === "medium") {
      return sum + 61;
    }
    return sum + 34;
  }, 0);
  return Math.round(total / cards.length);
});

const complianceLabel = computed(() => {
  if (overallRiskScore.value >= 75) {
    return "High";
  }
  if (overallRiskScore.value >= 45) {
    return "Medium";
  }
  return "Low";
});

const keyDates = computed(() => [
  { label: "檔案名稱", value: currentDocumentTitle.value },
  { label: "來源數量", value: `${sourceRows.value.length} 份` },
  { label: "檢視頁次", value: pageLabel.value.replace("Page ", "") },
]);

const quickActions = [
  { label: "建議修改", prompt: "請依據目前內容提出修約建議。" },
  { label: "法遵檢查", prompt: "請檢查這份合約的法遵風險。" },
  { label: "風險摘要", prompt: "請整理這份合約的主要風險。" },
  { label: "匯出報告", prompt: "請整理成可以輸出的風險摘要報告。" },
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
  currentPreviewPage.value = 0;
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

function goToPreviousPreviewPage() {
  if (!canGoToPreviousPreviewPage.value) {
    return;
  }
  currentPreviewPage.value -= 1;
}

function goToNextPreviewPage() {
  if (!canGoToNextPreviewPage.value) {
    return;
  }
  currentPreviewPage.value += 1;
}

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
  railTab.value = "assistant";

  const body: ChatRequest = {
    message: raw,
    history: priorHistory,
    top_k: settings.topK,
    strict: settings.strict,
    chat_id: convId,
    rag_scope_chat_id: settings.resolveRagScopeChatId(convId),
    ...pendingPart,
  };

  // 插入空的 assistant placeholder，streaming 時逐步填入
  conversation.appendStreamingPlaceholder(convId);
  let hasReceivedTokens = false;

  try {
    await postChatStream(body, {
      onStatus(message) {
        streamingStatus.value = message;
      },
      onToken(fragment) {
        if (!hasReceivedTokens) {
          hasReceivedTokens = true;
          streamingStatus.value = null;
        }
        conversation.appendStreamingToken(convId, fragment);
      },
      onMeta(meta) {
        conversation.finalizeStreamingMessage(convId, meta);
        conversation.applyChatResponseNextFields(meta as any);
      },
      onDone() {
        streamingStatus.value = null;
      },
      onError(message) {
        streamingStatus.value = null;
        // 如果還沒收到任何 token，移除 placeholder
        if (!hasReceivedTokens) {
          conversation.removeLastMessage(convId);
        }
        chatError.value = new Error(message);
        pushToast({ variant: "error", message });
      },
    });
  } catch (error) {
    streamingStatus.value = null;
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
  }
}
</script>

<template>
  <div class="workspace-page">
    <ChatRetrievalSettingsModal
      v-model:open="settingsOpen"
      :scope-sync-state="scopeSyncState"
    />

    <section class="review-workspace">
      <div class="document-frame ds-card">
        <header class="viewer-toolbar">
          <div class="viewer-toolbar__left">
            <button type="button" class="viewer-icon" aria-label="縮小">-</button>
            <span class="viewer-zoom">100%</span>
            <button type="button" class="viewer-icon" aria-label="放大">+</button>
          </div>

          <div class="viewer-toolbar__center">
            <button
              type="button"
              class="viewer-icon"
              :disabled="!canGoToPreviousPreviewPage"
              aria-label="上一頁"
              @click="goToPreviousPreviewPage"
            >
              &lt;
            </button>
            <span class="viewer-page">{{ pageLabel }}</span>
            <button
              type="button"
              class="viewer-icon"
              :disabled="!canGoToNextPreviewPage"
              aria-label="下一頁"
              @click="goToNextPreviewPage"
            >
              &gt;
            </button>
          </div>

          <div class="viewer-toolbar__right">
            <button type="button" class="viewer-icon viewer-icon--wide" aria-label="下載">Save</button>
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
              v-for="(paragraph, index) in activePreviewPageParagraphs"
              :key="`${index}-${paragraph.slice(0, 24)}`"
              class="paper-section"
            >
              <p class="paper-body">{{ paragraph }}</p>
            </section>
          </article>
        </div>
      </div>

      <aside class="analysis-rail ds-card">
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
        </div>

        <div v-if="railTab === 'risk'" class="rail-panel">
          <section class="score-card">
            <p class="score-card__label">COMPLIANCE SCORE</p>
            <div class="score-card__row">
              <p class="score-card__value">{{ overallRiskScore }}<span>/100</span></p>
              <span class="score-chip" :class="`score-chip--${complianceLabel.toLowerCase()}`">
                {{ complianceLabel }}
              </span>
            </div>
          </section>

          <section class="report-section">
            <p class="report-section__title">KEY FINDINGS</p>
            <article
              v-for="card in riskCards"
              :key="card.id"
              class="finding-card"
            >
              <div class="finding-card__head">
                <h3>{{ card.title }}</h3>
                <span class="finding-chip" :class="`finding-chip--${card.severity}`">
                  {{ severityLabel(card.severity) }}
                </span>
              </div>
              <p v-if="card.section" class="finding-card__section">{{ card.section }}</p>
              <p class="finding-card__body">{{ card.summary }}</p>
              <div v-if="card.suggestion" class="finding-card__suggestion">
                <p class="finding-card__suggestion-label">AI 建議</p>
                <p class="finding-card__suggestion-body">{{ card.suggestion }}</p>
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
                <div v-else-if="isAssistantMessage(msg)" class="assistant-reply">
                  <ChatAssistantMessage :message="msg" />
                </div>
              </template>

              <!-- Streaming 狀態指示器 -->
              <div v-if="streamingStatus" class="streaming-indicator">
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
              type="button"
              data-testid="chat-send"
              class="assistant-composer__send"
              :disabled="sending || !input.trim()"
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
  min-height: 0;
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
}

.rail-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
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

.score-chip,
.finding-chip {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 0.78rem;
  font-weight: 800;
}

.score-chip--high,
.finding-chip--high {
  background: #fde8e8;
  color: #d64545;
}

.score-chip--medium,
.finding-chip--medium {
  background: #fff3d6;
  color: #b7791f;
}

.score-chip--low,
.finding-chip--low {
  background: #e8f8ee;
  color: #2f9e5f;
}

.report-section {
  margin-top: 20px;
}

.finding-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #ffffff;
  padding: 14px;
}

.finding-card + .finding-card {
  margin-top: 12px;
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

.finding-card__section {
  margin: 6px 0 0;
  font-size: 0.8rem;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.04em;
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
</style>
