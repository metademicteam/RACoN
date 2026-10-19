"""Application entry point — creates and configures the main window."""
from __future__ import annotations

import logging
from racon.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class RaconApp(MainWindow):
    """RACoN Lab application window.

    Thin wrapper around MainWindow for __main__.py to instantiate.
    """
    pass
