"""Public Ingress Port & Tailscale Funnel Adapter.

Implements ADR-008 (Public Ingress Port Abstraction):
- Direct execution of `tailscale.exe funnel` using structured argument lists.
- Idempotent enable, disable, and status queries.
- Safe external reachability probe.
- One-click kill switch support.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel, Field

import httpx

logger = logging.getLogger(__name__)


class IngressStatus(BaseModel):
    is_enabled: bool = False
    public_url: Optional[str] = None
    public_port: int = 443
    local_port: int = 8765
    state: str = "disabled"  # "active", "disabled", "error", "not_installed", "unauthorized"
    error_message: Optional[str] = None
    last_checked: float = Field(default_factory=time.time)


class ReachabilityReport(BaseModel):
    is_reachable: bool = False
    http_status: Optional[int] = None
    latency_ms: float = 0.0
    error_message: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class PublicIngress(ABC):
    """Abstract port for public ingress management."""

    @abstractmethod
    def enable(self, local_port: int, public_port: int = 443) -> IngressStatus:
        ...

    @abstractmethod
    def disable(self) -> IngressStatus:
        ...

    @abstractmethod
    def get_status(self) -> IngressStatus:
        ...

    @abstractmethod
    def probe_reachability(self, timeout_s: float = 5.0) -> ReachabilityReport:
        ...


class TailscaleFunnelIngress(PublicIngress):
    """Tailscale Funnel adapter calling the tailscale CLI with argument lists."""

    def __init__(self, cli_path: Optional[str] = None) -> None:
        self._cli_path = cli_path or self._find_tailscale_cli()
        self._status = IngressStatus()
        self._lock = threading.Lock()

    def _find_tailscale_cli(self) -> Optional[str]:
        # Check standard Windows paths and PATH environment
        found = shutil.which("tailscale.exe") or shutil.which("tailscale")
        if found:
            return found

        standard_paths = [
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        ]
        for p in standard_paths:
            if shutil.os.path.exists(p):
                return p
        return None

    def get_status(self) -> IngressStatus:
        with self._lock:
            if not self._cli_path:
                self._status.state = "not_installed"
                self._status.error_message = (
                    "Tailscale CLI not found. Install Tailscale from https://tailscale.com/download"
                )
                self._status.is_enabled = False
                return self._status.model_copy()

            try:
                # Run `tailscale.exe funnel status` safely with arg list
                proc = subprocess.run(
                    [self._cli_path, "funnel", "status"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                output = proc.stdout.strip()
                if "Funnel is open" in output or "https://" in output:
                    self._status.is_enabled = True
                    self._status.state = "active"
                    # Parse public URL if present
                    for line in output.splitlines():
                        if "https://" in line:
                            parts = line.strip().split()
                            for part in parts:
                                if part.startswith("https://"):
                                    self._status.public_url = part.rstrip("/")
                    self._status.error_message = None
                else:
                    self._status.is_enabled = False
                    self._status.state = "disabled"
                    self._status.public_url = None
                    self._status.error_message = None
            except subprocess.TimeoutExpired:
                self._status.state = "error"
                self._status.error_message = "Tailscale CLI query timed out."
            except Exception as e:
                self._status.state = "error"
                self._status.error_message = str(e)

            self._status.last_checked = time.time()
            return self._status.model_copy()

    def enable(self, local_port: int, public_port: int = 443) -> IngressStatus:
        with self._lock:
            self._status.local_port = local_port
            self._status.public_port = public_port

            if not self._cli_path:
                self._status.state = "not_installed"
                self._status.error_message = "Tailscale CLI not installed on coordinator host."
                return self._status.model_copy()

            try:
                # `tailscale funnel 443 on` or `tailscale serve --funnel ...`
                cmd = [
                    self._cli_path,
                    "funnel",
                    f"{public_port}",
                    "on",
                    f"127.0.0.1:{local_port}",
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    self._status.is_enabled = True
                    self._status.state = "active"
                    self._status.error_message = None
                    logger.info("Tailscale Funnel enabled on port %d -> %d", public_port, local_port)
                else:
                    self._status.state = "error"
                    self._status.error_message = proc.stderr.strip() or proc.stdout.strip()
                    logger.warning("Tailscale Funnel enable failed: %s", self._status.error_message)
            except Exception as e:
                self._status.state = "error"
                self._status.error_message = str(e)

            return self.get_status()

    def disable(self) -> IngressStatus:
        """One-click kill switch to stop all public access (ADR-008)."""
        with self._lock:
            if not self._cli_path:
                self._status.is_enabled = False
                return self._status.model_copy()

            try:
                cmd = [self._cli_path, "funnel", f"{self._status.public_port}", "off"]
                subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                self._status.is_enabled = False
                self._status.state = "disabled"
                self._status.public_url = None
                self._status.error_message = None
                logger.info("Tailscale Funnel disabled (kill switch triggered)")
            except Exception as e:
                self._status.error_message = str(e)

            return self.get_status()

    def probe_reachability(self, timeout_s: float = 5.0) -> ReachabilityReport:
        status = self.get_status()
        if not status.is_enabled or not status.public_url:
            return ReachabilityReport(
                is_reachable=False,
                error_message="Funnel ingress is not active or public URL is unavailable.",
            )

        probe_url = f"{status.public_url}/health"
        start = time.monotonic()
        try:
            with httpx.Client(timeout=timeout_s, verify=True) as client:
                resp = client.get(probe_url)
                latency = (time.monotonic() - start) * 1000
                is_ok = resp.status_code == 200
                return ReachabilityReport(
                    is_reachable=is_ok,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    error_message=None if is_ok else f"HTTP {resp.status_code}",
                )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return ReachabilityReport(
                is_reachable=False,
                latency_ms=latency,
                error_message=str(e),
            )
