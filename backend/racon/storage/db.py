"""SQLite database connection, engine lifecycle, pragmas, and session management."""

from __future__ import annotations

import functools
import logging
from collections.abc import Generator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.racon.core.config import get_db_path
from backend.racon.storage.models import Base

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker[Session]] = None


class _SessionContextGenerator(Generator, AbstractContextManager):
    """Wrapper that simultaneously functions as a Python context manager and a FastAPI generator."""

    def __init__(self, gen: Generator[Session, None, None]) -> None:
        self._gen = gen

    def __enter__(self) -> Session:
        return next(self._gen)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Optional[bool]:
        try:
            if exc_type is not None:
                if isinstance(exc_val, BaseException):
                    self._gen.throw(exc_val)
                elif isinstance(exc_type, type) and issubclass(exc_type, BaseException):
                    self._gen.throw(exc_type(exc_val) if exc_val is not None else exc_type)
                else:
                    self._gen.throw(exc_type)
            else:
                try:
                    next(self._gen)
                except StopIteration:
                    pass
        except StopIteration:
            return True
        return False

    def __iter__(self) -> _SessionContextGenerator:
        return self

    def __next__(self) -> Session:
        return next(self._gen)

    def send(self, val: Any) -> Session:
        return self._gen.send(val)

    def throw(self, typ: Any, val: Any = None, tb: Any = None) -> Any:
        if val is None:
            if isinstance(typ, BaseException):
                return self._gen.throw(typ)
            return self._gen.throw(typ())
        if isinstance(val, BaseException):
            return self._gen.throw(val)
        return self._gen.throw(typ(val))

    def close(self) -> None:
        self._gen.close()


def _dual_session(func):
    """Decorator to allow a generator function to be used both with `with` and with FastAPI `Depends`."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> _SessionContextGenerator:
        return _SessionContextGenerator(func(*args, **kwargs))
    return wrapper


def init_db(db_path: Optional[Path] = None) -> Engine:
    """Initialize SQLite database engine, enable WAL & foreign keys, and create tables."""
    global _engine, _SessionFactory

    resolved_path = db_path or get_db_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite:///{resolved_path}"
    logger.info("Initializing SQLite database at: %s", resolved_path)

    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        future=True,
    )

    # SQLite Pragmas: WAL journal mode and foreign key enforcement
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=5000;")
        finally:
            cursor.close()

    # Create all tables defined in models.py
    Base.metadata.create_all(bind=engine)

    _engine = engine
    _SessionFactory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    logger.info("Database initialized successfully with WAL journal mode and foreign keys enabled.")
    return engine


def get_engine() -> Engine:
    """Return the active SQLAlchemy Engine, initializing default if not yet created."""
    global _engine
    if _engine is None:
        return init_db()
    return _engine


def close_db() -> None:
    """Close and dispose the active database engine."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionFactory = None
        logger.info("Database connection closed and engine disposed.")


