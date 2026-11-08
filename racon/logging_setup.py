"""Logging setup that also works in a windowed (console-less) frozen build."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from racon.config import get_log_dir


def _ensure_std_streams() -> bool:
    """Give windowed builds real stdout/stderr objects.

    PyInstaller ``console=False`` sets ``sys.stdout`` and ``sys.stderr`` to ``None``.
    Third-party code calls ``sys.stdout.isatty()`` (uvicorn's formatters do this during
    ``Config.__init__``), which raises ``AttributeError`` and aborts start-up. Installing
    a devnull stream keeps those libraries working while the file handler keeps the
    diagnostics. Returns whether stderr was usable before the substitution.
    """
    usable_stderr = sys.stderr is not None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    return usable_stderr


def configure_logging(level: int = logging.INFO) -> Path:
    """Attach a UTF-8 file handler and, when available, stderr.

    A PyInstaller ``console=False`` build has ``sys.stderr is None``. Logging only to
    stderr would silently discard every diagnostic, including desktop-shell startup
    failures. The file is always written under the per-user log directory.
    """
    root = logging.getLogger()
    if getattr(root, "_racon_configured", False):
        return Path(get_log_dir()) / "racon.log"

    usable_stderr = _ensure_std_streams()

    log_dir = Path(get_log_dir())
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "racon.log"

    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        pass
    if usable_stderr:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    setattr(logging.getLogger(), "_racon_configured", True)
    return log_file
