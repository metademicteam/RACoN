import {
  SystemStatus,
  NodeItem,
  ModelItem,
  SchedulerPreviewResult,
  JobItem,
  CreateJobPayload,
  BenchmarkRecord,
  ModelCatalogResponse,
  LogEvent,
} from "./types";

function readLaunchToken(): string {
  if (typeof window === "undefined") return "";
  try {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("token");
    if (fromUrl) return fromUrl.trim();
  } catch {
    // ignore malformed query strings
  }
  return "";
}

export const DEFAULT_API_BASE = (
  ((import.meta as any).env?.VITE_DEFAULT_API_BASE as string) ||
  "https://desktop-b7l73cl.tail6cb521.ts.net"
).replace(/\/$/, "");

// Limited-scope client token compiled into release builds. Operator-only routes still
// require the server's administrator token, which is never shipped in the app.
export const DEFAULT_AUTH_TOKEN =
  ((import.meta as any).env?.VITE_DEFAULT_AUTH_TOKEN as string) || "";

const LEGACY_DEFAULT_TOKEN = "racon-local-dev-token";

function getInitialApiBase(): string {
  if (typeof window !== "undefined") {
    const stored = localStorage.getItem("racon_api_url");
    if (stored) return stored.replace(/\/$/, "");
  }
  return DEFAULT_API_BASE;
}

let API_BASE = getInitialApiBase();

const launchToken = readLaunchToken();
const storedTokenRaw =
  typeof localStorage !== "undefined"
    ? localStorage.getItem("racon_auth_token") || ""
    : "";
// Previous builds shipped the shared development token. Drop it once the build has a
// scoped client token so upgraded installs stop sending a value the server now rejects.
const storedToken =
  storedTokenRaw === LEGACY_DEFAULT_TOKEN && DEFAULT_AUTH_TOKEN !== LEGACY_DEFAULT_TOKEN
    ? ""
    : storedTokenRaw;
const servedByLocalEngine =
  typeof window !== "undefined" && API_BASE === window.location.origin;

// Pair the token with the endpoint: the launch token belongs to the local engine,
// while the compiled default belongs to the RACoN server. This prevents the local
// per-install token from overwriting the server token on every desktop launch.
let AUTH_TOKEN = servedByLocalEngine
  ? launchToken || storedToken || DEFAULT_AUTH_TOKEN
  : storedToken || DEFAULT_AUTH_TOKEN;

if (typeof localStorage !== "undefined" && AUTH_TOKEN && !storedToken) {
  localStorage.setItem("racon_auth_token", AUTH_TOKEN);
}

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

function isLoopbackOrigin(origin: string): boolean {
  return /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i.test(origin);
}

// Hardware shown in the desktop UI belongs to this machine, not to the remote
// coordinator. The shell serves the SPA from the local engine, so ask that engine
// directly and fall back to the configured API when the UI is a remote web page.
export async function getLocalEngineStatus(): Promise<SystemStatus | null> {
  if (typeof window === "undefined") return null;
  const origin = window.location.origin;
  if (!isLoopbackOrigin(origin) || origin === API_BASE) return null;
  const token = launchToken || AUTH_TOKEN;
  try {
    const res = await fetch(`${origin}/api/v1/status`, {
      headers: token
        ? { "X-RACON-TOKEN": token, Authorization: `Bearer ${token}` }
        : {},
    });
    if (!res.ok) return null;
    return (await res.json()) as SystemStatus;
  } catch {
    return null;
  }
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra || {});
  headers.set("Content-Type", "application/json");
  if (AUTH_TOKEN) {
    headers.set("X-RACON-TOKEN", AUTH_TOKEN);
    headers.set("Authorization", `Bearer ${AUTH_TOKEN}`);
  }
  return headers;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: authHeaders(options.headers),
  });

  if (!res.ok) {
    let errorDetail = res.statusText;
    try {
      const json = await res.json();
      errorDetail =
        json?.error?.message || json?.detail || JSON.stringify(json);
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(`API ${res.status}: ${errorDetail}`);
  }

  return res.json();
}

export interface ChatStreamMeta {
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    reported?: boolean;
  };
  racon?: {
    runtime?: string;
    time_to_first_token_ms?: number | null;
    duration_ms?: number;
  };
}

