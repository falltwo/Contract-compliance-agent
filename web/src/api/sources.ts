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
