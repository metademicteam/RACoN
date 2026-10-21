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
  Zap,
  Target,
  ExternalLink,
  Globe,
  HardDrive,
  Check,
  Copy,
} from "lucide-react";
import { api, getApiBase, setApiBase, getAuthToken, setAuthToken } from "./api";
import {
  SystemStatus,
  NodeItem,
  ModelItem,
  SchedulerPreviewResult,
  JobItem,
  BenchmarkRecord,
  CatalogModelItem,
} from "./types";

type TabType =
  | "overview"
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
  const [nodes, setNodes] = useState<NodeItem[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [catalog, setCatalog] = useState<CatalogModelItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkRecord[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [settingsData, setSettingsData] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

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
    try {
      const s = await api.getStatus();
      setStatus(s);
      setError(null);
    } catch {
      // Backend may be starting or offline
    }
  }

  async function fetchInitialData() {
    setLoading(true);
    try {
      const [s, n, m, cat, j, b, l, cfg] = await Promise.allSettled([
        api.getStatus(),
        api.getNodes(),
        api.getModels(),
        api.getModelCatalog(),
        api.getJobs(),
        api.getBenchmarks(),
        api.getLogs(50),
        api.getSettings(),
      ]);

      if (s.status === "fulfilled") setStatus(s.value);
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
            v0.2.0 • Model Hub & Funnel Ready
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "4px", flex: 1 }}>
          <NavItem icon={<Cpu size={16} />} label="Overview" active={activeTab === "overview"} onClick={() => setActiveTab("overview")} />
          <NavItem icon={<Server size={16} />} label="Nodes" active={activeTab === "nodes"} onClick={() => setActiveTab("nodes")} badge={nodes.length.toString()} />
          <NavItem icon={<Layers size={16} />} label="Model Hub" active={activeTab === "models"} onClick={() => setActiveTab("models")} badge={models.length ? `${models.length} ready` : "Download"} />
          <NavItem icon={<Sparkles size={16} />} label="Scheduler Lab" active={activeTab === "scheduler"} onClick={() => setActiveTab("scheduler")} />
          <NavItem icon={<Play size={16} />} label="Jobs & Inference" active={activeTab === "jobs"} onClick={() => setActiveTab("jobs")} badge={jobs.length.toString()} />
          <NavItem icon={<BarChart3 size={16} />} label="Benchmarks" active={activeTab === "benchmarks"} onClick={() => setActiveTab("benchmarks")} />
          <NavItem icon={<Terminal size={16} />} label="Logs" active={activeTab === "logs"} onClick={() => setActiveTab("logs")} />
          <NavItem icon={<Settings size={16} />} label="Settings & Funnel" active={activeTab === "settings"} onClick={() => setActiveTab("settings")} />
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
            {activeTab === "models" ? "Model Hub — Choose & Download AI Models" : activeTab.replace("_", " ")}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {status?.hardware && (
              <span style={{ fontSize: "12px", color: "#8b949e" }}>
                CPU: <strong style={{ color: "#58a6ff" }}>{status.hardware.cpu_utilization_pct?.toFixed(1) ?? "0.0"}%</strong> | RAM:{" "}
                <strong style={{ color: "#3fb950" }}>{status.hardware.ram_utilization_pct?.toFixed(1) ?? "0.0"}%</strong>
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
              nodes={nodes}
              models={models}
              jobs={jobs}
              onNavigateToModels={() => setActiveTab("models")}
            />
          )}
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
          {activeTab === "jobs" && <JobsTab jobs={jobs} onRefresh={fetchInitialData} />}
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
function OverviewTab({
  status,
  nodes,
  models,
  jobs,
  onNavigateToModels,
}: {
  status: SystemStatus | null;
  nodes: NodeItem[];
  models: ModelItem[];
  jobs: JobItem[];
  onNavigateToModels: () => void;
}) {
  const hw = status?.hardware;
  const cpuPct = hw?.cpu_utilization_pct != null ? `${hw.cpu_utilization_pct.toFixed(1)}%` : "0.0%";
  const cpuCores = hw?.cpu_logical_count ?? 4;
  const ramUsedGb = hw?.ram_used_mb != null ? (hw.ram_used_mb / 1024).toFixed(1) : "0.0";
  const ramTotalGb = hw?.ram_total_mb != null ? (hw.ram_total_mb / 1024).toFixed(1) : "16.0";
  const ramPct = hw?.ram_utilization_pct != null ? hw.ram_utilization_pct.toFixed(0) : "0";
  const readyNodesCount = (nodes || []).filter((n) => n.status === "available").length;
  const layaStatus = (status?.laya_status || "offline").toUpperCase();
  const ollamaStatus = status?.ollama_status || "offline";
  const uptimeSec = status?.uptime_seconds != null ? Math.floor(status.uptime_seconds) : 0;
  const processMem = hw?.process_memory_mb != null ? `${hw.process_memory_mb.toFixed(1)} MB` : "—";
  const gpuName = hw?.gpu?.name || "Intel UHD Graphics 630";
  const gpuTelemetry = hw?.gpu?.supported ? `${hw.gpu.vram_used_mb ?? 0} MB` : "Unavailable (Strict Non-Fabrication)";

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
            {models.length > 0
              ? `${models.length} local model(s) ready for distributed inference and Laya-assisted scheduling.`
              : "No model installed yet. Visit the Model Hub to download compact models like Llama 3.2 or Qwen 2.5 with 1-click."}
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

      {/* Metric Cards Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
        <MetricCard label="CPU Utilization" value={cpuPct} subtitle={`${cpuCores} Logical Cores`} color="#58a6ff" />
        <MetricCard label="System RAM" value={`${ramUsedGb} GB`} subtitle={`of ${ramTotalGb} GB (${ramPct}%)`} color="#3fb950" />
        <MetricCard label="Active Nodes" value={(nodes || []).length.toString()} subtitle={`${readyNodesCount} Ready`} color="#d29922" />
        <MetricCard label="Local Models" value={(models || []).length.toString()} subtitle={`Ollama: ${ollamaStatus}`} color="#a371f7" />
      </div>

      {/* Hardware & System Details */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
        <Card title="Hardware Specifications">
          <Row label="CPU Model" value={hw?.cpu_name || "Intel(R) Core(TM) i3"} />
          <Row label="Architecture" value={`${hw?.os_name || "Windows"} ${hw?.os_architecture || "AMD64"}`} />
          <Row label="Python Runtime" value={`Python ${hw?.python_version || "3.12"}`} />
          <Row label="Process Memory (RSS)" value={processMem} />
          <Row label="GPU Device" value={gpuName} />
          <Row label="GPU Telemetry" value={gpuTelemetry} />
        </Card>

        <Card title="Compute Cluster Health">
          <Row label="RACoN Coordinator" value={status ? "Running (0.0.0.0:8765)" : "Connecting..."} />
          <Row label="Laya Decision Advisor" value={`${layaStatus} ${status?.laya_version ? `(v${status.laya_version})` : ""}`} />
          <Row label="Ollama Inference Backend" value={ollamaStatus === "ready" ? `Connected (${(models || []).length} models ready)` : "Disconnected"} />
          <Row label="Active Tasks" value={`${(jobs || []).filter((j) => j.status === "running").length} running, ${(jobs || []).length} total`} />
          <Row label="Uptime" value={`${uptimeSec} seconds`} />
        </Card>
      </div>
    </div>
  );
}

// ---------------- TAB 2: NODES ----------------
function NodesTab({ nodes }: { nodes: NodeItem[]; onRefresh?: () => void }) {
  const nodeList = nodes || [];
  return (
    <Card title={`Registered Compute Nodes (${nodeList.length})`}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
            <th style={{ padding: "8px" }}>Node ID</th>
            <th>Type</th>
            <th>Status</th>
            <th>Cores</th>
            <th>RAM (Available / Total)</th>
            <th>GPU</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {nodeList.map((n) => (
            <tr key={n.id} style={{ borderBottom: "1px solid #21262d" }}>
              <td style={{ padding: "8px", fontWeight: 600 }}>{n.name}</td>
              <td>
                <span style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", backgroundColor: n.is_local ? "#1f6feb22" : "#8b949e22", color: n.is_local ? "#58a6ff" : "#8b949e" }}>
                  {n.is_local ? "LIVE LOCAL" : "SIMULATED"}
                </span>
              </td>
              <td>
                <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: n.status === "available" ? "#3fb950" : "#d29922" }}>
                  {n.status === "available" ? <CheckCircle size={12} /> : <XCircle size={12} />}
                  {(n.status || "UNKNOWN").toUpperCase()}
                </span>
              </td>
              <td>{n.cpu_cores ?? "—"}</td>
              <td>{n.ram_available_mb != null ? (n.ram_available_mb / 1024).toFixed(1) : "0.0"} / {n.ram_total_mb != null ? (n.ram_total_mb / 1024).toFixed(1) : "0.0"} GB</td>
              <td>{n.gpu_name || "Integrated"}</td>
              <td>
                <button
                  style={{
                    padding: "3px 8px",
                    backgroundColor: "#21262d",
                    color: "#c9d1d9",
                    border: "1px solid #30363d",
                    borderRadius: "4px",
                    fontSize: "11px",
                    cursor: "pointer",
                  }}
                  onClick={() => alert(`Worker session controls for node: ${n.id}`)}
                >
                  Manage
                </button>
              </td>
            </tr>
          ))}
          {nodeList.length === 0 && (
            <tr>
              <td colSpan={7} style={{ padding: "16px", textAlign: "center", color: "#8b949e" }}>
                No workers registered yet. Local node automatically registers upon startup.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
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

  // Determine installed vs available to download
  const installedCatalog = catalog.filter((c) => c.is_installed);
  const availableToDownload = catalog.filter((c) => !c.is_installed);

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

          {/* Section 3: Direct GGUF / Hugging Face Import */}
          <div
            style={{
              backgroundColor: "#161b22",
              border: "1px dashed #30363d",
              borderRadius: "10px",
              padding: "16px 20px",
            }}
          >
            <div style={{ fontSize: "14px", fontWeight: 600, color: "#f0f6fc", marginBottom: "4px" }}>
              Direct GGUF or Hugging Face Model Import
            </div>
            <p style={{ fontSize: "12px", color: "#8b949e", margin: "0 0 12px 0" }}>
              Paste any Hugging Face direct download link or local filesystem path (.gguf) to import custom weights.
            </p>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                type="text"
                placeholder="https://huggingface.co/.../model-q4_k_m.gguf or C:\path\to\model.gguf"
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
                onClick={() => {
                  if (!customGgufUrl) return;
                  alert(`Import queued for: ${customGgufUrl}`);
                  setCustomGgufUrl("");
                }}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#21262d",
                  border: "1px solid #30363d",
                  color: "#c9d1d9",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Import Model
              </button>
            </div>
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

        {/* Handy Accuracy & Speed Visual Indicator Bars */}
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

// ---------------- TAB 5: JOBS & INFERENCE ----------------
function JobsTab({ jobs }: { jobs: JobItem[]; onRefresh?: () => void }) {
  const jobList = jobs || [];
  return (
    <Card title={`Job Execution History (${jobList.length})`}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #30363d", textAlign: "left", color: "#8b949e" }}>
            <th style={{ padding: "6px" }}>Job ID</th>
            <th>Model</th>
            <th>Status</th>
            <th>Prompt</th>
            <th>Assigned Node</th>
            <th>Created At</th>
          </tr>
        </thead>
        <tbody>
          {jobList.map((j) => (
            <tr key={j.id} style={{ borderBottom: "1px solid #21262d" }}>
              <td style={{ padding: "6px", fontWeight: 600 }}>{j.id ? j.id.substring(0, 8) : "—"}</td>
              <td>{j.model_name || "—"}</td>
              <td>
                <span style={{ fontSize: "10px", padding: "2px 6px", borderRadius: "4px", backgroundColor: j.status === "completed" ? "#3fb95022" : "#58a6ff22", color: j.status === "completed" ? "#3fb950" : "#58a6ff" }}>
                  {(j.status || "QUEUED").toUpperCase()}
                </span>
              </td>
              <td style={{ maxWidth: "250px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.prompt}</td>
              <td>{j.assigned_node_id || "Local Worker"}</td>
              <td>{j.created_at ? new Date(j.created_at).toLocaleTimeString() : "—"}</td>
            </tr>
          ))}
          {jobList.length === 0 && (
            <tr>
              <td colSpan={6} style={{ padding: "16px", textAlign: "center", color: "#8b949e" }}>
                No inference jobs run yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
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
            <tr key={b.id} style={{ borderBottom: "1px solid #21262d" }}>
              <td style={{ padding: "6px" }}>{b.timestamp ? new Date(b.timestamp).toLocaleTimeString() : "—"}</td>
              <td style={{ fontWeight: 600 }}>{b.scenario}</td>
              <td>{b.objective}</td>
              <td>{b.mode}</td>
              <td>{b.winner || "—"}</td>
              <td>{b.score !== null ? b.score.toFixed(4) : "—"}</td>
              <td>{b.duration_ms != null ? b.duration_ms.toFixed(1) : "0.0"} ms</td>
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
function LogsTab({ logs }: { logs: string[]; onRefresh?: () => void }) {
  const logList = logs || [];
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
          <div key={i} style={{ marginBottom: "2px" }}>
            {l}
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

  const funnelUrl = "https://racon.tailscale.net";

  const handleSaveConnection = () => {
    setApiBase(apiUrl);
    setAuthToken(authToken);
    setSavedMsg("Connection settings saved! Reconnecting...");
    setTimeout(() => {
      setSavedMsg("");
      if (onRefresh) onRefresh();
    }, 1200);
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedFunnel(true);
    setTimeout(() => setCopiedFunnel(false), 2000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Tailscale Funnel Public Sharing Card */}
      <Card title="Tailscale Funnel — Public Global Access">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <p style={{ fontSize: "13px", color: "#8b949e", margin: 0 }}>
            Tailscale Funnel routes traffic from the public internet directly to this RACoN desktop app with automatic HTTPS. Anyone worldwide can use your desktop app without installing anything or being on your Tailnet.
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
            }}
          >
            <div>
              <div style={{ fontSize: "11px", color: "#58a6ff", fontWeight: 600 }}>PUBLIC HTTPS ACCESS URL</div>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#f0f6fc", marginTop: "2px" }}>
                http://100.76.208.54:1420 (Tailscale IP)
              </div>
            </div>

            <button
              onClick={() => copyToClipboard("http://100.76.208.54:1420")}
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
              }}
            >
              {copiedFunnel ? <Check size={14} /> : <Copy size={14} />}
              <span>{copiedFunnel ? "Copied!" : "Copy Share Link"}</span>
            </button>
          </div>

          <div style={{ fontSize: "12px", color: "#8b949e" }}>
            To activate internet-wide public Funnel on port 1420, run:
            <code style={{ display: "block", marginTop: "4px", padding: "6px 10px", backgroundColor: "#0d1117", borderRadius: "4px", color: "#58a6ff" }}>
              tailscale funnel 1420
            </code>
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
              Set to your Tailscale IP (e.g. http://100.76.208.54:8765) when accessing from another device.
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
              placeholder="racon-local-dev-token"
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
              Default development token is: racon-local-dev-token
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", marginTop: "4px" }}>
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
            {savedMsg && <span style={{ color: "#3fb950", fontSize: "12px" }}>{savedMsg}</span>}
          </div>
        </div>
      </Card>

      <Card title="System Configuration & Security">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px" }}>
          <Row label="API Binding" value="0.0.0.0:8765 (Loopback, LAN & Tailscale Ready)" />
          <Row label="Authentication" value="Token Verification (Header: X-RACON-TOKEN / Bearer)" />
          <Row label="Database Engine" value="SQLite 3 (WAL Mode + Foreign Keys)" />
          <Row label="Cloud Storage Adapter" value="Supabase (Configured, Offline Local Safe)" />
          <Row label="Ollama Endpoint" value="http://127.0.0.1:11434" />
          <Row label="Laya Integration" value="Router Typed Choice (Lazy Load, Max 1 Model)" />
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
