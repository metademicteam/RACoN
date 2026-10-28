"""Entry point for `python -m racon`."""
import sys
import logging
from pathlib import Path


def _icon_path() -> Path:
    """Locate the application icon in dev and frozen builds."""
    meipass = getattr(sys, "_MEIPASS", "")
    candidates = [
        Path(meipass) / "apps" / "desktop" / "icon.ico" if meipass else None,
        Path(sys.executable).resolve().parent / "apps" / "desktop" / "icon.ico",
        Path(__file__).resolve().parents[1] / "apps" / "desktop" / "icon.ico",
        Path(meipass) / "icon.ico" if meipass else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return Path("icon.ico")


def main():
    # Configure root logging before anything else
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("racon")
    logger.info("RACoN v0.1.0 starting")

    try:
        from PySide6.QtWidgets import QApplication
        from racon.app import RaconApp
    except ImportError as e:
        print(f"Missing dependency: {e}", file=sys.stderr)
        print("Install dependencies: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("RACoN")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("RACoN")

    window = RaconApp()

    if "--autostart" in sys.argv:
        # Started at Windows logon: keep the worker running in the tray without a window.
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
