"""Coordinator Power Management & Sleep Prevention Guard.

Ensures the coordinator host remains awake and serving traffic (Section 5.5).
"""
from __future__ import annotations

import ctypes
import logging
import platform
import subprocess
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Windows API constants for SetThreadExecutionState
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


class CoordinatorPowerGuard:
    def __init__(self) -> None:
        self._is_preventing_sleep = False

    def check_power_plan(self) -> Dict[str, Any]:
        """Query Windows power configuration to check AC standby timeout."""
        if platform.system() != "Windows":
            return {"status": "unsupported", "is_ac_sleep_disabled": True, "message": "Non-Windows OS"}

        try:
            # Query powercfg for standby timeout on AC
            proc = subprocess.run(
                ["powercfg", "/q"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = proc.stdout
            standby_seconds = None
            for line in output.splitlines():
                if "Current AC Power Setting Index" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        try:
                            # Hex value like 0x00000000 (0 means never sleep)
                            val = int(parts[1].strip(), 16)
                            standby_seconds = val
                        except ValueError:
                            pass

            sleep_disabled = (standby_seconds == 0)
            msg = (
                "Coordinator host will not sleep on AC power (ideal)."
                if sleep_disabled
                else "Warning: Windows AC standby is enabled. Run 'powercfg /change standby-timeout-ac 0' to keep coordinator alive."
            )
            return {
                "status": "ok",
                "is_ac_sleep_disabled": sleep_disabled,
                "standby_timeout_seconds": standby_seconds,
                "message": msg,
            }
        except Exception as e:
            return {"status": "error", "is_ac_sleep_disabled": False, "message": str(e)}

    def prevent_sleep(self) -> bool:
        """Inform Windows OS that this background service requires the system to stay awake."""
        if platform.system() != "Windows":
            return False

        try:
            # Tell Windows kernel to keep system awake continuously
            res = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            )
            self._is_preventing_sleep = (res != 0)
            if self._is_preventing_sleep:
                logger.info("PowerGuard: System sleep prevented while coordinator is active")
            return self._is_preventing_sleep
        except Exception as e:
            logger.warning("PowerGuard failed to set execution state: %s", e)
            return False

    def allow_sleep(self) -> None:
        """Restore default Windows power management behavior."""
        if platform.system() == "Windows" and self._is_preventing_sleep:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                self._is_preventing_sleep = False
                logger.info("PowerGuard: Default system sleep behavior restored")
            except Exception:
                pass
