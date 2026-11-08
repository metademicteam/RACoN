"""Shared pytest configuration.

Tests must never depend on a developer's local ``.env`` or on a token that only exists on
one machine, so an explicit test token is exported before any application module reads
its settings.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Deterministic credentials for the API tests. Set before backend.racon.config is imported.
# AUTH_TOKEN is set as well because pydantic-settings reads a developer's .env directly;
# environment variables take precedence over that file.
os.environ.setdefault("RACON_AUTH_TOKEN", "racon-local-dev-token")
os.environ.setdefault("AUTH_TOKEN", "racon-local-dev-token")
os.environ.setdefault("RACON_DEV_MODE", "true")
os.environ.setdefault("RACON_DB_PATH", str(ROOT / ".pytest_cache" / "racon_test.db"))
# Keep coordinator keys, invites and model mirrors out of the developer's real profile.
os.environ.setdefault("RACON_DATA_DIR", str(ROOT / ".pytest_cache" / "racon_data"))
os.environ.setdefault("RACON_LOG_DIR", str(ROOT / ".pytest_cache" / "racon_logs"))
os.environ.setdefault("RACON_JOIN_CODE", "RCN-TEST-JOIN")
os.environ.setdefault("RACON_AUTO_APPROVE_WORKERS", "true")


@pytest.fixture()
def isolated_db(tmp_path):
    """Run a test against its own SQLite file, then restore the shared test database."""
    from backend.racon.storage import db as db_module

    db_module.close_db()
    db_module.init_db(tmp_path / "isolated.db")
    try:
        yield db_module.get_database()
    finally:
        db_module.close_db()
        restore = os.environ.get("RACON_DB_PATH")
        db_module.init_db(Path(restore) if restore else None)
