"""Structured model outputs used internally by Phase 2 agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr, SupportType
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.progress import ResearchProgress
from app.schemas.research import SearchRecord


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    query: NonEmptyStr


class SearchQueryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[SearchQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_queries(self) -> "SearchQueryBatch":
        pairs = [(item.question_id, item.query.casefold()) for item in self.queries]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Search queries must be unique")
        return self


class EvidenceCandidate(BaseModel):
    """Untrusted LLM extraction before IDs, offsets, and quality are assigned."""

    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    question_ids: list[Identifier] = Field(default_factory=list)
    evidence_summary: NonEmptyStr
    quote: NonEmptyStr
    locator: str | None
    support_type: SupportType

    @model_validator(mode="after")
    def normalize_question_ids(self) -> "EvidenceCandidate":
        if not self.question_ids:
            self.question_ids = [self.question_id]
        elif self.question_id not in self.question_ids:
            self.question_ids.insert(0, self.question_id)
        if len(self.question_ids) != len(set(self.question_ids)):
            raise ValueError("question_ids must be unique")
        return self


class EvidenceExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[EvidenceCandidate]


class RejectedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    source_id: Identifier
    reasons: list[NonEmptyStr] = Field(min_length=1)


class ResearchCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    searches: list[SearchRecord]
    sources: list[SourceRecord]
    evidence: list[EvidenceCard]
    rejected_evidence: list[RejectedEvidence]
    errors: list[NonEmptyStr]
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    pages_read: int = Field(ge=0)
    stop_reason: str | None = None
    progress: ResearchProgress | None = None
