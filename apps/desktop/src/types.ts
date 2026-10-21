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
  status: "available" | "busy" | "offline" | "unregistered";
  host: string;
  port: number;
  cpu_cores: number;
  ram_total_mb: number;
  ram_available_mb: number;
  gpu_name?: string | null;
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
  accuracy_score: number;
  speed_score: number;
  recommended_ram_gb: number;
  format: string;
  tags: string[];
  streaming: boolean;
  is_installed: boolean;
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

export interface JobItem {
  id: string;
  model_name: string;
  status: "queued" | "validating" | "scheduled" | "running" | "completed" | "failed" | "cancelled";
  prompt: string;
  response: string | null;
  assigned_node_id: string | null;
  created_at: string;
  duration_ms?: number;
  error?: string | null;
}

export interface BenchmarkRecord {
  id: string;
  timestamp: string;
  scenario: string;
  objective: string;
  mode: string;
  winner: string | null;
  score: number | null;
  duration_ms: number;
  success: boolean;
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
