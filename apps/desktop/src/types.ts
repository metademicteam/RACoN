export interface HardwareSnapshot {
  cpu_name: string;
  cpu_logical_count: number;
  cpu_physical_count: number | null;
  cpu_utilization_pct: number;
  cpu_freq_mhz: number | null;
  ram_total_mb: number;
  ram_available_mb: number;
  ram_used_mb: number;
  ram_utilization_pct: number;
  process_memory_mb: number;
  os_name: string;
  os_version: string;
  os_architecture: string;
  python_version: string;
  gpu: {
    name: string | null;
    vendor: string | null;
    vram_total_mb: number | null;
    vram_used_mb: number | null;
    utilization_pct: number | null;
    supported: boolean;
    detection_method: string;
  };
}

export interface NodeItem {
  id: string;
  name: string;
  is_local: boolean;
  status: string;
  host: string;
  port: number;
  cpu_cores: number;
  ram_total_mb: number;
  ram_available_mb: number;
  gpu_name?: string | null;
  device_type?: string;
  last_heartbeat?: string;
}

export interface ModelItem {
  id?: string;
  name: string;
  runtime?: string;
  size_bytes: number;
  modified_at?: string;
  format?: string;
  is_loaded?: boolean;
}

export interface CatalogModelItem {
  id: string;
  name: string;
  creator: string;
  description: string;
  size_bytes: number;
  size_display: string;
  parameter_size: string;
  accuracy_score: number | null;
  speed_score: number | null;
  recommended_ram_gb: number;
  format: string;
  tags: string[];
  streaming: boolean;
  is_installed: boolean;
  source?: string;
  repo_id?: string | null;
  filename?: string | null;
  downloadable?: boolean;
  license?: string;
  pull_status?: {
    status: "idle" | "pulling" | "downloading" | "verifying" | "completed" | "failed" | "installed";
    percentage: number;
    completed_bytes?: number;
    total_bytes?: number;
    message?: string;
    error?: string | null;
  };
}

export interface ModelCatalogResponse {
  catalog: CatalogModelItem[];
  installed_count: number;
  total_available: number;
}

export interface SchedulerPreviewCandidate {
  worker_id: string;
  is_feasible: boolean;
  score: number | null;
  rejection_reasons: string[];
  score_components: Record<string, number>;
  is_simulated: boolean;
}

export interface SchedulerPreviewResult {
  run_id: string;
  scenario_name: string;
  objective: string;
  mode: string;
  candidate_count: number;
  feasible_count: number;
  deterministic_choice: string | null;
  deterministic_score: number | null;
  laya_recommendation: string | null;
  laya_confidence: number | null;
  laya_used_successfully: boolean;
  final_choice: string | null;
  final_score: number | null;
  decision_duration_ms: number;
  fallback_used: boolean;
  fallback_reason: string | null;
  success: boolean;
  failure_reason: string | null;
  evaluations: SchedulerPreviewCandidate[];
}

/** One node of a job's task DAG, as persisted by the orchestration service. */
export interface JobTaskItem {
  task_id: string;
  job_id: string;
  title: string;
  instruction: string;
  depends_on: string[];
  state: "pending" | "assigned" | "running" | "completed" | "failed" | "cancelled" | string;
  worker_id: string | null;
  attempts: number;
  output: string | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number;
  max_tokens: number;
  timeout_s: number;
  retries: number;
}

export interface PlanTask {
  task_id: string;
  title: string;
  instruction: string;
  depends_on: string[];
}

export interface JobItem {
  id: string;
  name: string;
  prompt: string;
  model_name: string;
  status:
    | "queued"
    | "planning"
    | "running"
    | "synthesizing"
    | "completed"
    | "partial"
    | "failed"
    | "cancelled"
    | string;
  requested_mode: string;
  execution_mode: string | null;
  planner_mode: string | null;
  plan: { objective?: string; tasks?: PlanTask[]; synthesis?: string; planner_note?: string } | null;
  result: string | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number;
  max_tokens: number;
  max_parallel_tasks: number;
  tasks_total: number;
  tasks_completed: number;
  tasks_failed: number;
  tasks?: JobTaskItem[];
}

export interface CreateJobPayload {
  name?: string;
  model_name: string;
  prompt: string;
  max_tokens?: number;
  decompose?: boolean;
  execution_mode?: "auto" | "local" | "distributed";
  max_parallel_tasks?: number;
}

export interface BenchmarkRecord {
  run_id: string;
  timestamp: string;
  scenario_name: string;
  objective: string;
  mode: string;
  final_choice: string | null;
  final_score: number | null;
  decision_duration_ms: number;
  success: boolean;
}

export interface LogEvent {
  id?: number;
  timestamp?: string;
  level?: string;
  logger?: string;
  message: string;
  event_type?: string;
  details?: string;
}

export interface SystemStatus {
  status: string;
  uptime_seconds: number;
  version: string;
  laya_status: string;
  laya_version: string | null;
  ollama_status: string;
  ollama_models_count: number;
  hardware: HardwareSnapshot;
}
