<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { postIngestUpload } from "@/api/ingest";
import { getSources } from "@/api/sources";
import { IS_ADMIN_TARGET } from "@/config/runtime";
import { pushToast } from "@/state/toast";
import { useConversationStore } from "@/stores/conversation";
import { isAssistantMessage } from "@/types/conversation";
import { parseSourceRow } from "@/utils/sourceEntry";
import { syncRagScopeFromSourcesForChat } from "@/utils/syncRagScopeFromSources";

type LibraryDoc = {
  id: string;
  title: string;
  chatId: string | null;
  source: string | null;
  updatedAt: number;
};

const route = useRoute();
const router = useRouter();
const conversation = useConversationStore();

const libraryQuery = ref("");
const uploadDocs = ref<LibraryDoc[]>([]);
const uploadInput = ref<HTMLInputElement | null>(null);
const uploadBusy = ref(false);

const isFrontWorkspace = computed(() => !IS_ADMIN_TARGET);

const recentDocs = computed<LibraryDoc[]>(() =>
  conversation.conversationIdsByRecency.map((id) => ({
    id,
    title: conversation.conversations[id]?.title?.trim() || "未命名對話",
    chatId: id,
    source: null,
    updatedAt: conversation.conversations[id]?.updatedAt ?? Date.now(),
  })),
);

const filteredRecentDocs = computed(() => recentDocs.value.filter((doc) => matchesQuery(doc.title)));
const filteredIndexedDocs = computed(() =>
  uploadDocs.value.filter((doc) => matchesQuery(`${doc.title} ${doc.source ?? ""}`)),
);
const documentCount = computed(() => uploadDocs.value.length);

function matchesQuery(value: string): boolean {
  const query = libraryQuery.value.trim().toLowerCase();
  if (!query) {
    return true;
  }
  return value.toLowerCase().includes(query);
}

