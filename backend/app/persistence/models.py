"""EvidenceFlow V1 的 SQLAlchemy 业务数据模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SchemaMigrationModel(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResearchTaskModel(Base):
    __tablename__ = "research_tasks"

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_version: Mapped[str] = mapped_column(String(32), nullable=False)
    plan: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_stats: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    run_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    run_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SearchRecordModel(Base):
    __tablename__ = "search_records"
    __table_args__ = (
        UniqueConstraint("task_id", "query", name="uq_search_task_query"),
    )

    search_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    result_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SourceRecordModel(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "normalized_url",
            "content_hash",
            name="uq_source_task_url_content",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    search_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_records.search_id"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    url_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extraction_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_type_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvidenceCardModel(Base):
    __tablename__ = "evidence_cards"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "source_id", "quote_hash", name="uq_evidence_task_source_quote"
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_records.source_id"), nullable=False, index=True
    )
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    support_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ReportModel(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "workflow_version",
            "revision_count",
            name="uq_report_task_workflow_revision",
        ),
    )

    report_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    workflow_version: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    comparison_table: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    risks_and_limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    unverified_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict[str, float | None]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ReportClaimModel(Base):
    __tablename__ = "report_claims"

    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("reports.report_id"), primary_key=True, nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_citation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    uncertainty: Mapped[str] = mapped_column(String(32), nullable=False)


class ReviewModel(Base):
    __tablename__ = "reviews"

    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    report_id: Mapped[str] = mapped_column(
        ForeignKey("reports.report_id"), nullable=False, index=True
    )
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unsupported_claims: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    overclaimed_claims: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    citation_issues: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    missing_dimensions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    revision_instructions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    claim_support_results: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    evidence_support_results: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    requirement_checks: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    report_section_results: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    gap_requests: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkflowEventModel(Base):
    __tablename__ = "workflow_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class CallAttemptModel(Base):
    __tablename__ = "call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "logical_call_id",
            "attempt_number",
            name="uq_call_attempt_logical_number",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    logical_call_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    step_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    call_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    budget_phase: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    operation_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arguments_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    usage: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    reserved_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved_completion_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResearchProgressModel(Base):
    __tablename__ = "research_progress"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    coverage: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    open_gaps: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    conflicts: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    tool_history_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    conversation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget_remaining: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    no_progress_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ResearchStepModel(Base):
    __tablename__ = "research_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "step_index", name="uq_research_step_index"),
    )

    step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_decisions.decision_id"), nullable=True, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arguments: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    operation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="committed"
    )
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    uncertainty_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ResearchDecisionModel(Base):
    __tablename__ = "research_decisions"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "conversation_ref",
            "turn_index",
            name="uq_research_decision_turn",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), nullable=False, index=True
    )
    conversation_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input_messages: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    assistant_message: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class QuestionEvidenceModel(Base):
    __tablename__ = "question_evidence"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("research_tasks.task_id"), primary_key=True
    )
    question_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_cards.evidence_id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )



