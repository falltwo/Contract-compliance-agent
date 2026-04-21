import { apiClient } from "@/api/client";
import type { SourcesListResponse } from "@/types/api";

export interface SourcePreviewResponse {
  source: string;
  chat_id: string | null;
  title: string;
  content: string;
  chunk_count: number;
}

export async function getSources(
  chatId?: string | null,
  options?: { showLoading?: boolean },
): Promise<SourcesListResponse> {
  const params = chatId != null && chatId !== "" ? { chat_id: chatId } : undefined;
  const { data } = await apiClient.get<SourcesListResponse>("/api/v1/sources", {
    params,
    showLoading: options?.showLoading,
  });
  return data;
}

export async function deleteSource(
  source: string,
  chatId?: string | null,
  options?: { showLoading?: boolean },
): Promise<void> {
  const params =
    chatId != null && chatId !== ""
      ? { source, chat_id: chatId }
      : { source };
  await apiClient.delete("/api/v1/sources", {
    params,
    showLoading: options?.showLoading,
  });
}

export async function downloadSource(source: string): Promise<void> {
  const url = `/api/v1/sources/download?source=${encodeURIComponent(source)}`;
  const res = await fetch(url);
  if (!res.ok) {
    if (res.status === 404) {
      throw new Error("原始檔案不存在，請重新上傳後再下載。");
    }
    throw new Error(`下載失敗（${res.status}）`);
  }
  const blob = await res.blob();
  const filename = decodeURIComponent(source.split("/").pop() ?? "download");
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

export async function getSourcePreview(
  source: string,
  chatId?: string | null,
  options?: { showLoading?: boolean },
): Promise<SourcePreviewResponse> {
  const params =
    chatId != null && chatId !== ""
      ? { source, chat_id: chatId }
      : { source };
  const { data } = await apiClient.get<SourcePreviewResponse>("/api/v1/sources/preview", {
    params,
    showLoading: options?.showLoading,
  });
  return data;
}
