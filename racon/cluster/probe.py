"""Measure llama.cpp devices (head GPU plus RPC workers) with `llama-cli --list-devices`.

llama.cpp reports each RPC worker's total and free memory, so placement uses measured capacity
instead of values a worker reports about itself.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_RPC_LINE = re.compile(r"^\s*RPC\d+:\s*(\S+)\s*\(\s*([\d.]+)\s*MiB,\s*([\d.]+)\s*MiB free\)")
_LOCAL_LINE = re.compile(r"^\s*([A-Za-z]+\d+):\s*.*?\(\s*([\d.]+)\s*MiB,\s*([\d.]+)\s*MiB free\)")


@dataclass(frozen=True)
class ProbeResult:
    local_free_mb: Optional[float]
    rpc_free_mb: Dict[str, float] = field(default_factory=dict)


def parse_list_devices(output: str) -> ProbeResult:
    local_free: Optional[float] = None
    rpc: Dict[str, float] = {}
    for line in output.splitlines():
        m = _RPC_LINE.match(line)
        if m:
            rpc[m.group(1)] = float(m.group(3))
            continue
        m = _LOCAL_LINE.match(line)
        if m and local_free is None and not line.lstrip().startswith("RPC"):
            local_free = float(m.group(3))
    return ProbeResult(local_free_mb=local_free, rpc_free_mb=rpc)


def probe_devices(cli_bin: str, endpoints: List[str], timeout: float = 60.0) -> ProbeResult:
    """Ask llama.cpp which devices it can see. Unreachable endpoints are absent from the result."""
    cmd = [cli_bin]
    if endpoints:
        cmd.extend(["--rpc", ",".join(endpoints)])
    cmd.append("--list-devices")
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return parse_list_devices(proc.stdout + "\n" + proc.stderr)
