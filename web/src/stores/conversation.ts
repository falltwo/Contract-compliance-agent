import { defineStore } from "pinia";

import type { ChatRequest, ChatResponse } from "@/types/api";
import type {
  AssistantConversationMessage,
  ConversationEntry,
} from "@/types/conversation";
import {
  loadPersistedConversations,
  savePersistedConversations,
} from "@/stores/conversationPersistence";
import { NEW_CONVERSATION_TITLE, titleFromFirstUserMessage } from "@/utils/chatTitle";

export type { ConversationEntry };

function newChatId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for HTTP (non-secure) contexts where randomUUID is unavailable.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function createDefaultState() {
  const firstId = newChatId();
  const now = Date.now();
  return {
    conversations: {
      [firstId]: {
        id: firstId,
        title: NEW_CONVERSATION_TITLE,
        messages: [],
        updatedAt: now,
      },
    } as Record<string, ConversationEntry>,
    activeConversationId: firstId as string | null,
    pendingWebVsRagOriginalQuestion: null as string | null,
    pendingChartConfirmationQuestion: null as string | null,
  };
}

export const useConversationStore = defineStore("conversation", {
  state: () => {
    const loaded = loadPersistedConversations();
    if (loaded) {
      return {
        conversations: loaded.conversations,
        activeConversationId: loaded.activeConversationId,
        pendingWebVsRagOriginalQuestion: loaded.pendingWebVsRagOriginalQuestion,
        pendingChartConfirmationQuestion: loaded.pendingChartConfirmationQuestion,
      };
    }
    return createDefaultState();
  },
  getters: {
    activeConversation(state): ConversationEntry | null {
      const id = state.activeConversationId;
      if (!id) {
        return null;
      }
      return state.conversations[id] ?? null;
    },
    conversationIds(state): string[] {
      return Object.keys(state.conversations);
    },
    /** 依 updatedAt 新到舊（側欄列表） */
    conversationIdsByRecency(state): string[] {
      return Object.keys(state.conversations).sort(
        (a, b) =>
          (state.conversations[b]?.updatedAt ?? 0) -
          (state.conversations[a]?.updatedAt ?? 0),
      );
    },
  },
  actions: {
    _persist() {
      savePersistedConversations({
        conversations: this.conversations,
        activeConversationId: this.activeConversationId,
        pendingWebVsRagOriginalQuestion: this.pendingWebVsRagOriginalQuestion,
        pendingChartConfirmationQuestion: this.pendingChartConfirmationQuestion,
      });
    },
    addConversation(): string {
      this.clearPendingFields();
      const id = newChatId();
      const now = Date.now();
      this.conversations[id] = {
        id,
        title: NEW_CONVERSATION_TITLE,
        messages: [],
        updatedAt: now,
      };
      this.activeConversationId = id;
      this._persist();
      return id;
    },
    setActiveConversation(chatId: string) {
      if (!this.conversations[chatId]) {
        return;
      }
      if (this.activeConversationId !== chatId) {
        this.clearPendingFields();
      }
      this.activeConversationId = chatId;
      const conv = this.conversations[chatId];
      if (conv) {
        conv.updatedAt = Date.now();
      }
      this._persist();
    },
    renameConversation(chatId: string, title: string) {
      const conv = this.conversations[chatId];
      if (!conv) {
        return;
      }
      const t = title.trim();
      conv.title = t || NEW_CONVERSATION_TITLE;
      conv.updatedAt = Date.now();
      this._persist();
    },
    deleteConversation(chatId: string) {
      if (!(chatId in this.conversations)) {
        return;
      }
      const wasActive = this.activeConversationId === chatId;
      delete this.conversations[chatId];
      if (wasActive) {
        this.clearPendingFields();
      }
      if (this.activeConversationId !== chatId) {
        this._persist();
        return;
      }
      const remaining = Object.keys(this.conversations);
      if (remaining.length > 0) {
        this.activeConversationId = remaining[0];
        const c = this.conversations[remaining[0]];
        if (c) {
          c.updatedAt = Date.now();
        }
        this._persist();
        return;
      }
      const id = newChatId();
      const now = Date.now();
      this.conversations[id] = {
        id,
        title: NEW_CONVERSATION_TITLE,
        messages: [],
        updatedAt: now,
      };
      this.activeConversationId = id;
      this._persist();
    },
    appendUserMessage(chatId: string, content: string) {
      const conv = this.conversations[chatId];
      if (!conv) {
        return;
      }
      const text = content;
      conv.messages.push({ role: "user", content: text });
      if (conv.title === NEW_CONVERSATION_TITLE && conv.messages.length === 1) {
        conv.title = titleFromFirstUserMessage(text);
      }
      conv.updatedAt = Date.now();
      this._persist();
    },
    appendAssistantFromResponse(chatId: string, response: ChatResponse) {
      const conv = this.conversations[chatId];
      if (!conv) {
        return;
      }
      const msg: AssistantConversationMessage = {
        role: "assistant",
        content: response.answer ?? "",
        sources: [...(response.sources ?? [])],
        chunks: [...(response.chunks ?? [])],
        tool_name: response.tool_name ?? "",
        extra: response.extra ?? null,
      };
      conv.messages.push(msg);
      conv.updatedAt = Date.now();
      this._persist();
    },
    removeLastMessage(chatId: string) {
      const conv = this.conversations[chatId];
      if (!conv || conv.messages.length === 0) {
        return;
      }
      conv.messages.pop();
      conv.updatedAt = Date.now();
      this._persist();
    },
    applyChatResponseNextFields(
      response: Pick<
        ChatResponse,
        | "next_original_question_for_clarification"
        | "next_chart_confirmation_question"
      >,
    ) {
      this.pendingWebVsRagOriginalQuestion =
        response.next_original_question_for_clarification ?? null;
      this.pendingChartConfirmationQuestion =
        response.next_chart_confirmation_question ?? null;
      this._persist();
    },
    clearPendingFields() {
      this.pendingWebVsRagOriginalQuestion = null;
      this.pendingChartConfirmationQuestion = null;
      this._persist();
    },
    consumePendingForNextUserMessage(userMessage: string): Partial<
      Pick<
        ChatRequest,
        | "original_question"
        | "clarification_reply"
        | "chart_confirmation_question"
        | "chart_confirmation_reply"
      >
    > {
      if (this.pendingChartConfirmationQuestion != null) {
        const chart_confirmation_question = this.pendingChartConfirmationQuestion;
        this.pendingChartConfirmationQuestion = null;
        this._persist();
        return {
          chart_confirmation_question,
          chart_confirmation_reply: userMessage,
        };
      }
      if (this.pendingWebVsRagOriginalQuestion != null) {
        const original_question = this.pendingWebVsRagOriginalQuestion;
        this.pendingWebVsRagOriginalQuestion = null;
        this._persist();
        return {
          original_question,
          clarification_reply: userMessage,
        };
      }
      return {};
    },
    seedPendingForDev(
      partial: Partial<{
        pendingWebVsRagOriginalQuestion: string | null;
        pendingChartConfirmationQuestion: string | null;
      }>,
    ) {
      if ("pendingWebVsRagOriginalQuestion" in partial) {
        this.pendingWebVsRagOriginalQuestion =
          partial.pendingWebVsRagOriginalQuestion ?? null;
      }
      if ("pendingChartConfirmationQuestion" in partial) {
        this.pendingChartConfirmationQuestion =
          partial.pendingChartConfirmationQuestion ?? null;
      }
      this._persist();
    },
  },
});
