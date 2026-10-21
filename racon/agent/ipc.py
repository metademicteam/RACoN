"""Authenticated Loopback IPC Server for RACoN Agent.

Implements ADR-010:
Allows the RACoN Desktop UI to monitor, pause, and configure the background
worker agent over loopback with an ephemeral authentication token.
"""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, Optional
from platformdirs import user_data_dir
from fastapi import FastAPI, Header, HTTPException, status
import uvicorn

from racon.agent.worker import RaconAgent

logger = logging.getLogger(__name__)


def create_agent_ipc_app(agent: RaconAgent, auth_token: str) -> FastAPI:
    app = FastAPI(title="RACoN Agent Loopback IPC", docs_url=None, redoc_url=None)

    def _verify_auth(authorization: Optional[str] = Header(None)) -> None:
        if not authorization or authorization.replace("Bearer ", "").strip() != auth_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid IPC authorization token")

    @app.get("/agent/status")
    async def get_status(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        return agent.get_status()

    @app.post("/agent/enroll")
    async def enroll(payload: Dict[str, str], authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        invite_code = payload.get("invite_code", "")
        success = agent.enroll(invite_code)
        return {"success": success, "status": agent.get_status()}

    @app.post("/agent/start")
    async def start_worker(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        agent.start()
        return {"status": "running"}

    @app.post("/agent/stop")
    async def stop_worker(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        agent.stop()
        return {"status": "stopped"}

    @app.post("/agent/pause")
    async def pause_worker(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        agent.pause()
        return {"status": "paused"}

    @app.post("/agent/resume")
    async def resume_worker(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _verify_auth(authorization)
        agent.resume()
        return {"status": "resumed"}

    return app


class AgentIpcManager:
    def __init__(self, agent: RaconAgent, port: int = 8769) -> None:
        self.agent = agent
        self.port = port
        self.auth_token = secrets.token_urlsafe(32)
        self.app = create_agent_ipc_app(agent, self.auth_token)
        self._server: Optional[uvicorn.Server] = None

    def write_ipc_discovery(self) -> Path:
        """Write connection info to user AppData for RACoN Desktop UI discovery."""
        data_dir = Path(user_data_dir("RACoN", "RACoN"))
        data_dir.mkdir(parents=True, exist_ok=True)
        ipc_file = data_dir / ".agent_ipc.json"
        ipc_file.write_text(
            json.dumps({"port": self.port, "auth_token": self.auth_token}),
            encoding="utf-8",
        )
        return ipc_file

    def remove_ipc_discovery(self) -> None:
        try:
            data_dir = Path(user_data_dir("RACoN", "RACoN"))
            ipc_file = data_dir / ".agent_ipc.json"
            if ipc_file.exists():
                ipc_file.unlink()
        except Exception:
            pass
