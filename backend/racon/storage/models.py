"""SQLAlchemy 2.0 ORM models for RACoN Storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert model column attributes to dictionary."""
        return {
            col.name: getattr(self, col.name)
            for col in self.__table__.columns
        }


class Node(Base):
    """Cluster compute nodes and peer devices."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="offline", nullable=False)
    host: Mapped[str] = mapped_column(String(128), default="127.0.0.1", nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=8765, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    capabilities: Mapped[Optional[NodeCapability]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        uselist=False,
    )
    worker_sessions: Mapped[List[WorkerSession]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
    )


class NodeCapability(Base):
    """Hardware resources and available model runtimes for a node."""

    __tablename__ = "node_capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    cpu_name: Mapped[str] = mapped_column(String(128), nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_total_mb: Mapped[float] = mapped_column(Float, nullable=False)
    ram_available_mb: Mapped[float] = mapped_column(Float, nullable=False)
    gpu_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    gpu_vram_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    runtimes_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    # Relationships
    node: Mapped[Node] = relationship(back_populates="capabilities")


class WorkerSession(Base):
    """Active or historical worker execution processes on nodes."""

    __tablename__ = "worker_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    stopped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    node: Mapped[Node] = relationship(back_populates="worker_sessions")


class ModelRecord(Base):
    """AI models registered in the cluster."""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class InferenceTask(Base):
    """Inference job submitted for execution."""

    __tablename__ = "inference_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_node_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    transitions: Mapped[List[TaskTransition]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskTransition.timestamp.asc()",
    )
    assigned_node: Mapped[Optional[Node]] = relationship()


class TaskTransition(Base):
    """Lifecycle state machine transition records for an inference task."""

    __tablename__ = "task_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("inference_tasks.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    task: Mapped[InferenceTask] = relationship(back_populates="transitions")


class SchedulerDecision(Base):
    """Audit log of scheduler candidate evaluations and worker assignments."""

    __tablename__ = "scheduler_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    scenario_name: Mapped[str] = mapped_column(String(128), nullable=False)
    objective: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feasible_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chosen_worker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    deterministic_choice: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    laya_recommendation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    laya_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BenchmarkRun(Base):
    """Historical record of scheduler benchmark experiments and evaluations."""

    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    scenario: Mapped[str] = mapped_column(String(128), nullable=False)
    objective: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    winner: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Setting(Base):
    """Persistent key-value configuration overrides stored in SQLite."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Immutable system and security audit log events."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
