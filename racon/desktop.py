"""RACoN desktop shell.

Starts the local engine API on a loopback port and hosts the React UI in a native webview
window. One process, one port, one token: the SPA receives the token through its launch URL
and sends it back on every request.

Falls back to an Edge/Chromium ``--app`` window and then to the default browser when
pywebview is unavailable, so the application still starts on a machine without WebKitGTK.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The default-browser fallback is detached: webbrowser.open() returns immediately and we
# cannot wait for the tab. Keep the engine alive while the SPA polls it (every 3 seconds)
# and shut it down only after the tab stops talking to us.
UI_FIRST_REQUEST_TIMEOUT = 120.0
UI_IDLE_TIMEOUT = 120.0

# A running Chrome/Chromium accepts an --app window from a second launch and the launcher
# exits immediately. That fast exit is a hand-off, not the end of the UI session.
BROWSER_HANDOFF_SECONDS = 5.0

# Never route the loopback health probe through a system proxy: http_proxy is commonly
# exported on Linux desktops and would make a perfectly healthy local engine look dead.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def is_service_healthy(url: str, timeout: float = 1.5) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "RACoN-HealthCheck"})
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def find_dist_dir() -> Optional[Path]:
    """Locate the built React bundle across source, onedir and frozen layouts."""
    meipass = getattr(sys, "_MEIPASS", "")
    candidates = [
        Path(meipass) / "apps" / "desktop" / "dist" if meipass else None,
        Path(__file__).resolve().parents[1] / "apps" / "desktop" / "dist",
        Path(sys.executable).resolve().parent / "apps" / "desktop" / "dist",
        Path(sys.executable).resolve().parent / "_internal" / "apps" / "desktop" / "dist",
        Path.cwd() / "apps" / "desktop" / "dist",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


def resolve_token() -> str:
    """Resolve the local API token and export it so every component agrees."""
    from backend.racon.core.config import _default_auth_token

    token = _default_auth_token()
    # Pin both names: pydantic-settings reads AUTH_TOKEN from a .env file when only
    # RACON_AUTH_TOKEN is exported, which would make the engine reject the launch token
    # the desktop window received.
    os.environ["RACON_AUTH_TOKEN"] = token
    os.environ["AUTH_TOKEN"] = token
    return token


def _icon_path() -> Optional[Path]:
    meipass = getattr(sys, "_MEIPASS", "")
    for name in ("icon.png", "icon.ico"):
        for candidate in (
            Path(meipass) / "apps" / "desktop" / name if meipass else None,
            Path(__file__).resolve().parents[1] / "apps" / "desktop" / name,
            Path(sys.executable).resolve().parent / "apps" / "desktop" / name,
        ):
            if candidate and candidate.is_file():
                return candidate
    return None


class LocalEngineServer:
    """Runs the FastAPI application in a background thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port or find_free_port(host)
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._last_activity = 0.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _track_activity(self, app):
        """Record UI traffic so a detached browser tab can keep the engine alive."""

        async def tracked(scope, receive, send):
            if scope.get("type") == "http" and scope.get("path") != "/health":
                self._last_activity = time.monotonic()
            await app(scope, receive, send)

        return tracked

    def wait_for_ui(
        self,
        first_request_timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
    ) -> str:
        """Block while a detached browser UI is in use.

        Returns ``"idle"`` once the UI has been quiet for ``idle_timeout`` seconds, or
        ``"never-connected"`` when it never loaded at all.
        """
        if first_request_timeout is None:
            first_request_timeout = UI_FIRST_REQUEST_TIMEOUT
        if idle_timeout is None:
            idle_timeout = UI_IDLE_TIMEOUT

        started = time.monotonic()
        while self._last_activity == 0.0:
            if time.monotonic() - started >= first_request_timeout:
                logger.warning(
                    "No UI request arrived within %.0fs; closing the local engine",
                    first_request_timeout,
                )
                return "never-connected"
            time.sleep(0.2)

        while True:
            idle_for = time.monotonic() - self._last_activity
            if idle_for >= idle_timeout:
                logger.info("UI idle for %.0fs; closing the local engine", idle_for)
                return "idle"
            time.sleep(min(0.2, idle_timeout - idle_for))

    def start(self, wait_seconds: float = 20.0) -> None:
        import uvicorn

        from backend.racon.api.app import app

        self._last_activity = 0.0
        config = uvicorn.Config(
            self._track_activity(app),
            host=self.host,
            port=self.port,
            log_config=None,
            log_level="warning",
            access_log=False,
            use_colors=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="racon-engine", daemon=True)
        self._thread.start()

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if is_service_healthy(f"{self.base_url}/health"):
                logger.info("Local engine ready on %s", self.base_url)
                return
            if self._thread is not None and not self._thread.is_alive():
                raise RuntimeError("Local engine exited during start-up")
            time.sleep(0.15)
        raise RuntimeError(f"Local engine did not become healthy on {self.base_url}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self._server = None


def start_worker_agent(stop_event: threading.Event):
    """Enroll this machine and keep a provider agent alive in the background.

    The desktop app is also a network participant: without this the UI can submit jobs but
    the machine never appears in the admin inventory. ``RACON_AUTO_CONNECT=0`` disables it.
    """
    if os.environ.get("RACON_AUTO_CONNECT", "1").strip().lower() in ("0", "false", "no", "off"):
        logger.info("Background worker disabled by RACON_AUTO_CONNECT")
        return None
    try:
        from racon.agent.worker import RaconAgent
    except Exception:
        logger.exception("Worker agent unavailable")
        return None

    agent = RaconAgent()

    def _run() -> None:
        delay = 5.0
        while not stop_event.is_set():
            try:
                ok, message = agent.auto_connect()
            except Exception as exc:
                ok, message = False, f"{type(exc).__name__}: {exc}"
            logger.info("Worker: %s", message)
            if ok:
                try:
                    agent.start()
                except Exception:
                    logger.exception("Worker agent failed to start")
                return
            if stop_event.wait(delay):
                return
            delay = min(delay * 2, 60.0)

    threading.Thread(target=_run, name="racon-worker", daemon=True).start()
    return agent


def _browser_app_command(url: str) -> Optional[list[str]]:
    if os.name == "nt":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        ]
    else:
        candidates = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    executable = next((path for path in candidates if os.path.exists(path)), None)
    if not executable:
        for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                executable = found
                break
    if not executable:
        return None
    return [executable, f"--app={url}", "--window-size=1280,840"]


