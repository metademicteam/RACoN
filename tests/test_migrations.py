"""Database migration tests for the local engine schema."""
from __future__ import annotations

import sqlite3

from racon.storage import migrations
from racon.storage.database import Database


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_fresh_database_reaches_latest_version(tmp_path):
    db = Database(tmp_path / "fresh.db")
    db.initialize()
    conn = db.connect()

    assert migrations.current_version(conn) == migrations.LATEST_VERSION
    tables = _tables(conn)
    assert {"settings", "benchmark_runs", "candidate_evaluations"} <= tables
    assert {"model_installs", "inference_history", "engine_events", "provider_config"} <= tables
    db.close()


def test_migrations_are_idempotent(tmp_path):
    db = Database(tmp_path / "repeat.db")
    db.initialize()
    conn = db.connect()
    first = migrations.current_version(conn)

    applied = migrations.apply_migrations(conn)
    assert applied == first
    assert migrations.pending_migrations(conn) == []
    db.close()


def test_legacy_database_is_upgraded_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                               updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO settings (key, value) VALUES ('theme', 'dark');
        """
    )
    legacy.commit()
    legacy.close()

    db = Database(path)
    db.initialize()
    conn = db.connect()

    assert migrations.current_version(conn) == migrations.LATEST_VERSION
    row = conn.execute("SELECT value FROM settings WHERE key='theme'").fetchone()
    assert row[0] == "dark"
    assert "inference_history" in _tables(conn)
    db.close()


def test_inference_history_columns_support_metering(tmp_path):
    db = Database(tmp_path / "meter.db")
    db.initialize()
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO inference_history
            (id, request_id, model, runtime, prompt_tokens, completion_tokens,
             usage_reported, duration_ms, status)
        VALUES ('1','req','m','llama_cpp',10,5,1,120.0,'completed')
        """
    )
    conn.commit()
    row = conn.execute("SELECT prompt_tokens, completion_tokens, usage_reported FROM inference_history").fetchone()
    assert tuple(row) == (10, 5, 1)
    db.close()


def test_describe_lists_every_migration():
    described = migrations.describe()
    assert [d["version"] for d in described] == [m.version for m in migrations.MIGRATIONS]
    assert all(d["statements"] >= 1 for d in described)