export interface ChatStreamPayload {
  model: string;
  messages: { role: string; content: string }[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
}

export interface ChatStreamHandlers {
  onDelta: (text: string) => void;
  onDone: (meta: ChatStreamMeta) => void;
  onError: (error: { code: string; message: string }) => void;
}

/** Stream a completion over SSE. Returns an abort function. */
export function streamChat(
  payload: ChatStreamPayload,
  handlers: ChatStreamHandlers
): () => void {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/v1/chat/completions`, {
        method: "POST",
        headers: authHeaders({ Accept: "text/event-stream" }),
        body: JSON.stringify({ ...payload, stream: true }),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError({ code: "network_error", message: String(err) });
      }
      return;
    }

    if (!response.ok || !response.body) {
      let message = response.statusText;
      let code = "http_error";
      try {
        const body = await response.json();
        message = body?.error?.message || message;
        code = body?.error?.code || code;
      } catch {
        // ignore
      }
      handlers.onError({ code, message });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let meta: ChatStreamMeta = {};

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");

          if (!frame.startsWith("data:")) continue;
          const data = frame.slice(5).trim();
          if (data === "[DONE]") {
            handlers.onDone(meta);
            return;
          }
          try {
            const event = JSON.parse(data);
            if (event?.error) {
              handlers.onError({
                code: event.error.code || "inference_error",
                message: event.error.message || "Generation failed",
              });
              return;
            }
            if (event?.usage) meta.usage = event.usage;
            if (event?.racon) meta.racon = event.racon;
            const delta = event?.choices?.[0]?.delta?.content;
            if (delta) handlers.onDelta(delta);
          } catch {
            // ignore malformed frame
          }
        }
      }
      handlers.onDone(meta);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError({ code: "stream_error", message: String(err) });
      }
    }
  })();

  return () => controller.abort();
}

export interface JobStreamHandlers {
  onJob: (job: JobItem) => void;
  onDelta: (text: string) => void;
  onDone: (job: JobItem) => void;
  onError: (error: { code: string; message: string }) => void;
}

/** Follow a job's state and synthesis tokens over SSE. Returns an abort function. */
export function streamJob(jobId: string, handlers: JobStreamHandlers): () => void {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch(
        `${API_BASE}/api/v1/jobs/${encodeURIComponent(jobId)}/stream`,
        {
          headers: authHeaders({ Accept: "text/event-stream" }),
          signal: controller.signal,
        }
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError({ code: "network_error", message: String(err) });
      }
      return;
    }

    if (!response.ok || !response.body) {
      let message = response.statusText;
      let code = "http_error";
      try {
        const body = await response.json();
        message = body?.error?.message || body?.detail || message;
        code = body?.error?.code || code;
      } catch {
        // ignore non-JSON error bodies
      }
      handlers.onError({ code, message });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");

          if (!frame.startsWith("data:")) continue;
          const data = frame.slice(5).trim();
          if (!data) continue;

          try {
            const event = JSON.parse(data);
            if (event?.type === "job" && event.job) {
              handlers.onJob(event.job as JobItem);
            } else if (event?.type === "delta" && typeof event.text === "string") {
              handlers.onDelta(event.text);
            } else if (event?.type === "done" && event.job) {
              finished = true;
              handlers.onDone(event.job as JobItem);
              return;
            } else if (event?.type === "error") {
              finished = true;
              handlers.onError({
                code: event.code || "job_error",
                message: event.message || "Job stream failed",
              });
              return;
            }
          } catch {
            // ignore malformed frame
          }
        }
      }

      if (!finished) {
        handlers.onError({
          code: "stream_closed",
          message: "The job stream ended before the job reached a terminal state.",
        });
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        handlers.onError({ code: "stream_error", message: String(err) });
      }
    }
  })();

  return () => controller.abort();
}

export interface EngineModelInfo {
  id: string;
  path: string;
  filename: string;
  size_bytes: number;
  size_mb: number;
  runtime: string;
  family?: string;
  context_length?: number | null;
  quantization?: string;
  source?: string;
}

export interface EngineStatusResponse {
  runtime: Record<string, unknown>;
  health: {
    runtime: string;
    available: boolean;
    detail: string;
    load_state: string;
    models: string[];
  };
  loaded_model: string | null;
  models: EngineModelInfo[];
  models_dir: string;
  available_runtimes: string[];
}

export const api = {
  getStatus: () => request<SystemStatus>("/api/v1/status"),
  getHealth: () => request<{ status: string }>("/health"),

  // Engine
  getEngineStatus: () => request<EngineStatusResponse>("/api/v1/engine/status"),
  getEngineModels: () =>
    request<{
      installed: EngineModelInfo[];
      catalog: Record<string, unknown>[];
      models_dir: string;
      disk_free_mb: number;
    }>("/api/v1/engine/models"),
  loadModel: (model: string) =>
    request<{ model: EngineModelInfo; load_ms: number; estimate: Record<string, unknown> }>(
      "/api/v1/engine/load",
      { method: "POST", body: JSON.stringify({ model }) }
    ),
  unloadModel: () =>
    request<{ unloaded: string | null }>("/api/v1/engine/unload", { method: "POST" }),
  cancelGeneration: () =>
    request<{ cancelled: boolean }>("/api/v1/engine/cancel", { method: "POST" }),
  estimateModel: (model: string, contextSize = 4096) =>
    request<Record<string, unknown>>(
      `/api/v1/engine/models/${encodeURIComponent(model)}/estimate?context_size=${contextSize}`
    ),
  deleteModel: (model: string) =>
    request<{ deleted: string }>(`/api/v1/engine/models/${encodeURIComponent(model)}`, {
      method: "DELETE",
    }),
  getEngineHistory: (limit = 50) =>
    request<{ history: Record<string, unknown>[] }>(`/api/v1/engine/history?limit=${limit}`),
  getEngineEvents: (limit = 100) =>
    request<{ events: Record<string, unknown>[] }>(`/api/v1/engine/events?limit=${limit}`),
  getEngineUsage: () =>
    request<{
      requests: number;
      prompt_tokens: number;
      completion_tokens: number;
      failed: number;
      unmeasured: number;
    }>("/api/v1/engine/usage"),

  // Nodes
  getNodes: () => request<NodeItem[]>("/api/v1/nodes"),
  startNode: (nodeId: string) =>
    request<{ status: string }>(`/api/v1/nodes/${nodeId}/start`, { method: "POST" }),
  stopNode: (nodeId: string) =>
    request<{ status: string }>(`/api/v1/nodes/${nodeId}/stop`, { method: "POST" }),

  // Models & Runtimes
  getModels: () => request<ModelItem[]>("/api/v1/models"),
  getModelCatalog: () => request<ModelCatalogResponse>("/api/v1/models/catalog"),
  getRecommendations: () =>
    request<{
      measured: {
        ram_total_mb: number | null;
        ram_available_mb: number | null;
        gpu_name: string | null;
        gpu_vram_mb: number | null;
        gpu_telemetry_available: boolean;
      };
      recommended_id: string | null;
      recommendations: Array<{
        id: string;
        name: string;
        runtime: string;
        size_bytes?: number | null;
        min_ram_mb?: number | null;
        fits: boolean | null;
        installed: boolean;
        reason: string;
        source: string;
        downloadable?: boolean;
        repo_id?: string | null;
        filename?: string | null;
        sha256?: string | null;
      }>;
    }>("/api/v1/models/recommendations"),
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
  importModel: (path: string, modelId?: string) =>
    request<{
      model: { id: string; filename: string; path: string; size_bytes: number };
      verified: boolean;
    }>("/api/v1/engine/models/import", {
      method: "POST",
      body: JSON.stringify({ path, model_id: modelId ?? null, copy: true }),
    }),
  runInference: (modelName: string, prompt: string) =>
    request<{ response: string; duration_ms: number; simulated: boolean }>(
      "/api/v1/models/infer",
      {
        method: "POST",
        body: JSON.stringify({ model_name: modelName, prompt }),
      }
    ),

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

  // Jobs (agentic orchestration)
  getJobs: () => request<JobItem[]>("/api/v1/jobs"),
  getJob: (jobId: string) => request<JobItem>(`/api/v1/jobs/${jobId}`),
  createJob: (payload: CreateJobPayload) =>
    request<JobItem>("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
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

  // Cloud (optional Supabase control plane, read-only)
  getCloudStatus: () =>
    request<{
      configured: boolean;
      reachable: boolean;
      host: string;
      key_kind: string;
      tables: Record<string, { status: number; count?: string }>;
      error: string | null;
    }>("/api/v1/cloud/status"),
  getCloudModels: () =>
    request<{
      configured: boolean;
      models: Array<Record<string, unknown>>;
      endpoints: Record<string, Array<Record<string, unknown>>>;
      installable_count: number;
    }>("/api/v1/cloud/models"),

  // Logs & Settings
  getLogs: (limit = 100) => request<LogEvent[]>(`/api/v1/logs?limit=${limit}`),
  getSettings: () => request<Record<string, string>>("/api/v1/settings"),
  updateSetting: (key: string, value: string) =>
    request<{ status: string }>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({ key, value }),
    }),
};
