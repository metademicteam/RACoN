"""RACoN Desktop Application Executable Entry Point.

Launches the native desktop window, serves the UI, and coordinates the local engine.
"""
from __future__ import annotations

import functools
import http.server
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def is_port_listening(port: int) -> bool:
    """Check if a local TCP port is actively responding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_service_healthy(url: str) -> bool:
    """Check if an HTTP URL returns 200 OK."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RACoN-HealthCheck"})
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_dist_dir() -> Path | None:
    """Find the compiled apps/desktop/dist directory across bundle contexts."""
    meipass = getattr(sys, "_MEIPASS", "")
    candidates = [
        Path(meipass) / "apps" / "desktop" / "dist" if meipass else None,
        Path(__file__).resolve().parent / "apps" / "desktop" / "dist",
        Path(sys.executable).parent / "apps" / "desktop" / "dist",
        Path(sys.executable).parent / "_internal" / "apps" / "desktop" / "dist",
        Path.cwd() / "apps" / "desktop" / "dist",
        Path.cwd() / "dist",
    ]
    for c in candidates:
        if c and c.exists() and (c / "index.html").exists():
            return c
    return None


def run_ui_server(port: int = 1420):
    """Start local HTTP server on port 1420 to serve the desktop UI."""
    dist_dir = find_dist_dir()
    if not dist_dir:
        return

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dist_dir))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass


def run_backend(port: int = 8765):
    """Run FastAPI coordinator backend service on port 8765."""
    import uvicorn
    from backend.racon.api.app import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    # 1. Start backend coordinator on port 8765 if not already running healthy
    if not is_service_healthy("http://127.0.0.1:8765/health"):
        t_backend = threading.Thread(target=run_backend, daemon=True)
        t_backend.start()
        for _ in range(40):
            if is_service_healthy("http://127.0.0.1:8765/health"):
                break
            time.sleep(0.1)

    # 2. Start local UI server on port 1420 if not already running
    if not is_service_healthy("http://127.0.0.1:1420/"):
        t_ui = threading.Thread(target=lambda: run_ui_server(1420), daemon=True)
        t_ui.start()
        for _ in range(30):
            if is_service_healthy("http://127.0.0.1:1420/"):
                break
            time.sleep(0.1)

    # UI target URL: prefer 1420, fallback to 8765
    if is_service_healthy("http://127.0.0.1:1420/"):
        app_url = "http://127.0.0.1:1420/"
    elif is_service_healthy("http://127.0.0.1:8765/"):
        app_url = "http://127.0.0.1:8765/"
    else:
        app_url = "http://127.0.0.1:1420/"

    # Locate application icon
    meipass = getattr(sys, "_MEIPASS", "")
    icon_candidates = [
        Path(meipass) / "apps" / "desktop" / "icon.ico" if meipass else None,
        Path(__file__).resolve().parent / "apps" / "desktop" / "icon.ico",
        Path.cwd() / "apps" / "desktop" / "icon.ico",
        Path(sys.executable).parent / "apps" / "desktop" / "icon.ico",
        Path(sys.executable).parent / "_internal" / "apps" / "desktop" / "icon.ico",
    ]
    icon_path = next((p for p in icon_candidates if p and p.exists()), None)

    # 3. Try pywebview for native WebView2 desktop application window
    try:
        import webview

        window = webview.create_window(
            title="RACoN Desktop — Local Distributed AI Compute & Model Hub",
            url=app_url,
            width=1280,
            height=840,
            min_size=(1024, 700),
            background_color="#0d1117",
            confirm_close=False,
        )
        webview.start(icon=str(icon_path) if icon_path else None, debug=False)
        return
    except Exception as e:
        print(f"pywebview window init failed ({e}), falling back to native app mode...")

    # 4. Fallback to Windows native WebView2 / Edge App Mode
    # --app mode runs without browser UI, tabs, or address bar as an independent native window
    edge_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    edge_exe = next((p for p in edge_paths if os.path.exists(p)), None)

    if edge_exe:
        cmd = [
            edge_exe,
            f"--app={app_url}",
            "--window-size=1280,840",
            "--app-id=racon.desktop",
        ]
        proc = subprocess.Popen(cmd)
        proc.wait()
    else:
        import webbrowser
        webbrowser.open(app_url)


if __name__ == "__main__":
    main()