@_dual_session
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional SQLAlchemy database session.

    Usable as:
        1. Context manager:
           with get_session() as session:
               session.add(...)

        2. FastAPI dependency:
           @app.get("/items")
           def read_items(session: Session = Depends(get_session)):
               ...
    """
    global _SessionFactory
    if _SessionFactory is None:
        init_db()
        assert _SessionFactory is not None

    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.debug("Database session rolled back due to: %s", exc)
        raise
    finally:
        session.close()


# Convenient aliases
session_scope = get_session
get_db = get_session


import json
from datetime import datetime
from backend.racon.storage.models import (
    Node,
    NodeCapability,
    InferenceTask,
    BenchmarkRun,
    SchedulerDecision,
    Setting,
    AuditEvent,
)


class Database:
    """Repository manager wrapping SQLAlchemy session scope."""

    def initialize(self):
        init_db()

    def close(self):
        close_db()

    # --- Nodes ---

    def upsert_node(self, node: dict[str, Any]) -> None:
        with session_scope() as session:
            existing = session.get(Node, node["id"])
            if not existing:
                existing = Node(
                    id=node["id"],
                    name=node.get("name", node["id"]),
                    is_local=node.get("is_local", False),
                    status=node.get("status", "available"),
                )
                session.add(existing)
            else:
                existing.name = node.get("name", existing.name)
                existing.is_local = node.get("is_local", existing.is_local)
                existing.status = node.get("status", existing.status)
                existing.last_heartbeat = datetime.now()

            # Capability
            caps = node.get("capabilities", {})
            if not existing.capabilities:
                existing.capabilities = NodeCapability(
                    node_id=node["id"],
                    cpu_name=caps.get("cpu_name", "CPU"),
                    cpu_cores=caps.get("cpu_logical_count", 4),
                    ram_total_mb=float(node.get("ram_total_mb", 16384.0)),
                    ram_available_mb=float(node.get("ram_available_mb", 8192.0)),
                    gpu_name=caps.get("gpu", {}).get("name") if isinstance(caps.get("gpu"), dict) else None,
                    runtimes_json=json.dumps(caps.get("runtimes", ["cpu"])),
                )
            else:
                if "ram_total_mb" in node:
                    existing.capabilities.ram_total_mb = float(node["ram_total_mb"])
                if "ram_available_mb" in node:
                    existing.capabilities.ram_available_mb = float(node["ram_available_mb"])

    def get_nodes(self) -> list[dict[str, Any]]:
        with session_scope() as session:
            nodes = session.query(Node).all()
            res = []
            for n in nodes:
                d = n.to_dict()
                if n.capabilities:
                    d["ram_total_mb"] = n.capabilities.ram_total_mb
                    d["ram_available_mb"] = n.capabilities.ram_available_mb
                    d["device_type"] = "cuda" if n.capabilities.gpu_name else "cpu"
                else:
                    d["ram_total_mb"] = 16384.0
                    d["ram_available_mb"] = 8192.0
                    d["device_type"] = "cpu"
                res.append(d)
            return res

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        with session_scope() as session:
            node = session.get(Node, node_id)
            if not node:
                return None
            d = node.to_dict()
            if node.capabilities:
                d["ram_total_mb"] = node.capabilities.ram_total_mb
                d["ram_available_mb"] = node.capabilities.ram_available_mb
                d["device_type"] = "cuda" if node.capabilities.gpu_name else "cpu"
            return d

    def update_node_status(self, node_id: str, status: str) -> bool:
        with session_scope() as session:
            node = session.get(Node, node_id)
            if node:
                node.status = status
                node.last_heartbeat = datetime.now()
                return True
            return False

    # --- Jobs (InferenceTask) ---

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        with session_scope() as session:
            node_id = job.get("worker_id")
            if node_id:
                node = session.get(Node, node_id)
                if not node:
                    node = Node(
                        id=node_id,
                        name=f"Worker {node_id}",
                        is_local=(node_id == "node-local"),
                        status="available",
                    )
                    session.add(node)
                    session.flush()

            task = InferenceTask(
                id=job["id"],
                model_name=job.get("model_name", "unknown"),
                status=job.get("status", "queued"),
                prompt=job.get("prompt", ""),
                response=job.get("result"),
                assigned_node_id=node_id,
                error=job.get("error"),
            )
            session.add(task)
        return job

    def get_jobs(self, limit: int = 50, offset: int = 0, status: Optional[str] = None) -> list[dict[str, Any]]:
        with session_scope() as session:
            q = session.query(InferenceTask)
            if status:
                q = q.filter(InferenceTask.status == status)
            q = q.order_by(InferenceTask.created_at.desc()).limit(limit).offset(offset)
            tasks = q.all()
            return [
                {
                    "id": t.id,
                    "name": f"Job-{t.id[:8]}",
                    "model_name": t.model_name,
                    "status": t.status,
                    "prompt": t.prompt,
                    "result": t.response,
                    "worker_id": t.assigned_node_id,
                    "error": t.error,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tasks
            ]

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with session_scope() as session:
            t = session.get(InferenceTask, job_id)
            if not t:
                return None
            return {
                "id": t.id,
                "name": f"Job-{t.id[:8]}",
                "model_name": t.model_name,
                "status": t.status,
                "prompt": t.prompt,
                "result": t.response,
                "worker_id": t.assigned_node_id,
                "error": t.error,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }

    def update_job(self, job_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        with session_scope() as session:
            t = session.get(InferenceTask, job_id)
            if not t:
                return None
            if "status" in updates:
                t.status = updates["status"]
            if "error" in updates:
                t.error = updates["error"]
            if "result" in updates:
                t.response = updates["result"]
            return self.get_job(job_id)

    # --- Benchmarks ---

    def save_benchmark_run(self, run: dict[str, Any], evaluations: list[dict[str, Any]]) -> None:
        with session_scope() as session:
            bm = BenchmarkRun(
                id=run["run_id"],
                scenario=run.get("scenario_name", "Default"),
                objective=run.get("objective", "balanced"),
                mode=run.get("mode", "deterministic"),
                winner=run.get("final_choice"),
                score=run.get("final_score"),
                duration_ms=run.get("decision_duration_ms", 0.0),
                success=run.get("success", True),
            )
            session.merge(bm)

            sd = SchedulerDecision(
                id=run["run_id"],
                scenario_name=run.get("scenario_name", "Default"),
                objective=run.get("objective", "balanced"),
                mode=run.get("mode", "deterministic"),
                candidate_count=run.get("candidate_count", len(evaluations)),
                feasible_count=run.get("feasible_count", 0),
                chosen_worker_id=run.get("final_choice"),
                deterministic_choice=run.get("deterministic_choice"),
                laya_recommendation=run.get("laya_recommendation"),
                laya_confidence=run.get("laya_confidence"),
                duration_ms=run.get("decision_duration_ms", 0.0),
                fallback_used=run.get("fallback_used", False),
                fallback_reason=run.get("fallback_reason"),
            )
            session.merge(sd)

    def get_benchmark_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with session_scope() as session:
            runs = session.query(BenchmarkRun).order_by(BenchmarkRun.timestamp.desc()).limit(limit).all()
            return [
                {
                    "run_id": r.id,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    "scenario_name": r.scenario,
                    "objective": r.objective,
                    "mode": r.mode,
                    "final_choice": r.winner,
                    "final_score": r.score,
                    "decision_duration_ms": r.duration_ms,
                    "success": r.success,
                }
                for r in runs
            ]

    # --- Settings ---

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with session_scope() as session:
            s = session.get(Setting, key)
            return s.value if s else default

    def set_setting(self, key: str, value: str) -> None:
        with session_scope() as session:
            s = session.get(Setting, key)
            if not s:
                s = Setting(key=key, value=str(value))
                session.add(s)
            else:
                s.value = str(value)

    def get_all_settings(self) -> dict[str, str]:
        with session_scope() as session:
            items = session.query(Setting).all()
            return {i.key: i.value for i in items}

    # --- Audit Logs ---

    def log_audit(self, event_type: str, message: str, level: str = "INFO", details: Optional[str] = None) -> None:
        with session_scope() as session:
            ev = AuditEvent(
                event_type=event_type,
                severity=level.upper(),
                details_json=json.dumps({"message": message, "details": details or ""}),
            )
            session.add(ev)

    def get_audit_logs(self, limit: int = 100, level: Optional[str] = None) -> list[dict[str, Any]]:
        with session_scope() as session:
            q = session.query(AuditEvent)
            if level:
                q = q.filter(AuditEvent.severity == level.upper())
            evs = q.order_by(AuditEvent.timestamp.desc()).limit(limit).all()
            res = []
            for e in evs:
                try:
                    payload = json.loads(e.details_json)
                    msg = payload.get("message", "")
                    det = payload.get("details", "")
                except Exception:
                    msg = e.details_json
                    det = ""
                res.append({
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "event_type": e.event_type,
                    "level": e.severity,
                    "message": msg,
                    "details": det,
                })
            return res


_GLOBAL_DB: Optional[Database] = None


def get_database(db_path: Optional[Path] = None) -> Database:
    """Retrieve singleton Database repository instance."""
    global _GLOBAL_DB
    if _GLOBAL_DB is None:
        _GLOBAL_DB = Database()
    return _GLOBAL_DB
