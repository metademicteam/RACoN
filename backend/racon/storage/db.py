"""SQLite database connection, engine lifecycle, pragmas, and session management."""

from __future__ import annotations

import functools
import json
import logging
import os
from collections.abc import Generator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Engine, create_engine, event, inspect, text
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


def _normalise_database_url(url: str) -> str:
    """Accept the URL forms Docker/managed Postgres providers hand out."""
    value = url.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg2://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg2://" + value[len("postgresql://"):]
    return value


def resolve_database_url(db_path: Optional[Path] = None) -> tuple[str, dict[str, Any]]:
    """Return ``(url, engine_kwargs)`` for the configured database.

    ``DATABASE_URL`` (or ``RACON_DATABASE_URL``) selects PostgreSQL; otherwise RACoN uses
    the local SQLite file exactly as before. Tests set ``RACON_DB_PATH`` and therefore keep
    using SQLite.
    """
    explicit = os.environ.get("DATABASE_URL") or os.environ.get("RACON_DATABASE_URL")
    if explicit and explicit.strip():
        return _normalise_database_url(explicit), {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "pool_size": int(os.environ.get("RACON_DB_POOL_SIZE", "5")),
            "max_overflow": int(os.environ.get("RACON_DB_MAX_OVERFLOW", "10")),
        }

    resolved_path = db_path or get_db_path()
    resolved_path = Path(resolved_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{resolved_path}", {"connect_args": {"check_same_thread": False}}


_CAPABILITY_COLUMNS = {
    "os_name": "VARCHAR(128)",
    "rpc_endpoint": "VARCHAR(128)",
    "models_json": "TEXT",
    "features_json": "TEXT",
}


def _ensure_capability_columns(engine: Engine) -> None:
    """Add node_capabilities columns introduced after a deployment was created."""
    inspector = inspect(engine)
    if "node_capabilities" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("node_capabilities")}
    missing = {name: ddl for name, ddl in _CAPABILITY_COLUMNS.items() if name not in existing}
    if not missing:
        return
    with engine.begin() as connection:
        for name, ddl in missing.items():
            connection.execute(text(f"ALTER TABLE node_capabilities ADD COLUMN {name} {ddl}"))
            logger.info("Added node_capabilities.%s", name)


def _json_list(raw: Optional[str]) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def init_db(db_path: Optional[Path] = None) -> Engine:
    """Initialize the database engine and create every table.

    PostgreSQL is used when ``DATABASE_URL`` is set; otherwise SQLite with WAL and foreign
    keys. Table DDL is ``create_all`` (no migrations required for the new routing table).
    """
    global _engine, _SessionFactory

    db_url, engine_kwargs = resolve_database_url(db_path)
    is_sqlite = db_url.startswith("sqlite")

    if is_sqlite:
        target = db_url.split("///", 1)[-1]
        logger.info("Initializing SQLite database at: %s", target)
    else:
        logger.info("Initializing PostgreSQL database (%s)", db_url.split("@")[-1])

    engine = create_engine(db_url, future=True, **engine_kwargs)

    if is_sqlite:
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

    # Create all tables defined in models.py, then add columns introduced later.
    # ``create_all`` never alters an existing table.
    Base.metadata.create_all(bind=engine)
    _ensure_capability_columns(engine)

    _engine = engine
    _SessionFactory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    logger.info("Database initialized successfully (%s).", "SQLite WAL" if is_sqlite else "PostgreSQL")
    return engine


def database_info() -> dict[str, Any]:
    """Safe description of the active database. Never includes credentials."""
    if _engine is None:
        return {"backend": "uninitialized", "database": "", "host": "", "driver": ""}
    url = _engine.url
    return {
        "backend": url.get_backend_name(),
        "database": url.database or "",
        "host": url.host or "",
        "driver": url.drivername,
    }


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
from datetime import datetime, timezone
from backend.racon.storage.models import (
    Node,
    NodeCapability,
    InferenceTask,
    BenchmarkRun,
    SchedulerDecision,
    Setting,
    AuditEvent,
    Job,
    JobTask,
    JobEvent,
    RoutingEvent,
)

