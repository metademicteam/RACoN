"""Entry point for ``python -m racon``.

Default: the product desktop shell (local engine + React UI in a native window).
``--lab`` starts the PySide6 laboratory window instead.
``--headless`` runs only the provider agent (used by the Linux systemd user unit).
"""
import logging
import sys
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


def _ensure_qt_plugin_path() -> None:
    """Point Qt at the bundled platform plugins when running from a frozen bundle.

    PyInstaller's onedir layout stores them under _internal/PySide6/Qt/plugins. Without an
    explicit path Qt can fail with 'Could not find the Qt platform plugin "xcb" in ""'.
    """
    if not getattr(sys, "frozen", False):
        return
    import os

    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    plugins = base / "PySide6" / "Qt" / "plugins"
    if (plugins / "platforms").is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(plugins)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")


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


def _run_qt(error: Exception | None = None) -> int:
    """Start the PySide6 laboratory window."""
    _ensure_qt_plugin_path()
    try:
        from PySide6.QtWidgets import QApplication
        from racon.app import RaconApp
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install dependencies: pip install -r requirements.txt", file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("RACoN")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("RACoN")

    window = RaconApp()

    if "--autostart" in sys.argv:
        # Started at desktop login: keep the worker running in the tray without a window.
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        icon = QIcon(str(_icon_path()))
        tray = QSystemTrayIcon(icon, app)
        menu = QMenu()
        menu.addAction("Open RACoN", window.show)
        menu.addAction("Quit", app.quit)
        tray.setContextMenu(menu)
        tray.setToolTip("RACoN — joining the network in the background")
        tray.show()
        app.setQuitOnLastWindowClosed(False)
        logging.getLogger("racon").info("RACoN started in autostart mode")
    else:
        window.show()
        logging.getLogger("racon").info("RACoN Qt laboratory window launched")

    return app.exec()


def main() -> None:
    from racon.logging_setup import configure_logging

    configure_logging()
    logger = logging.getLogger("racon")
    logger.info("RACoN v%s starting", VERSION)

    if "--headless" in sys.argv:
        sys.exit(_run_headless())

    if "--lab" in sys.argv or "--qt" in sys.argv:
        sys.exit(_run_qt())

    try:
        from racon.desktop import main as desktop_main
    except Exception as exc:
        logger.warning("Desktop shell unavailable (%s); starting the Qt laboratory UI", exc)
        sys.exit(_run_qt(exc))

    code = desktop_main()
    if code == 0:
        sys.exit(0)

    logger.warning("Desktop shell could not start (exit %s); starting the Qt laboratory UI", code)
    sys.exit(_run_qt(RuntimeError(f"desktop shell exit {code}")))


if __name__ == "__main__":
    main()
