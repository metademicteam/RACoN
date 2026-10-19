"""Repository for storing and retrieving benchmark data."""
from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
from typing import Optional

from racon.scheduler.models import SchedulerResult, CandidateEvaluation
from racon.storage.database import Database

logger = logging.getLogger(__name__)


class BenchmarkRepository:
    """Handles persistence of benchmark runs and settings."""

    def __init__(self, db: Database):
        self.db = db
        self.db.initialize()

    def save_run(self, result: SchedulerResult) -> bool:
        """Save a benchmark run and its candidate evaluations."""
        conn = self.db.connect()
        try:
            conn.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, timestamp, scenario_name, objective, mode,
                    candidate_count, feasible_count,
                    deterministic_choice, deterministic_score,
                    laya_recommendation, laya_confidence, laya_used_successfully,
                    final_choice, final_score,
                    decision_duration_ms, fallback_used, fallback_reason,
                    success, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id, result.timestamp, result.scenario_name,
                    result.objective.value, result.mode.value,
                    result.candidate_count, result.feasible_count,
                    result.deterministic_choice, result.deterministic_score,
                    result.laya_recommendation, result.laya_confidence,
                    1 if result.laya_used_successfully else 0,
                    result.final_choice, result.final_score,
                    result.decision_duration_ms,
                    1 if result.fallback_used else 0, result.fallback_reason,
                    1 if result.success else 0, result.failure_reason,
                ),
            )

            for ev in result.evaluations:
                conn.execute(
                    """
                    INSERT INTO candidate_evaluations (
                        run_id, worker_id, is_feasible, is_simulated,
                        score, rejection_reasons, score_components
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id, ev.worker_id,
                        1 if ev.is_feasible else 0,
                        1 if ev.is_simulated else 0,
                        ev.score,
                        json.dumps(ev.rejection_reasons) if ev.rejection_reasons else None,
                        json.dumps(ev.score_components) if ev.score_components else None,
                    ),
                )

            conn.commit()
            logger.info("Saved benchmark run %s", result.run_id)
            return True
        except sqlite3.Error as e:
            logger.error("Failed to save benchmark run: %s", e)
            conn.rollback()
            return False

    def get_all_runs(self) -> list[dict]:
        """Retrieve all benchmark runs, ordered by timestamp descending."""
        conn = self.db.connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY timestamp DESC"
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error("Failed to retrieve runs: %s", e)
            return []

    def get_run(self, run_id: str) -> Optional[dict]:
        """Retrieve a single run with its evaluations."""
        conn = self.db.connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,)
            )
            run = cursor.fetchone()
            if not run:
                return None

            result = dict(run)
            ev_cursor = conn.execute(
                "SELECT * FROM candidate_evaluations WHERE run_id = ? ORDER BY worker_id",
                (run_id,),
            )
            result["evaluations"] = [dict(row) for row in ev_cursor.fetchall()]
            return result
        except sqlite3.Error as e:
            logger.error("Failed to retrieve run %s: %s", run_id, e)
            return None

    def get_run_count(self) -> int:
        """Return the number of completed runs."""
        conn = self.db.connect()
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM benchmark_runs")
            return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    def clear_all_runs(self) -> bool:
        """Delete all benchmark data."""
        conn = self.db.connect()
        try:
            conn.execute("DELETE FROM candidate_evaluations")
            conn.execute("DELETE FROM benchmark_runs")
            conn.commit()
            logger.info("Cleared all benchmark runs")
            return True
        except sqlite3.Error as e:
            logger.error("Failed to clear runs: %s", e)
            conn.rollback()
            return False

    def export_runs_csv(self) -> str:
        """Export all runs as CSV string."""
        runs = self.get_all_runs()
        if not runs:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=runs[0].keys())
        writer.writeheader()
        writer.writerows(runs)
        return output.getvalue()

    def export_runs_json(self) -> str:
        """Export all runs as JSON string."""
        runs = self.get_all_runs()
        return json.dumps(runs, indent=2, default=str)

    # --- Settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        """Retrieve a setting value."""
        conn = self.db.connect()
        try:
            cursor = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            return row["value"] if row else default
        except sqlite3.Error:
            return default

    def set_setting(self, key: str, value: str) -> bool:
        """Store a setting value."""
        conn = self.db.connect()
        try:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value),
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("Failed to save setting %s: %s", key, e)
            return False
