"""Entry point for `python -m racon`."""
import sys
import logging
from pathlib import Path

from racon.config import VERSION


def _icon_path() -> Path:
    """Locate the application icon in dev and frozen builds."""
    meipass = getattr(sys, "_MEIPASS", "")
    candidates = []
    for name in ("icon.png", "icon.ico"):
        candidates.extend([
            Path(meipass) / "apps" / "desktop" / name if meipass else None,
            Path(sys.executable).resolve().parent / "apps" / "desktop" / name,
            Path(__file__).resolve().parents[1] / "apps" / "desktop" / name,
            Path(meipass) / name if meipass else None,
        ])
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return Path("icon.png")


def _run_headless() -> int:
    """Join the cluster and serve RPC tasks without a GUI (Ubuntu Server, systemd)."""
    import threading
    import time

    from racon.agent.worker import RaconAgent

    agent = RaconAgent()
    delay = 5.0
    try:
        while True:
            ok, message = agent.auto_connect()
            print(message, file=sys.stdout if ok else sys.stderr, flush=True)
            if ok:
                break
            print(f"Retrying in {delay:.0f}s...", file=sys.stderr, flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    except KeyboardInterrupt:
        return 0

    agent.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
    return 0


def main():
    # Configure root logging before anything else
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("racon")
    logger.info("RACoN v%s starting", VERSION)

    if "--headless" in sys.argv:
        sys.exit(_run_headless())

    try:
        from PySide6.QtWidgets import QApplication
        from racon.app import RaconApp
    except ImportError as e:
        print(f"Missing dependency: {e}", file=sys.stderr)
        print("Install dependencies: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("RACoN")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("RACoN")

    window = RaconApp()

    if "--autostart" in sys.argv:
        # Started at desktop login (Windows Startup or Linux XDG autostart): keep the worker
        # running in the tray without a window.
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        icon = QIcon(str(_icon_path()))
        tray = QSystemTrayIcon(icon, app)
        menu = QMenu()
        menu.addAction("Open RACoN", lambda: window.show())
        menu.addAction("Quit", app.quit)
        tray.setContextMenu(menu)
        tray.setToolTip("RACoN — joining the network in the background")
        tray.show()
        app.setQuitOnLastWindowClosed(False)
        logger.info("RACoN started in autostart mode")
    else:
        window.show()
        logger.info("RACoN UI launched")

    exit_code = app.exec()
    logger.info("RACoN shutting down (code=%d)", exit_code)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