function displaySourceName(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const leaf = normalized.split("/").pop() || path;
  return leaf.replace(/#chunk\d+$/i, "");
}

function uniqueDocs(rows: { source: string; chat_id: string | null }[]): LibraryDoc[] {
  const seen = new Set<string>();
  return rows
    .map((row) => ({
      id: `${row.chat_id ?? "global"}-${row.source}`,
      title: displaySourceName(row.source),
      chatId: row.chat_id,
      source: row.source,
      updatedAt: Date.now(),
    }))
    .filter((item) => {
      const key = `${item.source ?? item.title}-${item.chatId ?? "global"}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function fallbackDocs(): LibraryDoc[] {
  return recentDocs.value.slice(0, 12);
}

function latestRiskTone(chatId: string | null): "high" | "medium" | "low" | "neutral" {
  if (!chatId || !conversation.conversations[chatId]) {
    return "neutral";
  }

  const messages = conversation.conversations[chatId].messages;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!isAssistantMessage(message)) {
      continue;
    }
    const count = message.chunks?.length ?? 0;
    if (count >= 3) {
      return "high";
    }
    if (count >= 1) {
      return "medium";
    }
    return "low";
  }

  return messages.length > 0 ? "low" : "neutral";
}

function openRecentConversation(chatId: string) {
  conversation.setActiveConversation(chatId);
  void router.push("/chat");
}

function triggerUploadPicker() {
  if (!uploadBusy.value) {
    uploadInput.value?.click();
  }
}

function newConversation() {
  const id = conversation.addConversation();
  void router.push({ path: "/chat", query: { chat: id } });
}

async function onUploadFileChange(event: Event) {
  const input = event.target as HTMLInputElement | null;
  const files = input?.files ? Array.from(input.files) : [];
  if (files.length === 0 || uploadBusy.value) {
    return;
  }

  let chatId = conversation.activeConversationId;
  if (!chatId) {
    chatId = conversation.addConversation();
  }

  uploadBusy.value = true;
  try {
    await postIngestUpload(files, chatId, { showLoading: true });
    await syncRagScopeFromSourcesForChat(chatId, { showLoading: false });
    await loadLibrary();
    pushToast({
      variant: "info",
      message: `已上傳 ${files.length} 份文件並完成索引。`,
    });
    conversation.setActiveConversation(chatId);
    void router.push({ path: "/chat", query: { chat: chatId } });
  } catch (error) {
    pushToast({
      variant: "error",
      message: error instanceof Error ? error.message : String(error),
    });
  } finally {
    uploadBusy.value = false;
    if (input) {
      input.value = "";
    }
  }
}

function openIndexedDoc(doc: LibraryDoc) {
  let activeId = conversation.activeConversationId;
  if (doc.chatId && conversation.conversations[doc.chatId]) {
    conversation.setActiveConversation(doc.chatId);
    activeId = doc.chatId;
  } else if (!activeId) {
    activeId = conversation.addConversation();
    conversation.renameConversation(activeId, doc.title);
  }

  const targetChatId = doc.chatId ?? activeId;
  void router.push({
    path: "/chat",
    query: {
      ...(doc.source ? { source: doc.source } : {}),
      ...(targetChatId ? { chat: targetChatId } : {}),
    },
  });
}

async function loadLibrary() {
  try {
    const response = await getSources(null, { showLoading: false });
    const rows = Array.isArray(response.entries)
      ? response.entries.map((row) => parseSourceRow(row as Record<string, unknown>))
      : [];
    const docs = uniqueDocs(rows);
    uploadDocs.value = docs.length > 0 ? docs : fallbackDocs();
  } catch {
    uploadDocs.value = fallbackDocs();
  }
}

watch(
  () => route.fullPath,
  () => {
    if (isFrontWorkspace.value) {
      void loadLibrary();
    }
  },
);

onMounted(() => {
  if (isFrontWorkspace.value) {
    void loadLibrary();
  }
});
</script>

<template>
  <div class="shell" :class="{ 'shell--admin': IS_ADMIN_TARGET }">
    <header class="topbar">
      <div class="topbar-brand">
        <div class="topbar-mark" aria-hidden="true">[]</div>
        <div class="topbar-copy">
          <p class="topbar-title">合約法遵助理</p>
          <p class="topbar-subtitle">Contract Review Workspace</p>
        </div>
      </div>

      <label v-if="isFrontWorkspace" class="topbar-search" for="global-doc-search">
        <span class="topbar-search__icon" aria-hidden="true">Q</span>
        <input
          id="global-doc-search"
          v-model="libraryQuery"
          type="search"
          class="topbar-search__input"
          placeholder="搜尋檔名、來源或審閱紀錄"
        >
      </label>

      <div v-if="isFrontWorkspace" class="topbar-actions"></div>
    </header>

    <aside class="sidebar" :aria-label="isFrontWorkspace ? '文件區' : '管理區'">
      <template v-if="isFrontWorkspace">
        <input
          ref="uploadInput"
          type="file"
          class="sr-only-upload"
          multiple
          accept=".txt,.md,.pdf,.docx"
          @change="onUploadFileChange"
        >

        <div class="sidebar-title">
          <h2>Documents</h2>
          <button type="button" class="sidebar-title__collapse" aria-label="收合側欄">
            &lt;
          </button>
        </div>

        <label class="sidebar-search" for="sidebar-doc-search">
          <span aria-hidden="true">Q</span>
          <input
            id="sidebar-doc-search"
            v-model="libraryQuery"
            type="search"
            placeholder="Search documents..."
          >
        </label>

        <div class="sidebar-actions">
          <button
            type="button"
            class="sidebar-upload"
            :disabled="uploadBusy"
            @click="triggerUploadPicker"
          >
            {{ uploadBusy ? "上傳中" : "上傳" }}
          </button>
          <button
            type="button"
            class="sidebar-new-chat"
            title="新對話"
            @click="newConversation"
          >
            +
          </button>
        </div>

        <section class="doc-group">
          <div class="doc-group__head">
            <span class="doc-group__chevron" aria-hidden="true">v</span>
            <h3>最近審閱</h3>
          </div>
          <div class="doc-group__list">
            <button
              v-for="doc in filteredRecentDocs"
              :key="doc.id"
              type="button"
              class="doc-node"
              :class="{ 'doc-node--active': conversation.activeConversationId === doc.chatId && route.path === '/chat' }"
              @click="openRecentConversation(doc.chatId || doc.id)"
            >
              <span class="doc-node__icon" aria-hidden="true"></span>
              <span class="doc-node__title">{{ doc.title }}</span>
              <span class="doc-node__dot" :class="`doc-node__dot--${latestRiskTone(doc.chatId)}`"></span>
            </button>
            <p v-if="filteredRecentDocs.length === 0" class="doc-empty">尚無最近審閱紀錄。</p>
          </div>
        </section>

        <section class="doc-group doc-group--grow">
          <div class="doc-group__head">
            <span class="doc-group__chevron" aria-hidden="true">v</span>
            <h3>已索引文件</h3>
            <span class="doc-group__count">{{ documentCount }}</span>
          </div>
          <div class="doc-group__list">
            <button
              v-for="doc in filteredIndexedDocs"
              :key="doc.id"
              type="button"
              class="doc-node"
              @click="openIndexedDoc(doc)"
            >
              <span class="doc-node__icon" aria-hidden="true"></span>
              <span class="doc-node__title">{{ doc.title }}</span>
              <span class="doc-node__dot" :class="`doc-node__dot--${latestRiskTone(doc.chatId)}`"></span>
            </button>
            <p v-if="filteredIndexedDocs.length === 0" class="doc-empty">找不到符合條件的文件。</p>
          </div>
        </section>

        <footer class="sidebar-footer">
          <p class="sidebar-footer__title">Risk Levels</p>
          <div class="risk-legend">
            <span><i class="legend-dot legend-dot--high"></i> High</span>
            <span><i class="legend-dot legend-dot--medium"></i> Medium</span>
            <span><i class="legend-dot legend-dot--low"></i> Low</span>
          </div>
        </footer>
      </template>

      <template v-else>
        <section class="doc-group doc-group--grow">
          <div class="doc-group__head">
            <h3>管理面板</h3>
          </div>
          <div class="doc-group__list">
            <RouterLink to="/admin" class="topbar-link topbar-link--block">前往後台</RouterLink>
          </div>
        </section>
      </template>
    </aside>

    <main class="main">
      <div class="main-inner">
        <slot />
      </div>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 244px minmax(0, 1fr);
  grid-template-rows: 68px minmax(0, 1fr);
  min-height: 100vh;
  min-height: 100dvh;
  background:
    linear-gradient(180deg, #eef3f9 0%, #f5f8fc 100%);
}

.shell--admin {
  background: #111827;
}

.topbar {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 20px;
  padding: 0 18px;
  background: #10293f;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  color: #f8fbff;
}

.topbar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.topbar-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.16);
  font-size: 0.74rem;
  font-weight: 800;
  letter-spacing: -0.04em;
}

.topbar-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.topbar-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 800;
}

.topbar-subtitle {
  margin: 0;
  font-size: 0.78rem;
  color: rgba(232, 240, 248, 0.7);
}

.topbar-search {
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(540px, 100%);
  min-height: 38px;
  padding: 0 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.topbar-search__icon {
  color: rgba(232, 240, 248, 0.72);
  font-size: 0.82rem;
  font-weight: 700;
}

.topbar-search__input {
  width: 100%;
  border: none;
  outline: none;
  background: transparent;
  color: #f8fbff;
  font: inherit;
}

.topbar-search__input::placeholder {
  color: rgba(232, 240, 248, 0.52);
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.topbar-link,
.topbar-upload {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 34px;
  padding: 0 13px;
  border-radius: 10px;
  font-size: 0.88rem;
  font-weight: 700;
  text-decoration: none;
}

.topbar-link {
  color: #e2e8f0;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.06);
}

.topbar-link--block {
  width: 100%;
}

.topbar-upload {
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: #ffffff;
  color: #10293f;
  cursor: pointer;
}

.topbar-upload:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 16px 10px 12px;
  background: #ffffff;
  border-right: 1px solid #d7e2ee;
}

.sidebar-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 4px;
}

.sidebar-title h2 {
  margin: 0;
  font-size: 0.98rem;
  font-weight: 800;
  color: #0f172a;
}

.sidebar-title__collapse {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #64748b;
  font-size: 0.92rem;
  cursor: pointer;
}

.sidebar-title__collapse:hover {
  background: #f1f5f9;
}

.sidebar-search {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #f6f8fb;
  color: #64748b;
}

.sidebar-search input {
  width: 100%;
  border: none;
  outline: none;
  background: transparent;
  color: #0f172a;
  font: inherit;
}

.sidebar-actions {
  display: flex;
  gap: 6px;
}

.sidebar-upload {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-height: 38px;
  border: 1px solid #d7e2ee;
  border-radius: 10px;
  background: #ffffff;
  color: #10293f;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
}

.sidebar-upload:hover:not(:disabled) {
  background: #f8fbff;
}

.sidebar-upload:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.sidebar-new-chat {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  min-height: 38px;
  flex-shrink: 0;
  border: 1px solid #d7e2ee;
  border-radius: 10px;
  background: #ffffff;
  color: #10293f;
  font-size: 1.25rem;
  font-weight: 400;
  cursor: pointer;
  line-height: 1;
}

.sidebar-new-chat:hover {
  background: #f8fbff;
}

.doc-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.doc-group--grow {
  flex: 1;
}

.doc-group__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px;
  color: #475569;
}

.doc-group__head h3 {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 800;
  color: #344256;
}

.doc-group__chevron {
  font-size: 0.74rem;
  color: #94a3b8;
}

.doc-group__count {
  margin-left: auto;
  border-radius: 999px;
  background: #edf2f7;
  padding: 2px 7px;
  font-size: 0.72rem;
  font-weight: 700;
  color: #64748b;
}

.doc-group__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
  overflow: auto;
}

.doc-node {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 11px 10px 11px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: #0f172a;
  transition: background-color 140ms ease, border-color 140ms ease, color 140ms ease;
}

.doc-node:hover {
  background: #f7fbff;
  border-color: #d7e2ee;
}

.doc-node--active {
  background: #10293f;
  border-color: #10293f;
  color: #ffffff;
  box-shadow: 0 8px 18px rgba(16, 41, 63, 0.16);
}

.doc-node__icon {
  position: relative;
  display: inline-block;
  width: 14px;
  height: 18px;
  border: 1.5px solid currentColor;
  border-radius: 3px;
  box-sizing: border-box;
  opacity: 0.72;
}

.doc-node__icon::before,
.doc-node__icon::after {
  content: "";
  position: absolute;
  left: 2px;
  right: 2px;
  height: 1.5px;
  background: currentColor;
  border-radius: 999px;
}

.doc-node__icon::before {
  top: 5px;
}

.doc-node__icon::after {
  top: 9px;
}

.doc-node__title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 0.92rem;
  font-weight: 600;
}

.doc-node__dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
}

.doc-node__dot--high,
.legend-dot--high {
  background: #ef4444;
}

.doc-node__dot--medium,
.legend-dot--medium {
  background: #eab308;
}

.doc-node__dot--low,
.legend-dot--low {
  background: #22c55e;
}

.doc-node__dot--neutral {
  background: #cbd5e1;
}

.doc-empty {
  margin: 0;
  padding: 8px 4px;
  font-size: 0.86rem;
  color: #94a3b8;
}

.sidebar-footer {
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid #e2e8f0;
}

.sidebar-footer__title {
  margin: 0;
  font-size: 0.8rem;
  font-weight: 800;
  color: #64748b;
}

.risk-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 0.78rem;
  color: #64748b;
}

.risk-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
}

.sr-only-upload {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.main {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  background: transparent;
}

.main-inner {
  height: calc(100vh - 68px - 32px);
  min-height: 0;
}

@media (max-width: 1180px) {
  .shell {
    grid-template-columns: 1fr;
    grid-template-rows: 68px auto minmax(0, 1fr);
  }

  .topbar {
    grid-template-columns: 1fr;
    gap: 12px;
    padding: 12px 16px;
  }

  .topbar-search {
    width: 100%;
  }

  .sidebar {
    border-right: none;
    border-bottom: 1px solid #d7e2ee;
  }

  .main-inner {
    height: auto;
  }
}
</style>
