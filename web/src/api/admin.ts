import { apiClient } from "@/api/client";
import type { HealthResponse } from "@/types/api";

export interface AdminServiceStatus {
  name: string;
  description?: string | null;
  active_state: string;
  sub_state: string;
  unit_file_state: string;
  error?: string | null;
}

export interface AdminServicesStatusResponse {
  services: AdminServiceStatus[];
}

export interface AdminServicesRestartResponse {
  requested_services: string[];
  restarted_services: string[];
  failed_services: string[];
  services: AdminServiceStatus[];
}

export interface AdminOllamaModel {
  name: string;
  model_id: string;
  size: string;
  modified: string;
}

export interface AdminOllamaModelsResponse {
  models: AdminOllamaModel[];
  error?: string | null;
}

export interface AdminDockerContainer {
  container_id: string;
  name: string;
  image: string;
  status: string;
  state: string;
}

export interface AdminDockerContainersResponse {
  engine_available: boolean;
  containers: AdminDockerContainer[];
  error?: string | null;
}

export async function getAdminHealth(
  options?: { showLoading?: boolean },
): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/health", {
    showLoading: options?.showLoading,
  });
  return data;
}

export async function getAdminServices(
  options?: { showLoading?: boolean },
): Promise<AdminServicesStatusResponse> {
  const { data } = await apiClient.get<AdminServicesStatusResponse>("/api/v1/admin/services", {
    showLoading: options?.showLoading,
  });
  return data;
}

export async function postAdminRestartServices(
  services: string[],
  options?: { showLoading?: boolean },
): Promise<AdminServicesRestartResponse> {
  const { data } = await apiClient.post<AdminServicesRestartResponse>(
    "/api/v1/admin/services/restart",
    { services },
    { showLoading: options?.showLoading },
  );
  return data;
}

export async function getAdminOllamaModels(
  options?: { showLoading?: boolean },
): Promise<AdminOllamaModelsResponse> {
  const { data } = await apiClient.get<AdminOllamaModelsResponse>("/api/v1/admin/ollama/models", {
    showLoading: options?.showLoading,
  });
  return data;
}

export async function getAdminDockerContainers(
  options?: { showLoading?: boolean },
): Promise<AdminDockerContainersResponse> {
  const { data } = await apiClient.get<AdminDockerContainersResponse>(
    "/api/v1/admin/docker/containers",
    { showLoading: options?.showLoading },
  );
  return data;
}