def open_window(url: str) -> bool:
    """Open the product window, degrading gracefully.

    Returns True when the UI lives outside this process: a detached browser tab, or an
    --app window handed to an already-running Chrome/Chromium. In both cases the caller
    must keep the engine running until the UI stops polling. Returns False when this call
    already blocked for the lifetime of the window.
    """
    try:
        import webview

        icon = _icon_path()
        webview.create_window(
            title="RACoN — Distributed AI Inference",
            url=url,
            width=1280,
            height=840,
            min_size=(1024, 700),
            background_color="#0b0f14",
            confirm_close=False,
        )
        webview.start(icon=str(icon) if icon else None, debug=False)
        return False
    except Exception as exc:
        logger.warning("pywebview unavailable (%s); falling back to an app window", exc)

    command = _browser_app_command(url)
    if command:
        process = subprocess.Popen(command)
        started = time.monotonic()
        process.wait()
        if time.monotonic() - started < BROWSER_HANDOFF_SECONDS:
            logger.info(
                "Browser handed the app window to a running instance; keeping the engine alive"
            )
            return True
        return False

    import webbrowser

    logger.info("Opening the RACoN UI in the default browser")
    if not webbrowser.open(url):
        raise RuntimeError(f"Could not open a browser for the RACoN UI at {url}")
    return True


def main(argv: Optional[list[str]] = None) -> int:
    from racon.logging_setup import configure_logging

    log_file = configure_logging()
    logger.info("Desktop shell starting (log: %s)", log_file)

    dist = find_dist_dir()
    if dist is None:
        logger.error(
            "The desktop UI bundle was not found. Build it with "
            "'cd apps/desktop && npm run build' or use 'python -m racon --lab'."
        )
        return 2

    try:
        token = resolve_token()
    except Exception:
        logger.exception("Could not resolve the local API token")
        return 1

    server = LocalEngineServer()
    try:
        server.start()
    except Exception:
        logger.exception("Could not start the local engine on port %s", server.port)
        return 1

    worker_stop = threading.Event()
    worker = start_worker_agent(worker_stop)

    url = f"{server.base_url}/?token={urllib.parse.quote(token)}"
    logger.info("Opening RACoN desktop UI at %s", server.base_url)
    try:
        detached = open_window(url)
        if detached:
            server.wait_for_ui()
    except Exception:
        logger.exception("Could not open the desktop window")
        return 1
    finally:
        worker_stop.set()
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                logger.debug("Worker agent stop failed", exc_info=True)
        server.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