TERMINAL_JOB_STATES = ("completed", "partial", "failed", "cancelled")
TERMINAL_TASK_STATES = ("completed", "failed", "cancelled")


def utc_now() -> datetime:
    """Naive UTC timestamp, matching SQLite's CURRENT_TIMESTAMP used for created_at."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
                    host=node.get("host", "127.0.0.1"),
                    port=int(node.get("port", 8765) or 8765),
                )
                session.add(existing)
                session.flush()
            else:
                existing.name = node.get("name", existing.name)
                existing.is_local = node.get("is_local", existing.is_local)
                existing.status = node.get("status", existing.status)
                if node.get("host"):
                    existing.host = node["host"]
                existing.last_heartbeat = utc_now()

            caps = node.get("capabilities") or {}
            gpu = caps.get("gpu") if isinstance(caps.get("gpu"), dict) else {}
            cpu_name = caps.get("cpu_name") or node.get("cpu_name")
            cpu_cores = (
                caps.get("cpu_logical_count")
                or caps.get("cpu_cores")
                or node.get("cpu_cores")
            )
            gpu_name = caps.get("gpu_name") or gpu.get("name") or node.get("gpu_name")
            gpu_vram = (
                caps.get("gpu_vram_mb")
                if caps.get("gpu_vram_mb") is not None
                else gpu.get("vram_total_mb", node.get("gpu_vram_mb"))
            )
            runtimes = caps.get("runtimes") or node.get("runtimes")
            os_name = caps.get("os") or caps.get("os_name") or node.get("os")
            rpc_endpoint = caps.get("rpc_endpoint") or node.get("rpc_endpoint")
            models = caps.get("models") or node.get("models")
            features = caps.get("features") or node.get("features")
            ram_total = node.get("ram_total_mb", caps.get("ram_total_mb"))
            ram_available = node.get("ram_available_mb", caps.get("ram_available_mb"))

            if not existing.capabilities:
                existing.capabilities = NodeCapability(
                    node_id=node["id"],
                    cpu_name=cpu_name or "CPU",
                    cpu_cores=int(cpu_cores or 4),
                    ram_total_mb=float(ram_total or 16384.0),
                    ram_available_mb=float(ram_available or 8192.0),
                    gpu_name=gpu_name,
                    gpu_vram_mb=float(gpu_vram) if gpu_vram is not None else None,
                    runtimes_json=json.dumps(list(runtimes or ["cpu"])),
                    os_name=os_name,
                    rpc_endpoint=rpc_endpoint,
                    models_json=json.dumps(list(models or [])),
                    features_json=json.dumps(list(features or [])),
                )
            else:
                capability = existing.capabilities
                if cpu_name:
                    capability.cpu_name = str(cpu_name)[:128]
                if cpu_cores:
                    capability.cpu_cores = int(cpu_cores)
                if ram_total is not None:
                    capability.ram_total_mb = float(ram_total)
                if ram_available is not None:
                    capability.ram_available_mb = float(ram_available)
                if gpu_name is not None:
                    capability.gpu_name = str(gpu_name)[:128]
                if gpu_vram is not None:
                    capability.gpu_vram_mb = float(gpu_vram)
                if runtimes:
                    capability.runtimes_json = json.dumps(list(runtimes))
                if os_name:
                    capability.os_name = str(os_name)[:128]
                if rpc_endpoint:
                    capability.rpc_endpoint = str(rpc_endpoint)[:128]
                if models is not None:
                    capability.models_json = json.dumps(list(models))
                if features is not None:
                    capability.features_json = json.dumps(list(features))

    @staticmethod
    def _node_dict(node: Node) -> dict[str, Any]:
        data = node.to_dict()
        capability = node.capabilities
        if capability:
            data["ram_total_mb"] = capability.ram_total_mb
            data["ram_available_mb"] = capability.ram_available_mb
            data["device_type"] = "cuda" if capability.gpu_name else "cpu"
            data["capabilities"] = {
                "cpu_name": capability.cpu_name,
                "cpu_logical_count": capability.cpu_cores,
                "cpu_cores": capability.cpu_cores,
                "ram_total_mb": capability.ram_total_mb,
                "ram_available_mb": capability.ram_available_mb,
                "gpu_name": capability.gpu_name,
                "gpu_vram_mb": capability.gpu_vram_mb,
                "gpu": {
                    "name": capability.gpu_name,
                    "vram_total_mb": capability.gpu_vram_mb,
                },
                "runtimes": _json_list(capability.runtimes_json),
                "os": capability.os_name,
                "rpc_endpoint": capability.rpc_endpoint,
                "models": _json_list(capability.models_json),
                "features": _json_list(capability.features_json),
            }
        else:
            data["ram_total_mb"] = 16384.0
            data["ram_available_mb"] = 8192.0
            data["device_type"] = "cpu"
            data["capabilities"] = {}
        return data

    def get_nodes(self) -> list[dict[str, Any]]:
        with session_scope() as session:
            return [self._node_dict(node) for node in session.query(Node).all()]

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        with session_scope() as session:
            node = session.get(Node, node_id)
            return self._node_dict(node) if node else None

    def update_node_status(self, node_id: str, status: str) -> bool:
        with session_scope() as session:
            node = session.get(Node, node_id)
            if node:
                node.status = status
                node.last_heartbeat = utc_now()
                return True
            return False

    def update_node_heartbeat(
        self,
        node_id: str,
        *,
        ram_available: Optional[float] = None,
        models: Optional[list[str]] = None,
    ) -> bool:
        """Record a local telemetry sample for a node.

        Only values that were actually measured are written. ``models=None`` keeps the
        stored inventory; an empty list clears it, matching a worker that reports no
        installed models. Workload is not persisted because the schema has no column for
        it; the orchestration selector reads live CPU load directly instead of inventing
        a stored figure.
        """
        with session_scope() as session:
            node = session.get(Node, node_id)
            if not node:
                return False
            node.last_heartbeat = utc_now()
            if ram_available is not None and node.capabilities:
                node.capabilities.ram_available_mb = float(ram_available)
            if models is not None and node.capabilities:
                node.capabilities.models_json = json.dumps(list(models))
            return True

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

    # --- Orchestration jobs ---

    JOB_FIELDS = {
        "name",
        "status",
        "requested_mode",
        "execution_mode",
        "planner_mode",
        "plan_json",
        "result_text",
        "error",
        "started_at",
        "completed_at",
        "duration_ms",
        "max_tokens",
        "max_parallel_tasks",
        "tasks_total",
        "tasks_completed",
        "tasks_failed",
    }

    TASK_FIELDS = {
        "title",
        "instruction",
        "depends_on_json",
        "state",
        "worker_id",
        "attempts",
        "output",
        "error",
        "started_at",
        "completed_at",
        "duration_ms",
        "max_tokens",
        "timeout_s",
        "retries",
    }

    def create_orchestration_job(self, job: dict[str, Any]) -> None:
        with session_scope() as session:
            record = Job(
                id=job["id"],
                name=job.get("name", ""),
                prompt=job["prompt"],
                model_name=job["model_name"],
                status=job.get("status", "queued"),
                requested_mode=job.get("requested_mode", "auto"),
                execution_mode=job.get("execution_mode"),
                planner_mode=job.get("planner_mode"),
                plan_json=job.get("plan_json"),
                result_text=job.get("result_text"),
                error=job.get("error"),
                max_tokens=int(job.get("max_tokens", 512)),
                max_parallel_tasks=int(job.get("max_parallel_tasks", 3)),
                tasks_total=int(job.get("tasks_total", 0)),
                tasks_completed=int(job.get("tasks_completed", 0)),
                tasks_failed=int(job.get("tasks_failed", 0)),
            )
            session.add(record)

    def update_orchestration_job(self, job_id: str, updates: dict[str, Any]) -> bool:
        cleaned = {key: value for key, value in updates.items() if key in self.JOB_FIELDS}
        if not cleaned:
            return False
        with session_scope() as session:
            record = session.get(Job, job_id)
            if not record:
                return False
            for key, value in cleaned.items():
                setattr(record, key, value)
            return True

    def get_orchestration_job(self, job_id: str, include_tasks: bool = True) -> Optional[dict[str, Any]]:
        with session_scope() as session:
            record = session.get(Job, job_id)
            if not record:
                return None
            return self._job_to_dict(record, include_tasks=include_tasks)

    def list_orchestration_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with session_scope() as session:
            query = session.query(Job)
            if status:
                query = query.filter(Job.status == status)
            records = (
                query.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()
            )
            return [self._job_to_dict(record, include_tasks=False) for record in records]

    def upsert_orchestration_task(self, job_id: str, task: dict[str, Any]) -> None:
        with session_scope() as session:
            record = session.get(JobTask, (job_id, task["task_id"]))
            if record is None:
                record = JobTask(
                    job_id=job_id,
                    task_id=task["task_id"],
                    title=task.get("title", ""),
                    instruction=task.get("instruction", ""),
                    depends_on_json=task.get("depends_on_json", "[]"),
                    state=task.get("state", "pending"),
                    retries=int(task.get("retries", 1)),
                    max_tokens=int(task.get("max_tokens", 512)),
                    timeout_s=float(task.get("timeout_s", 180.0)),
                )
                session.add(record)
            for key, value in task.items():
                if key in self.TASK_FIELDS:
                    setattr(record, key, value)

    def list_orchestration_tasks(self, job_id: str) -> list[dict[str, Any]]:
        with session_scope() as session:
            records = (
                session.query(JobTask)
                .filter(JobTask.job_id == job_id)
                .order_by(JobTask.task_id.asc())
                .all()
            )
            return [self._task_to_dict(record) for record in records]

    def add_job_event(
        self,
        job_id: str,
        event_type: str,
        message: str = "",
        *,
        task_id: Optional[str] = None,
        level: str = "info",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        with session_scope() as session:
            session.add(
                JobEvent(
                    job_id=job_id,
                    task_id=task_id,
                    event_type=event_type,
                    level=level,
                    message=message[:4000],
                    detail_json=json.dumps(detail or {}),
                )
            )

    def list_job_events(
        self,
        job_id: str,
        limit: int = 500,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        with session_scope() as session:
            records = (
                session.query(JobEvent)
                .filter(JobEvent.job_id == job_id, JobEvent.id > after_id)
                .order_by(JobEvent.id.asc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": record.id,
                    "job_id": record.job_id,
                    "task_id": record.task_id,
                    "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                    "event_type": record.event_type,
                    "level": record.level,
                    "message": record.message,
                    "detail": self._safe_json(record.detail_json),
                }
                for record in records
            ]

    # --- Routing audit (node -> node request flow) ---

    def log_routing_event(
        self,
        event_type: str,
        *,
        source: str = "server",
        target: str = "server",
        job_id: Optional[str] = None,
        task_id: Optional[str] = None,
        state: Optional[str] = None,
        message: str = "",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append one routing event. Logging must never break the request path."""
        try:
            with session_scope() as session:
                session.add(
                    RoutingEvent(
                        event_type=event_type[:48],
                        source_node=(source or "server")[:128],
                        target_node=(target or "server")[:128],
                        job_id=job_id,
                        task_id=task_id,
                        state=(state or "")[:32] or None,
                        message=(message or "")[:4000],
                        detail_json=json.dumps(detail or {}),
                    )
                )
        except Exception as exc:
            logger.warning("Could not record routing event %s: %s", event_type, exc)

    def list_routing_events(
        self,
        limit: int = 200,
        *,
        after_id: int = 0,
        event_type: Optional[str] = None,
        node: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with session_scope() as session:
            query = session.query(RoutingEvent)
            if after_id:
                query = query.filter(RoutingEvent.id > after_id)
            if event_type:
                query = query.filter(RoutingEvent.event_type == event_type)
            if node:
                query = query.filter(
                    (RoutingEvent.source_node == node) | (RoutingEvent.target_node == node)
                )
            records = query.order_by(RoutingEvent.id.desc()).limit(limit).all()
            records.reverse()
            return [
                {
                    "id": record.id,
                    "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                    "event_type": record.event_type,
                    "source": record.source_node,
                    "target": record.target_node,
                    "job_id": record.job_id,
                    "task_id": record.task_id,
                    "state": record.state,
                    "message": record.message,
                    "detail": self._safe_json(record.detail_json),
                }
                for record in records
            ]

    def prune_routing_events(self, keep: int = 10000) -> int:
        """Keep the routing log bounded so a long-running server cannot grow forever."""
        with session_scope() as session:
            total = session.query(RoutingEvent).count()
            excess = total - max(0, int(keep))
            if excess <= 0:
                return 0
            ids = [
                row[0]
                for row in session.query(RoutingEvent.id)
                .order_by(RoutingEvent.id.asc())
                .limit(excess)
                .all()
            ]
            if ids:
                session.query(RoutingEvent).filter(RoutingEvent.id.in_(ids)).delete(
                    synchronize_session=False
                )
            return len(ids)

    def mark_interrupted_jobs(self) -> int:
        """Fail jobs/tasks left non-terminal by a process exit. Never silently resume."""
        now = utc_now()
        reason = "Interrupted by process restart; the previous execution cannot be resumed safely."
        count = 0
        with session_scope() as session:
            jobs = (
                session.query(Job)
                .filter(~Job.status.in_(TERMINAL_JOB_STATES))
                .all()
            )
            for job in jobs:
                job.status = "failed"
                job.error = reason
                job.completed_at = now
                count += 1
                tasks = (
                    session.query(JobTask)
                    .filter(
                        JobTask.job_id == job.id,
                        ~JobTask.state.in_(TERMINAL_TASK_STATES),
                    )
                    .all()
                )
                for task in tasks:
                    task.state = "failed"
                    task.error = reason
                    task.completed_at = now
                session.add(
                    JobEvent(
                        job_id=job.id,
                        event_type="job_interrupted",
                        level="error",
                        message=reason,
                    )
                )
        return count

    @staticmethod
    def _safe_json(value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    @staticmethod
    def _task_to_dict(record: JobTask) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "job_id": record.job_id,
            "title": record.title,
            "instruction": record.instruction,
            "depends_on": Database._safe_json(record.depends_on_json) or [],
            "state": record.state,
            "worker_id": record.worker_id,
            "attempts": record.attempts,
            "output": record.output,
            "error": record.error,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            "duration_ms": record.duration_ms,
            "max_tokens": record.max_tokens,
            "timeout_s": record.timeout_s,
            "retries": record.retries,
        }

    @staticmethod
    def _job_to_dict(record: Job, include_tasks: bool = True) -> dict[str, Any]:
        payload = {
            "id": record.id,
            "name": record.name,
            "prompt": record.prompt,
            "model_name": record.model_name,
            "status": record.status,
            "requested_mode": record.requested_mode,
            "execution_mode": record.execution_mode,
            "planner_mode": record.planner_mode,
            "plan": Database._safe_json(record.plan_json),
            "result": record.result_text,
            "error": record.error,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            "duration_ms": record.duration_ms,
            "max_tokens": record.max_tokens,
            "max_parallel_tasks": record.max_parallel_tasks,
            "tasks_total": record.tasks_total,
            "tasks_completed": record.tasks_completed,
            "tasks_failed": record.tasks_failed,
        }
        if include_tasks:
            payload["tasks"] = [Database._task_to_dict(task) for task in record.tasks]
        return payload


_GLOBAL_DB: Optional[Database] = None


def get_database(db_path: Optional[Path] = None) -> Database:
    """Retrieve singleton Database repository instance."""
    global _GLOBAL_DB
    if _GLOBAL_DB is None:
        _GLOBAL_DB = Database()
    return _GLOBAL_DB
