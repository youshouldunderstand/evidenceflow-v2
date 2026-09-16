import type { ReportResponse, ResearchArtifacts, ResearchProgress, ResearchTask } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail ?? message;
    } catch {
      // 非 JSON 错误响应沿用状态码消息。
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function createResearch(query: string): Promise<string> {
  const response = await request<{ task_id: string }>("/api/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, depth: "standard" }),
  });
  return response.task_id;
}

export function getResearch(taskId: string): Promise<ResearchTask> {
  return request(`/api/research/${encodeURIComponent(taskId)}`);
}

export function getReport(taskId: string): Promise<ReportResponse> {
  return request(`/api/reports/${encodeURIComponent(taskId)}`);
}

export function getArtifacts(taskId: string): Promise<ResearchArtifacts> {
  return request(`/api/research/${encodeURIComponent(taskId)}/artifacts`);
}

export async function getProgress(taskId: string): Promise<ResearchProgress> {
  const response = await request<{ progress: ResearchProgress }>(
    `/api/research/${encodeURIComponent(taskId)}/progress`,
  );
  return response.progress;
}

export function resumeResearch(taskId: string): Promise<{ accepted: boolean }> {
  return request(`/api/research/${encodeURIComponent(taskId)}/resume`, {
    method: "POST",
  });
}

export function eventStreamUrl(taskId: string, after = 0): string {
  return `${API_BASE}/api/research/${encodeURIComponent(taskId)}/events?after=${after}`;
}
