"""Persisted research progress and deterministic tool observations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr, SupportType


class CoverageStatus(StrEnum):
    UNANSWERED = "unanswered"
    PARTIAL = "partial"
    SUPPORTED = "supported"
    CONFLICTING = "conflicting"


class ProgressQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    text: NonEmptyStr
    requirement_ids: list[Identifier] = Field(default_factory=list)


class QuestionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    status: CoverageStatus = CoverageStatus.UNANSWERED
    evidence_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "QuestionCoverage":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("coverage evidence_ids must be unique")
        if self.status == CoverageStatus.UNANSWERED and self.evidence_ids:
            raise ValueError("unanswered coverage cannot reference evidence")
        return self


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    evidence_ids: list[Identifier] = Field(min_length=2)
    reason: NonEmptyStr


class BudgetRemaining(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_model_attempts: int = Field(default=0, ge=0)
    research_model_attempts: int = Field(default=0, ge=0)
    global_remaining_model_attempts: int = Field(default=0, ge=0)
    research_remaining_model_attempts: int = Field(default=0, ge=0)
    downstream_reserved_model_attempts: int = Field(default=0, ge=0)
    global_remaining_tokens: int = Field(default=0, ge=0)
    downstream_reserved_tokens: int = Field(default=0, ge=0)


class ResearchProgress(BaseModel):
    """Small, versioned state saved after each research tool step."""

    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    schema_version: int = Field(default=1, ge=1)
    step_index: int = Field(default=0, ge=0)
    questions: list[ProgressQuestion]
    coverage: list[QuestionCoverage]
    open_gaps: list[NonEmptyStr] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    source_refs: list[Identifier] = Field(default_factory=list)
    evidence_refs: list[Identifier] = Field(default_factory=list)
    tool_history_refs: list[Identifier] = Field(default_factory=list)
    conversation_ref: str | None = None
    budget_remaining: BudgetRemaining = Field(default_factory=BudgetRemaining)
    no_progress_streak: int = Field(default=0, ge=0)
    stop_reason: str | None = None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_question_coverage(self) -> "ResearchProgress":
        question_ids = [item.question_id for item in self.questions]
        coverage_ids = [item.question_id for item in self.coverage]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("progress question_ids must be unique")
        if coverage_ids != question_ids:
            raise ValueError("coverage must contain every question in plan order")
        return self


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    CACHE_HIT = "cache_hit"
    ALREADY_READ = "already_read"


class ValidatedEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    question_ids: list[Identifier] = Field(min_length=1)
    source_id: Identifier
    summary: NonEmptyStr
    support_type: SupportType


class ResearchObservation(BaseModel):
    """Program-owned feedback paired with one provider tool_call_id."""

    model_config = ConfigDict(extra="forbid")

    step_id: Identifier
    step_index: int = Field(ge=1)
    tool_call_id: NonEmptyStr
    tool_name: NonEmptyStr
    status: ObservationStatus
    externally_executed: bool = False
    invalid_action: bool = False
    source_ids: list[Identifier] = Field(default_factory=list)
    candidate_urls: list[str] = Field(default_factory=list)
    new_candidate_urls: list[str] = Field(default_factory=list)
    new_evidence_ids: list[Identifier] = Field(default_factory=list)
    validated_evidence: list[ValidatedEvidenceSummary] = Field(
        default_factory=list
    )
    coverage: list[QuestionCoverage] = Field(default_factory=list)
    coverage_changes: list[QuestionCoverage] = Field(default_factory=list)
    open_gaps: list[NonEmptyStr] = Field(default_factory=list)
    errors: list[NonEmptyStr] = Field(default_factory=list)
    budget_remaining: BudgetRemaining
    created_at: AwareDatetime


class ResearchActionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_tool_actions: int = Field(default=0, ge=0)
    local_tool_actions: int = Field(default=0, ge=0)
    invalid_tool_actions: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)


class ResearchProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    progress: ResearchProgress
    recent_steps: list[ResearchObservation] = Field(default_factory=list)
    action_stats: ResearchActionStats = Field(default_factory=ResearchActionStats)
