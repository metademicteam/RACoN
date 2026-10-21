import {
  SystemStatus,
  NodeItem,
  ModelItem,
  SchedulerPreviewResult,
  JobItem,
  BenchmarkRecord,
  ModelCatalogResponse,
} from "./types";

function getInitialApiBase(): string {
  if (typeof window !== "undefined") {
    const stored = localStorage.getItem("racon_api_url");
    if (stored) return stored.replace(/\/$/, "");

    const hostname = window.location.hostname;
    if (hostname && hostname !== "localhost" && hostname !== "127.0.0.1" && hostname !== "tauri.localhost") {
      return `${window.location.protocol}//${hostname}:8765`;
    }
  }
  return ((import.meta as any).env?.VITE_API_BASE as string) || "http://127.0.0.1:8765";
}

let API_BASE = getInitialApiBase();
let AUTH_TOKEN = (typeof localStorage !== "undefined" && localStorage.getItem("racon_auth_token")) || "racon-local-dev-token";

export function getApiBase(): string {
  return API_BASE;
}

export function setApiBase(url: string) {
  API_BASE = url.replace(/\/$/, "");
  if (typeof localStorage !== "undefined") {
    localStorage.setItem("racon_api_url", API_BASE);
  }
}

export function getAuthToken(): string {
  return AUTH_TOKEN;
}

export function setAuthToken(token: string) {
  AUTH_TOKEN = token;
  if (typeof localStorage !== "undefined") {
    localStorage.setItem("racon_auth_token", token);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  if (AUTH_TOKEN) {
    headers.set("X-RACON-TOKEN", AUTH_TOKEN);
    headers.set("Authorization", `Bearer ${AUTH_TOKEN}`);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let errorDetail = res.statusText;
    try {
      const json = await res.json();
      errorDetail = json.detail || JSON.stringify(json);
    } catch {
      // ignore
    }
    throw new Error(`API ${res.status}: ${errorDetail}`);
  }

  return res.json();
}

export const api = {
  getStatus: () => request<SystemStatus>("/api/v1/status"),
  getHealth: () => request<{ status: string }>("/health"),

  // Nodes
  getNodes: () => request<NodeItem[]>("/api/v1/nodes"),
  startNode: (nodeId: string) =>
    request<{ status: string }>(`/api/v1/nodes/${nodeId}/start`, { method: "POST" }),
  stopNode: (nodeId: string) =>
    request<{ status: string }>(`/api/v1/nodes/${nodeId}/stop`, { method: "POST" }),

  // Models & Runtimes
  getModels: () => request<ModelItem[]>("/api/v1/models"),
  getModelCatalog: () => request<ModelCatalogResponse>("/api/v1/models/catalog"),
  pullModel: (modelName: string) =>
    request<{ status: string; message: string }>("/api/v1/models/pull", {
      method: "POST",
      body: JSON.stringify({ model_name: modelName }),
    }),
  getPullStatus: (modelName: string) =>
    request<{
      status: string;
      percentage: number;
      message: string;
      error?: string | null;
      completed_bytes?: number;
      total_bytes?: number;
    }>(`/api/v1/models/pull/status?model_name=${encodeURIComponent(modelName)}`),
  refreshModels: () =>
    request<{ refreshed: number }>("/api/v1/models/refresh", { method: "POST" }),
  runInference: (modelName: string, prompt: string) =>
    request<{ response: string; duration_ms: number }>("/api/v1/models/infer", {
      method: "POST",
      body: JSON.stringify({ model_name: modelName, prompt }),
    }),

  // Scheduler
  getSchedulerConfig: () => request<{ mode: string; objective: string }>("/api/v1/scheduler/config"),
  updateSchedulerConfig: (config: { mode: string; objective: string }) =>
    request<{ status: string }>("/api/v1/scheduler/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  previewScheduler: (scenarioName: string, objective: string, mode: string) =>
    request<SchedulerPreviewResult>("/api/v1/scheduler/preview", {
      method: "POST",
      body: JSON.stringify({ scenario_name: scenarioName, objective, mode }),
    }),

  // Jobs
  getJobs: () => request<JobItem[]>("/api/v1/jobs"),
  createJob: (modelName: string, prompt: string) =>
    request<JobItem>("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify({ model_name: modelName, prompt }),
    }),
  cancelJob: (jobId: string) =>
    request<{ status: string }>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" }),

  // Benchmarks
  getBenchmarks: () => request<BenchmarkRecord[]>("/api/v1/benchmarks"),
  runBenchmark: (scenarioName: string, objective: string, mode: string) =>
    request<BenchmarkRecord>("/api/v1/benchmarks", {
      method: "POST",
      body: JSON.stringify({ scenario_name: scenarioName, objective, mode }),
    }),

  // Logs & Settings
  getLogs: (limit = 100) => request<string[]>(`/api/v1/logs?limit=${limit}`),
  getSettings: () => request<Record<string, string>>("/api/v1/settings"),
  updateSetting: (key: string, value: string) =>
    request<{ status: string }>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({ key, value }),
    }),
};
