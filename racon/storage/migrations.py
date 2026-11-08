"""Versioned SQLite migrations for the local desktop database.

The migration set is embedded rather than loaded from ``.sql`` files so a frozen
PyInstaller build never depends on data files being collected.

Rules:
* migrations are append-only and idempotent;
* the applied version lives in ``PRAGMA user_version``;
* a migration that cannot apply raises and is never marked as done.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)

BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

LOCAL_ENGINE_SQL = """
CREATE TABLE IF NOT EXISTS model_installs (
    id TEXT PRIMARY KEY,
    runtime TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    family TEXT,
    context_length INTEGER,
    quantization TEXT,
    source TEXT NOT NULL DEFAULT 'downloaded',
    installed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inference_history (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    model TEXT NOT NULL,
    runtime TEXT NOT NULL,
    worker_id TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    usage_reported INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    ttft_ms REAL,
    tokens_per_second REAL,
    status TEXT NOT NULL DEFAULT 'completed',
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inference_history_created
    ON inference_history(created_at);

CREATE TABLE IF NOT EXISTS engine_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_engine_events_category
    ON engine_events(category, created_at);

CREATE TABLE IF NOT EXISTS provider_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", BASELINE_SQL),
    Migration(2, "local_engine", LOCAL_ENGINE_SQL),
)

LATEST_VERSION = max(m.version for m in MIGRATIONS)


def current_version(conn: sqlite3.Connection) -> int:
    """Return the applied schema version for this database."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def applied_migrations(conn: sqlite3.Connection) -> list[int]:
    return [m.version for m in MIGRATIONS if m.version <= current_version(conn)]


def pending_migrations(conn: sqlite3.Connection) -> list[Migration]:
    version = current_version(conn)
    return [m for m in MIGRATIONS if m.version > version]


def apply_migrations(conn: sqlite3.Connection, target: int | None = None) -> int:
    """Apply every pending migration and return the resulting version."""
    target_version = LATEST_VERSION if target is None else target
    applied: list[Migration] = []
    for migration in MIGRATIONS:
        if migration.version > target_version:
            break
        if migration.version <= current_version(conn):
            continue
        logger.info("Applying migration %d (%s)", migration.version, migration.name)
        try:
            conn.executescript(migration.sql)
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(migration)

    if applied:
        logger.info("Database schema now at v%d", current_version(conn))
    return current_version(conn)


def describe() -> list[dict[str, object]]:
    """Human-readable migration list used by diagnostics and docs generation."""
    return [
        {"version": m.version, "name": m.name, "statements": m.sql.count(";")}
        for m in MIGRATIONS
    ]
