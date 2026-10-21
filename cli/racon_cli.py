"""RACoN Companion CLI — Typer-based command line interface."""
from __future__ import annotations

import sys
import json
import httpx
import typer
from rich.console import Console
from rich.table import Table

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = typer.Typer(help="RACoN CLI — Resource-Aware Collaborative Network")
node_app = typer.Typer(help="Manage compute nodes")
worker_app = typer.Typer(help="Manage worker sessions")
models_app = typer.Typer(help="Inspect and manage models")
scheduler_app = typer.Typer(help="Scheduler preview and configuration")
benchmark_app = typer.Typer(help="Benchmark execution")
jobs_app = typer.Typer(help="Inference jobs")

app.add_typer(node_app, name="node")
app.add_typer(worker_app, name="worker")
app.add_typer(models_app, name="models")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(jobs_app, name="jobs")

console = Console()
DEFAULT_API_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN = "racon-local-dev-token"


def get_client(api_url: str = DEFAULT_API_URL, token: str = DEFAULT_TOKEN) -> httpx.Client:
    return httpx.Client(
        base_url=api_url,
        headers={"X-RACON-TOKEN": token, "Content-Type": "application/json"},
        timeout=10.0,
    )


@app.command()
def status(
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """Check RACoN Coordinator status and hardware metrics."""
    with get_client(api_url, token) as client:
        try:
            res = client.get("/api/v1/status")
            if res.status_code != 200:
                console.print(f"[red]Error fetching status: HTTP {res.status_code}[/red]")
                return
            data = res.json()
            hw = data.get("hardware", {})

            table = Table(title="RACoN Cluster Status", header_style="bold cyan")
            table.add_column("Property", style="bold")
            table.add_column("Value")

            table.add_row("Coordinator Status", f"[green]{data.get('status', 'unknown').upper()}[/green]")
            table.add_row("Uptime", f"{data.get('uptime_seconds', 0):.0f} seconds")
            table.add_row("Version", data.get("version", "0.1.0"))
            table.add_row("Laya Decision Engine", data.get("laya_status", "unknown").upper())
            table.add_row("Ollama Inference Backend", data.get("ollama_status", "unknown").upper())
            table.add_row("CPU Model", hw.get("cpu_name", "Unknown"))
            table.add_row("CPU Utilization", f"{hw.get('cpu_utilization_pct', 0):.1f}%")
            table.add_row("System RAM", f"{hw.get('ram_used_mb', 0) / 1024:.1f} GB used / {hw.get('ram_total_mb', 0) / 1024:.1f} GB total ({hw.get('ram_utilization_pct', 0):.0f}%)")
            table.add_row("Process RSS", f"{hw.get('process_memory_mb', 0):.1f} MB")
            table.add_row("GPU Device", hw.get("gpu", {}).get("name") or "Intel UHD Graphics 630")
            console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to connect to backend at {api_url}: {e}[/red]")


@node_app.command("list")
def list_nodes(
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """List registered compute nodes."""
    with get_client(api_url, token) as client:
        try:
            res = client.get("/api/v1/nodes")
            if res.status_code != 200:
                console.print(f"[red]Error: HTTP {res.status_code}[/red]")
                return
            nodes = res.json()
            table = Table(title="Registered Nodes", header_style="bold green")
            table.add_column("Node ID", style="bold")
            table.add_column("Type")
            table.add_column("Status")
            table.add_column("Cores")
            table.add_column("RAM (Avail/Total)")
            table.add_column("GPU")

            for n in nodes:
                node_type = "[blue]LIVE LOCAL[/blue]" if n.get("is_local") else "[dim]SIMULATED[/dim]"
                status_color = "green" if n.get("status") == "available" else "yellow"
                table.add_row(
                    n.get("name", n.get("id")),
                    node_type,
                    f"[{status_color}]{n.get('status', 'unknown').upper()}[/{status_color}]",
                    str(n.get("cpu_cores", 0)),
                    f"{n.get('ram_available_mb', 0)/1024:.1f} / {n.get('ram_total_mb', 0)/1024:.1f} GB",
                    n.get("gpu_name") or "Integrated",
                )
            console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to fetch nodes: {e}[/red]")


@worker_app.command("start")
def start_worker(
    node_id: str = typer.Argument("local-node", help="Node ID"),
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """Start a worker session for a node."""
    with get_client(api_url, token) as client:
        try:
            res = client.post(f"/api/v1/nodes/{node_id}/start")
            console.print(f"[green]Worker session started for {node_id}: {res.json()}[/green]")
        except Exception as e:
            console.print(f"[red]Error starting worker: {e}[/red]")


@worker_app.command("stop")
def stop_worker(
    node_id: str = typer.Argument("local-node", help="Node ID"),
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """Stop an active worker session for a node."""
    with get_client(api_url, token) as client:
        try:
            res = client.post(f"/api/v1/nodes/{node_id}/stop")
            console.print(f"[yellow]Worker session stopped for {node_id}: {res.json()}[/yellow]")
        except Exception as e:
            console.print(f"[red]Error stopping worker: {e}[/red]")


@models_app.command("list")
def list_models(
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """List available models from local runtimes."""
    with get_client(api_url, token) as client:
        try:
            res = client.get("/api/v1/models")
            models = res.json()
            table = Table(title="Local Models", header_style="bold magenta")
            table.add_column("Model Name", style="bold")
            table.add_column("Runtime")
            table.add_column("Size (MB)")
            table.add_column("Status")

            for m in models:
                table.add_row(
                    m.get("name"),
                    m.get("runtime", "ollama"),
                    f"{m.get('size_bytes', 0) / (1024 * 1024):.0f}",
                    "[green]Ready[/green]" if m.get("is_loaded") else "[dim]Cached[/dim]",
                )
            console.print(table)
        except Exception as e:
            console.print(f"[red]Error listing models: {e}[/red]")


@scheduler_app.command("preview")
def scheduler_preview(
    scenario: str = typer.Option("Multiple Feasible Candidates", help="Scenario name"),
    objective: str = typer.Option("balanced", help="Scheduling objective (balanced, lowest_latency, highest_throughput, resource_efficiency)"),
    mode: str = typer.Option("deterministic", help="Mode (deterministic, laya_shadow, laya_assisted)"),
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """Evaluate and preview a scheduler placement decision."""
    with get_client(api_url, token) as client:
        try:
            res = client.post(
                "/api/v1/scheduler/preview",
                json={"scenario_name": scenario, "objective": objective, "mode": mode},
            )
            data = res.json()
            console.print(f"\n[bold cyan]Scheduler Preview Result: {scenario}[/bold cyan]")
            console.print(f"Objective: [bold]{objective}[/bold] | Mode: [bold]{mode}[/bold]")
            console.print(f"Deterministic Winner: [green]{data.get('deterministic_choice')}[/green] (Score: {data.get('deterministic_score')})")
            if data.get("laya_recommendation"):
                console.print(f"Laya Recommendation: [magenta]{data.get('laya_recommendation')}[/magenta] (Confidence: {data.get('laya_confidence')})")
            console.print(f"Final Selection: [bold green]{data.get('final_choice')}[/bold green] (in {data.get('decision_duration_ms', 0):.1f} ms)\n")

            table = Table(title="Candidate Breakdown", header_style="bold")
            table.add_column("Worker ID")
            table.add_column("Feasible")
            table.add_column("Score")
            table.add_column("Rejection Reason")

            for ev in data.get("evaluations", []):
                table.add_row(
                    ev.get("worker_id"),
                    "[green]YES[/green]" if ev.get("is_feasible") else "[red]NO[/red]",
                    f"{ev.get('score'):.4f}" if ev.get("score") is not None else "-",
                    ", ".join(ev.get("rejection_reasons", [])) or "None",
                )
            console.print(table)
        except Exception as e:
            console.print(f"[red]Error previewing scheduler: {e}[/red]")


@benchmark_app.command("run")
def run_benchmark(
    scenario: str = typer.Option("Multiple Feasible Candidates", help="Scenario name"),
    objective: str = typer.Option("balanced", help="Scheduling objective"),
    mode: str = typer.Option("deterministic", help="Scheduling mode"),
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """Run and record a scheduler benchmark."""
    with get_client(api_url, token) as client:
        try:
            res = client.post(
                "/api/v1/benchmarks",
                json={"scenario_name": scenario, "objective": objective, "mode": mode},
            )
            data = res.json()
            console.print(f"[green][OK] Benchmark recorded: {data}[/green]")
        except Exception as e:
            console.print(f"[red]Error running benchmark: {e}[/red]")


@jobs_app.command("list")
def list_jobs(
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
    token: str = typer.Option(DEFAULT_TOKEN, help="Auth token"),
):
    """List inference jobs."""
    with get_client(api_url, token) as client:
        try:
            res = client.get("/api/v1/jobs")
            jobs = res.json()
            table = Table(title="Inference Tasks", header_style="bold cyan")
            table.add_column("Job ID")
            table.add_column("Model")
            table.add_column("Status")
            table.add_column("Prompt")
            table.add_column("Created At")

            for j in jobs:
                table.add_row(
                    j.get("id", "")[:8],
                    j.get("model_name"),
                    j.get("status"),
                    j.get("prompt", "")[:40] + "...",
                    j.get("created_at"),
                )
            console.print(table)
        except Exception as e:
            console.print(f"[red]Error listing jobs: {e}[/red]")


@app.command()
def doctor(
    api_url: str = typer.Option(DEFAULT_API_URL, help="Backend API URL"),
):
    """Perform diagnostic health check on all RACoN subsystems."""
    console.print("\n[bold][*] Running RACoN Doctor Diagnostic Check...[/bold]\n")

    # 1. Check local backend
    try:
        r = httpx.get(f"{api_url}/health", timeout=3.0)
        if r.status_code == 200:
            console.print("[green][OK] Local Backend Service:[/green] Connected on 127.0.0.1:8765")
        else:
            console.print(f"[red][FAIL] Local Backend Service:[/red] HTTP {r.status_code}")
    except Exception as e:
        console.print(f"[yellow][OFFLINE] Local Backend Service:[/yellow] ({e})")

    # 2. Check Ollama
    try:
        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3.0)
        if r.status_code == 200:
            models = [m.get("name") for m in r.json().get("models", [])]
            console.print(f"[green][OK] Ollama Runtime:[/green] Online with {len(models)} models ({', '.join(models[:3])})")
        else:
            console.print(f"[yellow][WARN] Ollama Runtime:[/yellow] HTTP {r.status_code}")
    except Exception:
        console.print("[dim][INFO] Ollama Runtime:[/dim] Not detected on 127.0.0.1:11434 (Optional)")

    # 3. Check hardware psutil
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        console.print(f"[green][OK] Hardware Discovery:[/green] CPU: {cpu_pct:.1f}%, RAM: {mem.percent}% ({mem.available // (1024*1024)} MB available)")
    except Exception as e:
        console.print(f"[red][FAIL] Hardware Discovery Error:[/red] {e}")

    # 4. Check Laya
    try:
        import laya
        console.print(f"[green][OK] Laya Decision Engine:[/green] Installed (v{getattr(laya, '__version__', 'unknown')})")
    except ImportError:
        console.print("[dim][INFO] Laya Decision Engine:[/dim] Not installed (Optional, install with pip install laya)")

    console.print("\n[bold green]Diagnostics complete![/bold green]\n")


def main():
    app()


if __name__ == "__main__":
    main()
