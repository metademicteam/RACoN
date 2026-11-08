import React, { useState, useEffect } from "react";
import {
  Activity,
  Cpu,
  Layers,
  Sparkles,
  Play,
  BarChart3,
  Terminal,
  Settings,
  RefreshCw,
  Server,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Send,
  Download,
  Check,
  Copy,
  MessageSquare,
  Square,
  GitBranch,
  X,
} from "lucide-react";
import {
  api,
  getApiBase,
  setApiBase,
  getAuthToken,
  setAuthToken,
  streamChat,
  streamJob,
  DEFAULT_API_BASE,
  DEFAULT_AUTH_TOKEN,
  getLocalEngineStatus,
  EngineStatusResponse,
  EngineModelInfo,
} from "./api";
import {
  SystemStatus,
  NodeItem,
  ModelItem,
  SchedulerPreviewResult,
  JobItem,
  BenchmarkRecord,
  CatalogModelItem,
  LogEvent,
} from "./types";

type TabType =
  | "overview"
  | "playground"
  | "nodes"
  | "models"
  | "scheduler"
  | "jobs"
  | "benchmarks"
  | "logs"
  | "settings";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [localStatus, setLocalStatus] = useState<SystemStatus | null>(null);
  const [nodes, setNodes] = useState<NodeItem[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [engineInstalled, setEngineInstalled] = useState<EngineModelInfo[]>([]);
  const [catalog, setCatalog] = useState<CatalogModelItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkRecord[]>([]);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [settingsData, setSettingsData] = useState<Record<string, string>>({});
  const [cloudStatus, setCloudStatus] = useState<{
    configured: boolean;
    reachable: boolean;
    host: string;
    key_kind: string;
    tables: Record<string, { status: number; count?: string }>;
    error: string | null;
  } | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Task Center state
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobItem | null>(null);
  const [submittingJob, setSubmittingJob] = useState<boolean>(false);
  const [jobForm, setJobForm] = useState<JobForm>({
    prompt:
      "Research the trade-offs between CPU and GPU inference for small language models, compare them, and prepare a short recommendation.",
    model: "gemma-3-1b-racon",
    mode: "auto" as "auto" | "local" | "distributed",
    decompose: true,
    parallel: 3,
  });

  // Active download tracking per model ID
  const [pullProgress, setPullProgress] = useState<
    Record<string, { percentage: number; message: string; status: string }>
  >({});

  // Scheduler Lab State
  const [scenarioName, setScenarioName] = useState<string>("Multiple Feasible Candidates");
  const [objective, setObjective] = useState<string>("balanced");
  const [mode, setMode] = useState<string>("deterministic");
  const [previewResult, setPreviewResult] = useState<SchedulerPreviewResult | null>(null);

  // Model Inference State
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [inferencePrompt, setInferencePrompt] = useState<string>(
    "Hello RACoN! Briefly explain how resource-aware scheduling optimizes local AI compute."
  );
  const [inferenceOutput, setInferenceOutput] = useState<string>("");
  const [inferencing, setInferencing] = useState<boolean>(false);

  useEffect(() => {
    fetchInitialData();
    const interval = setInterval(fetchMetrics, 3000);
    return () => clearInterval(interval);
  }, []);

  // Poll active downloads if any are in progress
  useEffect(() => {
    const activeDownloads = Object.entries(pullProgress).filter(
      ([_, info]) => info.status === "downloading" || info.status === "pulling"
    );
    if (activeDownloads.length === 0) return;

    const pollInterval = setInterval(async () => {
      for (const [modelId] of activeDownloads) {
        try {
          const res = await api.getPullStatus(modelId);
          setPullProgress((prev) => ({
            ...prev,
            [modelId]: {
              percentage: res.percentage,
              message: res.message,
              status: res.status,
            },
          }));
          if (res.status === "completed") {
            fetchInitialData();
          }
        } catch {
          // ignore transient poll error
        }
      }
    }, 1000);

    return () => clearInterval(pollInterval);
  }, [pullProgress]);

  async function fetchMetrics() {
    const [remote, local] = await Promise.allSettled([
      api.getStatus(),
      getLocalEngineStatus(),
    ]);
    if (remote.status === "fulfilled") {
      setStatus(remote.value);
      setError(null);
    }
    setLocalStatus(local.status === "fulfilled" ? local.value : null);
  }

  async function fetchInitialData() {
    setLoading(true);
    try {
      const [s, n, m, cat, j, b, l, cfg, cloud, eng, local] = await Promise.allSettled([
        api.getStatus(),
        api.getNodes(),
        api.getModels(),
        api.getModelCatalog(),
        api.getJobs(),
        api.getBenchmarks(),
        api.getLogs(50),
        api.getSettings(),
        api.getCloudStatus(),
        api.getEngineModels(),
        getLocalEngineStatus(),
      ]);

      if (s.status === "fulfilled") setStatus(s.value);
      setLocalStatus(local.status === "fulfilled" ? local.value : null);
      if (n.status === "fulfilled") setNodes(n.value);
      if (m.status === "fulfilled") {
        setModels(m.value);
        if (m.value.length > 0 && !selectedModel) {
          setSelectedModel(m.value[0].name);
        }
      }
      if (cat.status === "fulfilled") {
        setCatalog(cat.value.catalog);
      }
      if (j.status === "fulfilled") setJobs(j.value);
      if (b.status === "fulfilled") setBenchmarks(b.value);
      if (l.status === "fulfilled") setLogs(l.value);
      if (cfg.status === "fulfilled") setSettingsData(cfg.value);
      if (cloud.status === "fulfilled") setCloudStatus(cloud.value);
      if (eng.status === "fulfilled") setEngineInstalled(eng.value.installed);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to communicate with local backend.");
    } finally {
      setLoading(false);
    }
  }

  async function handleDownloadModel(modelId: string) {
    try {
      setPullProgress((prev) => ({
        ...prev,
        [modelId]: { percentage: 5, message: "Initiating download...", status: "pulling" },
      }));
      await api.pullModel(modelId);
    } catch (err: any) {
      setError(`Failed to download ${modelId}: ${err.message}`);
      setPullProgress((prev) => ({
        ...prev,
        [modelId]: { percentage: 0, message: "Download failed", status: "failed" },
      }));
    }
  }

  async function handleRunScheduler() {
    setLoading(true);
    try {
      const res = await api.previewScheduler(scenarioName, objective, mode);
      setPreviewResult(res);
      const b = await api.getBenchmarks();
      setBenchmarks(b);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Scheduler execution failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleRunInference() {
    if (!selectedModel) {
      setError("Please select a model first.");
      return;
    }
    setInferencing(true);
    setInferenceOutput("Generating response from local model via Ollama...");
    try {
      const res = await api.runInference(selectedModel, inferencePrompt);
      setInferenceOutput(res.response + `\n\n[Latency: ${res.duration_ms.toFixed(1)} ms]`);
      const j = await api.getJobs();
      setJobs(j);
    } catch (err: any) {
      setInferenceOutput(`Error running inference: ${err.message}`);
    } finally {
      setInferencing(false);
    }
  }

  // Poll the selected job while it is active so the Task Center reflects real state.
  useEffect(() => {
    if (!selectedJobId) return;
    let cancelled = false;

    const load = async () => {
      try {
        const job = await api.getJob(selectedJobId);
        if (!cancelled) setSelectedJob(job);
      } catch {
        // transient poll failure; the next tick retries
      }
    };

    load();
    const interval = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedJobId]);

  async function handleSubmitJob() {
    if (!jobForm.prompt.trim()) {
      setError("Enter a request before submitting.");
      return;
    }
    setSubmittingJob(true);
    try {
      const job = await api.createJob({
        model_name: jobForm.model,
        prompt: jobForm.prompt,
        max_tokens: 2048,
        decompose: jobForm.decompose,
        execution_mode: jobForm.mode,
        max_parallel_tasks: jobForm.parallel,
      });
      setSelectedJobId(job.id);
      setSelectedJob(job);
      const list = await api.getJobs();
      setJobs(list);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Job submission failed");
    } finally {
      setSubmittingJob(false);
    }
  }

  async function handleSelectJob(jobId: string) {
    setSelectedJobId(jobId);
    try {
      setSelectedJob(await api.getJob(jobId));
    } catch (err: any) {
      setError(err.message || "Could not load the job");
    }
  }

  async function handleCancelJob(jobId: string) {
    try {
      await api.cancelJob(jobId);
      const [job, list] = await Promise.all([api.getJob(jobId), api.getJobs()]);
      setSelectedJob(job);
      setJobs(list);
    } catch (err: any) {
      setError(err.message || "Cancel failed");
    }
  }

  // Hardware metrics always describe the machine running the app. The remote
  // coordinator reports its own container hardware, which is not this computer.
  const hardware = localStatus?.hardware ?? status?.hardware;

  return (
    <div style={{ display: "flex", height: "100vh", backgroundColor: "#0d1117", color: "#c9d1d9", fontFamily: "Segoe UI, -apple-system, sans-serif" }}>
      {/* Sidebar Navigation */}
      <nav
        style={{
          width: "230px",
          backgroundColor: "#161b22",
          borderRight: "1px solid #30363d",
          display: "flex",
          flexDirection: "column",
          padding: "16px 8px",
        }}
      >
        <div style={{ padding: "0 12px 16px 12px", borderBottom: "1px solid #30363d", marginBottom: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: "bold", fontSize: "16px", color: "#f0f6fc" }}>
            <Activity size={20} color="#58a6ff" />
            <span>RACoN Desktop</span>
          </div>
          <div style={{ fontSize: "11px", color: "#8b949e", marginTop: "4px" }}>
            v{status?.version || "—"} • local-first distributed AI
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "4px", flex: 1 }}>
          <NavItem icon={<Cpu size={16} />} label="Overview" active={activeTab === "overview"} onClick={() => setActiveTab("overview")} />
          <NavItem icon={<MessageSquare size={16} />} label="Friend AI" active={activeTab === "playground"} onClick={() => setActiveTab("playground")} />
          <NavItem icon={<Server size={16} />} label="Worker Center" active={activeTab === "nodes"} onClick={() => setActiveTab("nodes")} badge={nodes.length.toString()} />
          <NavItem icon={<Layers size={16} />} label="Model Manager" active={activeTab === "models"} onClick={() => setActiveTab("models")} badge={models.length + engineInstalled.length > 0 ? `${models.length + engineInstalled.length} ready` : "Download"} />
          <NavItem icon={<Sparkles size={16} />} label="Scheduler Lab" active={activeTab === "scheduler"} onClick={() => setActiveTab("scheduler")} />
          <NavItem
            icon={<GitBranch size={16} />}
            label="Task Center"
            active={activeTab === "jobs"}
            onClick={() => setActiveTab("jobs")}
            badge={jobs.filter((j) => ["queued", "planning", "running", "synthesizing"].includes(j.status)).length.toString()}
          />
          <NavItem icon={<BarChart3 size={16} />} label="Benchmarks" active={activeTab === "benchmarks"} onClick={() => setActiveTab("benchmarks")} />
          <NavItem icon={<Terminal size={16} />} label="Logs" active={activeTab === "logs"} onClick={() => setActiveTab("logs")} />
          <NavItem icon={<Settings size={16} />} label="Settings" active={activeTab === "settings"} onClick={() => setActiveTab("settings")} />
        </div>

        {/* Backend Heartbeat Indicator */}
        <div style={{ padding: "8px 12px", backgroundColor: "#0d1117", borderRadius: "6px", border: "1px solid #30363d" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px" }}>
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: status ? "#3fb950" : "#f85149",
                display: "inline-block",
              }}
            />
            <span style={{ color: status ? "#c9d1d9" : "#f85149", fontWeight: 500 }}>
              {status ? "Coordinator Online" : "Backend Disconnected"}
            </span>
          </div>
          <div style={{ fontSize: "11px", color: "#8b949e", marginTop: "3px", wordBreak: "break-all" }}>
            {getApiBase()}
          </div>
          {status && (
            <div style={{ fontSize: "10px", color: "#8b949e", marginTop: "4px" }}>
              Laya: {status.laya_status || "offline"} | Ollama: {status.ollama_status || "offline"}
            </div>
          )}
        </div>
      </nav>

      {/* Main Content Pane */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Top Header */}
        <header
          style={{
            height: "50px",
            backgroundColor: "#161b22",
            borderBottom: "1px solid #30363d",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 24px",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: "14px", color: "#f0f6fc", textTransform: "capitalize" }}>
            {activeTab === "models"
              ? "Model Hub — Choose & Download AI Models"
              : activeTab === "playground"
              ? "Friend AI — Local Assistant & Network Tasks"
              : activeTab === "jobs"
              ? "Task Center — Plans, Workers & Results"
              : activeTab === "nodes"
              ? "Worker Center — This Computer on the Network"
              : activeTab.replace("_", " ")}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {hardware && (
              <span style={{ fontSize: "12px", color: "#8b949e" }}>
                CPU: <strong style={{ color: "#58a6ff" }}>{hardware.cpu_utilization_pct?.toFixed(1) ?? "0.0"}%</strong> | RAM:{" "}
                <strong style={{ color: "#3fb950" }}>{hardware.ram_utilization_pct?.toFixed(1) ?? "0.0"}%</strong>
              </span>
            )}
            <button
              onClick={fetchInitialData}
              disabled={loading}
              style={{
                padding: "4px 10px",
                backgroundColor: "#21262d",
                color: "#c9d1d9",
                border: "1px solid #30363d",
                borderRadius: "6px",
                display: "flex",
                alignItems: "center",
                gap: "4px",
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              <RefreshCw size={12} />
              <span>Refresh</span>
            </button>
          </div>
        </header>

        {/* Global Error Banner */}
        {error && (
          <div
            style={{
              padding: "8px 24px",
              backgroundColor: "rgba(248, 81, 73, 0.15)",
              borderBottom: "1px solid #f85149",
              color: "#ff7b72",
              fontSize: "12px",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <AlertTriangle size={14} />
            <span>{error}</span>
          </div>
        )}

        {/* Tab View Container */}
        <div style={{ flex: 1, padding: "24px", overflowY: "auto" }}>
          {activeTab === "overview" && (
            <OverviewTab
              status={status}
              localStatus={localStatus}
              nodes={nodes}
              models={models}
              jobs={jobs}
              cloudStatus={cloudStatus}
              onNavigateToModels={() => setActiveTab("models")}
              onNavigateToPlayground={() => setActiveTab("playground")}
              onDownloadModel={handleDownloadModel}
              pullProgress={pullProgress}
              hasLocalModel={models.length + engineInstalled.length > 0}
              installedModelCount={engineInstalled.length}
            />
          )}
          {activeTab === "playground" && <PlaygroundTab />}
          {activeTab === "nodes" && <NodesTab nodes={nodes} onRefresh={fetchInitialData} />}
          {activeTab === "models" && (
            <HandyModelHubTab
              catalog={catalog}
              models={models}
              pullProgress={pullProgress}
              onDownload={handleDownloadModel}
              selectedModel={selectedModel}
              setSelectedModel={setSelectedModel}
              prompt={inferencePrompt}
              setPrompt={setInferencePrompt}
              output={inferenceOutput}
              onRunInference={handleRunInference}
              inferencing={inferencing}
              onRefresh={fetchInitialData}
            />
          )}
          {activeTab === "scheduler" && (
            <SchedulerTab
              scenarioName={scenarioName}
              setScenarioName={setScenarioName}
              objective={objective}
              setObjective={setObjective}
              mode={mode}
              setMode={setMode}
              previewResult={previewResult}
              onRun={handleRunScheduler}
              loading={loading}
            />
          )}
          {activeTab === "jobs" && (
            <JobsTab
              jobs={jobs}
              selectedJob={selectedJob}
              form={jobForm}
              setForm={setJobForm}
              submitting={submittingJob}
              onSubmit={handleSubmitJob}
              onSelect={handleSelectJob}
              onCancel={handleCancelJob}
              onRefresh={fetchInitialData}
            />
          )}
          {activeTab === "benchmarks" && <BenchmarksTab benchmarks={benchmarks} onRefresh={fetchInitialData} />}
          {activeTab === "logs" && <LogsTab logs={logs} onRefresh={fetchInitialData} />}
          {activeTab === "settings" && <SettingsTab settings={settingsData} onRefresh={fetchInitialData} />}
        </div>
      </main>
    </div>
  );
}

function NavItem({
  icon,
  label,
  active,
  onClick,
  badge,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  badge?: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "8px 12px",
        borderRadius: "6px",
        backgroundColor: active ? "#21262d" : "transparent",
        color: active ? "#58a6ff" : "#8b949e",
        fontWeight: active ? 600 : 400,
        border: "none",
        textAlign: "left",
        width: "100%",
        fontSize: "13px",
        cursor: "pointer",
      }}
    >
      {icon}
      <span style={{ flex: 1 }}>{label}</span>
      {badge && (
        <span
          style={{
            fontSize: "10px",
            padding: "2px 6px",
            backgroundColor: "#30363d",
            borderRadius: "10px",
            color: "#c9d1d9",
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

// ---------------- TAB 1: OVERVIEW ----------------
interface RecommendationResult {
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
  }>;
}

function FriendAiSetupCard({
  onDownloadModel,
  pullProgress,
  onNavigateToModels,
  onNavigateToPlayground,
}: {
  onDownloadModel: (modelId: string) => void;
  pullProgress: Record<string, { percentage: number; message: string; status: string }>;
  onNavigateToModels: () => void;
  onNavigateToPlayground: () => void;
}) {
  const [recs, setRecs] = useState<RecommendationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getRecommendations()
      .then((result) => {
        if (!cancelled) setRecs(result);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const best =
    recs?.recommendations.find((r) => r.id === recs.recommended_id && r.downloadable) ??
    recs?.recommendations.find((r) => r.fits === true && r.downloadable) ??
    null;
  const progress = best ? pullProgress[best.id] : undefined;
  const downloading =
    progress != null && (progress.status === "downloading" || progress.status === "pulling");
  const measured = recs?.measured;

  return (
    <div
      style={{
        border: "1px solid #1f6feb66",
        borderRadius: "8px",
        padding: "18px 20px",
        background: "linear-gradient(90deg, #1f6feb14 0%, #3fb95014 100%)",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
      }}
    >
      <div>
        <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#f0f6fc", margin: "0 0 4px 0" }}>
          Set up Friend AI
        </h2>
        <p style={{ fontSize: "13px", color: "#8b949e", margin: 0 }}>
          Friend AI needs one local model. RACoN measured this computer and will download a file
          that fits it, verifying the SHA-256 before use.
        </p>
      </div>

      {measured && (
        <div style={{ fontSize: "12px", color: "#8b949e" }}>
          Measured: RAM {measured.ram_total_mb != null ? `${(measured.ram_total_mb / 1024).toFixed(1)} GB` : "unavailable"}
          {" · "}
          GPU {measured.gpu_name || "not detected"}
          {measured.gpu_telemetry_available && measured.gpu_vram_mb != null
            ? ` (${(measured.gpu_vram_mb / 1024).toFixed(1)} GB VRAM)`
            : " (VRAM not measurable)"}
        </div>
      )}

      {error && (
        <div style={{ fontSize: "12px", color: "#ffa198" }}>
          Could not load recommendations: {error}
        </div>
      )}

      {!recs && !error && <div style={{ fontSize: "12px", color: "#8b949e" }}>Evaluating hardware…</div>}

      {recs && !best && (
        <div style={{ fontSize: "12px", color: "#d29922" }}>
          No downloadable model is recommended for this machine. Open the Model Hub to import a
          local GGUF that fits within measured RAM.
        </div>
      )}

      {best && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px" }}>
          <div style={{ flex: 1, minWidth: "260px" }}>
            <div style={{ fontSize: "13px", color: "#e6edf3", fontWeight: 600 }}>{best.name}</div>
            <div style={{ fontSize: "11px", color: "#8b949e" }}>
              {best.size_bytes ? `${(best.size_bytes / (1024 * 1024)).toFixed(0)} MB` : "size unknown"} ·{" "}
              {best.runtime} · {best.reason}
            </div>
          </div>

          {downloading ? (
            <div style={{ minWidth: "220px" }}>
              <div style={{ fontSize: "11px", color: "#8b949e", marginBottom: "4px" }}>
                {progress?.message || "Downloading…"} ({progress?.percentage ?? 0}%)
              </div>
              <div style={{ height: "6px", backgroundColor: "#21262d", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.max(0, Math.min(100, progress?.percentage ?? 0))}%`,
                    height: "100%",
                    backgroundColor: "#1f6feb",
                  }}
                />
              </div>
            </div>
          ) : (
            <button onClick={() => onDownloadModel(best.id)} style={buttonStyle("#238636")}>
              <Download size={14} /> Download {best.name}
            </button>
          )}

          <button onClick={onNavigateToModels} style={buttonStyle("#21262d")}>
            Choose another model
          </button>
        </div>
      )}

      <div style={{ fontSize: "11px", color: "#8b949e" }}>
        After the download completes, open{" "}
        <button
          onClick={onNavigateToPlayground}
          style={{
            background: "none",
            border: "none",
            color: "#58a6ff",
            cursor: "pointer",
            padding: 0,
            fontSize: "11px",
            textDecoration: "underline",
          }}
        >
          Friend AI
        </button>{" "}
        to load the model and start chatting locally.
      </div>
    </div>
  );
}

function OverviewTab({
  status,
  localStatus,
  nodes,
  models,
  jobs,
  cloudStatus,
  onNavigateToModels,
  onNavigateToPlayground,
  onDownloadModel,
  pullProgress,
  hasLocalModel,
  installedModelCount,
}: {
  status: SystemStatus | null;
  localStatus: SystemStatus | null;
  nodes: NodeItem[];
  models: ModelItem[];
  jobs: JobItem[];
  cloudStatus: {
    configured: boolean;
    reachable: boolean;
    host: string;
    tables: Record<string, { status: number; count?: string }>;
    error: string | null;
  } | null;
  onNavigateToModels: () => void;
  onNavigateToPlayground: () => void;
  onDownloadModel: (modelId: string) => void;
  pullProgress: Record<string, { percentage: number; message: string; status: string }>;
  hasLocalModel: boolean;
  installedModelCount: number;
}) {
  const hw = localStatus?.hardware ?? status?.hardware;
  const cpuPct = hw?.cpu_utilization_pct != null ? `${hw.cpu_utilization_pct.toFixed(1)}%` : "—";
  const cpuCores = hw?.cpu_logical_count != null ? `${hw.cpu_logical_count} Logical Cores` : "Unavailable";
  const ramUsedGb = hw?.ram_used_mb != null ? (hw.ram_used_mb / 1024).toFixed(1) : "—";
  const ramTotalGb = hw?.ram_total_mb != null ? (hw.ram_total_mb / 1024).toFixed(1) : "—";
  const ramPct = hw?.ram_utilization_pct != null ? hw.ram_utilization_pct.toFixed(0) : "—";
  const readyNodesCount = (nodes || []).filter((n) => n.status === "available").length;
  const layaStatus = (status?.laya_status || "offline").toUpperCase();
  const ollamaStatus = status?.ollama_status || "offline";
  const uptimeSec = status?.uptime_seconds != null ? Math.floor(status.uptime_seconds) : 0;
  const processMem = hw?.process_memory_mb != null ? `${hw.process_memory_mb.toFixed(1)} MB` : "—";
  const gpuName = hw?.gpu?.name || "Not detected";
  const gpuTelemetry = hw?.gpu?.supported ? `${hw.gpu.vram_used_mb ?? "—"} MB` : "Unavailable (not measurable)";
  const activeJobs = (jobs || []).filter((j) =>
    ["queued", "planning", "running", "synthesizing"].includes(j.status)
  ).length;
  const cloudLabel = !cloudStatus
    ? "Checking…"
    : !cloudStatus.configured
    ? "Not configured (local-only mode)"
    : cloudStatus.reachable
    ? `Connected (${cloudStatus.host}) · models ${
        cloudStatus.tables?.models?.count ?? "?"
      }, nodes ${cloudStatus.tables?.nodes?.count ?? "?"}`
    : `Configured, unreachable${cloudStatus.error ? ` (${cloudStatus.error})` : ""}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Quick Launch Banner */}
      <div
        style={{
          background: "linear-gradient(90deg, #1f6feb22 0%, #a371f722 100%)",
          border: "1px solid #1f6feb44",
          borderRadius: "8px",
          padding: "16px 20px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#f0f6fc", margin: "0 0 4px 0" }}>
            Welcome to RACoN Compute Platform
          </h2>
          <p style={{ fontSize: "13px", color: "#8b949e", margin: 0 }}>
            {hasLocalModel
              ? `${installedModelCount} local GGUF model(s) installed. Friend AI can use them for local chat and distributed tasks.`
              : "No model installed yet. Set up Friend AI below with a verified model that fits this computer."}
          </p>
        </div>
        <button
          onClick={onNavigateToModels}
          style={{
            padding: "8px 16px",
            backgroundColor: "#238636",
            color: "#ffffff",
            border: "none",
            borderRadius: "6px",
            fontWeight: 600,
            fontSize: "13px",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            cursor: "pointer",
          }}
        >
          <Download size={14} />
          <span>Browse Model Hub</span>
        </button>
      </div>

      {!hasLocalModel && (
        <FriendAiSetupCard
          onDownloadModel={onDownloadModel}
          pullProgress={pullProgress}
          onNavigateToModels={onNavigateToModels}
          onNavigateToPlayground={onNavigateToPlayground}
        />
      )}

      {/* Metric Cards Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
        <MetricCard label="CPU Utilization" value={cpuPct} subtitle={cpuCores} color="#58a6ff" />
        <MetricCard label="System RAM" value={`${ramUsedGb} GB`} subtitle={`of ${ramTotalGb} GB (${ramPct}%)`} color="#3fb950" />
        <MetricCard label="Active Nodes" value={(nodes || []).length.toString()} subtitle={`${readyNodesCount} Ready`} color="#d29922" />
        <MetricCard label="Local Models" value={installedModelCount.toString()} subtitle={`GGUF installed · Ollama: ${ollamaStatus}`} color="#a371f7" />
      </div>

      {/* Hardware & System Details */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
        <Card title="Hardware Specifications">
          <Row label="CPU Model" value={hw?.cpu_name || "Not detected"} />
          <Row label="Architecture" value={hw ? `${hw.os_name} ${hw.os_architecture}` : "—"} />
          <Row label="Python Runtime" value={hw?.python_version ? `Python ${hw.python_version}` : "—"} />
          <Row label="Process Memory (RSS)" value={processMem} />
          <Row label="GPU Device" value={gpuName} />
          <Row label="GPU Telemetry" value={gpuTelemetry} />
        </Card>

        <Card title="Compute Cluster Health">
          <Row label="Local Engine" value={status ? "Running (loopback)" : "Disconnected"} />
          <Row label="Cloud Registry" value={cloudLabel} />
          <Row label="Laya Decision Advisor" value={`${layaStatus} ${status?.laya_version ? `(v${status.laya_version})` : ""}`} />
          <Row label="Ollama Inference Backend" value={ollamaStatus === "ready" ? `Connected (${(models || []).length} models ready)` : "Disconnected"} />
          <Row label="Task Center" value={`${activeJobs} active, ${(jobs || []).length} total`} />
          <Row label="Uptime" value={`${uptimeSec} seconds`} />
        </Card>
      </div>
    </div>
  );
}

// ---------------- TAB 2: WORKER CENTER ----------------
function NodesTab({ nodes, onRefresh }: { nodes: NodeItem[]; onRefresh?: () => void }) {
  const [busyNode, setBusyNode] = useState<string | null>(null);
  const [nodeError, setNodeError] = useState<string | null>(null);
  const nodeList = nodes || [];

  async function toggleNode(node: NodeItem) {
    setBusyNode(node.id);
    setNodeError(null);
    try {
      if (node.status === "available") {
        await api.stopNode(node.id);
      } else {
        await api.startNode(node.id);
      }
      if (onRefresh) await onRefresh();
    } catch (err: any) {
      setNodeError(err.message || `Could not change state for ${node.id}`);
    } finally {
      setBusyNode(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <Card title={`Registered Compute Nodes (${nodeList.length})`}>
        {nodeError && (
          <div style={{ color: "#ff7b72", fontSize: "12px", marginBottom: "8px" }}>{nodeError}</div>
        )}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
              <th style={{ padding: "8px" }}>Node</th>
              <th>Origin</th>
              <th>Status</th>
              <th>Cores</th>
              <th>RAM (Available / Total)</th>
              <th>Device</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {nodeList.map((n) => (
              <tr key={n.id} style={{ borderBottom: "1px solid #21262d" }}>
                <td style={{ padding: "8px", fontWeight: 600 }}>
                  {n.name}
                  <div style={{ fontSize: "10px", color: "#8b949e", fontWeight: 400 }}>{n.id}</div>
                </td>
                <td>
                  <span
                    style={{
                      fontSize: "11px",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      backgroundColor: n.is_local ? "#1f6feb22" : "#8b949e22",
                      color: n.is_local ? "#58a6ff" : "#8b949e",
                    }}
                  >
                    {n.is_local ? "LOCAL ENGINE" : "REGISTERED"}
                  </span>
                </td>
                <td>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      color: n.status === "available" ? "#3fb950" : "#d29922",
                    }}
                  >
                    {n.status === "available" ? <CheckCircle size={12} /> : <XCircle size={12} />}
                    {(n.status || "unknown").toUpperCase()}
                  </span>
                </td>
                <td>{n.cpu_cores ?? "—"}</td>
                <td>
                  {n.ram_available_mb != null ? (n.ram_available_mb / 1024).toFixed(1) : "—"} /{" "}
                  {n.ram_total_mb != null ? (n.ram_total_mb / 1024).toFixed(1) : "—"} GB
                </td>
                <td>{n.device_type || "cpu"}</td>
                <td>
                  <button
                    disabled={busyNode === n.id}
                    style={{
                      padding: "3px 8px",
                      backgroundColor: n.status === "available" ? "#3d1f1f" : "#1f3d2b",
                      color: n.status === "available" ? "#ff7b72" : "#3fb950",
                      border: "1px solid #30363d",
                      borderRadius: "4px",
                      fontSize: "11px",
                      cursor: busyNode === n.id ? "wait" : "pointer",
                    }}
                    onClick={() => toggleNode(n)}
                  >
                    {busyNode === n.id ? "Working…" : n.status === "available" ? "Stop" : "Start"}
                  </button>
                </td>
              </tr>
            ))}
            {nodeList.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: "16px", textAlign: "center", color: "#8b949e" }}>
                  No workers registered yet. The local node registers automatically at startup.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <Card title="Worker participation">
        <p style={{ fontSize: "12px", color: "#8b949e", margin: 0 }}>
          Stopping a node removes it from scheduling immediately; the local engine keeps serving
          requests. Remote enrollment is performed by the provider agent and is verified with
          Ed25519 signatures. Resource limits for remote workers are reported by their heartbeat —
          RACoN does not invent telemetry for them.
        </p>
      </Card>
    </div>
  );
}

// ---------------- TAB 3: HANDY-STYLE MODEL HUB ----------------
function HandyModelHubTab({
  catalog,
  models,
  pullProgress,
  onDownload,
  selectedModel,
  setSelectedModel,
  prompt,
  setPrompt,
  output,
  onRunInference,
  inferencing,
  onRefresh,
}: {
  catalog: CatalogModelItem[];
  models: ModelItem[];
  pullProgress: Record<string, { percentage: number; message: string; status: string }>;
  onDownload: (modelId: string) => void;
  selectedModel: string;
  setSelectedModel: (m: string) => void;
  prompt: string;
  setPrompt: (p: string) => void;
  output: string;
  onRunInference: () => void;
  inferencing: boolean;
  onRefresh: () => void;
}) {
  const [activeSubTab, setActiveSubTab] = useState<"catalog" | "inference">("catalog");
  const [customGgufUrl, setCustomGgufUrl] = useState<string>("");
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState<boolean>(false);
  const [recommendation, setRecommendation] = useState<{
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
      fits: boolean | null;
      installed: boolean;
      reason: string;
      min_ram_mb?: number | null;
    }>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getRecommendations()
      .then((res) => {
        if (!cancelled) setRecommendation(res);
      })
      .catch(() => {
        // Recommendation is advisory; the catalog still works without it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Determine installed vs available to download
  const installedCatalog = catalog.filter((c) => c.is_installed);
  const availableToDownload = catalog.filter((c) => !c.is_installed);
  const recommendedEntry = recommendation?.recommendations.find(
    (item) => item.id === recommendation.recommended_id
  );
  const recommendedProgress = recommendation?.recommended_id
    ? pullProgress[recommendation.recommended_id]
    : undefined;
  const recommendedDownloading =
    recommendedProgress?.status === "downloading" || recommendedProgress?.status === "pulling";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Handy Hero Header */}
      <div
        style={{
          textAlign: "center",
          padding: "24px 16px 12px 16px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "8px",
        }}
      >
        <div
          style={{
            fontSize: "36px",
            fontWeight: 800,
            letterSpacing: "-0.5px",
            color: "#f472b6",
            fontFamily: "system-ui, sans-serif",
            textShadow: "0 2px 10px rgba(244, 114, 182, 0.3)",
          }}
        >
          RACoN Model Hub
        </div>
        <p style={{ fontSize: "14px", color: "#8b949e", margin: 0 }}>
          To get started, choose an AI model or download one with a single click
        </p>

        {/* View Switcher Tabs */}
        <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
          <button
            onClick={() => setActiveSubTab("catalog")}
            style={{
              padding: "6px 16px",
              borderRadius: "20px",
              border: "1px solid",
              borderColor: activeSubTab === "catalog" ? "#f472b6" : "#30363d",
              backgroundColor: activeSubTab === "catalog" ? "rgba(244, 114, 182, 0.15)" : "#161b22",
              color: activeSubTab === "catalog" ? "#f472b6" : "#8b949e",
              fontWeight: 600,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            Model Catalog ({catalog.length})
          </button>
          <button
            onClick={() => setActiveSubTab("inference")}
            style={{
              padding: "6px 16px",
              borderRadius: "20px",
              border: "1px solid",
              borderColor: activeSubTab === "inference" ? "#58a6ff" : "#30363d",
              backgroundColor: activeSubTab === "inference" ? "rgba(88, 166, 255, 0.15)" : "#161b22",
              color: activeSubTab === "inference" ? "#58a6ff" : "#8b949e",
              fontWeight: 600,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            Live Inference Playground
          </button>
        </div>
      </div>

      {activeSubTab === "catalog" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "24px", maxWidth: "900px", margin: "0 auto", width: "100%" }}>
          {recommendation && (
            <div
              style={{
                backgroundColor: "rgba(88, 166, 255, 0.08)",
                border: "1px solid #1f6feb55",
                borderRadius: "10px",
                padding: "12px 16px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "#58a6ff", marginBottom: "4px" }}>
                  Hardware-based recommendation
                </div>
                <div style={{ fontSize: "12px", color: "#c9d1d9" }}>
                  {recommendedEntry
                    ? `Recommended: ${recommendedEntry.name} — ${recommendedEntry.reason}`
                    : "No catalog model is declared compatible with the measured memory. Installed models are listed below."}
                </div>
                <div style={{ fontSize: "11px", color: "#8b949e", marginTop: "4px" }}>
                  Measured:{" "}
                  {recommendation.measured.ram_available_mb != null
                    ? `${(recommendation.measured.ram_available_mb / 1024).toFixed(1)} GB RAM available`
                    : "RAM unavailable"}
                  {recommendation.measured.ram_total_mb != null
                    ? ` of ${(recommendation.measured.ram_total_mb / 1024).toFixed(1)} GB`
                    : ""}
                  {" · "}
                  {recommendation.measured.gpu_telemetry_available && recommendation.measured.gpu_vram_mb != null
                    ? `GPU VRAM: ${(recommendation.measured.gpu_vram_mb / 1024).toFixed(1)} GB`
                    : "GPU VRAM: not measurable on this device"}
                </div>
              </div>
              {recommendedEntry && recommendation.recommended_id && (
                recommendedEntry.installed ? (
                  <span
                    style={{
                      fontSize: "11px",
                      padding: "4px 8px",
                      backgroundColor: "#23863622",
                      color: "#3fb950",
                      borderRadius: "4px",
                      border: "1px solid #23863644",
                      whiteSpace: "nowrap",
                    }}
                  >
                    Installed
                  </span>
                ) : (
                  <button
                    disabled={recommendedDownloading}
                    onClick={() => onDownload(recommendation.recommended_id as string)}
                    style={{
                      padding: "6px 12px",
                      backgroundColor: recommendedDownloading ? "#21262d" : "#238636",
                      color: "#ffffff",
                      border: "none",
                      borderRadius: "6px",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: recommendedDownloading ? "wait" : "pointer",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {recommendedDownloading
                      ? `Downloading ${(recommendedProgress?.percentage || 0).toFixed(0)}%`
                      : "Install recommended"}
                  </button>
                )
              )}
            </div>
          )}

          {/* Section 1: Compatible & Installed Models */}
          {installedCatalog.length > 0 && (
            <div>
              <div style={{ fontSize: "13px", fontWeight: 600, color: "#8b949e", marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                Compatible & Installed Models ({installedCatalog.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {installedCatalog.map((item) => (
                  <HandyModelCard
                    key={item.id}
                    model={item}
                    progress={pullProgress[item.id]}
                    onDownload={onDownload}
                    onSelect={() => {
                      setSelectedModel(item.id);
                      setActiveSubTab("inference");
                    }}
                    isSelected={selectedModel === item.id}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Section 2: Available to Download */}
          <div>
            <div style={{ fontSize: "13px", fontWeight: 600, color: "#8b949e", marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Available to Download ({availableToDownload.length})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {availableToDownload.map((item) => (
                <HandyModelCard
                  key={item.id}
                  model={item}
                  progress={pullProgress[item.id]}
                  onDownload={onDownload}
                  onSelect={() => {
                    setSelectedModel(item.id);
                    setActiveSubTab("inference");
                  }}
                  isSelected={selectedModel === item.id}
                />
              ))}
            </div>
          </div>

          {/* Section 3: Local GGUF import */}
          <div
            style={{
              backgroundColor: "#161b22",
              border: "1px dashed #30363d",
              borderRadius: "10px",
              padding: "16px 20px",
            }}
          >
            <div style={{ fontSize: "14px", fontWeight: 600, color: "#f0f6fc", marginBottom: "4px" }}>
              Import a local GGUF file
            </div>
            <p style={{ fontSize: "12px", color: "#8b949e", margin: "0 0 12px 0" }}>
              Enter the full path to a .gguf file on this machine. RACoN copies it into the managed
              model directory and validates the GGUF header before reporting it as installed.
            </p>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                type="text"
                placeholder="C:\models\llama-3.2-1b-q4_k_m.gguf"
                value={customGgufUrl}
                onChange={(e) => setCustomGgufUrl(e.target.value)}
                style={{
                  flex: 1,
                  padding: "8px 12px",
                  backgroundColor: "#0d1117",
                  border: "1px solid #30363d",
                  borderRadius: "6px",
                  color: "#c9d1d9",
                  fontSize: "12px",
                }}
              />
              <button
                disabled={importing}
                onClick={async () => {
                  if (!customGgufUrl.trim()) return;
                  setImporting(true);
                  setImportMessage(null);
                  try {
                    const result = await api.importModel(customGgufUrl.trim());
                    setImportMessage(`Installed ${result.model.id} (${result.model.filename}).`);
                    setCustomGgufUrl("");
                    onRefresh();
                  } catch (err: any) {
                    setImportMessage(`Import failed: ${err.message}`);
                  } finally {
                    setImporting(false);
                  }
                }}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#21262d",
                  border: "1px solid #30363d",
                  color: "#c9d1d9",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: importing ? "wait" : "pointer",
                }}
              >
                {importing ? "Importing…" : "Import Model"}
              </button>
            </div>
            {importMessage && (
              <div style={{ fontSize: "12px", color: "#8b949e", marginTop: "8px" }}>{importMessage}</div>
            )}
          </div>
        </div>
      ) : (
        /* Live Inference Playground */
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <Card title="Model Selection">
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <label style={{ fontSize: "12px", color: "#8b949e" }}>Select Installed Model:</label>
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                style={{
                  padding: "8px 12px",
                  backgroundColor: "#0d1117",
                  border: "1px solid #30363d",
                  borderRadius: "6px",
                  color: "#c9d1d9",
                  fontSize: "13px",
                }}
              >
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} ({m.size_bytes ? `${(m.size_bytes / (1024 * 1024)).toFixed(0)} MB` : "GGUF"})
                  </option>
                ))}
                {models.length === 0 && <option value="">No models installed yet</option>}
              </select>

              <div style={{ marginTop: "12px" }}>
                <label style={{ fontSize: "12px", color: "#8b949e", display: "block", marginBottom: "6px" }}>
                  Inference Prompt:
                </label>
                <textarea
                  rows={5}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "10px",
                    backgroundColor: "#0d1117",
                    border: "1px solid #30363d",
                    borderRadius: "6px",
                    color: "#c9d1d9",
                    fontSize: "13px",
                    resize: "vertical",
                  }}
                />
              </div>

              <button
                onClick={onRunInference}
                disabled={inferencing || !selectedModel}
                style={{
                  padding: "10px 16px",
                  backgroundColor: "#238636",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  fontWeight: 600,
                  fontSize: "13px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "6px",
                  cursor: inferencing || !selectedModel ? "not-allowed" : "pointer",
                }}
              >
                <Send size={14} />
                <span>{inferencing ? "Generating on CPU..." : "Execute Inference"}</span>
              </button>
            </div>
          </Card>

          <Card title="Response Output">
            <pre
              style={{
                backgroundColor: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: "6px",
                padding: "12px",
                color: "#c9d1d9",
                fontSize: "12px",
                fontFamily: "Consolas, monospace",
                minHeight: "260px",
                maxHeight: "450px",
                overflowY: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {output || "Model generation output will appear here after clicking Execute Inference."}
            </pre>
          </Card>
        </div>
      )}
    </div>
  );
}

// Handy-Style Model Card Component matching image 1
function HandyModelCard({
  model,
  progress,
  onDownload,
  onSelect,
  isSelected,
}: {
  model: CatalogModelItem;
  progress?: { percentage: number; message: string; status: string };
  onDownload: (id: string) => void;
  onSelect: () => void;
  isSelected: boolean;
}) {
  const isDownloading = progress?.status === "downloading" || progress?.status === "pulling";
  const pct = progress?.percentage || 0;

  return (
    <div
      style={{
        backgroundColor: "#161b22",
        border: isSelected ? "1px solid #58a6ff" : "1px solid #30363d",
        borderRadius: "10px",
        padding: "14px 18px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        transition: "border-color 0.15s ease",
      }}
    >
      {/* Top Row: Name + Accuracy & Speed Bars */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <span style={{ fontSize: "15px", fontWeight: 700, color: "#f0f6fc" }}>{model.name}</span>
          <span style={{ fontSize: "12px", color: "#8b949e", marginLeft: "8px" }}>by {model.creator}</span>
        </div>

        {/* Handy Accuracy & Speed Visual Indicator Bars (only for curated entries) */}
        {model.accuracy_score != null && model.speed_score != null && (
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", width: "130px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "10px", color: "#8b949e" }}>
              <span>accuracy</span>
              <div style={{ width: "70px", height: "5px", backgroundColor: "#30363d", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    width: `${model.accuracy_score}%`,
                    height: "100%",
                    backgroundColor: "#f472b6",
                    borderRadius: "3px",
                  }}
                />
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "10px", color: "#8b949e" }}>
              <span>speed</span>
              <div style={{ width: "70px", height: "5px", backgroundColor: "#30363d", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    width: `${model.speed_score}%`,
                    height: "100%",
                    backgroundColor: "#f472b6",
                    borderRadius: "3px",
                  }}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Description */}
      <div style={{ fontSize: "12px", color: "#8b949e" }}>{model.description}</div>

      {/* Progress Bar (if actively downloading) */}
      {isDownloading && (
        <div style={{ margin: "6px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "#58a6ff", marginBottom: "4px" }}>
            <span>{progress?.message || "Downloading..."}</span>
            <span>{pct.toFixed(0)}%</span>
          </div>
          <div style={{ height: "6px", backgroundColor: "#21262d", borderRadius: "3px", overflow: "hidden" }}>
            <div
              style={{
                width: `${pct}%`,
                height: "100%",
                backgroundColor: "#1f6feb",
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>
      )}

      {/* Bottom Row: Tags on left, Size and Action button on right */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "2px" }}>
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
          {model.tags.map((t) => (
            <span
              key={t}
              style={{
                fontSize: "11px",
                color: "#8b949e",
                padding: "2px 8px",
                backgroundColor: "#21262d",
                borderRadius: "4px",
                border: "1px solid #30363d",
              }}
            >
              {t}
            </span>
          ))}
          <span style={{ fontSize: "11px", color: "#58a6ff" }}>{model.format}</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ fontSize: "12px", fontWeight: 600, color: "#8b949e" }}>
            💾 {model.size_display}
          </span>

          {model.is_installed ? (
            <div style={{ display: "flex", gap: "6px" }}>
              <span
                style={{
                  fontSize: "11px",
                  padding: "4px 8px",
                  backgroundColor: "#23863622",
                  color: "#3fb950",
                  borderRadius: "4px",
                  border: "1px solid #23863644",
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                }}
              >
                <Check size={12} />
                Installed
              </span>
              <button
                onClick={onSelect}
                style={{
                  padding: "4px 10px",
                  backgroundColor: "#1f6feb",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "4px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Use Model
              </button>
            </div>
          ) : isDownloading ? (
            <button
              disabled
              style={{
                padding: "4px 12px",
                backgroundColor: "#21262d",
                color: "#58a6ff",
                border: "1px solid #30363d",
                borderRadius: "4px",
                fontSize: "12px",
                cursor: "wait",
              }}
            >
              Downloading ({pct.toFixed(0)}%)...
            </button>
          ) : (
            <button
              onClick={() => onDownload(model.id)}
              style={{
                padding: "5px 12px",
                backgroundColor: "#21262d",
                color: "#f0f6fc",
                border: "1px solid #30363d",
                borderRadius: "6px",
                fontSize: "12px",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: "5px",
                cursor: "pointer",
              }}
            >
              <Download size={13} color="#f472b6" />
              <span>Download</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------- TAB 4: SCHEDULER LAB ----------------
function SchedulerTab({
  scenarioName,
  setScenarioName,
  objective,
  setObjective,
  mode,
  setMode,
  previewResult,
  onRun,
  loading,
}: {
  scenarioName: string;
  setScenarioName: (s: string) => void;
  objective: string;
  setObjective: (o: string) => void;
  mode: string;
  setMode: (m: string) => void;
  previewResult: SchedulerPreviewResult | null;
  onRun: () => void;
  loading: boolean;
}) {
  const scenarios = [
    "Single Feasible Worker",
    "Multiple Feasible Candidates",
    "Insufficient Memory Worker",
    "Incompatible Runtime Worker",
    "High Latency Network",
    "Busy Worker",
    "Unavailable Worker",
    "No Feasible Solution",
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <Card title="Scheduler Lab Controls">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: "12px", alignItems: "end" }}>
          <div>
            <label style={{ fontSize: "12px", color: "#8b949e", display: "block", marginBottom: "4px" }}>
              Scenario:
            </label>
            <select
              value={scenarioName}
              onChange={(e) => setScenarioName(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", backgroundColor: "#0d1117", border: "1px solid #30363d", color: "#c9d1d9", borderRadius: "4px" }}
            >
              {scenarios.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ fontSize: "12px", color: "#8b949e", display: "block", marginBottom: "4px" }}>
              Objective:
            </label>
            <select
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", backgroundColor: "#0d1117", border: "1px solid #30363d", color: "#c9d1d9", borderRadius: "4px" }}
            >
              <option value="balanced">Balanced Policy</option>
              <option value="lowest_latency">Lowest Latency</option>
              <option value="highest_throughput">Highest Throughput</option>
              <option value="resource_efficiency">Resource Efficiency</option>
            </select>
          </div>

          <div>
            <label style={{ fontSize: "12px", color: "#8b949e", display: "block", marginBottom: "4px" }}>
              Mode:
            </label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", backgroundColor: "#0d1117", border: "1px solid #30363d", color: "#c9d1d9", borderRadius: "4px" }}
            >
              <option value="deterministic">Deterministic (Default Rule-Based)</option>
              <option value="laya_shadow">Laya Shadow (Log parallel choice)</option>
              <option value="laya_assisted">Laya-Assisted (AI selection + fallback)</option>
            </select>
          </div>

          <button
            onClick={onRun}
            disabled={loading}
            style={{
              padding: "7px 20px",
              backgroundColor: "#1f6feb",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {loading ? "Evaluating..." : "Run Scheduler"}
          </button>
        </div>
      </Card>

      {previewResult && (
        <Card title={`Evaluation Result: ${previewResult.scenario_name}`}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginBottom: "16px" }}>
            <div style={{ padding: "8px", backgroundColor: "#0d1117", borderRadius: "4px", border: "1px solid #30363d" }}>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>Deterministic Pick</div>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#58a6ff" }}>
                {previewResult.deterministic_choice || "None"}
              </div>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>
                Score: {previewResult.deterministic_score?.toFixed(4) || "—"}
              </div>
            </div>

            <div style={{ padding: "8px", backgroundColor: "#0d1117", borderRadius: "4px", border: "1px solid #30363d" }}>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>Laya Recommendation</div>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#a371f7" }}>
                {previewResult.laya_recommendation || "Not Loaded / Offline"}
              </div>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>
                Confidence: {previewResult.laya_confidence ? previewResult.laya_confidence.toFixed(3) : "—"}
              </div>
            </div>

            <div style={{ padding: "8px", backgroundColor: "#0d1117", borderRadius: "4px", border: "1px solid #30363d" }}>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>Final Accepted Target</div>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#3fb950" }}>
                {previewResult.final_choice || "Infeasible"}
              </div>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>
                Duration: {previewResult.decision_duration_ms != null ? previewResult.decision_duration_ms.toFixed(1) : "0.0"} ms
              </div>
            </div>
          </div>

          {previewResult.fallback_used && (
            <div style={{ padding: "6px 12px", backgroundColor: "#d2992222", border: "1px solid #d29922", borderRadius: "4px", color: "#d29922", fontSize: "12px", marginBottom: "12px" }}>
              Fallback Triggered: {previewResult.fallback_reason}
            </div>
          )}

          {/* Candidate Table */}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
                <th style={{ padding: "6px" }}>Candidate ID</th>
                <th>Type</th>
                <th>Feasible</th>
                <th>Score</th>
                <th>Rejection Reasons</th>
              </tr>
            </thead>
            <tbody>
              {(previewResult.evaluations || []).map((ev) => (
                <tr key={ev.worker_id} style={{ borderBottom: "1px solid #21262d", backgroundColor: ev.worker_id === previewResult.final_choice ? "#1f6feb15" : "transparent" }}>
                  <td style={{ padding: "6px", fontWeight: 600 }}>{ev.worker_id}</td>
                  <td>{ev.is_simulated ? "SIMULATED" : "LIVE"}</td>
                  <td>
                    <span style={{ color: ev.is_feasible ? "#3fb950" : "#f85149" }}>
                      {ev.is_feasible ? "✓ FEASIBLE" : "✗ REJECTED"}
                    </span>
                  </td>
                  <td>{ev.score !== null ? ev.score.toFixed(4) : "—"}</td>
                  <td style={{ color: "#8b949e" }}>{(ev.rejection_reasons || []).join(", ") || "None (All constraints satisfied)"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ---------------- TAB 5: TASK CENTER ----------------
const TERMINAL_JOB_STATES = ["completed", "partial", "failed", "cancelled"];
const ACTIVE_JOB_STATES = ["queued", "planning", "running", "synthesizing"];

type JobForm = {
  prompt: string;
  model: string;
  mode: "auto" | "local" | "distributed";
  decompose: boolean;
  parallel: number;
};

function jobStatusColor(status: string): string {
  if (status === "completed") return "#3fb950";
  if (status === "partial") return "#d29922";
  if (status === "failed") return "#f85149";
  if (status === "cancelled") return "#8b949e";
  return "#58a6ff";
}

function JobsTab({
  jobs,
  selectedJob,
  form,
  setForm,
  submitting,
  onSubmit,
  onSelect,
  onCancel,
  onRefresh,
}: {
  jobs: JobItem[];
  selectedJob: JobItem | null;
  form: JobForm;
  setForm: React.Dispatch<React.SetStateAction<JobForm>>;
  submitting: boolean;
  onSubmit: () => void;
  onSelect: (jobId: string) => void;
  onCancel: (jobId: string) => void;
  onRefresh?: () => void;
}) {
  const jobList = jobs || [];
  const activeJob = selectedJob && !TERMINAL_JOB_STATES.includes(selectedJob.status);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <Card title="Submit a request">
        <textarea
          value={form.prompt}
          onChange={(e) => setForm({ ...form, prompt: e.target.value })}
          rows={3}
          placeholder="Describe what you want RACoN to do. A planner will split it into tasks."
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "10px",
            backgroundColor: "#0d1117",
            color: "#c9d1d9",
            border: "1px solid #30363d",
            borderRadius: "6px",
            fontSize: "13px",
            resize: "vertical",
            fontFamily: "inherit",
          }}
        />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 170px 130px 110px",
            gap: "10px",
            marginTop: "10px",
            alignItems: "end",
          }}
        >
          <label style={{ fontSize: "11px", color: "#8b949e" }}>
            Model
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              style={{
                display: "block",
                width: "100%",
                marginTop: "4px",
                padding: "7px 8px",
                backgroundColor: "#0d1117",
                color: "#c9d1d9",
                border: "1px solid #30363d",
                borderRadius: "6px",
                fontSize: "12px",
                boxSizing: "border-box",
              }}
            />
          </label>
          <label style={{ fontSize: "11px", color: "#8b949e" }}>
            Execution
            <select
              value={form.mode}
              onChange={(e) => setForm({ ...form, mode: e.target.value as typeof form.mode })}
              style={{
                display: "block",
                width: "100%",
                marginTop: "4px",
                padding: "7px 8px",
                backgroundColor: "#0d1117",
                color: "#c9d1d9",
                border: "1px solid #30363d",
                borderRadius: "6px",
                fontSize: "12px",
              }}
            >
              <option value="auto">Auto (prefer workers)</option>
              <option value="local">Local only</option>
              <option value="distributed">Distributed only</option>
            </select>
          </label>
          <label style={{ fontSize: "11px", color: "#8b949e" }}>
            Max parallel
            <input
              type="number"
              min={1}
              max={12}
              value={form.parallel}
              onChange={(e) => setForm({ ...form, parallel: Math.max(1, Number(e.target.value) || 1) })}
              style={{
                display: "block",
                width: "100%",
                marginTop: "4px",
                padding: "7px 8px",
                backgroundColor: "#0d1117",
                color: "#c9d1d9",
                border: "1px solid #30363d",
                borderRadius: "6px",
                fontSize: "12px",
                boxSizing: "border-box",
              }}
            />
          </label>
          <button
            onClick={onSubmit}
            disabled={submitting}
            style={{
              padding: "8px 12px",
              backgroundColor: submitting ? "#21262d" : "#238636",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 600,
              fontSize: "12px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "6px",
              cursor: submitting ? "wait" : "pointer",
            }}
          >
            <Play size={13} />
            {submitting ? "Submitting…" : "Run"}
          </button>
        </div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            marginTop: "10px",
            fontSize: "12px",
            color: "#8b949e",
          }}
        >
          <input
            type="checkbox"
            checked={form.decompose}
            onChange={(e) => setForm({ ...form, decompose: e.target.checked })}
          />
          Decompose into a task DAG (disable to run the request as one task)
        </label>
        <p style={{ fontSize: "11px", color: "#8b949e", margin: "10px 0 0 0" }}>
          Auto mode prefers approved remote workers and falls back to the local engine. The mode
          actually used is recorded on every job; a distributed-only job fails loudly when no
          worker is available instead of silently running locally.
        </p>
      </Card>

      <Card title={`Jobs (${jobList.length})`}>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "8px" }}>
          <button
            onClick={onRefresh}
            style={{
              padding: "4px 10px",
              backgroundColor: "#21262d",
              color: "#c9d1d9",
              border: "1px solid #30363d",
              borderRadius: "4px",
              fontSize: "11px",
              cursor: "pointer",
            }}
          >
            Refresh
          </button>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
              <th style={{ padding: "6px" }}>Job</th>
              <th>Status</th>
              <th>Planner</th>
              <th>Execution</th>
              <th>Tasks</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {jobList.map((j) => (
              <tr
                key={j.id}
                onClick={() => onSelect(j.id)}
                style={{
                  borderBottom: "1px solid #21262d",
                  cursor: "pointer",
                  backgroundColor: selectedJob?.id === j.id ? "#161b22" : "transparent",
                }}
              >
                <td style={{ padding: "6px", fontWeight: 600 }}>
                  {j.name || j.id}
                  <div style={{ fontSize: "10px", color: "#8b949e", fontWeight: 400 }}>
                    {j.id} · {j.model_name}
                  </div>
                </td>
                <td>
                  <span
                    style={{
                      fontSize: "10px",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      backgroundColor: `${jobStatusColor(j.status)}22`,
                      color: jobStatusColor(j.status),
                    }}
                  >
                    {j.status.toUpperCase()}
                  </span>
                </td>
                <td>{j.planner_mode || "—"}</td>
                <td>{j.execution_mode || "—"}</td>
                <td>
                  {j.tasks_completed ?? 0}/{j.tasks_total ?? 0} done
                  {j.tasks_failed ? ` · ${j.tasks_failed} failed` : ""}
                </td>
                <td>{j.created_at ? new Date(j.created_at).toLocaleTimeString() : "—"}</td>
              </tr>
            ))}
            {jobList.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: "16px", textAlign: "center", color: "#8b949e" }}>
                  No jobs yet. Submit a request above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {selectedJob && (
        <Card title={`Job detail — ${selectedJob.id}`}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
              <span
                style={{
                  fontSize: "11px",
                  padding: "3px 8px",
                  borderRadius: "4px",
                  backgroundColor: `${jobStatusColor(selectedJob.status)}22`,
                  color: jobStatusColor(selectedJob.status),
                  fontWeight: 600,
                }}
              >
                {selectedJob.status.toUpperCase()}
              </span>
              <span style={{ fontSize: "11px", color: "#8b949e" }}>
                planner: {selectedJob.planner_mode || "—"} · execution: {selectedJob.execution_mode || "—"} ·{" "}
                {selectedJob.duration_ms ? `${selectedJob.duration_ms.toFixed(0)} ms` : "running"}
              </span>
            </div>
            {activeJob && (
              <button
                onClick={() => onCancel(selectedJob.id)}
                style={{
                  padding: "4px 10px",
                  backgroundColor: "#3d1f1f",
                  color: "#ff7b72",
                  border: "1px solid #f8514955",
                  borderRadius: "4px",
                  fontSize: "11px",
                  display: "flex",
                  alignItems: "center",
                  gap: "4px",
                  cursor: "pointer",
                }}
              >
                <X size={12} />
                Cancel job
              </button>
            )}
          </div>

          {selectedJob.plan?.planner_note && (
            <div style={{ fontSize: "11px", color: "#d29922", marginBottom: "8px" }}>
              Planner note: {selectedJob.plan.planner_note}
            </div>
          )}

          {selectedJob.error && (
            <div
              style={{
                padding: "8px 10px",
                marginBottom: "10px",
                backgroundColor: "rgba(248, 81, 73, 0.12)",
                border: "1px solid #f8514955",
                borderRadius: "6px",
                color: "#ff7b72",
                fontSize: "12px",
              }}
            >
              {selectedJob.error}
            </div>
          )}

          <div style={{ marginBottom: "12px" }}>
            <div style={{ fontSize: "11px", color: "#8b949e", marginBottom: "4px" }}>Request</div>
            <div style={{ fontSize: "12px", whiteSpace: "pre-wrap" }}>{selectedJob.prompt}</div>
          </div>

          {(selectedJob.tasks || []).length > 0 && (
            <div style={{ marginBottom: "14px" }}>
              <div style={{ fontSize: "11px", color: "#8b949e", marginBottom: "6px" }}>Task DAG</div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
                    <th style={{ padding: "5px" }}>Task</th>
                    <th>State</th>
                    <th>Worker</th>
                    <th>Depends on</th>
                    <th>Attempts</th>
                    <th>Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {(selectedJob.tasks || []).map((t) => (
                    <tr key={t.task_id} style={{ borderBottom: "1px solid #21262d" }}>
                      <td style={{ padding: "5px", fontWeight: 600 }}>
                        {t.task_id}
                        <div style={{ color: "#8b949e", fontWeight: 400 }}>{t.title}</div>
                      </td>
                      <td>
                        <span style={{ color: jobStatusColor(t.state === "completed" ? "completed" : t.state) }}>
                          {t.state.toUpperCase()}
                        </span>
                      </td>
                      <td>{t.worker_id || "—"}</td>
                      <td>{t.depends_on.length ? t.depends_on.join(", ") : "—"}</td>
                      <td>{t.attempts}</td>
                      <td>{t.duration_ms ? `${t.duration_ms.toFixed(0)} ms` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(selectedJob.tasks || []).some((t) => t.output || t.error) && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "11px", color: "#8b949e", marginBottom: "6px" }}>Task outcomes</div>
              {(selectedJob.tasks || []).map((t) =>
                t.output || t.error ? (
                  <details key={t.task_id} style={{ marginBottom: "6px", fontSize: "12px" }}>
                    <summary style={{ cursor: "pointer", color: t.error ? "#ff7b72" : "#58a6ff" }}>
                      {t.task_id} — {t.error ? "failed" : "completed"}
                    </summary>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        backgroundColor: "#0d1117",
                        border: "1px solid #30363d",
                        borderRadius: "6px",
                        padding: "8px",
                        fontSize: "11px",
                        color: t.error ? "#ff7b72" : "#c9d1d9",
                      }}
                    >
                      {t.error || t.output}
                    </pre>
                  </details>
                ) : null
              )}
            </div>
          )}

          <div>
            <div style={{ fontSize: "11px", color: "#8b949e", marginBottom: "4px" }}>
              Final answer {selectedJob.result ? "" : "(not available)"}
            </div>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                backgroundColor: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: "6px",
                padding: "10px",
                fontSize: "12px",
                color: selectedJob.result ? "#c9d1d9" : "#8b949e",
                margin: 0,
              }}
            >
              {selectedJob.result ||
                (ACTIVE_JOB_STATES.includes(selectedJob.status)
                  ? "The job is still running. This panel refreshes every 2 seconds."
                  : "No synthesized answer was produced.")}
            </pre>
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------- TAB 6: BENCHMARKS ----------------
function BenchmarksTab({ benchmarks }: { benchmarks: BenchmarkRecord[]; onRefresh?: () => void }) {
  const benchmarkList = benchmarks || [];
  return (
    <Card title={`Benchmark Traces (${benchmarkList.length})`}>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginBottom: "12px" }}>
        <button
          onClick={() => {
            const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(benchmarkList, null, 2));
            const dl = document.createElement("a");
            dl.setAttribute("href", dataStr);
            dl.setAttribute("download", "racon_benchmarks.json");
            dl.click();
          }}
          style={{ padding: "4px 10px", backgroundColor: "#21262d", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: "4px", fontSize: "11px", display: "flex", alignItems: "center", gap: "4px", cursor: "pointer" }}
        >
          <Download size={12} />
          Export JSON
        </button>
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
            <th style={{ padding: "6px" }}>Timestamp</th>
            <th>Scenario</th>
            <th>Objective</th>
            <th>Mode</th>
            <th>Winner</th>
            <th>Score</th>
            <th>Latency</th>
          </tr>
        </thead>
        <tbody>
          {benchmarkList.map((b) => (
            <tr key={b.run_id} style={{ borderBottom: "1px solid #21262d" }}>
              <td style={{ padding: "6px" }}>{b.timestamp ? new Date(b.timestamp).toLocaleTimeString() : "—"}</td>
              <td style={{ fontWeight: 600 }}>{b.scenario_name}</td>
              <td>{b.objective}</td>
              <td>{b.mode}</td>
              <td>{b.final_choice || "—"}</td>
              <td>{b.final_score != null ? b.final_score.toFixed(4) : "—"}</td>
              <td>{b.decision_duration_ms != null ? b.decision_duration_ms.toFixed(1) : "—"} ms</td>
            </tr>
          ))}
          {benchmarkList.length === 0 && (
            <tr>
              <td colSpan={7} style={{ padding: "16px", textAlign: "center", color: "#8b949e" }}>
                No benchmarks recorded yet. Run a preview in Scheduler Lab to record benchmarks.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}

// ---------------- TAB 7: LOGS ----------------
function LogsTab({ logs }: { logs: LogEvent[]; onRefresh?: () => void }) {
  const logList = logs || [];
  const levelColor = (level?: string) => {
    const value = (level || "INFO").toUpperCase();
    if (value === "ERROR" || value === "CRITICAL") return "#ff7b72";
    if (value === "WARN" || value === "WARNING") return "#d29922";
    if (value === "DEBUG") return "#8b949e";
    return "#58a6ff";
  };
  return (
    <Card title={`Application Logs (${logList.length} events)`}>
      <pre
        style={{
          backgroundColor: "#0d1117",
          padding: "12px",
          borderRadius: "6px",
          border: "1px solid #30363d",
          color: "#8b949e",
          fontSize: "11px",
          fontFamily: "Consolas, monospace",
          height: "400px",
          overflowY: "auto",
        }}
      >
        {logList.map((l, i) => (
          <div key={l.id ?? i} style={{ marginBottom: "3px", whiteSpace: "pre-wrap" }}>
            <span style={{ color: levelColor(l.level) }}>[{(l.level || "INFO").toUpperCase()}]</span>{" "}
            {l.timestamp && <span>{new Date(l.timestamp).toLocaleTimeString()} </span>}
            <span>{l.message}</span>
          </div>
        ))}
        {logList.length === 0 && <div>No recent log events available.</div>}
      </pre>
    </Card>
  );
}

// ---------------- TAB 8: SETTINGS & TAILSCALE FUNNEL ----------------
function SettingsTab({ settings, onRefresh }: { settings: Record<string, string>; onRefresh?: () => void }) {
  const [apiUrl, setApiUrl] = useState(getApiBase());
  const [authToken, setToken] = useState(getAuthToken());
  const [savedMsg, setSavedMsg] = useState("");
  const [copiedFunnel, setCopiedFunnel] = useState(false);

  const handleSaveConnection = () => {
    setApiBase(apiUrl);
    setAuthToken(authToken);
    setSavedMsg("Connection settings saved! Reconnecting...");
    setTimeout(() => {
      setSavedMsg("");
      if (onRefresh) onRefresh();
    }, 1200);
  };

  const applyServerDefaults = () => {
    setApiUrl(DEFAULT_API_BASE);
    setToken(DEFAULT_AUTH_TOKEN);
    setApiBase(DEFAULT_API_BASE);
    setAuthToken(DEFAULT_AUTH_TOKEN);
    setSavedMsg("RACoN server defaults applied.");
    setTimeout(() => setSavedMsg(""), 2000);
    if (onRefresh) onRefresh();
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedFunnel(true);
    setTimeout(() => setCopiedFunnel(false), 2000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Compiled-in RACoN server defaults */}
      <Card title="RACoN Server — Preconfigured">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <p style={{ fontSize: "13px", color: "#8b949e", margin: 0 }}>
            This build connects to the RACoN server automatically on first launch. The endpoint
            is published over HTTPS, so it works from any network without Tailscale or extra setup.
          </p>

          <div
            style={{
              padding: "12px 16px",
              backgroundColor: "#0d1117",
              border: "1px solid #1f6feb",
              borderRadius: "6px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
            }}
          >
            <div>
              <div style={{ fontSize: "11px", color: "#58a6ff", fontWeight: 600 }}>DEFAULT SERVER URL</div>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#f0f6fc", marginTop: "2px", wordBreak: "break-all" }}>
                {DEFAULT_API_BASE}
              </div>
            </div>

            <button
              onClick={() => copyToClipboard(DEFAULT_API_BASE)}
              style={{
                padding: "6px 12px",
                backgroundColor: "#1f6feb",
                color: "#ffffff",
                border: "none",
                borderRadius: "4px",
                fontSize: "12px",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: "6px",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {copiedFunnel ? <Check size={14} /> : <Copy size={14} />}
              <span>{copiedFunnel ? "Copied!" : "Copy URL"}</span>
            </button>
          </div>

          <div style={{ fontSize: "12px", color: "#8b949e" }}>
            To use this computer's own engine instead, set the base URL to the address shown in
            this window and use the local per-install token. “Use RACoN Server Defaults” restores
            the preconfigured connection.
          </div>
        </div>
      </Card>

      <Card title="Coordinator Connection (Local / Tailscale / Docker)">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px" }}>
          <div>
            <label style={{ display: "block", color: "#8b949e", marginBottom: "4px" }}>
              Coordinator API Base URL:
            </label>
            <input
              type="text"
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="http://127.0.0.1:8765 or http://100.76.208.54:8765"
              style={{
                width: "100%",
                padding: "8px 12px",
                backgroundColor: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: "6px",
                color: "#c9d1d9",
                fontSize: "13px",
              }}
            />
            <span style={{ fontSize: "11px", color: "#8b949e" }}>
              Preconfigured to the RACoN server. Change this only to use a local engine or a
              different host.
            </span>
          </div>

          <div>
            <label style={{ display: "block", color: "#8b949e", marginBottom: "4px" }}>
              Authentication Token (X-RACON-TOKEN / Bearer):
            </label>
            <input
              type="text"
              value={authToken}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste the server API token"
              style={{
                width: "100%",
                padding: "8px 12px",
                backgroundColor: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: "6px",
                color: "#c9d1d9",
                fontSize: "13px",
              }}
            />
            <span style={{ fontSize: "11px", color: "#8b949e" }}>
              Preconfigured for the RACoN server. When the base URL is this window's own origin,
              the local per-install token is used instead.
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", marginTop: "4px", flexWrap: "wrap" }}>
            <button
              onClick={handleSaveConnection}
              style={{
                padding: "8px 16px",
                backgroundColor: "#238636",
                color: "#ffffff",
                border: "none",
                borderRadius: "6px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Save & Reconnect
            </button>
            <button
              onClick={applyServerDefaults}
              style={{
                padding: "8px 16px",
                backgroundColor: "#21262d",
                color: "#c9d1d9",
                border: "1px solid #30363d",
                borderRadius: "6px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Use RACoN Server Defaults
            </button>
            {savedMsg && <span style={{ color: "#3fb950", fontSize: "12px" }}>{savedMsg}</span>}
          </div>
        </div>
      </Card>

      <Card title="System Configuration & Security">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px" }}>
          <Row label="API Binding" value={`${getApiBase()} (loopback by default)`} />
          <Row label="Authentication" value="Per-install token (X-RACON-TOKEN / Bearer)" />
          <Row label="Database Engine" value="SQLite 3 (WAL Mode + migrations)" />
          <Row label="Inference Runtime" value="llama.cpp / Ollama adapters" />
          <Row label="Ollama Endpoint" value="http://127.0.0.1:11434" />
          <div style={{ marginTop: "12px", borderTop: "1px solid #30363d", paddingTop: "12px" }}>
            <h4 style={{ fontSize: "13px", color: "#f0f6fc", marginBottom: "6px" }}>Persistent Settings in SQLite:</h4>
            {Object.entries(settings || {}).map(([k, v]) => (
              <Row key={k} label={k} value={v} />
            ))}
            {Object.keys(settings || {}).length === 0 && (
              <div style={{ color: "#8b949e" }}>Using default production settings.</div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Playground — real local inference with streaming and measured metrics
// ---------------------------------------------------------------------------

interface PlaygroundMessage {
  role: "user" | "assistant";
  content: string;
  job?: JobItem;
}

interface PlaygroundMetrics {
  runtime?: string;
  timeToFirstTokenMs?: number | null;
  durationMs?: number;
  promptTokens?: number;
  completionTokens?: number;
  usageReported?: boolean;
  tokensPerSecond?: number | null;
}

const FRIEND_AI_SYSTEM_PROMPT = [
  "You are Friend AI, the on-device assistant inside RACoN.",
  "You run as a local model on this computer.",
  "Be concise and direct. Never claim that remote workers were used and never invent",
  "results for work you cannot perform locally. When a request needs the RACoN network,",
  "explain that it will run as a task graph and that the user can switch to network mode.",
].join(" ");

function jobStateColor(state: string): string {
  switch (state) {
    case "completed":
      return "#3fb950";
    case "assigned":
    case "running":
      return "#58a6ff";
    case "failed":
      return "#f85149";
    case "cancelled":
      return "#8b949e";
    default:
      return "#d29922";
  }
}

function JobMessageView({ job }: { job: JobItem }) {
  const tasks = job.tasks ?? [];
  const active = !TERMINAL_JOB_STATES.includes(job.status);
  const failedTasks = tasks.filter((t) => t.state === "failed");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px", fontSize: "13px" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", fontSize: "11px" }}>
        <span
          style={{
            padding: "2px 7px",
            borderRadius: "4px",
            fontWeight: 600,
            color: jobStatusColor(job.status),
            backgroundColor: `${jobStatusColor(job.status)}22`,
          }}
        >
          {job.status.toUpperCase()}
        </span>
        <span style={{ color: "#8b949e" }}>planner: {job.planner_mode || "pending"}</span>
        <span style={{ color: "#30363d" }}>·</span>
        <span style={{ color: "#8b949e" }}>
          {job.execution_mode ? `${job.execution_mode} execution` : `${job.requested_mode} requested`}
        </span>
        {job.tasks_total > 0 && (
          <>
            <span style={{ color: "#30363d" }}>·</span>
            <span style={{ color: "#8b949e" }}>
              {job.tasks_completed}/{job.tasks_total} tasks
              {job.tasks_failed ? ` · ${job.tasks_failed} failed` : ""}
            </span>
          </>
        )}
      </div>

      {active && (
        <div style={{ color: "#8b949e", fontSize: "12px" }}>
          Working across the network. The synthesized answer streams in as it is produced.
        </div>
      )}

      {job.planner_mode === "fallback_single" && job.plan?.planner_note && (
        <div style={{ color: "#d29922", fontSize: "11px" }}>
          Decomposition unavailable: {job.plan.planner_note}
        </div>
      )}

      {tasks.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          {tasks.map((t) => (
            <div key={t.task_id} style={{ display: "flex", gap: "8px", alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ color: jobStateColor(t.state), fontSize: "10px", minWidth: "78px", fontWeight: 600 }}>
                {t.state.toUpperCase()}
              </span>
              <span style={{ color: "#c9d1d9" }}>{t.title}</span>
              {t.worker_id && (
                <span style={{ color: "#8b949e", fontSize: "11px" }}>→ {t.worker_id}</span>
              )}
              {t.attempts > 1 && (
                <span style={{ color: "#d29922", fontSize: "11px" }}>attempt {t.attempts}</span>
              )}
              {t.error && <span style={{ color: "#ffa198", fontSize: "11px" }}>{t.error}</span>}
            </div>
          ))}
        </div>
      )}

      {job.result && (
        <div
          style={{
            borderTop: "1px solid #30363d",
            paddingTop: "10px",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "#e6edf3",
          }}
        >
          {job.result}
        </div>
      )}

      {job.status === "partial" && (
        <div style={{ color: "#d29922", fontSize: "12px" }}>
          Partial result: {job.tasks_failed} of {job.tasks_total} tasks did not complete. Missing work
          is listed above and was not invented.
        </div>
      )}

      {job.status === "failed" && (
        <div style={{ color: "#ffa198", fontSize: "12px" }}>
          {job.error || "The job failed before producing a result."}
        </div>
      )}

      {failedTasks.length > 0 && job.status !== "failed" && (
        <div style={{ color: "#ffa198", fontSize: "11px" }}>
          Failed tasks: {failedTasks.map((t) => t.title).join(", ")}
        </div>
      )}
    </div>
  );
}

function PlaygroundTab() {
  const [engine, setEngine] = useState<EngineStatusResponse | null>(null);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [messages, setMessages] = useState<PlaygroundMessage[]>([]);
  const [input, setInput] = useState<string>("");
  const [streaming, setStreaming] = useState<boolean>(false);
  const [metrics, setMetrics] = useState<PlaygroundMetrics | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mode, setMode] = useState<"local" | "network">("local");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const abortRef = React.useRef<(() => void) | null>(null);
  const jobIntervalRef = React.useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.();
      if (jobIntervalRef.current) clearInterval(jobIntervalRef.current);
    };
  }, []);

  const refresh = React.useCallback(async () => {
    try {
      const status = await api.getEngineStatus();
      setEngine(status);
      setEngineError(null);
      if (!model && status.models.length > 0) {
        setModel(status.models[0].id);
      }
    } catch (err) {
      setEngineError(String(err));
    }
  }, [model]);

  useEffect(() => {
    refresh();
  }, []);

  const updateJobMessage = (job: JobItem, streamedText = "") => {
    setMessages((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].job?.id === job.id) {
          const mergedResult =
            job.result ?? (streamedText || next[i].job?.result || null);
          next[i] = { ...next[i], job: { ...job, result: mergedResult } };
          return next;
        }
      }
      return [
        ...next,
        { role: "assistant", content: "", job: { ...job, result: job.result ?? (streamedText || null) } },
      ];
    });
  };

  const sendToNetwork = async (prompt: string, history: PlaygroundMessage[]) => {
    setMessages([...history, { role: "assistant", content: "Planning a task graph…" }]);
    setInput("");
    setStreaming(true);
    setMetrics(null);

    let job: JobItem;
    try {
      job = await api.createJob({
        model_name: model,
        prompt,
        max_tokens: 2048,
        decompose: true,
        execution_mode: "auto",
        max_parallel_tasks: 3,
      });
    } catch (err) {
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: "assistant",
          content: `⚠ Job submission failed: ${String(err)}`,
        };
        return next;
      });
      setStreaming(false);
      return;
    }

    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = { role: "assistant", content: "", job };
      return next;
    });
    setActiveJobId(job.id);

    let streamed = "";
    abortRef.current = streamJob(job.id, {
      onJob: (latest) => {
        updateJobMessage(latest, streamed);
      },
      onDelta: (text) => {
        streamed += text;
        setMessages((prev) => {
          const next = [...prev];
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].job?.id === job.id) {
              const current = next[i].job!;
              next[i] = {
                ...next[i],
                job: { ...current, result: `${current.result ?? ""}${text}` },
              };
              return next;
            }
          }
          return next;
        });
      },
      onDone: (latest) => {
        updateJobMessage(latest, streamed);
        if (jobIntervalRef.current) clearInterval(jobIntervalRef.current);
        jobIntervalRef.current = null;
        setActiveJobId(null);
        setStreaming(false);
        abortRef.current = null;
      },
      onError: (err) => {
        setEngineError(`Network stream ${err.code}: ${err.message}`);
        // If this browser cannot hold the SSE connection, keep polling so the final
        // synthesized answer is still shown instead of being lost.
        if (jobIntervalRef.current) clearInterval(jobIntervalRef.current);
        jobIntervalRef.current = setInterval(async () => {
          try {
            const latest = await api.getJob(job.id);
            updateJobMessage(latest, streamed);
            if (TERMINAL_JOB_STATES.includes(latest.status)) {
              if (jobIntervalRef.current) clearInterval(jobIntervalRef.current);
              jobIntervalRef.current = null;
              setActiveJobId(null);
              setStreaming(false);
            }
          } catch {
            // transient poll failure; the next tick retries
          }
        }, 1500);
        abortRef.current = null;
      },
    });
  };

  const send = () => {
    const prompt = input.trim();
    if (!prompt || streaming || !model) return;

    const history: PlaygroundMessage[] = [...messages, { role: "user", content: prompt }];

    if (mode === "network") {
      sendToNetwork(prompt, history);
      return;
    }

    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);
    setMetrics(null);

    const started = performance.now();
    let firstDeltaAt: number | null = null;

    abortRef.current = streamChat(
      {
        model,
        messages: [
          { role: "system", content: FRIEND_AI_SYSTEM_PROMPT },
          ...history.map((m) => ({ role: m.role, content: m.content })),
        ],
        max_tokens: 512,
        temperature: 0.7,
      },
      {
        onDelta: (text) => {
          if (firstDeltaAt === null) firstDeltaAt = performance.now();
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, content: last.content + text };
            return next;
          });
        },
        onDone: (meta) => {
          const durationMs = performance.now() - started;
          const completion = meta.usage?.completion_tokens ?? 0;
          const generationMs = firstDeltaAt ? durationMs - (firstDeltaAt - started) : 0;
          setMetrics({
            runtime: meta.racon?.runtime,
            timeToFirstTokenMs: firstDeltaAt ? firstDeltaAt - started : null,
            durationMs: meta.racon?.duration_ms ?? durationMs,
            promptTokens: meta.usage?.prompt_tokens,
            completionTokens: meta.usage?.completion_tokens,
            usageReported: meta.usage?.reported ?? false,
            tokensPerSecond:
              completion > 0 && generationMs > 0
                ? Math.round((completion / (generationMs / 1000)) * 100) / 100
                : null,
          });
          setStreaming(false);
          abortRef.current = null;
        },
        onError: (err) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              content: `⚠ ${err.code}: ${err.message}`,
            };
            return next;
          });
          setStreaming(false);
          abortRef.current = null;
        },
      }
    );
  };

  const stop = async () => {
    if (mode === "network" && activeJobId) {
      abortRef.current?.();
      abortRef.current = null;
      try {
        await api.cancelJob(activeJobId);
        updateJobMessage(await api.getJob(activeJobId));
      } catch (err) {
        setEngineError(String(err));
      }
      if (jobIntervalRef.current) clearInterval(jobIntervalRef.current);
      jobIntervalRef.current = null;
      setActiveJobId(null);
      setStreaming(false);
      return;
    }

    abortRef.current?.();
    abortRef.current = null;
    try {
      await api.cancelGeneration();
    } catch {
      // the abort above already stopped the stream
    }
    setStreaming(false);
  };

  const loadModel = async () => {
    if (!model) return;
    setBusy("Loading model…");
    try {
      await api.loadModel(model);
      await refresh();
    } catch (err) {
      setEngineError(String(err));
    } finally {
      setBusy(null);
    }
  };

  const unloadModel = async () => {
    setBusy("Unloading model…");
    try {
      await api.unloadModel();
      await refresh();
    } catch (err) {
      setEngineError(String(err));
    } finally {
      setBusy(null);
    }
  };

  const health = engine?.health;
  const loaded = engine?.loaded_model;
  const installed = engine?.models ?? [];
  const selectedModel = installed.find((m) => m.id === model);
  // The configured runtime is only the default: an installed GGUF is served by llama.cpp
  // even when Ollama is configured, so show the runtime that will actually run this model.
  const effectiveRuntime = selectedModel?.runtime || health?.runtime || "n/a";
  const noRuntimeAvailable =
    !!health && !health.available && (engine?.available_runtimes?.length ?? 0) === 0;

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px", height: "100%", overflow: "auto" }}>
      {engineError && (
        <div style={{ backgroundColor: "#3d1a1a", border: "1px solid #f85149", borderRadius: "6px", padding: "10px 14px", color: "#ffa198", fontSize: "13px" }}>
          {engineError}
        </div>
      )}

      {noRuntimeAvailable && (
        <div style={{ backgroundColor: "#2d2410", border: "1px solid #d29922", borderRadius: "6px", padding: "10px 14px", color: "#e3b341", fontSize: "13px" }}>
          Inference runtime unavailable. {health?.detail || "Install llama.cpp or start Ollama."}
        </div>
      )}

      <Card title="Model & Runtime">
        <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" }}>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            style={{ padding: "8px 12px", backgroundColor: "#0d1117", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: "6px", minWidth: "280px", fontSize: "13px" }}
          >
            {installed.length === 0 && <option value="">No models installed</option>}
            {installed.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} ({m.size_mb} MB)
              </option>
            ))}
          </select>

          <button onClick={loadModel} disabled={!model || busy !== null} style={buttonStyle("#21262d")}>
            {busy ?? "Load"}
          </button>
          <button onClick={unloadModel} disabled={busy !== null} style={buttonStyle("#21262d")}>
            Unload
          </button>
          <button onClick={refresh} style={buttonStyle("#21262d")}>
            <RefreshCw size={13} /> Refresh
          </button>

          <span style={{ fontSize: "12px", color: "#8b949e" }}>
            Runtime: <strong style={{ color: "#58a6ff" }}>{effectiveRuntime}</strong> · State:{" "}
            <strong style={{ color: loaded ? "#3fb950" : "#8b949e" }}>{health?.load_state || "unknown"}</strong>
            {loaded ? ` · ${loaded}` : ""}
          </span>
        </div>
        {installed.length === 0 && (
          <div style={{ marginTop: "10px", fontSize: "12px", color: "#8b949e" }}>
            No GGUF models found. Add one to {engine?.models_dir ?? "the models directory"} and refresh.
          </div>
        )}
      </Card>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.04em" }}>
          Ask as
        </span>
        <button onClick={() => setMode("local")} disabled={streaming} style={pillStyle(mode === "local")}>
          <MessageSquare size={12} /> Friend AI · local
        </button>
        <button onClick={() => setMode("network")} disabled={streaming} style={pillStyle(mode === "network")}>
          <GitBranch size={12} /> RACoN network
        </button>
        <span style={{ fontSize: "11px", color: "#8b949e" }}>
          {mode === "local"
            ? "Runs entirely on this computer and never claims remote work."
            : "Plans a task graph, runs independent tasks on compatible workers, then streams the synthesized answer."}
        </span>
      </div>

      <div style={{ display: "flex", gap: "16px", flex: 1, minHeight: "360px" }}>
        <div style={{ flex: 2, display: "flex", flexDirection: "column", backgroundColor: "#161b22", border: "1px solid #30363d", borderRadius: "8px", overflow: "hidden" }}>
          <div style={{ flex: 1, overflow: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: "12px" }}>
            {messages.length === 0 && (
              <div style={{ color: "#8b949e", fontSize: "13px" }}>
                {mode === "local"
                  ? "Ask Friend AI anything. Tokens stream from the local runtime as they are produced."
                  : "Describe a request that benefits from several independent tasks. RACoN shows the plan, each task's worker, and the synthesized answer."}
              </div>
            )}
            {messages.map((m, index) => {
              if (m.job) {
                return (
                  <div
                    key={index}
                    style={{
                      alignSelf: "stretch",
                      maxWidth: "100%",
                      backgroundColor: "#0d1117",
                      border: "1px solid #30363d",
                      borderRadius: "8px",
                      padding: "12px",
                    }}
                  >
                    <JobMessageView job={m.job} />
                  </div>
                );
              }
              return (
                <div
                  key={index}
                  style={{
                    alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                    maxWidth: "85%",
                    backgroundColor: m.role === "user" ? "#1f6feb" : "#0d1117",
                    border: m.role === "user" ? "none" : "1px solid #30363d",
                    borderRadius: "8px",
                    padding: "10px 12px",
                    fontSize: "13px",
                    color: "#e6edf3",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {m.content || (streaming && index === messages.length - 1 ? "▋" : "")}
                </div>
              );
            })}
          </div>

          <div style={{ borderTop: "1px solid #30363d", padding: "12px", display: "flex", gap: "10px" }}>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={
                !model
                  ? "Select a model first"
                  : mode === "local"
                  ? "Ask Friend AI…  (Enter to send, Shift+Enter for newline)"
                  : "Describe the work RACoN should distribute…  (Enter to send)"
              }
              rows={2}
              style={{ flex: 1, resize: "none", padding: "10px", backgroundColor: "#0d1117", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: "6px", fontSize: "13px", fontFamily: "inherit" }}
            />
            {streaming ? (
              <button onClick={stop} style={buttonStyle("#da3633")}>
                <Square size={14} /> {mode === "network" ? "Cancel job" : "Stop"}
              </button>
            ) : (
              <button onClick={send} disabled={!model || !input.trim()} style={buttonStyle("#238636")}>
                <Send size={14} /> Send
              </button>
            )}
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px" }}>
          <Card title="Generation Metrics">
            {!metrics ? (
              <div style={{ fontSize: "12px", color: "#8b949e" }}>No generation yet.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                <Row label="Runtime" value={metrics.runtime ?? "unknown"} />
                <Row
                  label="Time to first token"
                  value={metrics.timeToFirstTokenMs != null ? `${metrics.timeToFirstTokenMs.toFixed(0)} ms` : "not measured"}
                />
                <Row label="Duration" value={`${(metrics.durationMs ?? 0).toFixed(0)} ms`} />
                <Row
                  label="Tokens / second"
                  value={metrics.tokensPerSecond != null ? metrics.tokensPerSecond.toFixed(2) : "not measured"}
                />
                <Row label="Prompt tokens" value={String(metrics.promptTokens ?? 0)} />
                <Row label="Completion tokens" value={String(metrics.completionTokens ?? 0)} />
                {!metrics.usageReported && (
                  <div style={{ fontSize: "11px", color: "#d29922", marginTop: "6px" }}>
                    The runtime did not report token counts for this response; values are left empty rather than estimated.
                  </div>
                )}
              </div>
            )}
          </Card>

          <Card title="Installed Models">
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {installed.length === 0 && <div style={{ fontSize: "12px", color: "#8b949e" }}>None found.</div>}
              {installed.map((m) => (
                <div key={m.id} style={{ borderBottom: "1px solid #21262d", paddingBottom: "6px" }}>
                  <div style={{ fontSize: "12px", color: "#c9d1d9" }}>{m.id}</div>
                  <div style={{ fontSize: "11px", color: "#8b949e" }}>
                    {m.size_mb} MB · {m.family || "unknown family"} · ctx {m.context_length ?? "?"}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function pillStyle(active: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 11px",
    borderRadius: "999px",
    fontSize: "12px",
    cursor: active ? "default" : "pointer",
    border: `1px solid ${active ? "#1f6feb" : "#30363d"}`,
    backgroundColor: active ? "#1f6feb33" : "#21262d",
    color: active ? "#79c0ff" : "#c9d1d9",
    fontWeight: active ? 600 : 400,
  };
}

function buttonStyle(background: string): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    padding: "8px 14px",
    backgroundColor: background,
    color: "#ffffff",
    border: background === "#21262d" ? "1px solid #30363d" : "none",
    borderRadius: "6px",
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
  };
}

// Helper components
function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        backgroundColor: "#161b22",
        border: "1px solid #30363d",
        borderRadius: "8px",
        padding: "16px",
      }}
    >
      <h3 style={{ fontSize: "14px", fontWeight: 600, color: "#f0f6fc", marginBottom: "12px" }}>{title}</h3>
      {children}
    </div>
  );
}

function MetricCard({
  label,
  value,
  subtitle,
  color,
}: {
  label: string;
  value: string;
  subtitle: string;
  color: string;
}) {
  return (
    <div
      style={{
        backgroundColor: "#161b22",
        border: "1px solid #30363d",
        borderRadius: "8px",
        padding: "16px",
      }}
    >
      <div style={{ fontSize: "12px", color: "#8b949e", marginBottom: "6px" }}>{label}</div>
      <div style={{ fontSize: "22px", fontWeight: "bold", color }}>{value}</div>
      <div style={{ fontSize: "11px", color: "#8b949e", marginTop: "4px" }}>{subtitle}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid #21262d" }}>
      <span style={{ color: "#8b949e" }}>{label}</span>
      <span style={{ color: "#c9d1d9", fontWeight: 500 }}>{value}</span>
    </div>
  );
}
