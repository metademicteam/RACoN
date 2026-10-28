"""Command-line entry point for the multi-PC cluster: `python -m racon.cluster <command>`."""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from racon.cluster.auth import ApiKeyStore
from racon.cluster.config import ClusterConfig, default_config_path, default_keys_path, load_config
from racon.cluster.discovery import discover_workers
from racon.cluster.gateway import create_gateway
from racon.cluster.head import HeadServer
from racon.cluster.supervisor import DEFAULT_PROBE_INTERVAL_S, ClusterSupervisor
from racon.runtime.rpc_worker import RpcServerProcess

logger = logging.getLogger(__name__)

app = typer.Typer(help="RACoN multi-PC distributed inference cluster", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys for the public gateway", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


def _config_option() -> Optional[Path]:
    return typer.Option(None, "--config", help="Path to cluster.json (default: per-user data dir)")


@app.command("init")
def init_config(
    model_path: str = typer.Option(..., help="Path to the GGUF model on the head PC"),
    path: Optional[Path] = typer.Option(None, "--config", help="Where to write cluster.json"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config"),
) -> None:
    """Write a starter cluster.json. Add worker Tailscale IPs to the 'workers' list afterwards."""
    target = path or default_config_path()
    if target.exists() and not force:
        typer.echo(f"Config already exists: {target} (use --force to overwrite)")
        raise typer.Exit(1)
    starter = {
        "model_path": model_path,
        "model_alias": "gemma-3-1b",
        "workers": [],
        "head_host": "127.0.0.1",
        "head_port": 8080,
        "gateway_host": "127.0.0.1",
        "gateway_port": 8000,
        "n_gpu_layers": 26,
        "context_size": 8192,
        "threads": 4,
        "max_tokens_limit": 2048,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(starter, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {target}")


@app.command("serve")
def serve(
    config: Optional[Path] = _config_option(),
    gateway_host: Optional[str] = typer.Option(None, help="Override gateway bind address"),
    gateway_port: Optional[int] = typer.Option(None, help="Override gateway port"),
    log_file: Optional[Path] = typer.Option(None, help="Write llama-server output to this file"),
) -> None:
    """Start the head (llama-server with RPC workers) and the public API gateway."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = _load(config)
    host = gateway_host or cfg.gateway_host
    port = gateway_port or cfg.gateway_port

    keys = ApiKeyStore(default_keys_path())
    if not keys.list():
        typer.echo("No API keys exist yet. Create one with: python -m racon.cluster keys create <name>")

    discovered = discover_workers()
    if discovered:
        cfg.workers = discovered
        typer.echo(f"Discovered {len(discovered)} approved RPC worker(s) from the coordinator registry.")
    else:
        typer.echo("No approved RPC workers discovered; the head will run alone unless cluster.json lists workers.")

    head = HeadServer(cfg, log_path=str(log_file) if log_file else None)
    supervisor = ClusterSupervisor(cfg, head)
    try:
        plan = supervisor.reconcile()
    except (RuntimeError, FileNotFoundError, TimeoutError, ValueError) as exc:
        typer.echo(f"Head failed to start: {exc}", err=True)
        raise typer.Exit(1)

    stop_loop = threading.Event()

    def _watch() -> None:
        while not stop_loop.wait(DEFAULT_PROBE_INTERVAL_S):
            try:
                supervisor.update_workers(discover_workers())
                changed = supervisor.reconcile()
                if changed is not None:
                    logger.warning("Cluster replanned after membership change.")
            except Exception:
                logger.exception("Supervisor reconcile failed; will retry.")

    threading.Thread(target=_watch, name="cluster-supervisor", daemon=True).start()

    if plan is not None:
        typer.echo(f"Layers: {[(a.device, a.layer_start, a.layer_end) for a in plan.assignments]}")
    typer.echo(f"Head ready at {head.base_url}.")
    typer.echo(f"Gateway listening on http://{host}:{port}  (Funnel should target this port only)")
    try:
        gateway = create_gateway(cfg, keys, head.base_url, head.internal_key)
        uvicorn.run(gateway, host=host, port=port, log_level="info")
    finally:
        stop_loop.set()
        head.stop()


@app.command("worker")
def worker(
    bind: str = typer.Option(..., help="This PC's Tailscale IP (100.x.y.z). Never 0.0.0.0 on a public network"),
    port: int = typer.Option(50052, help="RPC port to expose to the head"),
    threads: int = typer.Option(4, help="CPU threads for rpc-server"),
) -> None:
    """Run an RPC layer worker on this PC, bound to a single address the head can reach over Tailscale."""
    import ipaddress

    try:
        ipaddress.ip_address(bind)
    except ValueError:
        typer.echo(f"--bind must be an IP address, got '{bind}'", err=True)
        raise typer.Exit(1)
    if bind in ("0.0.0.0", "::"):
        typer.echo("Refusing to bind rpc-server to all interfaces; llama.cpp RPC has no authentication.", err=True)
        raise typer.Exit(1)

    rpc = RpcServerProcess(host=bind, port=port, threads=threads, enable_cache=True)
    if not rpc.start():
        typer.echo("rpc-server failed to start. Check that llama.cpp is installed.", err=True)
        raise typer.Exit(1)
    typer.echo(f"rpc-server listening on {bind}:{port}. Add this to the head's cluster.json. Press Ctrl+C to stop.")
    try:
        while rpc.is_running():
            time.sleep(2)
        typer.echo("rpc-server exited.", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        rpc.stop()


@app.command("status")
def status(config: Optional[Path] = _config_option()) -> None:
    """Show configured topology and whether each RPC worker port accepts connections."""
    import socket

    cfg = _load(config)
    typer.echo(f"Model: {cfg.model_path} (alias {cfg.model_alias})")
    typer.echo(f"Head:  {cfg.head_host}:{cfg.head_port}")
    if not cfg.workers:
        typer.echo("Workers: none configured (single-PC mode)")
    for w in cfg.workers:
        try:
            with socket.create_connection((w.host, w.port), timeout=2.0):
                state = "reachable"
        except OSError as exc:
            state = f"unreachable ({exc.__class__.__name__})"
        typer.echo(f"  - {w.name}: {w.address} {state}")


@keys_app.command("create")
def keys_create(name: str = typer.Argument(..., help="Label for the key, e.g. 'laya-agent'")) -> None:
    """Create an API key. The plaintext key is shown once and cannot be recovered."""
    token = ApiKeyStore(default_keys_path()).create(name)
    typer.echo(token)
    typer.echo("Store this key now. RACoN keeps only its SHA-256 digest.", err=True)


@keys_app.command("list")
def keys_list() -> None:
    """List API keys without their secrets."""
    entries = ApiKeyStore(default_keys_path()).list()
    if not entries:
        typer.echo("No API keys.")
        return
    for e in entries:
        state = "revoked" if e["revoked"] else "active"
        typer.echo(f"{e['id']}  {state:8}  {e['name']}")


@keys_app.command("revoke")
def keys_revoke(key_id: str = typer.Argument(..., help="Key id from 'keys list'")) -> None:
    """Revoke an API key so it stops working immediately."""
    if ApiKeyStore(default_keys_path()).revoke(key_id):
        typer.echo(f"Revoked {key_id}")
    else:
        typer.echo(f"No active key with id {key_id}")
        raise typer.Exit(1)


def _load(config: Optional[Path]) -> ClusterConfig:
    try:
        return load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(1)


def main() -> None:
    sys.exit(app())
