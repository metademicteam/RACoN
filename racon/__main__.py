"""Entry point for `python -m racon`."""
import sys
import logging

def main():
    # Configure root logging before anything else
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("racon")
    logger.info("RACoN Lab v0.1.0 starting")

    try:
        from PySide6.QtWidgets import QApplication
        from racon.app import RaconApp
    except ImportError as e:
        print(f"Missing dependency: {e}", file=sys.stderr)
        print("Install dependencies: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("RACoN Lab")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("RACoN")

    window = RaconApp()
    window.show()

    logger.info("RACoN Lab UI launched")
    exit_code = app.exec()
    logger.info("RACoN Lab shutting down (code=%d)", exit_code)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
