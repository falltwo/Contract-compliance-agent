/**
 * SSE streaming chat client.
 *
 * 使用 POST + EventSource pattern 呼叫 `/api/v1/chat/stream`，
 * 透過回呼即時收到每個 token fragment，讓 UI 呈現打字機效果。
 */
import type { ChatRequest, ChatResponse } from "@/types/api";

/** resolveApiBaseUrl 與 client.ts 共用邏輯 */
function resolveApiBaseUrl(): string {
  const explicit = (import.meta.env.VITE_API_BASE_URL || "").trim();
  if (explicit) {
    return explicit.replace(/\/+$/, "");
  }
  if (import.meta.env.DEV) {
    return "";
  }
  if (typeof window === "undefined") {
    return "";
  }
  const apiPort = (import.meta.env.VITE_API_PORT || "8000").trim() || "8000";
  return `${window.location.protocol}//${window.location.hostname}:${apiPort}`;
}

export interface StreamChatCallbacks {
  /** 收到 status 事件（路由/檢索/生成等階段提示） */
  onStatus?: (message: string) => void;
  /** 收到 token fragment（增量文字片段，持續累加出完整回答） */
  onToken?: (fragment: string) => void;
  /** 收到 meta 事件（sources, chunks, tool_name 等最終資料） */
  onMeta?: (meta: Partial<ChatResponse>) => void;
  /** stream 結束 */
  onDone?: () => void;
  /** 發生錯誤 */
  onError?: (message: string) => void;
}

/**
 * 使用 fetch + ReadableStream 讀取 SSE。
 * 比原生 EventSource 更靈活（支援 POST body）。
 */
export async function postChatStream(
  body: ChatRequest,
  callbacks: StreamChatCallbacks,
): Promise<void> {
  const baseUrl = resolveApiBaseUrl();
  const url = `${baseUrl}/api/v1/chat/stream`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    callbacks.onError?.(`無法連接後端：${err instanceof Error ? err.message : String(err)}`);
    return;
  }

  if (!response.ok) {
    callbacks.onError?.(`HTTP ${response.status}: ${response.statusText}`);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onError?.("無法讀取 response stream");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // 解析 SSE：每個事件以 \n\n 分隔
      const parts = buffer.split("\n\n");
      // 最後一部分可能不完整，保留在 buffer
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.trim()) continue;

        let eventType = "";
        let dataStr = "";

        for (const line of part.split("\n")) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            dataStr = line.slice(6);
          }
        }

        if (!eventType || !dataStr) continue;

        try {
          const data = JSON.parse(dataStr);

          switch (eventType) {
            case "status":
              callbacks.onStatus?.(data.message || "");
              break;
            case "token":
              callbacks.onToken?.(data.t || "");
              break;
            case "meta":
              callbacks.onMeta?.(data);
              break;
            case "done":
              callbacks.onDone?.();
              break;
            case "error":
              callbacks.onError?.(data.message || "Unknown error");
              break;
          }
        } catch {
          // JSON parse error, skip this event
        }
      }
    }
  } catch (err) {
    callbacks.onError?.(`Stream 中斷：${err instanceof Error ? err.message : String(err)}`);
  } finally {
    reader.releaseLock();
  }
}
