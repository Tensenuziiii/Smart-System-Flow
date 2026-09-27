"""SQLAlchemy ORM models — exact schema from the spec."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ──────────────────────────────────────────────────────────────────────

class RepoStatus:
    QUEUED = "queued"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class SymbolType:
    FN = "fn"
    CLASS = "class"
    VAR = "var"
    ROUTE = "route"


class RelType:
    IMPORTS = "imports"
    CALLS = "calls"
    DEPENDS_ON = "depends_on"


class NodeType:
    FRONTEND = "frontend"
    API = "api"
    SERVICE = "service"
    DB = "db"
    EXTERNAL = "external"


class TraceStatus:
    COMPLETE = "complete"
    PARTIAL = "partial"
    ABSTAINED = "abstained"


class Difficulty:
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


# ── Models ─────────────────────────────────────────────────────────────────────

class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        Enum("queued", "analyzing", "ready", "failed", "stale", name="repo_status"),
        default="queued",
        nullable=False,
    )
    languages: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    pipeline_stage: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    partial_coverage: Mapped[bool] = mapped_column(default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    files: Mapped[List["File"]] = relationship("File", back_populates="repository", cascade="all, delete-orphan")
    arch_nodes: Mapped[List["ArchitectureNode"]] = relationship("ArchitectureNode", back_populates="repository", cascade="all, delete-orphan")
    traces: Mapped[List["Trace"]] = relationship("Trace", back_populates="repository", cascade="all, delete-orphan")
    starter_tasks: Mapped[List["StarterTask"]] = relationship("StarterTask", back_populates="repository", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "url", "commit", name="uq_tenant_repo_commit"),
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # sha256
    language: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # stored for Monaco viewer

    repository: Mapped["Repository"] = relationship("Repository", back_populates="files")
    symbols: Mapped[List["Symbol"]] = relationship("Symbol", back_populates="file", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("repository_id", "path", name="uq_repo_file_path"),)


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        String(10),
        Enum("fn", "class", "var", "route", name="symbol_type"),
        nullable=False,
    )
    line_start: Mapped[int] = mapped_column(Integer, default=0)
    line_end: Mapped[int] = mapped_column(Integer, default=0)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    file: Mapped["File"] = relationship("File", back_populates="symbols")


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)  # Symbol id
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)  # Symbol id
    type: Mapped[str] = mapped_column(
        String(15),
        Enum("imports", "calls", "depends_on", name="rel_type"),
        nullable=False,
    )
    evidence: Mapped[Optional[Dict]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class ArchitectureNode(Base):
    __tablename__ = "architecture_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        String(15),
        Enum("frontend", "api", "service", "db", "external", name="node_type"),
        nullable=False,
    )
    file_ids: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_: Mapped[Optional[Dict]] = mapped_column("metadata", JSON, default=dict)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="arch_nodes")


class ArchitectureEdge(Base):
    __tablename__ = "architecture_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("architecture_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rel_label: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence: Mapped[Optional[Dict]] = mapped_column(JSON, default=dict)


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(
        String(15),
        Enum("complete", "partial", "abstained", name="trace_status"),
        default="complete",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="traces")


class StarterTask(Base):
    __tablename__ = "starter_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    difficulty: Mapped[str] = mapped_column(
        String(15),
        Enum("beginner", "intermediate", "advanced", name="difficulty"),
        nullable=False,
    )
    files: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[Optional[List]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="starter_tasks")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(500), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CodeChunk(Base):
    """Stores symbol-boundary code chunks for RAG retrieval."""
    __tablename__ = "code_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)  # float[] serialized as JSON (pgvector in prod)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[Optional[Dict]] = mapped_column("metadata", JSON, default=dict)
