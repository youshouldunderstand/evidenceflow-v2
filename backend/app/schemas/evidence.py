"""Source snapshot and evidence schemas."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import (
    Identifier,
    NonEmptyStr,
    SourceQuality,
    SourceType,
    SupportType,
)


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Identifier
    task_id: Identifier
    search_id: Identifier | None
    title: NonEmptyStr
    url: NonEmptyStr
    source_type: SourceType
    retrieved_at: AwareDatetime
    content_hash: NonEmptyStr
    raw_content: str
    normalized_content: str
    normalized_url: str | None = None
    url_fragment: str | None = None
    content_type: str | None = None
    extraction_version: str = "readable-v1"
    published_at: AwareDatetime | None = None
    source_updated_at: AwareDatetime | None = None
    source_type_reason: str | None = None


class EvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    question_id: Identifier
    question_ids: list[Identifier] = Field(default_factory=list)
    evidence_summary: NonEmptyStr
    quote: NonEmptyStr
    source_id: Identifier
    locator: str | None
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    support_type: SupportType
    source_quality: SourceQuality

    @model_validator(mode="after")
    def validate_offsets(self) -> "EvidenceCard":
        if not self.question_ids:
            self.question_ids = [self.question_id]
        elif self.question_id not in self.question_ids:
            self.question_ids.insert(0, self.question_id)
        if len(self.question_ids) != len(set(self.question_ids)):
            raise ValueError("question_ids must be unique")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset and end_offset must be provided together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.start_offset >= self.end_offset
        ):
            raise ValueError("start_offset must be smaller than end_offset")
        return self
