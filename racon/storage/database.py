"""SQLite database initialization and connection management."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from racon.config import get_db_path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    objective TEXT NOT NULL,
    mode TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    feasible_count INTEGER NOT NULL DEFAULT 0,
    deterministic_choice TEXT,
    deterministic_score REAL,
    laya_recommendation TEXT,
    laya_confidence REAL,
    laya_used_successfully INTEGER NOT NULL DEFAULT 0,
    final_choice TEXT,
    final_score REAL,
    decision_duration_ms REAL NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS candidate_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    is_feasible INTEGER NOT NULL,
    is_simulated INTEGER NOT NULL DEFAULT 1,
    score REAL,
    rejection_reasons TEXT,
    score_components TEXT,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_timestamp
    ON benchmark_runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_run_id
    ON candidate_evaluations(run_id);
"""


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or get_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False

    @property
    def path(self) -> Path:
        return self._db_path

    def connect(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._conn is None:
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(str(self._db_path))
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                logger.info("Connected to database: %s", self._db_path)
            except sqlite3.Error as e:
                logger.error("Database connection failed: %s", e)
                raise
        return self._conn

    def initialize(self):
        """Create tables if they don't exist."""
        if self._initialized:
            return
        conn = self.connect()
        try:
            conn.executescript(CREATE_TABLES_SQL)
            # Check schema version
            cursor = conn.execute("SELECT MAX(version) FROM schema_info")
            row = cursor.fetchone()
            if row[0] is None:
                conn.execute(
                    "INSERT INTO schema_info (version) VALUES (?)",
                    (SCHEMA_VERSION,)
                )
                conn.commit()
            self._initialized = True
            logger.info("Database initialized (schema v%d)", SCHEMA_VERSION)
        except sqlite3.Error as e:
            logger.error("Database initialization failed: %s", e)
            raise

    def close(self):
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
            self._initialized = False
            logger.info("Database connection closed")
